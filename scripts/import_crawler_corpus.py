#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
genshin-story-crawler -> 原神剧情助手知识库适配脚本

读取 crawler 仓库的 data/manifest.jsonl，校验 status / content_hash，
把每条文档归一化为项目统一 JSONL，并按 doc_id + content_hash 维护增量状态。

输出目录默认 content_data/crawler_corpus/：
  task.jsonl / map_text.jsonl / character.jsonl / ...
  _state.json     本次处理后的 doc_id -> content_hash 状态
  _changes.json   本次新增 / 更新 / 删除 / 跳过
  _summary.json   各模块数量、总字数、校验失败与顺序推断统计
  _skipped.json   被跳过的记录及原因（文件缺失、哈希不符、status 非 ready 等）

本脚本只读取 crawler manifest 和正文，不修改 content_data 下现有 JSON，
也不修改 Agent 代码。后续索引构建脚本可以直接读取这里的 *.jsonl。
"""

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = str(PROJECT_DIR.parent / "genshin-story-crawler" / "data" / "manifest.jsonl")
DEFAULT_OUT_DIR = str(PROJECT_DIR / "content_data" / "crawler_corpus")

# ====== 模块映射 ======
# title_keys 是正文头部中可直接作为条目标题的字段，按顺序取第一个非空值。
MODULE_INFO = {
    "task": {"category": "任务", "title_keys": ["任务名称"]},
    "map_text": {"category": "地图文本", "title_keys": ["文本名称"]},
    "character": {"category": "角色", "title_keys": []},
    "character_anecdote": {"category": "角色逸闻", "title_keys": []},
    "weapon": {"category": "武器", "title_keys": ["武器名称"]},
    "artifact": {"category": "圣遗物", "title_keys": ["圣遗物套装"]},
    "organization": {"category": "组织", "title_keys": ["组织名称"]},
    "enemy": {"category": "敌人", "title_keys": ["敌人名称"]},
    "food": {"category": "食物", "title_keys": ["食物名称"]},
    "item": {"category": "物品", "title_keys": ["物品名称"]},
    "animal": {"category": "动物", "title_keys": ["动物名称"]},
    "book": {"category": "书籍", "title_keys": ["书籍名称"]},
    "commission": {"category": "委托", "title_keys": ["委托名称"]},
    "npc": {"category": "NPC", "title_keys": ["NPC名称"]},
    "domain": {"category": "秘境", "title_keys": ["秘境名称"]},
    "furnishing": {"category": "洞天摆设", "title_keys": ["摆设名称"]},
    "phantom_theater": {"category": "幻想真境剧诗", "title_keys": ["卡牌名称"]},
    "namecard": {"category": "名片", "title_keys": ["名片名称"]},
    "outfit": {"category": "装扮", "title_keys": ["装扮名称"]},
    "gray_eye": {"category": "灰眸", "title_keys": ["灰眸词条"]},
}

# 主线章节排序，用于 task 文档的稳定排序；数值与 genshin_knowledge_base/main_story.py 保持一致。
CHAPTER_ORDER = {
    "开场动画": -1,
    "序章": 0,
    "第一章": 1,
    "第二章": 2,
    "第三章": 3,
    "第四章": 4,
    "第五章": 5,
    "空月之歌": 6,
    "第七章": 7,
    "间章": 8,
}

CN_DIGITS = {
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}

KEY_VALUE_RE = re.compile(r"^([^：:\n]{1,30})[：:]\s*(.*)$")
TASK_SECTION_RE = re.compile(r"^={3,}\s*(.*?)\s*={3,}$")
TASK_QUEST_NAME_RE = re.compile(r"^【(.+)】$")
TASK_PAGE_RE = re.compile(
    r"^(开场动画|序章|间章|空月之歌|第[一二三四五六七八九十百]+章)(?:\s*(.+))?$"
)
ACT_NUM_RE = re.compile(r"第([一二三四五六七八九十百]+)幕")

PLACEHOLDER_MARKERS = ("【待补充】", "【缺失】")


def _cn_to_int(value: str):
    """把中文数字转成 int；支持 十 / 十一 / 二十三 / 一百 等常见写法。"""
    if not value:
        return None
    if value.isdigit():
        return int(value)
    total = 0
    section = 0
    number = 0
    for char in value:
        if char in CN_DIGITS:
            number = CN_DIGITS[char]
        elif char == "十":
            if number == 0:
                number = 1
            section += number * 10
            number = 0
        elif char == "百":
            if number == 0:
                number = 1
            section += number * 100
            number = 0
    return section + number


def _parse_header_meta(text: str) -> dict:
    """提取正文开头 `键：值` 形式的头部元数据。"""
    meta = {}
    for raw in text.splitlines()[:40]:
        line = raw.strip()
        if not line:
            if meta:
                break
            continue
        if line.startswith("【") or line.startswith("===") or line.startswith("#"):
            break
        matched = KEY_VALUE_RE.match(line)
        if matched:
            key = matched.group(1).strip()
            value = matched.group(2).strip()
            if key not in meta:
                meta[key] = value
        elif meta:
            break
    return meta


def _parse_task_section(text: str) -> str:
    """从任务正文第一个 `=== 子任务名 ===` 中提取子任务名。"""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("==="):
            continue
        matched = TASK_SECTION_RE.match(stripped)
        if matched:
            return matched.group(1).strip()
    return ""


def _parse_task_quest_name(text: str) -> str:
    """从 `=== 一 ===` 之后的第一个 `【任务名】` 行提取真实任务名。

    魔神任务页面的 module_group 名常是「一 / 二 / 三」，真实任务名
    （如「白幕降下」）在正文第一个【】标题里；世界任务通常两者相同。
    """
    in_section = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("==="):
            in_section = True
            continue
        if not in_section or not stripped:
            continue
        matched = TASK_QUEST_NAME_RE.match(stripped)
        if matched:
            return matched.group(1).strip()
        return ""
    return ""


def _parse_subtask_order(subtask: str):
    """解析子任务名中的序号：一 / 二 / 十一 / 1 等。"""
    if not subtask:
        return None
    value = subtask.strip()
    matched = re.match(r"^[（(]?([一二三四五六七八九十百]+)[）)、.．]?$", value)
    if matched:
        return _cn_to_int(matched.group(1))
    matched = re.match(r"^(\d+)[、.．]?$", value)
    if matched:
        return int(matched.group(1))
    return None


def _parse_act_order(act_name: str):
    """解析幕序号：第二幕 -> 2；序奏 / 序幕 -> 0。"""
    if not act_name:
        return None
    if act_name.startswith("序奏") or act_name.startswith("序幕"):
        return 0
    matched = ACT_NUM_RE.search(act_name)
    if matched:
        return _cn_to_int(matched.group(1))
    return None


def _parse_chapter_act(task_name: str) -> dict:
    """把 `第七章 第二幕「死魂灵的夜曲」` 拆成章节和幕。"""
    if not task_name:
        return {}
    normalized = " ".join(task_name.split())
    matched = TASK_PAGE_RE.match(normalized)
    if not matched:
        return {}
    chapter_name = matched.group(1)
    act_name = (matched.group(2) or "").strip()
    return {
        "chapter_name": chapter_name,
        "chapter_order": CHAPTER_ORDER.get(chapter_name),
        "act_name": act_name,
        "act_order": _parse_act_order(act_name),
    }


def _is_placeholder(text: str) -> bool:
    """判断 crawler 在无内容时写入的占位正文。"""
    head = text[:300]
    return any(marker in head for marker in PLACEHOLDER_MARKERS)


def _atomic_write_text(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _atomic_write_json(path: Path, data):
    _atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2))


def _load_json(path: Path, default):
    if not path.exists():
        return default
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def _load_manifest(manifest_path: Path):
    records = []
    errors = []
    with open(manifest_path, "r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, 1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                errors.append({"line": line_number, "reason": f"JSON 解析失败: {exc.msg}"})
    return records, errors


def _normalize_record(raw: dict, crawler_root: Path, hash_mode: str):
    doc_id = str(raw.get("doc_id") or "").strip()
    module = str(raw.get("module") or "").strip()
    if not doc_id:
        return None, "缺少 doc_id"
    if module not in MODULE_INFO:
        return None, f"未知模块: {module}"
    if raw.get("status") != "ready":
        return None, f"status={raw.get('status')}"

    relative_path = str(raw.get("path") or "").strip()
    if not relative_path:
        return None, "缺少 path"
    source_path = crawler_root / relative_path
    if not source_path.exists():
        return None, f"正文不存在: {source_path}"
    try:
        raw_bytes = source_path.read_bytes()
    except OSError as exc:
        return None, f"正文读取失败: {exc}"

    file_hash = "sha256:" + hashlib.sha256(raw_bytes).hexdigest()
    text = raw_bytes.decode("utf-8", errors="strict")
    if text.startswith("\ufeff"):
        text = text.lstrip("\ufeff")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # content 模式对应 crawler 写入前的字符串哈希：只做换行归一化，不 strip，
    # 因为 save_document() 是对 content 原字符串计算哈希的。
    content_hash = "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
    text = text.strip()

    manifest_hash = str(raw.get("content_hash") or "")
    verified_by = ""
    if hash_mode == "file" and manifest_hash != file_hash:
        return None, f"content_hash 不一致: manifest={manifest_hash} file={file_hash}"
    if hash_mode == "content" and manifest_hash != content_hash:
        return None, f"content_hash 不一致: manifest={manifest_hash} content={content_hash}"
    if hash_mode == "either":
        if manifest_hash == file_hash:
            verified_by = "file"
        elif manifest_hash == content_hash:
            verified_by = "content"
        else:
            return None, (
                f"content_hash 不一致: manifest={manifest_hash} "
                f"file={file_hash} content={content_hash}"
            )
    else:
        verified_by = hash_mode

    if not text:
        return None, "正文为空"

    header_meta = _parse_header_meta(text)
    info = MODULE_INFO[module]

    page_title = ""
    for key in info["title_keys"]:
        value = header_meta.get(key)
        if value:
            page_title = value
            break
    if not page_title:
        page_title = str(raw.get("title") or "").strip()

    title = page_title
    extra_meta = {}
    if module == "task":
        subtask = _parse_task_section(text)
        quest_name = _parse_task_quest_name(text)
        extra_meta["page_title"] = page_title
        extra_meta["subtask"] = subtask
        extra_meta["quest_name"] = quest_name
        extra_meta["subtask_order"] = _parse_subtask_order(subtask)
        extra_meta.update(_parse_chapter_act(page_title))
        title = quest_name or subtask or page_title

    metadata = {}
    metadata.update(header_meta)
    metadata.update(extra_meta)
    metadata["doc_id"] = doc_id
    metadata["module"] = module
    metadata["entry_id"] = str(raw.get("entry_id") or "")
    metadata["section_id"] = "" if raw.get("section_id") is None else str(raw.get("section_id"))
    metadata["category"] = info["category"]
    metadata["source_type"] = raw.get("source_type")
    metadata["source_url"] = raw.get("source_url")
    metadata["content_hash"] = manifest_hash
    metadata["crawled_at"] = raw.get("crawled_at")
    metadata["release"] = raw.get("release")

    imported_at = datetime.now(timezone.utc).isoformat()
    record = {
        "id": f"crawler:{doc_id}",
        "doc_id": doc_id,
        "module": module,
        "entry_id": str(raw.get("entry_id") or ""),
        "section_id": "" if raw.get("section_id") is None else str(raw.get("section_id")),
        "title": title,
        "page_title": page_title,
        "category": info["category"],
        "text": text,
        "metadata": metadata,
        "source_type": raw.get("source_type"),
        "source_url": raw.get("source_url"),
        "content_hash": manifest_hash,
        "crawled_at": raw.get("crawled_at"),
        "release": raw.get("release"),
        "imported_at": imported_at,
        "quality": {
            "is_placeholder": _is_placeholder(text),
            "char_count": len(text),
            "hash_verified_by": verified_by,
        },
    }
    return record, None


def _task_sort_key(record: dict):
    meta = record.get("metadata", {})
    chapter_order = meta.get("chapter_order")
    act_order = meta.get("act_order")
    order = meta.get("order")
    return (
        chapter_order if chapter_order is not None else 9999,
        act_order if act_order is not None else 9999,
        order if order is not None else 9999,
        record.get("doc_id", ""),
    )


def _assign_task_orders(records: list) -> Counter:
    """按页面分组计算 task 子任务的 order。

    优先使用子任务名里的数字序号；没有数字序号时使用 crawled_at（文件保存时间），
    只有时间也相同才退回 manifest 原始顺序。
    """
    methods = Counter()
    groups = defaultdict(list)
    for record in records:
        if record["module"] == "task":
            groups[record["entry_id"]].append(record)

    for items in groups.values():
        has_numeric_order = all(
            item["metadata"].get("subtask_order") is not None for item in items
        )
        if has_numeric_order:
            ordered = sorted(items, key=lambda x: x["metadata"]["subtask_order"])
            method = "subtask_title"
        else:
            times = [item.get("crawled_at") or "" for item in items]
            if all(times) and len(set(times)) == len(times):
                ordered = sorted(items, key=lambda x: x["crawled_at"])
                method = "crawled_at"
            else:
                ordered = list(items)
                method = "manifest_order"
        for index, item in enumerate(ordered, start=1):
            item["metadata"]["order"] = index
            item["metadata"]["order_method"] = method
        methods[method] += 1
    return methods


def _write_module_files(out_dir: Path, records: list, selected_modules: set, dry_run: bool):
    by_module = defaultdict(list)
    for record in records:
        if record["module"] in selected_modules:
            by_module[record["module"]].append(record)

    written = {}
    for module, items in sorted(by_module.items()):
        if module == "task":
            items = sorted(items, key=_task_sort_key)
        else:
            items = sorted(items, key=lambda x: (x.get("doc_id") or ""))
        lines = [json.dumps(item, ensure_ascii=False) for item in items]
        payload = "\n".join(lines) + ("\n" if lines else "")
        target = out_dir / f"{module}.jsonl"
        if not dry_run:
            _atomic_write_text(target, payload)
        written[module] = {
            "path": str(target),
            "docs": len(items),
            "chars": sum(len(item.get("text", "")) for item in items),
        }
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description="把 genshin-story-crawler 的 manifest 适配为项目知识库 JSONL")
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST, help="crawler 的 data/manifest.jsonl 路径")
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR, help="输出目录，默认 content_data/crawler_corpus")
    parser.add_argument("--modules", default="", help="只处理指定模块，多个用逗号分隔；默认全部")
    parser.add_argument("--dry-run", action="store_true", help="只计算和报告，不写任何文件")
    parser.add_argument(
        "--hash-mode",
        choices=["file", "content", "either"],
        default="file",
        help="content_hash 校验方式：file=文件字节（build_manifest 默认）、content=换行归一化后的正文、either=任一匹配即可",
    )
    parser.add_argument("--skip-placeholders", action="store_true", help="跳过包含【待补充】/【缺失】的占位文档")
    args = parser.parse_args()

    manifest_path = Path(args.manifest).resolve()
    if not manifest_path.exists():
        print(f"[错误] manifest 不存在: {manifest_path}")
        return 2

    crawler_root = manifest_path.parent.parent
    if not (crawler_root / "data").exists():
        print(f"[错误] 无法从 manifest 推断 crawler 根目录: {crawler_root}")
        return 2

    out_dir = Path(args.out_dir).resolve()
    state_path = out_dir / "_state.json"
    changes_path = out_dir / "_changes.json"
    summary_path = out_dir / "_summary.json"
    skipped_path = out_dir / "_skipped.json"

    requested_modules = {item.strip() for item in args.modules.split(",") if item.strip()}
    if requested_modules:
        unknown = requested_modules - set(MODULE_INFO)
        if unknown:
            print(f"[错误] 不支持的模块: {', '.join(sorted(unknown))}")
            return 2
        selected_modules = requested_modules
    else:
        selected_modules = set(MODULE_INFO)

    raw_records, manifest_errors = _load_manifest(manifest_path)

    records = []
    skipped = []
    skipped_reasons = Counter()
    for raw in raw_records:
        module = str(raw.get("module") or "")
        if requested_modules and module not in selected_modules:
            continue
        record, reason = _normalize_record(raw, crawler_root, args.hash_mode)
        if reason:
            skipped.append({"doc_id": raw.get("doc_id"), "module": module, "reason": reason})
            skipped_reasons[reason.split(":", 1)[0]] += 1
            continue
        if args.skip_placeholders and record["quality"]["is_placeholder"]:
            skipped.append({"doc_id": record["doc_id"], "module": module, "reason": "placeholder"})
            skipped_reasons["placeholder"] += 1
            continue
        records.append(record)

    order_methods = _assign_task_orders(records)

    old_state = _load_json(state_path, {"documents": {}})
    old_documents = old_state.get("documents", {}) if isinstance(old_state, dict) else {}
    new_documents = {
        record["doc_id"]: {
            "module": record["module"],
            "title": record["title"],
            "content_hash": record["content_hash"],
            "imported_at": record["imported_at"],
        }
        for record in records
    }

    scoped_old = {
        doc_id: info
        for doc_id, info in old_documents.items()
        if info.get("module") in selected_modules
    }
    added = sorted(doc_id for doc_id in new_documents if doc_id not in scoped_old)
    updated = sorted(
        doc_id
        for doc_id in new_documents
        if doc_id in scoped_old
        and scoped_old[doc_id].get("content_hash") != new_documents[doc_id].get("content_hash")
    )
    removed = sorted(
        doc_id for doc_id in scoped_old if doc_id not in new_documents
    )
    unchanged = len(new_documents) - len(added) - len(updated)

    merged_documents = dict(old_documents)
    for doc_id, info in new_documents.items():
        merged_documents[doc_id] = info
    for doc_id in removed:
        merged_documents.pop(doc_id, None)

    changes = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "manifest": str(manifest_path),
        "modules": sorted(selected_modules),
        "added": added,
        "updated": updated,
        "removed": removed,
        "unchanged": unchanged,
        "skipped": len(skipped),
        "counts": {
            "added": len(added),
            "updated": len(updated),
            "removed": len(removed),
            "unchanged": unchanged,
        },
    }

    written = _write_module_files(out_dir, records, selected_modules, args.dry_run)

    module_stats = {}
    for module in selected_modules:
        items = [record for record in records if record["module"] == module]
        if not items:
            continue
        module_stats[module] = {
            "docs": len(items),
            "chars": sum(len(item.get("text", "")) for item in items),
            "placeholders": sum(1 for item in items if item["quality"]["is_placeholder"]),
        }

    summary = {
        "generated_at": changes["generated_at"],
        "manifest": str(manifest_path),
        "crawler_root": str(crawler_root),
        "out_dir": str(out_dir),
        "dry_run": args.dry_run,
        "modules": module_stats,
        "total_docs": len(records),
        "total_chars": sum(len(record.get("text", "")) for record in records),
        "manifest_errors": manifest_errors,
        "skipped_reasons": dict(skipped_reasons),
        "order_methods": dict(order_methods),
        "changes": changes["counts"],
        "written": written,
    }

    if not args.dry_run:
        # 清理本次没有产出的旧模块文件；--modules 过滤时只动本次选中的模块。
        existing_modules = {
            path.stem for path in out_dir.glob("*.jsonl") if path.stem in MODULE_INFO
        }
        for stale_module in sorted(existing_modules - set(written)):
            if requested_modules and stale_module not in selected_modules:
                continue
            (out_dir / f"{stale_module}.jsonl").unlink(missing_ok=True)

        _atomic_write_json(state_path, {"generated_at": summary["generated_at"], "documents": merged_documents})
        _atomic_write_json(changes_path, changes)
        _atomic_write_json(summary_path, summary)
        if skipped:
            _atomic_write_json(skipped_path, skipped)
        else:
            skipped_path.unlink(missing_ok=True)

    print(f"manifest: {manifest_path}")
    print(f"模块: {', '.join(sorted(selected_modules))}")
    print(f"可导入文档: {len(records)}；跳过: {len(skipped)}；manifest 错误: {len(manifest_errors)}")
    print(f"新增: {len(added)}；更新: {len(updated)}；删除: {len(removed)}；不变: {unchanged}")
    if order_methods:
        print(f"task 顺序推断: {dict(order_methods)}")
    if skipped_reasons:
        print(f"跳过原因: {dict(skipped_reasons)}")
    if args.dry_run:
        print("dry-run：未写入任何文件")
    else:
        print(f"输出目录: {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
