# -*- coding: utf-8 -*-
"""
米游社观测枢专用任务抓取脚本。

数据来源：米游社观测枢官网前端使用的公开只读 JSON 接口。
本脚本直接调用与网页相同的接口，不需要登录、不需要签名、不需要验证码。
只能用于低频、内部、非商业的知识库维护，严禁高频批量抓取和公开分发原文。

抓取流程：
  1. 拉取任务频道（channel_id=43）的全部任务卡片列表。
  2. 按 ext 中的“版本号”过滤（默认 7.0）。
  3. 逐个调用 entry_page 详情接口，保存完整页面 JSON（含任务概述、流程、完整对话）。
  4. 输出到 content_data/mihoyo_tasks_raw.json，后续再解析合并进 quests_*.json。

用法：
  python _fetch_mihoyo_tasks.py                     # 抓取 7.0 任务详情
  python _fetch_mihoyo_tasks.py --list-only         # 只拉列表，不拉详情
  python _fetch_mihoyo_tasks.py --version 7.0 --limit 10
  python _fetch_mihoyo_tasks.py --all-versions      # 不按版本过滤
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.parse

# ====== 路径 ======

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
CONTENT_DIR = os.path.join(BASE_DIR, "content_data")
DEFAULT_OUTPUT_FILE = os.path.join(CONTENT_DIR, "mihoyo_tasks_raw.json")

# ====== 米游社观测枢接口 ======

BLACKBOARD_BASE = "https://act-api-takumi-static.mihoyo.com/common/blackboard/ys_obc/v1"
WIKI_BASE = "https://act-api-takumi-static.mihoyo.com/hoyowiki/genshin"
NO_CDN_WIKI_BASE = "https://act-api-takumi.mihoyo.com/hoyowiki/genshin"

TASK_CHANNEL_ID = 43
APP_SIGN = "ys_obc"
WIKI_APP = "genshin"

HEADERS = [
    "-H", "x-rpc-wiki_app: genshin",
    "-H", "Accept: application/json, text/plain, */*",
    "-H", "Accept-Language: zh-CN,zh;q=0.9,en;q=0.8",
    "-H", "Referer: https://baike.mihoyo.com/ys/obc/",
    "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]

DEFAULT_VERSION = "7.0"
DEFAULT_DELAY = 1.0


def log(msg):
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def safe_write(filepath, data):
    tmp = filepath + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, filepath)


def curl_get(url, params=None, timeout=30):
    """使用 curl.exe 请求米游社公开 JSON 接口，返回 dict 或 None。"""
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    fd, tmp_path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        result = subprocess.run(
            [
                "curl.exe", "-s", "--compressed",
                "-w", "%{http_code}",
                "-o", tmp_path,
                *HEADERS,
                "--max-time", str(timeout),
                url,
            ],
            capture_output=True,
            text=True,
            timeout=timeout + 5,
        )
        http_code = result.stdout.strip()
        if http_code != "200":
            log(f"  HTTP {http_code}: {url}")
            return None
        with open(tmp_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("retcode") != 0:
            log(f"  接口返回错误 retcode={data.get('retcode')} message={data.get('message')}")
            return None
        return data
    except subprocess.TimeoutExpired:
        log(f"  curl 超时: {url}")
        return None
    except Exception as e:
        log(f"  请求失败: {e}")
        return None
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def fetch_task_list(channel_id=TASK_CHANNEL_ID):
    """拉取任务频道卡片列表，返回 task items 列表。"""
    url = BLACKBOARD_BASE + "/home/content/list"
    data = curl_get(url, {"channel_id": channel_id, "app_sn": APP_SIGN, "lang": "zh-cn"})
    if not data:
        return []
    nodes = data.get("data", {}).get("list", [])
    if not nodes:
        log("  任务频道列表为空")
        return []
    return nodes[0].get("list", [])


def parse_task_filters(item):
    """从卡片 ext 中解析 任务类型/任务区域/版本号。"""
    ext = item.get("ext", "")
    if not isinstance(ext, str):
        return {}
    try:
        ext_obj = json.loads(ext)
        channel = ext_obj.get("c_43", {})
        filter_text = channel.get("filter", {}).get("text", "")
        return {"filters_text": filter_text}
    except Exception:
        return {}


def parse_ext_version(item):
    """从 ext 的 filter.text 中提取“版本号/x.y”。"""
    filters = parse_task_filters(item)
    text = filters.get("filters_text", "")
    if not text:
        return ""
    try:
        arr = json.loads(text)
    except Exception:
        return ""
    for entry in arr:
        if entry.startswith("版本号/"):
            return entry.split("/", 1)[1]
    return ""


def item_summary(item):
    """输出一条卡片摘要，便于日志中检查。"""
    cid = item.get("content_id")
    title = item.get("title", "")
    version = parse_ext_version(item)
    filters = parse_task_filters(item).get("filters_text", "")
    return f"{cid} | {title} | 版本={version} | {filters}"


def fetch_entry_page(content_id):
    """拉取单个任务详情页完整 JSON。"""
    url = WIKI_BASE + "/wapi/entry_page"
    data = curl_get(
        url,
        {"entry_page_id": content_id, "app_sn": APP_SIGN, "lang": "zh-cn"},
    )
    if not data:
        return None
    return data.get("data", {}).get("page")


def main():
    parser = argparse.ArgumentParser(description="米游社观测枢任务抓取脚本")
    parser.add_argument("--list-only", action="store_true", help="只拉任务列表，不拉详情")
    parser.add_argument("--version", default=DEFAULT_VERSION, help="按版本号过滤，默认 7.0")
    parser.add_argument("--all-versions", action="store_true", help="不按版本过滤")
    parser.add_argument("--limit", type=int, default=0, help="最多抓取条数，0 表示全部")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY, help="详情请求间隔秒数")
    parser.add_argument("--output", default=DEFAULT_OUTPUT_FILE, help="输出 JSON 路径")
    parser.add_argument("--ids-file", default=None, help="从对齐JSON的missing_items读取content_id列表，只抓这些详情")
    args = parser.parse_args()

    log(f"拉取任务频道列表 channel_id={TASK_CHANNEL_ID} ...")
    items = fetch_task_list()
    log(f"任务卡片总数: {len(items)}")

    if args.ids_file:
        with open(args.ids_file, "r", encoding="utf-8") as f:
            align = json.load(f)
        wanted = {int(x["content_id"]) for x in align.get("missing_items", [])}
        by_id = {int(it.get("content_id")): it for it in items if it.get("content_id") is not None}
        items = [by_id[i] for i in wanted if i in by_id]
        missing_ids = wanted - set(by_id.keys())
        if missing_ids:
            log(f"列表里未找到的 content_id: {sorted(missing_ids)}")
        log(f"按 ids-file 选择: {len(items)} 条")
    elif not args.all_versions:
        filtered = [it for it in items if parse_ext_version(it) == args.version]
        log(f"按版本 {args.version} 过滤后: {len(filtered)} 条")
        if not filtered:
            log("没有匹配任务，直接退出")
            return
        items = filtered

    if args.list_only:
        summary = []
        for it in items:
            summary.append({
                "content_id": it.get("content_id"),
                "title": it.get("title"),
                "summary": it.get("summary", ""),
                "alias_name": it.get("alias_name", ""),
                "corner_mark": it.get("corner_mark", ""),
                "filters": parse_task_filters(it),
            })
        safe_write(args.output, {
            "source": "米游社观测枢任务频道",
            "channel_id": TASK_CHANNEL_ID,
            "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "items": summary,
        })
        log(f"列表已写入: {args.output}")
        return

    if args.limit > 0:
        items = items[:args.limit]

    results = []
    total = len(items)
    for i, it in enumerate(items, 1):
        cid = it.get("content_id")
        log(f"[{i}/{total}] {item_summary(it)}")
        page = fetch_entry_page(cid)
        if page:
            results.append({
                "content_id": cid,
                "title": it.get("title", ""),
                "filters": parse_task_filters(it),
                "page": page,
            })
        else:
            results.append({
                "content_id": cid,
                "title": it.get("title", ""),
                "filters": parse_task_filters(it),
                "page": None,
                "fetch_error": True,
            })
        if i < total:
            time.sleep(args.delay)

    safe_write(args.output, {
        "source": "米游社观测枢任务频道",
        "channel_id": TASK_CHANNEL_ID,
        "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total": total,
        "success": sum(1 for r in results if r.get("page")),
        "items": results,
    })
    log(f"详情抓取完成，成功 {sum(1 for r in results if r.get('page'))}/{total}")
    log(f"已写入: {args.output}")


if __name__ == "__main__":
    sys.exit(main())
