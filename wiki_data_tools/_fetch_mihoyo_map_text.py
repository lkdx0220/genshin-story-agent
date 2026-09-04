# -*- coding: utf-8 -*-
"""
米游社观测枢专用地图文本抓取脚本。

数据来源：米游社观测枢官网前端使用的公开只读 JSON 接口。
本脚本直接调用与网页相同的接口，不需要登录、不需要签名、不需要验证码。
只能用于低频、内部、非商业的知识库维护，严禁高频批量抓取和公开分发原文。

抓取流程：
  1. 拉取地图文本频道（channel_id=251）的全部卡片列表。
  2. 按 ext 中的“地区”过滤（默认 至冬）。
  3. 逐个调用 entry_page 详情接口，保存完整页面 JSON（含交互文本/相关任务）。
  4. 输出到 content_data/mihoyo_map_text_raw.json，后续再解析合并进知识库。

用法：
  python _fetch_mihoyo_map_text.py                     # 抓取至冬地图文本详情
  python _fetch_mihoyo_map_text.py --list-only         # 只拉列表
  python _fetch_mihoyo_map_text.py --region 纳塔 --limit 10
  python _fetch_mihoyo_map_text.py --all-regions      # 不按地区过滤
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
DEFAULT_OUTPUT_FILE = os.path.join(CONTENT_DIR, "mihoyo_map_text_raw.json")

# ====== 米游社观测枢接口 ======

BLACKBOARD_BASE = "https://act-api-takumi-static.mihoyo.com/common/blackboard/ys_obc/v1"
WIKI_BASE = "https://act-api-takumi-static.mihoyo.com/hoyowiki/genshin"
NO_CDN_WIKI_BASE = "https://act-api-takumi.mihoyo.com/hoyowiki/genshin"

TASK_CHANNEL_ID = 251
APP_SIGN = "ys_obc"
WIKI_APP = "genshin"

HEADERS = [
    "-H", "x-rpc-wiki_app: genshin",
    "-H", "Accept: application/json, text/plain, */*",
    "-H", "Accept-Language: zh-CN,zh;q=0.9,en;q=0.8",
    "-H", "Referer: https://baike.mihoyo.com/ys/obc/",
    "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]

DEFAULT_REGION = "至冬"
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
        channel = ext_obj.get("c_251", {})
        filter_text = channel.get("filter", {}).get("text", "")
        return {"filters_text": filter_text}
    except Exception:
        return {}


def parse_filter_entries(filters_text):
    """把 filter.text 的 JSON 字符串解析为字符串列表。"""
    if not filters_text:
        return []
    try:
        arr = json.loads(filters_text)
        return arr if isinstance(arr, list) else []
    except Exception:
        return []


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
    """拉取单个地图文本详情页完整 JSON。"""
    url = WIKI_BASE + "/wapi/entry_page"
    data = curl_get(
        url,
        {"entry_page_id": content_id, "app_sn": APP_SIGN, "lang": "zh-cn"},
    )
    if not data:
        return None
    return data.get("data", {}).get("page")


def main():
    parser = argparse.ArgumentParser(description="米游社观测枢地图文本抓取脚本")
    parser.add_argument("--list-only", action="store_true", help="只拉地图文本列表，不拉详情")
    parser.add_argument("--region", default=DEFAULT_REGION, help="按地区过滤，默认 至冬")
    parser.add_argument("--all-regions", action="store_true", help="不按地区过滤")
    parser.add_argument("--limit", type=int, default=0, help="最多抓取条数，0 表示全部")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY, help="详情请求间隔秒数")
    parser.add_argument("--output", default=DEFAULT_OUTPUT_FILE, help="输出 JSON 路径")
    args = parser.parse_args()

    log(f"拉取地图文本频道列表 channel_id={TASK_CHANNEL_ID} ...")
    items = fetch_task_list()
    log(f"地图文本卡片总数: {len(items)}")

    if not args.all_regions:
        filtered = []
        for it in items:
            entries = parse_filter_entries(parse_task_filters(it).get("filters_text", ""))
            if any(e.startswith("地区/" + args.region) for e in entries):
                filtered.append(it)
        log(f"按地区 {args.region} 过滤后: {len(filtered)} 条")
        if not filtered:
            log("没有匹配地图文本，直接退出")
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
            "source": "米游社观测枢地图文本频道",
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
        "source": "米游社观测枢地图文本频道",
        "channel_id": TASK_CHANNEL_ID,
        "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total": total,
        "success": sum(1 for r in results if r.get("page")),
        "items": results,
    })
    log(f"地图文本详情抓取完成，成功 {sum(1 for r in results if r.get('page'))}/{total}")
    log(f"已写入: {args.output}")


if __name__ == "__main__":
    sys.exit(main())
