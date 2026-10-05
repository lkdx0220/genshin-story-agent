# -*- coding: utf-8 -*-
"""L3 第五步图谱扩展 A/B 证据包实验（临时脚本，不提交）。

变体：
  on100 = 现状：扩展开启，per-seed expand limit=100
  on20  = 扩展开启，per-seed expand limit=20
  off   = 关闭第五步扩展（_LORE_EXPAND_TYPES 置空）

只构建全景证据包，不调用回答 LLM，用于比较：
- 任务/地图/实体数量
- 证据字符数
- 扩展出来的“未被正文点名”的实体（潜在噪声）
- 构建耗时
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

PROJECT = Path(r"C:\Users\24701\Desktop\原神剧情\CASE-原神剧情助手-修改用")
sys.path.insert(0, str(PROJECT))
os.chdir(PROJECT)

import app.agent.nodes as nodes  # noqa: E402
from wiki_entry_graph import WikiEntryGraph  # noqa: E402


CASES = {
    "C4": (
        "我没看明白至冬国的“她的宫殿正坍塌向风雪、爱憎的赫斯珀利德斯、"
        "一边是宫殿一边是陵阙、在生命的寓所”这四条大型支线任务组成的庞大的支线任务，"
        "你能结合任务文本、对应地区的地图文本，帮我梳理一下剧情，并评价参与其中出现的所有角色吗？"
        "部分角色还与别的支线任务、地图文本有所关联，需要综合来看"
    ),
    "P1": "综合梳理「她的宫殿正坍塌向风雪」和「在生命的寓所」的剧情，并评价相关所有角色",
    "P2": "综合梳理「爱憎的赫斯珀利德斯」和「一边是宫殿一边是陵阙」的剧情，并评价相关所有角色",
}

EXPECTED = {
    "C4": [
        "伊兹梅洛", "卡谢伊", "莫罗", "阿尔维斯", "拉莉莎", "弗瑞奥萨",
        "欧维", "弗谢沃洛德", "完全树", "沙皇白桦", "扎拉",
        "炉火融炼之心", "勒庇依", "雪国的妖精", "影中沉凝的幻灭",
        "深廊终曲", "一引三收", "曾为灵魂的木舟", "纸页泛黄的文件",
    ],
    "P1": ["她的宫殿正坍塌向风雪", "在生命的寓所"],
    "P2": ["爱憎的赫斯珀利德斯", "一边是宫殿一边是陵阙"],
}


def apply_variant(name: str) -> None:
    if name == "off":
        # 第五步只允许 _LORE_EXPAND_TYPES 中的类型进入扩展；
        # 置空后所有图谱邻居都会被 continue 掉，等效于关闭。
        nodes._LORE_EXPAND_TYPES = set()
    elif name == "on20":
        original = WikiEntryGraph.expand

        def _expand_with_cap(self, entry_id, limit=20):
            return original(self, entry_id, limit=min(int(limit or 0), 20))

        WikiEntryGraph.expand = _expand_with_cap


def run_case(case_id: str, question: str) -> dict:
    graph = nodes._load_wiki_graph_cached()
    if graph is None:
        return {"case_id": case_id, "error": "graph not loaded"}

    t0 = time.time()
    matched = nodes._match_graph_task_titles(graph, question)
    maps = nodes._collect_graph_map_texts(graph, matched) if matched else []
    task_texts = [nodes._entry_story_text(e) or (e.full_text or "") for e in matched]
    entities = (
        nodes._collect_related_entity_entries(graph, matched, task_texts, maps)
        if matched
        else []
    )
    collect_sec = round(time.time() - t0, 3)

    t1 = time.time()
    result = nodes._maybe_auto_full_text_panoramic(
        {
            "user_query": question,
            "rewritten_query": question,
            "run_id": f"exp_evidence_{case_id}",
        },
        [],
        [],
        0,
    )
    pack_sec = round(time.time() - t1, 3)

    content = ""
    if result:
        for msg in result.get("messages") or []:
            content = getattr(msg, "content", "") or ""

    combined = "\n".join(
        task_texts + [nodes._entry_story_text(m) for m in maps]
    )
    speakers = nodes._extract_speaker_names(task_texts)

    named = []
    unnamed = []
    speaker_hits = []
    for entry in entities:
        row = {
            "id": entry.entry_id,
            "title": entry.title,
            "type": entry.entry_type,
            "chars": len(nodes._entry_story_text(entry)),
        }
        if nodes._entity_is_speaker(entry, speakers):
            speaker_hits.append(row)
            continue
        hits = nodes._entity_specific_alias_hits(entry, combined)
        row["hits"] = hits
        if hits > 0:
            named.append(row)
        else:
            unnamed.append(row)

    expected = EXPECTED.get(case_id, [])
    expected_hits = [k for k in expected if k in content]

    return {
        "case_id": case_id,
        "question": question,
        "matched_tasks": [
            {"id": e.entry_id, "title": e.title, "chars": len(nodes._entry_story_text(e))}
            for e in matched
        ],
        "map_count": len(maps),
        "maps": [{"id": e.entry_id, "title": e.title, "chars": len(nodes._entry_story_text(e))} for e in maps],
        "entity_count": len(entities),
        "entity_chars": sum(len(nodes._entry_story_text(e)) for e in entities),
        "entity_ids": [e.entry_id for e in entities],
        "named_entities": named,
        "speaker_entities": speaker_hits,
        "unnamed_entities": unnamed,
        "expected_hits": expected_hits,
        "expected_total": len(expected),
        "evidence_chars": len(content),
        "collect_sec": collect_sec,
        "pack_sec": pack_sec,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", choices=["on100", "on20", "off"], required=True)
    parser.add_argument("--cases", default="C4,P1,P2")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    apply_variant(args.variant)
    case_ids = [x.strip() for x in args.cases.split(",") if x.strip()]
    rows = []
    for case_id in case_ids:
        question = CASES[case_id]
        row = run_case(case_id, question)
        row["variant"] = args.variant
        rows.append(row)
        print(
            f"[{args.variant}] {case_id} tasks={len(row['matched_tasks'])} "
            f"maps={row['map_count']} ents={row['entity_count']} "
            f"named={len(row['named_entities'])} speaker={len(row['speaker_entities'])} "
            f"unnamed={len(row['unnamed_entities'])} chars={row['evidence_chars']} "
            f"collect={row['collect_sec']}s pack={row['pack_sec']}s",
            flush=True,
        )
        for e in row["unnamed_entities"][:20]:
            print(f"    unnamed: {e['id']} {e['type']} {e['title']} ({e['chars']}字) hits=0", flush=True)

    if args.out:
        Path(args.out).write_text(
            json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[{args.variant}] -> {args.out}", flush=True)


if __name__ == "__main__":
    main()
