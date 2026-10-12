#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""任务 metadata.出场人物 完备性巡检。

用途（T1 + 2026-10-12 遗留项 2）：
- 统计 content_data/quests_*.json 每个分类的 cast 字段覆盖率；
- 彩蛋剧情同时输出 raw / narrative_only 双口径；
- 写入 kb_vectors/cast_coverage.json，供下游和后续巡检对比；
- 非空率 < 80%，或较上次下降 > 10 个百分点时打印告警。

双口径定义（遗留项 2）：
- raw：该分类全部条目，分子=metadata.出场人物 非空；
- narrative_only：仅“有剧情/语音内容”的条目（正文有标准说话人行，
  或显式含「剧情对话 / 彩蛋语音 / 说话者」标记），分子=其中 cast 非空。

设计约束：
- 只读 quests_*.json，不修改任何数据；
- 清洗规则与 app.entity_retrieval._clean_cast_value 保持一致；
- 幂等，可重复运行。
"""

import argparse
import json
import re
import sys
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from app.entity_retrieval import _clean_cast_value  # noqa: E402

CONTENT_DIR = BASE_DIR / "content_data"
OUTPUT_PATH = BASE_DIR / "kb_vectors" / "cast_coverage.json"

LOW_RATE_THRESHOLD = 0.80
DROP_THRESHOLD = 0.10

# 标准剧情说话人行：文本里的 *角色：xxx（Genshin 剧情正文格式）
_SPEAKER_LINE_RE = re.compile(r"(?:^|\n)\s*\*\s*([^\n：:]{1,20}?)\s*[：:]")
_NARRATIVE_MARKERS = ("剧情对话", "彩蛋语音", "说话者", "台词")
# 排除 markdown 文件/网址/表格头等伪说话人行
_FAKE_SPEAKER_TOKENS = (
    "分类:", "http", "文件:", "图片:", "width=", "{|", "|}", "center|",
    "link=", "style=", "rowspan", "colspan", "px", "border",
)


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def classify_value(raw_value) -> str:
    """返回 empty / dirty / non_empty。"""
    raw = str(raw_value or "").strip()
    if not raw:
        return "empty"
    if "=" in raw or "|" in raw or "任务描述" in raw:
        return "dirty"
    if not _clean_cast_value(raw):
        return "dirty"
    return "non_empty"


def _has_standard_speaker_line(text: str) -> bool:
    for match in _SPEAKER_LINE_RE.finditer(str(text or "")):
        name = str(match.group(1) or "").strip()
        if not name or any(token in name for token in _FAKE_SPEAKER_TOKENS):
            continue
        if any(token in name for token in ("width", "style", "rowspan", "colspan")):
            continue
        return True
    return False


def has_narrative_content(item: dict) -> bool:
    """是否有剧情/语音内容：标准 *角色：说话人行，或显式剧情/语音标记。"""
    text = str(item.get("text") or "")
    if _has_standard_speaker_line(text):
        return True
    return any(marker in text for marker in _NARRATIVE_MARKERS)


def _classify_egg_items(items: list) -> dict:
    """彩蛋剧情双口径统计与无剧情条目清单。"""
    raw_total = len(items)
    raw_non = 0
    narrative_total = 0
    narrative_non = 0
    non_narrative = []
    narrative_empty = []
    for item in items:
        metadata = item.get("metadata") or {}
        non_empty = classify_value(metadata.get("出场人物")) == "non_empty"
        if non_empty:
            raw_non += 1
        if has_narrative_content(item):
            narrative_total += 1
            if non_empty:
                narrative_non += 1
            else:
                narrative_empty.append({
                    "title": item.get("title") or "",
                    "reason": "有剧情/语音内容，但无标准说话人行或 wiki 无出场人物字段",
                })
        else:
            non_narrative.append({
                "title": item.get("title") or "",
                "reason": "无标准说话人行、无剧情对话/语音标记；正文为音乐/截图/道具/攻略说明",
            })
    return {
        "raw": {
            "total": raw_total,
            "non_empty": raw_non,
            "rate": round(raw_non / max(1, raw_total), 4),
        },
        "narrative_only": {
            "total": narrative_total,
            "non_empty": narrative_non,
            "rate": round(narrative_non / max(1, narrative_total), 4),
        },
        "non_narrative_items": non_narrative,
        "narrative_empty_items": narrative_empty,
    }


def scan() -> dict:
    categories = OrderedDict()
    for path in sorted(CONTENT_DIR.glob("quests_*.json")):
        if "processed" in path.name or ".bak" in path.name or "before" in path.name:
            continue
        data = load_json(path)
        if data is None:
            continue
        items = [item for item in (data if isinstance(data, list) else list(data.values())) if isinstance(item, dict)]
        category = path.stem.replace("quests_", "")
        stat = {
            "total": 0,
            "key_present": 0,
            "non_empty": 0,
            "empty": 0,
            "no_key": 0,
            "dirty": 0,
        }
        for item in items:
            stat["total"] += 1
            metadata = item.get("metadata") or {}
            if "出场人物" not in metadata:
                stat["no_key"] += 1
                continue
            stat["key_present"] += 1
            kind = classify_value(metadata.get("出场人物"))
            stat[kind] += 1
        rate = stat["non_empty"] / max(1, stat["total"])
        stat["non_empty_rate"] = round(rate, 4)
        stat["raw"] = {
            "total": stat["total"],
            "non_empty": stat["non_empty"],
            "rate": round(rate, 4),
        }
        if category == "彩蛋剧情":
            stat.update(_classify_egg_items(items))
        else:
            narrative_total = sum(1 for item in items if has_narrative_content(item))
            narrative_non = sum(
                1
                for item in items
                if has_narrative_content(item) and classify_value((item.get("metadata") or {}).get("出场人物")) == "non_empty"
            )
            stat["narrative_only"] = {
                "total": narrative_total,
                "non_empty": narrative_non,
                "rate": round(narrative_non / max(1, narrative_total), 4),
            }
        categories[category] = stat
    return categories


def build_alerts(categories: dict, previous: dict) -> list:
    previous_categories = (previous or {}).get("categories") or {}
    alerts = []
    for category, stat in categories.items():
        rate = stat["non_empty_rate"]
        previous_rate = (previous_categories.get(category) or {}).get("non_empty_rate")
        if rate < LOW_RATE_THRESHOLD:
            alerts.append({
                "category": category,
                "type": "low_rate",
                "rate": rate,
                "previous_rate": previous_rate,
            })
        elif previous_rate is not None and previous_rate - rate > DROP_THRESHOLD:
            alerts.append({
                "category": category,
                "type": "rate_drop",
                "rate": rate,
                "previous_rate": previous_rate,
            })
    return alerts


def main():
    parser = argparse.ArgumentParser(description="任务出场人物元数据完备性巡检")
    parser.add_argument("--output", default=str(OUTPUT_PATH))
    parser.add_argument("--quiet", action="store_true", help="只写文件，不打印逐分类行")
    args = parser.parse_args()

    previous = load_json(Path(args.output))
    categories = scan()
    alerts = build_alerts(categories, previous)

    result = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "low_rate_threshold": LOW_RATE_THRESHOLD,
        "drop_threshold": DROP_THRESHOLD,
        "coverage_definitions": {
            "raw": "分类全部条目，分子=metadata.出场人物 非空",
            "narrative_only": "仅有剧情/语音内容的条目（标准说话人行或剧情对话标记），分子=其中 cast 非空",
        },
        "categories": categories,
        "alerts": alerts,
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(output_path)

    if not args.quiet:
        for category, stat in categories.items():
            rate_pct = round(stat["non_empty_rate"] * 100)
            print(f"[cast] {category} 非空 {stat['non_empty']}/{stat['total']} ({rate_pct}%)")
            if category == "彩蛋剧情":
                narrative = stat.get("narrative_only") or {}
                print(
                    f"[cast] 彩蛋剧情 有剧情/语音口径 "
                    f"{narrative.get('non_empty', 0)}/{narrative.get('total', 0)} "
                    f"({round((narrative.get('rate') or 0) * 100)}%)"
                )
                print(f"[cast] 彩蛋剧情 无剧情条目 {len(stat.get('non_narrative_items') or [])} 条")
        for alert in alerts:
            previous_rate = alert.get("previous_rate")
            previous_text = "无" if previous_rate is None else f"{round(previous_rate * 100)}%"
            print(f"[告警] 分类={alert['category']} cast 非空率 {round(alert['rate'] * 100)}%"
                  f"（上次 {previous_text}）")

    return 0


if __name__ == "__main__":
    sys.exit(main())
