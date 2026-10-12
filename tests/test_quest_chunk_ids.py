# -*- coding: utf-8 -*-
"""index_quests 的 chunk ID 生成测试（2026-10-12 ID 碰撞修复）。

覆盖：
- 跨文件同名任务：首个来源（QUEST_SOURCE_PRIORITY）拿裸 ID，后续来源 @source_key；
- 优先级顺序决定 canonical 归属；
- 同文件内同名任务：@source_key#2 兜底；
- 全量无重复、多次运行结果一致（确定性）；
- meta 带 source_key。
"""

import os

# kb_vector_store 顶部强制检查嵌入 key（import kb_build_index 会连带触发），
# 测试环境无真实 key 时给假值；真实 .env 存在时 setdefault 不覆盖。
os.environ.setdefault("DASHSCOPE_API_KEY", "test-key-for-unit-tests")

import json

import pytest

import kb_build_index as kbi


class _CaptureStore:
    """只捕获 add 调用，不写文件、不嵌入。"""

    def __init__(self):
        self.calls = []

    def add(self, collection, ids, documents, metadatas):
        self.calls.append((collection, list(ids), list(documents), list(metadatas)))


_LONG_TEXT_A = "这是来源甲的同名任务正文，" * 8  # >50 字
_LONG_TEXT_B = "这是来源乙的同名任务正文，内容不同，" * 8
_LONG_TEXT_C = "这是来源丙的同名任务正文，又不一样，" * 8


def _write_quests(directory, filename, entries):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / filename).write_text(
        json.dumps(entries, ensure_ascii=False), encoding="utf-8")


def _capture(monkeypatch, tmp_path):
    monkeypatch.setattr(kbi, "CONTENT_DIR", str(tmp_path))
    store = _CaptureStore()
    kbi.index_quests(store, {})
    ids, docs, metas = [], [], []
    for collection, batch_ids, batch_docs, batch_metas in store.calls:
        assert collection == "kb_quests_vec"
        ids += batch_ids
        docs += batch_docs
        metas += batch_metas
    return ids, docs, metas


def test_cross_file_same_title_gets_suffix(monkeypatch, tmp_path):
    """传说任务与活动剧情同名：传说任务 canonical，活动剧情带 @后缀。"""
    _write_quests(tmp_path, "quests_传说任务.json",
                  [{"title": "同名任务", "category": "传说任务", "text": _LONG_TEXT_A}])
    _write_quests(tmp_path, "quests_活动剧情.json",
                  [{"title": "同名任务", "category": "活动剧情", "text": _LONG_TEXT_B}])
    ids, docs, metas = _capture(monkeypatch, tmp_path)
    assert "quest:同名任务:chunk:0" in ids
    assert "quest:同名任务:chunk:0@活动剧情" in ids
    assert len(ids) == len(set(ids)), "跨文件同名任务不得产生重复 ID"


def test_priority_decides_canonical(monkeypatch, tmp_path):
    """魔神任务优先级高于传说任务：魔神任务拿裸 ID。"""
    _write_quests(tmp_path, "quests_魔神任务.json",
                  [{"title": "同名任务", "category": "魔神任务", "text": _LONG_TEXT_C}])
    _write_quests(tmp_path, "quests_传说任务.json",
                  [{"title": "同名任务", "category": "传说任务", "text": _LONG_TEXT_A}])
    ids, _, _ = _capture(monkeypatch, tmp_path)
    assert "quest:同名任务:chunk:0" in ids  # 魔神任务 canonical
    assert "quest:同名任务:chunk:0@传说任务" in ids


def test_unknown_file_sorts_after_known(monkeypatch, tmp_path):
    """未知来源文件名字典序排在已知之后，确定性不依赖 os.listdir。"""
    _write_quests(tmp_path, "quests_传说任务.json",
                  [{"title": "同名任务", "category": "", "text": _LONG_TEXT_A}])
    _write_quests(tmp_path, "quests_新来源.json",
                  [{"title": "同名任务", "category": "", "text": _LONG_TEXT_B}])
    ids, _, _ = _capture(monkeypatch, tmp_path)
    assert "quest:同名任务:chunk:0" in ids  # 已知优先级文件 canonical
    assert "quest:同名任务:chunk:0@新来源" in ids


def test_same_file_same_title_hash_fallback(monkeypatch, tmp_path):
    """同文件内两个同名条目：第二个用 @source_key#2，仍匹配 retrieval 正则。"""
    _write_quests(tmp_path, "quests_传说任务.json", [
        {"title": "同名任务", "category": "", "text": _LONG_TEXT_A},
        {"title": "同名任务", "category": "", "text": _LONG_TEXT_B},
    ])
    ids, _, _ = _capture(monkeypatch, tmp_path)
    assert "quest:同名任务:chunk:0" in ids
    assert "quest:同名任务:chunk:0@传说任务#2" in ids
    assert len(ids) == len(set(ids))


def test_deterministic_across_runs(monkeypatch, tmp_path):
    """同一输入两次捕获，ID 序列完全一致。"""
    _write_quests(tmp_path, "quests_传说任务.json",
                  [{"title": "同名任务", "category": "", "text": _LONG_TEXT_A}])
    _write_quests(tmp_path, "quests_活动剧情.json",
                  [{"title": "同名任务", "category": "", "text": _LONG_TEXT_B}])
    _write_quests(tmp_path, "quests_世界任务.json",
                  [{"title": "另一任务", "category": "", "text": _LONG_TEXT_C}])
    first = _capture(monkeypatch, tmp_path)
    second = _capture(monkeypatch, tmp_path)
    assert first[0] == second[0], "ID 生成必须确定（canonical 归属不随运行漂移）"
    assert first[1] == second[1]


def test_meta_contains_source_key(monkeypatch, tmp_path):
    _write_quests(tmp_path, "quests_传说任务.json",
                  [{"title": "任务甲", "category": "", "text": _LONG_TEXT_A}])
    _, _, metas = _capture(monkeypatch, tmp_path)
    assert metas and metas[0]["source_key"] == "传说任务"
    assert metas[0]["source_file"] == "quests_传说任务.json"


def test_docs_unchanged_by_id_scheme(monkeypatch, tmp_path):
    """ID 方案修复不得影响 docs 生成（rekey 复用向量的前提）。"""
    _write_quests(tmp_path, "quests_传说任务.json",
                  [{"title": "同名任务", "category": "", "text": _LONG_TEXT_A}])
    _write_quests(tmp_path, "quests_活动剧情.json",
                  [{"title": "同名任务", "category": "", "text": _LONG_TEXT_B}])
    _, docs, _ = _capture(monkeypatch, tmp_path)
    assert any("任务：同名任务" in d and "来源甲" in d for d in docs)
    assert any("任务：同名任务" in d and "来源乙" in d for d in docs), "两个来源的正文都必须保留"
    assert len([d for d in docs if "来源甲" in d]) >= 1
    assert len([d for d in docs if "来源乙" in d]) >= 1
