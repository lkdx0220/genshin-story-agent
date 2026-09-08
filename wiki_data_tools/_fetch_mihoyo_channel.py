# -*- coding: utf-8 -*-
"""米游社观测枢通用频道抓取脚本（全量 wiki 链接图数据源）。

数据来源：米游社观测枢官网前端公开只读 JSON 接口。
本脚本复用 _fetch_mihoyo_tasks.py 验证过的 curl 包装（不用 requests），
低频、内部、非商业使用；必须带 --delay 控制频率。

流程：
  1. 按 channel_id 拉取该频道全部卡片列表；
  2. 过滤 Tier（--tier 1/2/3/all）或指定频道（--channel）；
  3. 逐个抓 entry_page 详情，写 content_data/wiki_raw/channel_<id>.json；
  4. 默认断点续传：已有成功 page 的 content_id 跳过，fetch_error 重试。

用法：
  python _fetch_mihoyo_channel.py --tier 1                # 抓全部 Tier1
  python _fetch_mihoyo_channel.py --channel 25            # 只抓角色
  python _fetch_mihoyo_channel.py --channel 43 --limit 10
  python _fetch_mihoyo_channel.py --list-only --tier 1
  python _fetch_mihoyo_channel.py --channel 43 --seed content_data/mihoyo_tasks_raw.json
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from wiki_graph_channels import CHANNEL_TYPE_MAP, CHANNEL_TIERS, CHANNEL_NAMES

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
RAW_DIR = os.path.join(BASE_DIR, "content_data", "wiki_raw")
MANIFEST_PATH = os.path.join(RAW_DIR, "_manifest.json")
REPORT_PATH = os.path.join(RAW_DIR, "_last_report.json")

BLACKBOARD_BASE = "https://act-api-takumi-static.mihoyo.com/common/blackboard/ys_obc/v1"
WIKI_BASE = "https://act-api-takumi-static.mihoyo.com/hoyowiki/genshin"
APP_SIGN = "ys_obc"
DEFAULT_DELAY = 0.6
SAVE_EVERY = 25

HEADERS = [
    "-H", "x-rpc-wiki_app: genshin",
    "-H", "Accept: application/json, text/plain, */*",
    "-H", "Accept-Language: zh-CN,zh;q=0.9,en;q=0.8",
    "-H", "Referer: https://baike.mihoyo.com/ys/obc/",
    "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]


def log(msg):
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def safe_write(filepath, data):
    tmp = filepath + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, filepath)


def curl_get(url, params=None, timeout=30):
    """用 curl.exe 请求公开 JSON 接口，返回 dict 或 None。"""
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


def fetch_channel_list(channel_id):
    """拉取指定频道全部卡片列表。"""
    url = BLACKBOARD_BASE + "/home/content/list"
    data = curl_get(url, {"channel_id": channel_id, "app_sn": APP_SIGN, "lang": "zh-cn"})
    if not data:
        return []
    nodes = data.get("data", {}).get("list", [])
    if not nodes:
        log(f"  频道 {channel_id} 列表为空")
        return []
    return nodes[0].get("list", [])


def parse_channel_filters(item, channel_id):
    """从卡片 ext.c_<channel_id>.filter.text 解析 filters_text。"""
    ext = item.get("ext", "")
    if not isinstance(ext, str):
        return {}
    try:
        ext_obj = json.loads(ext)
        channel = ext_obj.get(f"c_{channel_id}", {})
        filter_text = channel.get("filter", {}).get("text", "")
        return {"filters_text": filter_text or "[]"}
    except Exception:
        return {"filters_text": "[]"}


def parse_filter_entries(filters_text):
    if not filters_text:
        return []
    try:
        arr = json.loads(filters_text)
        return arr if isinstance(arr, list) else []
    except Exception:
        return []


def fetch_entry_page(content_id):
    url = WIKI_BASE + "/wapi/entry_page"
    data = curl_get(url, {"entry_page_id": content_id, "app_sn": APP_SIGN, "lang": "zh-cn"})
    if not data:
        return None
    return data.get("data", {}).get("page")


# ====== manifest / 指纹工具 ======

def _sha1_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _stable_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def card_fingerprint(item: dict, channel_id: int) -> str:
    """卡片级指纹：列表字段变化即视为词条卡片更新。"""
    payload = {
        "title": str(item.get("title") or ""),
        "filters_text": str(parse_channel_filters(item, channel_id).get("filters_text") or "[]"),
        "alias_name": str(item.get("alias_name") or ""),
        "corner_mark": str(item.get("corner_mark") or ""),
        "summary": str(item.get("summary") or ""),
    }
    return _sha1_text(_stable_json(payload))


def page_content_hash(page) -> str:
    """详情页 JSON 的内容哈希，用于变更检测（page 结构级）。"""
    if not isinstance(page, dict):
        return ""
    return _sha1_text(_stable_json(page))


def load_manifest(path: str = MANIFEST_PATH):
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_manifest(manifest: dict, path: str = MANIFEST_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    safe_write(path, manifest)


def save_report(report: dict, path: str = REPORT_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    safe_write(path, report)


def read_raw_channel(channel_id: int):
    """读取频道 raw；不存在返回 None。"""
    output_path = os.path.join(RAW_DIR, f"channel_{channel_id}.json")
    if not os.path.exists(output_path):
        return None
    try:
        with open(output_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def raw_page_hash_map(channel_id: int) -> dict:
    """返回该频道 raw 中 content_id -> page_hash 的映射（无 page 的给空串）。"""
    doc = read_raw_channel(channel_id)
    out = {}
    if not doc:
        return out
    for it in doc.get("items") or []:
        cid = str(it.get("content_id"))
        page = it.get("page")
        out[cid] = str(it.get("page_hash") or page_content_hash(page) if page else "")
    return out


def select_channels(channel_ids, tier):
    """返回按建议顺序排列的 (channel_id, graph_type) 列表。"""
    order = [
        43, 251, 20, 25, 261, 5, 218, 68, 6, 255, 13, 105, 278,
        276, 54, 55, 257, 21, 49, 227, 252, 244, 109, 211, 130,
        65, 249, 275, 260,
    ]
    if channel_ids:
        selected = [cid for cid in order if cid in channel_ids]
        if len(selected) != len(channel_ids):
            log(f"警告：部分 channel 不在映射中，忽略")
    else:
        selected = [
            cid for cid in order
            if cid in CHANNEL_TYPE_MAP and (tier == "all" or CHANNEL_TIERS[CHANNEL_TYPE_MAP[cid]] <= int(tier))
        ]
    return [(cid, CHANNEL_TYPE_MAP[cid]) for cid in selected]


def load_existing(output_path):
    if not os.path.exists(output_path):
        return None
    try:
        with open(output_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def seed_from_file(output_path, seed_path, channel_id):
    """把旧 raw 文件的内容复制为频道 raw 文件（用于跳过已抓好的 43/251）。"""
    with open(seed_path, "r", encoding="utf-8") as f:
        doc = json.load(f)
    doc = dict(doc)
    doc["channel_id"] = channel_id
    doc["source"] = f"米游社观测枢频道 {CHANNEL_NAMES.get(channel_id, channel_id)}（seed 自 {os.path.basename(seed_path)}）"
    doc.setdefault("total", len(doc.get("items") or []))
    doc.setdefault("success", sum(1 for it in doc.get("items") or [] if it.get("page")))
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    safe_write(output_path, doc)
    log(f"  seed 完成: {len(doc.get('items') or [])} 条 -> {output_path}")


def process_channel(channel_id, graph_type, args):
    channel_name = CHANNEL_NAMES.get(channel_id, str(channel_id))
    output_path = os.path.join(RAW_DIR, f"channel_{channel_id}.json")
    log(f"===== 频道 {channel_id} ({channel_name}) type={graph_type} =====")

    if args.seed and not os.path.exists(output_path):
        seed_from_file(output_path, args.seed, channel_id)

    log(f"拉取频道列表 channel_id={channel_id} ...")
    items = fetch_channel_list(channel_id)
    log(f"频道卡片总数: {len(items)}")
    if not items:
        return

    if args.limit > 0:
        items = items[:args.limit]

    # --ids：只处理指定 content_id（用于增量抓取 new/updated）。
    if getattr(args, "ids", None):
        id_set = set(str(x) for x in args.ids)
        items = [it for it in items if str(it.get("content_id")) in id_set]
        log(f"按 --ids 过滤后待处理: {len(items)}")
        if not items:
            log("没有匹配的 ID，跳过")
            return

    if args.list_only:
        summary = []
        for it in items:
            summary.append({
                "content_id": it.get("content_id"),
                "title": it.get("title"),
                "summary": it.get("summary", ""),
                "alias_name": it.get("alias_name", ""),
                "corner_mark": it.get("corner_mark", ""),
                "filters": parse_channel_filters(it, channel_id),
            })
        os.makedirs(RAW_DIR, exist_ok=True)
        safe_write(output_path, {
            "source": f"米游社观测枢频道 {channel_name}",
            "channel_id": channel_id,
            "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "items": summary,
        })
        log(f"列表已写入: {output_path}")
        return

    # 断点续传：读已有文件，建立成功/失败的 content_id 集合。
    doc = load_existing(output_path)
    existing_by_id = {}
    if doc and doc.get("items"):
        for it in doc.get("items"):
            cid = str(it.get("content_id"))
            old = existing_by_id.get(cid)
            # 去重：优先保留有 page 的详情；同状态保留最后一条。
            if old is None or (not old.get("page") and it.get("page")):
                existing_by_id[cid] = it
        log(f"已有文件 {len(doc.get('items'))} 条（去重后 {len(existing_by_id)} 条），续传模式")
    else:
        os.makedirs(RAW_DIR, exist_ok=True)

    results = list(existing_by_id.values())
    results_by_id = dict(existing_by_id)

    pending = 0
    for it in items:
        cid = str(it.get("content_id"))
        old = results_by_id.get(cid)
        if old and old.get("page") and not args.force:
            continue
        pending += 1
    log(f"待抓取详情: {pending}/{len(items)}")

    count = 0
    for it in items:
        cid = str(it.get("content_id"))
        old = results_by_id.get(cid)
        if old and old.get("page") and not args.force:
            continue
        # 无论是 force 重抓，还是旧文件里只有 list-only 摘要/失败记录，都先移除旧条目再写入新条目。
        if old is not None:
            results.remove(old)
            results_by_id.pop(cid, None)

        log(f"[{channel_id}:{count+1}/{pending}] {cid} | {it.get('title')}")
        page = fetch_entry_page(cid)
        item = {
            "content_id": cid,
            "title": it.get("title", ""),
            "filters": parse_channel_filters(it, channel_id),
            "page": page,
        }
        if page is not None:
            item["page_hash"] = page_content_hash(page)
        else:
            item["fetch_error"] = True
        results.append(item)
        results_by_id[cid] = item
        count += 1

        if count % SAVE_EVERY == 0:
            safe_write(output_path, {
                "source": f"米游社观测枢频道 {channel_name}",
                "channel_id": channel_id,
                "graph_type": graph_type,
                "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "total": len(items),
                "success": sum(1 for r in results if r.get("page")),
                "items": results,
            })
        if args.delay > 0:
            time.sleep(args.delay)

    safe_write(output_path, {
        "source": f"米游社观测枢频道 {channel_name}",
        "channel_id": channel_id,
        "graph_type": graph_type,
        "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total": len(items),
        "success": sum(1 for r in results if r.get("page")),
        "items": results,
    })
    log(f"频道 {channel_id} 完成: 成功 {sum(1 for r in results if r.get('page'))}/{len(results)}，写入 {output_path}")


def main():
    parser = argparse.ArgumentParser(description="米游社观测枢通用频道抓取脚本")
    parser.add_argument("--channel", type=int, action="append", default=None, help="指定频道 ID，可多次传")
    parser.add_argument("--tier", default="1", help="1/2/3/all，默认 1")
    parser.add_argument("--list-only", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY)
    parser.add_argument("--force", action="store_true", help="强制重抓已成功详情")
    parser.add_argument("--seed", default=None, help="把旧 raw 文件 seed 成频道文件（跳过重复抓取）")
    parser.add_argument("--ids", default=None, help="逗号分隔的 content_id，或包含 id 列表的 JSON 文件路径")
    args = parser.parse_args()

    if args.ids:
        if os.path.exists(args.ids):
            with open(args.ids, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, list):
                args.ids = [str(x) for x in loaded]
            elif isinstance(loaded, dict):
                args.ids = [str(x) for x in loaded.get("ids", [])]
            else:
                args.ids = []
        else:
            args.ids = [x.strip() for x in args.ids.split(",") if x.strip()]
        args.ids = set(args.ids)
        log(f"--ids 共 {len(args.ids)} 个")

    channels = select_channels(args.channel, args.tier)
    log(f"计划处理 {len(channels)} 个频道: {[(cid, CHANNEL_NAMES.get(cid)) for cid, _ in channels]}")
    if args.seed and len(channels) != 1:
        log("--seed 只支持单个 --channel")
        return 2
    for channel_id, graph_type in channels:
        process_channel(channel_id, graph_type, args)
    log("全部频道处理完成")


if __name__ == "__main__":
    main()
