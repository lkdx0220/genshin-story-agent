# -*- coding: utf-8 -*-
"""抓取 wiki 词条链接图试点缺失的关键词条。

来源：观测枢 entry_page 接口（WorkBuddy 逆向确认）。
用法：
    python scripts/fetch_wiki_missing_entries.py
输出：
    content_data/wiki_missing_entries_raw.json（与 mihoyo_tasks_raw.json 相同的
    {"source":..., "items":[{"content_id","title","filters","page"}]} 结构）
"""
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent.parent
OUT_PATH = BASE_DIR / "content_data" / "wiki_missing_entries_raw.json"

API_URL = "https://api-takumi.mihoyo.com/hoyowiki/genshin/wapi/entry_page"
APP_SN = "bll8iq97cem8"

# 只抓试点图中缺失的高价值节点。
# 注：509226 不要抓——raw 里它同时被标成“石兽如幽灵般端坐/
# 普洛克路斯忒斯的寝床”等多个名字，entry_page 实测是“旅行者·冰”，
# 属于观测枢脏链接；wiki_entry_graph._extract_links 已丢弃这种多名字 ID。
TARGET_IDS = [
    ("509399", "「灰眸」"),
    ("509397", "「仪式」观礼请柬"),
    ("509511", "第七章 第一幕「无神怜爱的雪国」"),
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Referer": "https://baike.mihoyo.com/",
    "Accept": "application/json, text/plain, */*",
}


def fetch_page(entry_id: str):
    resp = requests.get(
        API_URL,
        params={"entry_page_id": entry_id, "lang": "zh-cn", "app_sn": APP_SN},
        headers=HEADERS,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("retcode") != 0:
        raise RuntimeError(f"retcode={data.get('retcode')} message={data.get('message')}")
    page = data.get("data", {}).get("page")
    if not isinstance(page, dict) or str(page.get("id")) != str(entry_id):
        raise RuntimeError("返回 page 缺少或 ID 不匹配")
    return page


def to_item(entry_id: str, page: dict) -> dict:
    # 原 raw 文件的 filters 来自列表接口；entry_page 接口没有该字段，
    # 保存空 filters，类型推断交给 wiki_entry_graph._infer_type 的模块名兜底。
    return {
        "content_id": page.get("id") or entry_id,
        "title": page.get("name") or page.get("desc") or entry_id,
        "filters": {"filters_text": "[]"},
        "page": page,
    }


def main():
    items = []
    for entry_id, name in TARGET_IDS:
        print(f"fetching {entry_id} {name}", flush=True)
        try:
            page = fetch_page(entry_id)
        except Exception as e:
            print(f"  FAILED {entry_id} {name}: {e}", file=sys.stderr, flush=True)
            continue
        print(f"  OK {page.get('name')} modules={len(page.get('modules') or [])}", flush=True)
        items.append(to_item(entry_id, page))
        time.sleep(0.5)

    doc = {
        "source": "entry_page_api",
        "channel_id": "genshin",
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total": len(items),
        "success": len(items),
        "items": items,
    }
    OUT_PATH.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved {OUT_PATH} with {len(items)}/{len(TARGET_IDS)} items", flush=True)
    return 0 if len(items) == len(TARGET_IDS) else 2


if __name__ == "__main__":
    raise SystemExit(main())
