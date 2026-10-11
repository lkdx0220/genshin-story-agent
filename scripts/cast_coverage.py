#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""任务 metadata.出场人物 完备性巡检。

用途（T1）：
- 统计 content_data/quests_*.json 每个分类的 cast 字段覆盖率；
- 写入 kb_vectors/cast_coverage.json，供下游和后续巡检对比；
- 非空率 < 80%，或较上次下降 > 10 个百分点时打印告警。

设计约束：
- 只读 quests_*.json，不修改任何数据；
- 清洗规则与 app.entity_retrieval._clean_cast_value 保持一致；
- 幂等，可重复运行。
"""
import argparse
import json
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


def scan() -> dict:
    categories = OrderedDict()
    for path in sorted(CONTENT_DIR.glob("quests_*.json")):
        if "processed" in path.name or ".bak" in path.name or "before" in path.name:
            continue
        data = load_json(path)
        if data is None:
            continue
        items = data if isinstance(data, list) else list(data.values())
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
            if not isinstance(item, dict):
                continue
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
        for alert in alerts:
            previous_rate = alert.get("previous_rate")
            previous_text = "无" if previous_rate is None else f"{round(previous_rate * 100)}%"
            print(f"[告警] 分类={alert['category']} cast 非空率 {round(alert['rate'] * 100)}%"
                  f"（上次 {previous_text}）")

    return 0


if __name__ == "__main__":
    sys.exit(main())
