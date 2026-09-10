#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
B站 Wiki 地基层 + 米游社官方适配层的保守合并脚本（v1）

只处理 task / map_text / npc 三个模块：
  - task     ：官方子任务按真实任务名与 B站任务标题匹配
  - map_text ：官方地图文本按“地区 + 地点 + 正文重合度”匹配 B站地点文本
  - npc      ：官方 NPC 按规范化名称匹配 B站 NPC，名称有歧义就进复核清单

原则：
  1. 官方来源作为 primary_source，B站作为 supplement / bwiki_only 保留；
  2. 不做模糊标题拼表，不在低置信度下自动合并；
  3. 无法唯一匹配、正文重合度过低的实体统一写入 _review.json；
  4. 本脚本只读 content_data 现有数据与 crawler_corpus，只写 content_data/merged/，
     不修改任何原文件。

用法：
  python scripts/merge_crawler_corpus.py --dry-run
  python scripts/merge_crawler_corpus.py --modules task,map_text,npc
  python scripts/merge_crawler_corpus.py --coverage-threshold 0.85
"""

import argparse
import hashlib
import json
import os
import re
import sys
import difflib
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_OFFICIAL_DIR = str(PROJECT_DIR / "content_data" / "crawler_corpus")
DEFAULT_BWIKI_DIR = str(PROJECT_DIR / "content_data")
DEFAULT_OUT_DIR = str(PROJECT_DIR / "content_data" / "merged")

SUPPORTED_MODULES = ("task", "map_text", "npc")

NORMALIZE_RE = re.compile(r"\s+")
LOCATION_RE = re.compile(r"【(.+?)】")
ACT_PREFIX_RE = re.compile(r"^第[一二三四五六七八九十百]+幕")
TASK_PAGE_RE = re.compile(
    r"^(开场动画|序章|间章|空月之歌|第[一二三四五六七八九十百]+章)(?:\s*(.+))?$"
)


def _normalize(text: str) -> str:
    """去掉空白，用于中文文本包含/相似度判断。"""
    return NORMALIZE_RE.sub("", text or "")


def _short_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def _normalize_for_match(text: str) -> str:
    """去掉空白和 B站 常见行首标记，用于包含判断。"""
    return _normalize(text).lstrip("*+-：:")


def _act_core(text: str) -> str:
    """把「第二幕「死魂灵的夜曲」」归一成「死魂灵的夜曲」，方便跨源对齐幕名。"""
    value = _normalize(text)
    value = ACT_PREFIX_RE.sub("", value)
    return value.strip("「」『』")


def _atomic_write_text(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _atomic_write_json(path: Path, data):
    _atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2))


def _atomic_write_jsonl(path: Path, records: list):
    lines = [json.dumps(record, ensure_ascii=False) for record in records]
    _atomic_write_text(path, "\n".join(lines) + ("\n" if lines else ""))


def _load_jsonl(path: Path) -> list:
    records = []
    if not path.exists():
        return records
    with open(path, "r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _load_json(path: Path, default):
    if not path.exists():
        return default
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def _text_score(primary: str, secondary: str, min_line_len: int = 4) -> float:
    """计算 secondary 在 primary 中的重合度，范围 0~1。"""
    p = _normalize(primary)
    s = _normalize(secondary)
    if not p or not s:
        return 0.0
    if p == s:
        return 1.0

    p_match = _normalize_for_match(primary)
    s_match = _normalize_for_match(secondary)
    shorter, longer = (p_match, s_match) if len(p_match) <= len(s_match) else (s_match, p_match)
    if shorter in longer and len(shorter) / len(longer) >= 0.5:
        return 1.0

    lines = [line for line in (secondary or "").splitlines() if len(_normalize(line)) >= min_line_len]
    if lines:
        matched = sum(1 for line in lines if _normalize_for_match(line) in p_match)
        line_coverage = matched / len(lines)
    else:
        line_coverage = 0.0

    ratio = difflib.SequenceMatcher(None, p, s).ratio()
    return max(line_coverage, ratio)


def _unique_supplement(primary: str, secondary: str, min_line_len: int = 4) -> str:
    """从 secondary 中提取 primary 没有的段落，避免重复入库。

    对行首的 `*` / `-` / `：` 等 B站 常见标记做归一化后再判断包含关系，
    但保留原始行文本写入 supplement_text。
    """
    p_match = _normalize_for_match(primary)
    output = []
    seen = set()
    for line in (secondary or "").splitlines():
        stripped = line.strip()
        normalized = _normalize(stripped)
        if len(normalized) < min_line_len:
            continue
        if (
            stripped.startswith(".")
            or stripped.startswith("<")
            or stripped.startswith("{{")
            or stripped.startswith("[[")
            or stripped.startswith("{|")
            or stripped.startswith("|-")
            or stripped.startswith("|}")
            or stripped.startswith("__")
            or (stripped.startswith("==") and stripped.endswith("=="))
        ):
            continue
        match_key = _normalize_for_match(stripped)
        if match_key in p_match or match_key in seen:
            continue
        seen.add(match_key)
        output.append(stripped)
    return "\n".join(output)


def _source_ref(source: str, doc_id: str, title: str, content_hash: str = "", role: str = "") -> dict:
    return {
        "source": source,
        "doc_id": doc_id,
        "title": title,
        "content_hash": content_hash,
        "role": role,
    }


def _parse_chapter_act_from_series(series: str) -> tuple:
    """B站任务的 系列任务 形如「第七章,死魂灵的夜曲」。"""
    if not series:
        return "", ""
    parts = [part.strip() for part in str(series).split(",") if part.strip()]
    if len(parts) >= 2:
        return parts[0], parts[1]
    if len(parts) == 1:
        return parts[0], ""
    return "", ""


# ====== B站数据加载 ======

def load_bwiki_tasks(content_dir: Path):
    records = []
    for path in sorted(content_dir.glob("quests_*.json")):
        if path.name == "quests_processed.json":
            continue
        data = _load_json(path, [])
        if not isinstance(data, list):
            continue
        for item in data:
            metadata = item.get("metadata") or {}
            title = item.get("title") or metadata.get("任务名称") or ""
            name = metadata.get("任务名称") or title
            if not name:
                continue
            pageid = item.get("pageid")
            pageid = str(pageid) if pageid not in ("", None, 0, "0") else ""
            series = metadata.get("系列任务") or ""
            chapter = metadata.get("chapter_name") or ""
            act = metadata.get("act_name") or ""
            if not chapter and not act:
                chapter, act = _parse_chapter_act_from_series(series)
            key = f"bwiki:task:{pageid}" if pageid else f"bwiki:task:{_short_hash(path.name + '|' + name)}"
            records.append({
                "key": key,
                "module": "task",
                "name": name,
                "norm_name": _normalize(name),
                "title": title,
                "chapter": chapter,
                "act": act,
                "norm_chapter": _normalize(chapter),
                "norm_act": _normalize(act),
                "act_core": _act_core(act),
                "region": metadata.get("任务地区") or "",
                "version": metadata.get("所属版本") or "",
                "category": item.get("category") or "",
                "pageid": pageid,
                "source_file": path.name,
                "text": item.get("text") or "",
                "metadata": metadata,
            })
    return records


def load_bwiki_map_text(content_dir: Path):
    lore = _load_json(content_dir / "lore.json", [])
    records = []
    for item in lore:
        title = str(item.get("title") or "")
        if not title.startswith("地图文本"):
            continue
        parts = [part.strip() for part in title.split("/")]
        if len(parts) < 2:
            continue
        region = parts[1] if len(parts) >= 3 else ""
        location = parts[-1]
        if not location:
            continue
        text = item.get("text") or ""
        key = f"bwiki:map_text:{_short_hash(region + '|' + location + '|' + text[:120])}"
        records.append({
            "key": key,
            "module": "map_text",
            "region": region,
            "location": location,
            "norm_region": _normalize(region),
            "norm_location": _normalize(location),
            "title": title,
            "text": text,
            "source": item.get("source") or "",
        })
    return records


def load_bwiki_npcs(content_dir: Path):
    data = _load_json(content_dir / "npcs_processed.json", {})
    records = []
    if not isinstance(data, dict):
        return records
    for raw_name, item in data.items():
        if not isinstance(item, dict):
            continue
        name = item.get("name") or raw_name
        base_name = name
        for sep in ("【", "（", "("):
            index = base_name.find(sep)
            if index > 0:
                base_name = base_name[:index]
        text = item.get("doc_for_embed") or item.get("dialogue") or ""
        records.append({
            "key": f"bwiki:npc:{name}",
            "module": "npc",
            "name": name,
            "norm_name": _normalize(name),
            "base_name": base_name,
            "norm_base_name": _normalize(base_name),
            "text": text,
            "metadata": item,
        })
    return records


# ====== 通用合并结果构造 ======

def _base_record(canonical_id: str, module: str, title: str, primary_source: str,
                 merge_status: str, text: str, metadata: dict, sources: list,
                 supplement_text: str = "", supplement_metadata=None,
                 aliases=None, merge_notes=None) -> dict:
    return {
        "canonical_id": canonical_id,
        "module": module,
        "title": title,
        "primary_source": primary_source,
        "merge_status": merge_status,
        "text": text,
        "supplement_text": supplement_text,
        "metadata": metadata or {},
        "supplement_metadata": supplement_metadata or {},
        "sources": sources or [],
        "aliases": aliases or [],
        "merge_notes": merge_notes or [],
    }


def merge_tasks(official: list, bwiki: list, coverage_threshold: float, review: list):
    index = defaultdict(list)
    for record in bwiki:
        index[record["norm_name"]].append(record)

    used = set()
    output = []
    counts = Counter()

    for off in official:
        metadata = off.get("metadata") or {}
        official_name = metadata.get("quest_name") or off.get("title") or ""
        norm_name = _normalize(official_name)
        candidates = list(index.get(norm_name, []))
        if not candidates:
            subtask = metadata.get("subtask") or ""
            if subtask and _normalize(subtask) != norm_name:
                candidates = list(index.get(_normalize(subtask), []))
        if not candidates:
            output.append(_base_record(
                canonical_id=f"official:{off['doc_id']}",
                module="task",
                title=official_name,
                primary_source="official_hoyowiki",
                merge_status="official_only",
                text=off.get("text") or "",
                metadata=metadata,
                sources=[_source_ref("official_hoyowiki", off["doc_id"], official_name, off.get("content_hash", ""), "primary")],
                merge_notes=["no_bwiki_candidate"],
            ))
            counts["official_only"] += 1
            continue

        chapter_norm = _normalize(metadata.get("chapter_name") or "")
        act_core = _act_core(metadata.get("act_name") or "")
        filtered = []
        for candidate in candidates:
            if chapter_norm and candidate["norm_chapter"] and candidate["norm_chapter"] != chapter_norm:
                continue
            if act_core and candidate["act_core"] and candidate["act_core"] != act_core:
                continue
            filtered.append(candidate)

        if (chapter_norm or act_core) and not filtered:
            review.append({
                "module": "task",
                "reason": "chapter_act_filtered_all_bwiki_candidates",
                "official": {
                    "doc_id": off["doc_id"],
                    "title": official_name,
                    "chapter": metadata.get("chapter_name"),
                    "act": metadata.get("act_name"),
                },
                "candidates": [
                    {"key": item["key"], "title": item["name"], "chapter": item["chapter"], "act": item["act"]}
                    for item in candidates
                ],
            })
            output.append(_base_record(
                canonical_id=f"official:{off['doc_id']}",
                module="task",
                title=official_name,
                primary_source="official_hoyowiki",
                merge_status="review",
                text=off.get("text") or "",
                metadata=metadata,
                sources=[_source_ref("official_hoyowiki", off["doc_id"], official_name, off.get("content_hash", ""), "primary")],
                merge_notes=["chapter_act_filtered_all_bwiki_candidates"],
            ))
            counts["review"] += 1
            continue

        candidates = filtered

        if len(candidates) > 1:
            review.append({
                "module": "task",
                "reason": "ambiguous_bwiki_candidates",
                "official": {"doc_id": off["doc_id"], "title": official_name, "chapter": metadata.get("chapter_name"), "act": metadata.get("act_name")},
                "candidates": [{"key": item["key"], "title": item["name"], "chapter": item["chapter"], "act": item["act"]} for item in candidates],
            })
            output.append(_base_record(
                canonical_id=f"official:{off['doc_id']}",
                module="task",
                title=official_name,
                primary_source="official_hoyowiki",
                merge_status="review",
                text=off.get("text") or "",
                metadata=metadata,
                sources=[_source_ref("official_hoyowiki", off["doc_id"], official_name, off.get("content_hash", ""), "primary")],
                merge_notes=["ambiguous_bwiki_candidates"],
            ))
            counts["review"] += 1
            continue

        candidate = candidates[0]
        supplement = _unique_supplement(off.get("text") or "", candidate["text"])
        sources = [
            _source_ref("official_hoyowiki", off["doc_id"], official_name, off.get("content_hash", ""), "primary"),
            _source_ref("bilibili_wiki", candidate["key"], candidate["name"], "", "supplement"),
        ]
        output.append(_base_record(
            canonical_id=f"official:{off['doc_id']}",
            module="task",
            title=official_name,
            primary_source="official_hoyowiki",
            merge_status="merged",
            text=off.get("text") or "",
            metadata=metadata,
            sources=sources,
            supplement_text=supplement,
            supplement_metadata=candidate["metadata"],
            aliases=[candidate["title"], candidate["name"], candidate["pageid"]],
            merge_notes=["matched_by_quest_name"],
        ))
        used.add(candidate["key"])
        counts["merged"] += 1
        if supplement:
            counts["merged_with_supplement"] += 1

    for record in bwiki:
        if record["key"] in used:
            continue
        output.append(_base_record(
            canonical_id=record["key"],
            module="task",
            title=record["name"],
            primary_source="bilibili_wiki",
            merge_status="bwiki_only",
            text=record["text"],
            metadata=record["metadata"],
            sources=[_source_ref("bilibili_wiki", record["key"], record["name"], "", "primary")],
            aliases=[record["title"], record["pageid"]],
            merge_notes=["no_official_counterpart"],
        ))
        counts["bwiki_only"] += 1
    return output, counts


def _parse_official_location(record: dict) -> tuple:
    metadata = record.get("metadata") or {}
    region = metadata.get("地区") or ""
    title = record.get("page_title") or record.get("title") or ""
    matched = LOCATION_RE.search(title)
    if matched:
        return region, matched.group(1).strip(), title.split("【", 1)[0].strip()
    return region, title.strip(), ""


def merge_map_text(official: list, bwiki: list, coverage_threshold: float, review: list, min_line_len: int):
    by_region_location = defaultdict(list)
    by_location = defaultdict(list)
    for record in bwiki:
        by_region_location[(record["norm_region"], record["norm_location"])].append(record)
        by_location[record["norm_location"]].append(record)

    used = set()
    output = []
    counts = Counter()

    for off in official:
        metadata = off.get("metadata") or {}
        region, location, item_type = _parse_official_location(off)
        norm_region = _normalize(region)
        norm_location = _normalize(location)
        candidates = list(by_region_location.get((norm_region, norm_location), []))
        if not candidates and norm_location:
            location_candidates = list(by_location.get(norm_location, []))
            if len({item["norm_region"] for item in location_candidates}) > 1:
                review.append({
                    "module": "map_text",
                    "reason": "location_in_multiple_regions",
                    "official": {
                        "doc_id": off["doc_id"],
                        "title": off.get("page_title") or off.get("title"),
                        "region": region,
                        "location": location,
                    },
                    "candidates": [
                        {"key": item["key"], "region": item["region"], "location": item["location"]}
                        for item in location_candidates[:10]
                    ],
                })
                output.append(_base_record(
                    canonical_id=f"official:{off['doc_id']}",
                    module="map_text",
                    title=off.get("page_title") or off.get("title") or "",
                    primary_source="official_hoyowiki",
                    merge_status="review",
                    text=off.get("text") or "",
                    metadata=metadata,
                    sources=[_source_ref("official_hoyowiki", off["doc_id"], off.get("title") or "", off.get("content_hash", ""), "primary")],
                    merge_notes=["location_in_multiple_regions"],
                ))
                counts["review"] += 1
                continue
            candidates = location_candidates

        if not candidates:
            output.append(_base_record(
                canonical_id=f"official:{off['doc_id']}",
                module="map_text",
                title=off.get("page_title") or off.get("title") or "",
                primary_source="official_hoyowiki",
                merge_status="official_only",
                text=off.get("text") or "",
                metadata=metadata,
                sources=[_source_ref("official_hoyowiki", off["doc_id"], off.get("title") or "", off.get("content_hash", ""), "primary")],
                merge_notes=["no_bwiki_candidate"],
            ))
            counts["official_only"] += 1
            continue

        scored = []
        for candidate in candidates:
            score = _text_score(off.get("text") or "", candidate["text"], min_line_len=min_line_len)
            if item_type and _normalize(item_type) and _normalize(item_type) in _normalize(candidate["text"]):
                score = min(1.0, score + 0.1)
            scored.append((score, candidate))
        scored.sort(key=lambda item: item[0], reverse=True)
        best_score, best_candidate = scored[0]
        near_best = [item for item in scored if item[0] >= best_score - 0.05]

        if best_score < coverage_threshold:
            review.append({
                "module": "map_text",
                "reason": "location_matched_but_low_text_overlap",
                "official": {"doc_id": off["doc_id"], "title": off.get("page_title") or off.get("title"), "region": region, "location": location},
                "candidates": [{"key": candidate["key"], "score": round(score, 4), "text_head": candidate["text"][:80]} for score, candidate in scored[:5]],
            })
            output.append(_base_record(
                canonical_id=f"official:{off['doc_id']}",
                module="map_text",
                title=off.get("page_title") or off.get("title") or "",
                primary_source="official_hoyowiki",
                merge_status="review",
                text=off.get("text") or "",
                metadata=metadata,
                sources=[_source_ref("official_hoyowiki", off["doc_id"], off.get("title") or "", off.get("content_hash", ""), "primary")],
                merge_notes=["low_text_overlap"],
            ))
            counts["review"] += 1
            continue

        if len(near_best) > 1:
            review.append({
                "module": "map_text",
                "reason": "multiple_bwiki_candidates_with_similar_score",
                "official": {"doc_id": off["doc_id"], "title": off.get("page_title") or off.get("title"), "region": region, "location": location},
                "candidates": [{"key": candidate["key"], "score": round(score, 4), "text_head": candidate["text"][:80]} for score, candidate in near_best[:5]],
            })
            output.append(_base_record(
                canonical_id=f"official:{off['doc_id']}",
                module="map_text",
                title=off.get("page_title") or off.get("title") or "",
                primary_source="official_hoyowiki",
                merge_status="review",
                text=off.get("text") or "",
                metadata=metadata,
                sources=[_source_ref("official_hoyowiki", off["doc_id"], off.get("title") or "", off.get("content_hash", ""), "primary")],
                merge_notes=["multiple_candidates"],
            ))
            counts["review"] += 1
            continue

        if best_candidate["key"] in used:
            review.append({
                "module": "map_text",
                "reason": "bwiki_candidate_already_used",
                "official": {"doc_id": off["doc_id"], "title": off.get("page_title") or off.get("title"), "region": region, "location": location},
                "candidates": [{"key": best_candidate["key"], "score": round(best_score, 4)}],
            })
            output.append(_base_record(
                canonical_id=f"official:{off['doc_id']}",
                module="map_text",
                title=off.get("page_title") or off.get("title") or "",
                primary_source="official_hoyowiki",
                merge_status="review",
                text=off.get("text") or "",
                metadata=metadata,
                sources=[_source_ref("official_hoyowiki", off["doc_id"], off.get("title") or "", off.get("content_hash", ""), "primary")],
                merge_notes=["candidate_already_used"],
            ))
            counts["review"] += 1
            continue

        supplement = _unique_supplement(off.get("text") or "", best_candidate["text"], min_line_len)
        output.append(_base_record(
            canonical_id=f"official:{off['doc_id']}",
            module="map_text",
            title=off.get("page_title") or off.get("title") or "",
            primary_source="official_hoyowiki",
            merge_status="merged",
            text=off.get("text") or "",
            metadata=metadata,
            sources=[
                _source_ref("official_hoyowiki", off["doc_id"], off.get("title") or "", off.get("content_hash", ""), "primary"),
                _source_ref("bilibili_wiki", best_candidate["key"], best_candidate["title"], "", "supplement"),
            ],
            supplement_text=supplement,
            aliases=[best_candidate["location"], region],
            merge_notes=[f"text_score={round(best_score, 4)}"],
        ))
        used.add(best_candidate["key"])
        counts["merged"] += 1
        if supplement:
            counts["merged_with_supplement"] += 1

    for record in bwiki:
        if record["key"] in used:
            continue
        output.append(_base_record(
            canonical_id=record["key"],
            module="map_text",
            title=record["location"],
            primary_source="bilibili_wiki",
            merge_status="bwiki_only",
            text=record["text"],
            metadata={"地区": record["region"], "地点": record["location"]},
            sources=[_source_ref("bilibili_wiki", record["key"], record["location"], "", "primary")],
            aliases=[record["title"]],
            merge_notes=["no_official_counterpart"],
        ))
        counts["bwiki_only"] += 1
    return output, counts


def merge_npcs(official: list, bwiki: list, min_line_len: int, review: list):
    by_name = defaultdict(list)
    by_base = defaultdict(list)
    for record in bwiki:
        by_name[record["norm_name"]].append(record)
        by_base[record["norm_base_name"]].append(record)

    used = set()
    output = []
    counts = Counter()

    for off in official:
        metadata = off.get("metadata") or {}
        official_name = metadata.get("NPC名称") or off.get("title") or ""
        norm_name = _normalize(official_name)
        candidates = list(by_name.get(norm_name, []))
        if not candidates:
            candidates = list(by_base.get(norm_name, []))
        if not candidates:
            output.append(_base_record(
                canonical_id=f"official:{off['doc_id']}",
                module="npc",
                title=official_name,
                primary_source="official_hoyowiki",
                merge_status="official_only",
                text=off.get("text") or "",
                metadata=metadata,
                sources=[_source_ref("official_hoyowiki", off["doc_id"], official_name, off.get("content_hash", ""), "primary")],
                merge_notes=["no_bwiki_candidate"],
            ))
            counts["official_only"] += 1
            continue

        if len(candidates) > 1:
            review.append({
                "module": "npc",
                "reason": "ambiguous_bwiki_candidates",
                "official": {"doc_id": off["doc_id"], "title": official_name},
                "candidates": [{"key": item["key"], "title": item["name"]} for item in candidates],
            })
            output.append(_base_record(
                canonical_id=f"official:{off['doc_id']}",
                module="npc",
                title=official_name,
                primary_source="official_hoyowiki",
                merge_status="review",
                text=off.get("text") or "",
                metadata=metadata,
                sources=[_source_ref("official_hoyowiki", off["doc_id"], official_name, off.get("content_hash", ""), "primary")],
                merge_notes=["ambiguous_bwiki_candidates"],
            ))
            counts["review"] += 1
            continue

        candidate = candidates[0]
        supplement = _unique_supplement(off.get("text") or "", candidate["text"], min_line_len)
        output.append(_base_record(
            canonical_id=f"official:{off['doc_id']}",
            module="npc",
            title=official_name,
            primary_source="official_hoyowiki",
            merge_status="merged",
            text=off.get("text") or "",
            metadata=metadata,
            sources=[
                _source_ref("official_hoyowiki", off["doc_id"], official_name, off.get("content_hash", ""), "primary"),
                _source_ref("bilibili_wiki", candidate["key"], candidate["name"], "", "supplement"),
            ],
            supplement_text=supplement,
            supplement_metadata=candidate["metadata"],
            aliases=[candidate["name"]],
            merge_notes=["matched_by_npc_name"],
        ))
        used.add(candidate["key"])
        counts["merged"] += 1
        if supplement:
            counts["merged_with_supplement"] += 1

    for record in bwiki:
        if record["key"] in used:
            continue
        output.append(_base_record(
            canonical_id=record["key"],
            module="npc",
            title=record["name"],
            primary_source="bilibili_wiki",
            merge_status="bwiki_only",
            text=record["text"],
            metadata=record["metadata"],
            sources=[_source_ref("bilibili_wiki", record["key"], record["name"], "", "primary")],
            merge_notes=["no_official_counterpart"],
        ))
        counts["bwiki_only"] += 1
    return output, counts


def main() -> int:
    parser = argparse.ArgumentParser(description="B站 Wiki 与官方 crawler 语料的保守合并 v1")
    parser.add_argument("--official-dir", default=DEFAULT_OFFICIAL_DIR, help="官方适配层目录，默认 content_data/crawler_corpus")
    parser.add_argument("--bwiki-dir", default=DEFAULT_BWIKI_DIR, help="B站数据目录，默认 content_data")
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR, help="合并输出目录，默认 content_data/merged")
    parser.add_argument("--modules", default="task,map_text,npc", help="要处理的模块，多个用逗号分隔")
    parser.add_argument("--coverage-threshold", type=float, default=0.8, help="map_text 正文重合度阈值，默认 0.8")
    parser.add_argument("--min-line-length", type=int, default=4, help="纳入重合度计算的正文最小行长，默认 4")
    parser.add_argument("--review-limit", type=int, default=3000, help="复核清单最多写入条数，默认 3000")
    parser.add_argument("--allow-empty-official", action="store_true", help="允许官方层某模块为空，仅输出该模块的 B站记录")
    parser.add_argument("--dry-run", action="store_true", help="只计算和报告，不写文件")
    args = parser.parse_args()

    official_dir = Path(args.official_dir).resolve()
    bwiki_dir = Path(args.bwiki_dir).resolve()
    out_dir = Path(args.out_dir).resolve()

    modules = [item.strip() for item in args.modules.split(",") if item.strip()]
    unknown = set(modules) - set(SUPPORTED_MODULES)
    if unknown:
        print(f"[错误] v1 只支持 {', '.join(SUPPORTED_MODULES)}，不支持: {', '.join(sorted(unknown))}")
        return 2
    if not official_dir.exists():
        print(f"[错误] 官方适配层目录不存在: {official_dir}")
        return 2
    if not bwiki_dir.exists():
        print(f"[错误] B站数据目录不存在: {bwiki_dir}")
        return 2

    official_layers = {}
    for module in modules:
        layer = _load_jsonl(official_dir / f"{module}.jsonl")
        if not layer and not args.allow_empty_official:
            print(
                f"[错误] 官方层 {module}.jsonl 为空；"
                "请先运行 scripts/import_crawler_corpus.py，或加 --allow-empty-official"
            )
            return 2
        official_layers[module] = layer

    review = []
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "official_dir": str(official_dir),
        "bwiki_dir": str(bwiki_dir),
        "out_dir": str(out_dir),
        "dry_run": args.dry_run,
        "modules": {},
        "review_total": 0,
    }

    if "task" in modules:
        official = official_layers["task"]
        bwiki = load_bwiki_tasks(bwiki_dir)
        merged, counts = merge_tasks(official, bwiki, args.coverage_threshold, review)
        summary["modules"]["task"] = {
            "official_total": len(official),
            "bwiki_total": len(bwiki),
            "merged": counts.get("merged", 0),
            "merged_with_supplement": counts.get("merged_with_supplement", 0),
            "official_only": counts.get("official_only", 0),
            "bwiki_only": counts.get("bwiki_only", 0),
            "review": counts.get("review", 0),
        }
        if not args.dry_run:
            _atomic_write_jsonl(out_dir / "task.jsonl", merged)
        print(f"[task] 官方 {len(official)} / B站 {len(bwiki)} -> 合并 {counts.get('merged', 0)}，B站独有 {counts.get('bwiki_only', 0)}，复核 {counts.get('review', 0)}")

    if "map_text" in modules:
        official = official_layers["map_text"]
        bwiki = load_bwiki_map_text(bwiki_dir)
        merged, counts = merge_map_text(official, bwiki, args.coverage_threshold, review, args.min_line_length)
        summary["modules"]["map_text"] = {
            "official_total": len(official),
            "bwiki_total": len(bwiki),
            "merged": counts.get("merged", 0),
            "merged_with_supplement": counts.get("merged_with_supplement", 0),
            "official_only": counts.get("official_only", 0),
            "bwiki_only": counts.get("bwiki_only", 0),
            "review": counts.get("review", 0),
        }
        if not args.dry_run:
            _atomic_write_jsonl(out_dir / "map_text.jsonl", merged)
        print(f"[map_text] 官方 {len(official)} / B站 {len(bwiki)} -> 合并 {counts.get('merged', 0)}，B站独有 {counts.get('bwiki_only', 0)}，复核 {counts.get('review', 0)}")

    if "npc" in modules:
        official = official_layers["npc"]
        bwiki = load_bwiki_npcs(bwiki_dir)
        merged, counts = merge_npcs(official, bwiki, args.min_line_length, review)
        summary["modules"]["npc"] = {
            "official_total": len(official),
            "bwiki_total": len(bwiki),
            "merged": counts.get("merged", 0),
            "merged_with_supplement": counts.get("merged_with_supplement", 0),
            "official_only": counts.get("official_only", 0),
            "bwiki_only": counts.get("bwiki_only", 0),
            "review": counts.get("review", 0),
        }
        if not args.dry_run:
            _atomic_write_jsonl(out_dir / "npc.jsonl", merged)
        print(f"[npc] 官方 {len(official)} / B站 {len(bwiki)} -> 合并 {counts.get('merged', 0)}，B站独有 {counts.get('bwiki_only', 0)}，复核 {counts.get('review', 0)}")

    summary["review_total"] = len(review)
    summary["review_written"] = min(len(review), args.review_limit)
    if not args.dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(out_dir / "_summary.json", summary)
        _atomic_write_json(out_dir / "_review.json", review[:args.review_limit])
    print(f"复核清单: {len(review)} 条（写入 {min(len(review), args.review_limit)} 条）")
    if args.dry_run:
        print("dry-run：未写入任何文件")
    else:
        print(f"输出目录: {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
