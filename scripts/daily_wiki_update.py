#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""知识库自动更新检测器（Phase 1：只检测和报告，不修改知识库数据）。

每天由 Windows 任务计划程序调用（见 scripts/run_daily_update.ps1）：
1. 扫描 B站 Wiki 分类 + 地图文本页，对比 manifest 中的 lastrevid；
2. 扫描米游社观测枢频道，对比 content_id 和卡片指纹；
3. 对所有新增/变化条目抓取原文，调用 wiki_update_validators 做完整性校验；
4. 任一新增/变化条目缺字段 -> 整批暂缓，只写报告和通知；
5. 首次运行只建立基线，不逐页抓取校验，避免第一天下载量过大。

安全约束：
- 只访问白名单主机；
- 不用 shell=True，不执行外部传入内容；
- 不写 content_data，只写 logs/kb_auto_update；
- 所有网络请求失败都会进入报告，不会静默忽略。
"""
import argparse
import hashlib
import importlib.util
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, PROJECT_DIR)
sys.path.insert(0, os.path.join(PROJECT_DIR, "wiki_data_tools"))
sys.path.insert(0, os.path.join(PROJECT_DIR, "scripts"))

from wiki_update_validators import validate_bwiki, validate_mihoyo  # noqa: E402
from _safe_http import ensure_wiki_url  # noqa: E402

DEFAULT_CONFIG = os.path.join(PROJECT_DIR, "config", "daily_update.json")
LOGGER = logging.getLogger("daily_wiki_update")

BWIKI_API = "https://wiki.biligame.com/ys/api.php"
BWIKI_RAW = "https://wiki.biligame.com/ys/index.php"
BWIKI_HEADERS = [
    "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept: application/json, text/plain, */*",
    "Referer: https://wiki.biligame.com/ys/",
]
# Windows 命令行长度有限，revisions 批量不能太大，否则 curl.exe 会直接失败（HTTP 000）。
BWIKI_REVISION_BATCH = 20


# ====== 基础工具 ======

def setup_logging(log_path: str):
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    LOGGER.setLevel(logging.INFO)
    LOGGER.handlers.clear()
    fmt_file = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    fmt_console = logging.Formatter("%(levelname)s %(message)s")
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt_file)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt_console)
    LOGGER.addHandler(fh)
    LOGGER.addHandler(sh)


def now_text() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load_json(path: str, default):
    if not path or not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def atomic_write_json(path: str, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def sha1_text(text: str) -> str:
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()


def curl_to_file(url: str, headers, timeout: int, retries: int = 3) -> str:
    """用 curl.exe 下载到临时文件，返回临时文件路径；失败返回空串。

    B站 Wiki 在批量请求时可能返回 567（CDN 限流），这里做指数退避重试。
    """
    ensure_wiki_url(url)
    fd, tmp_path = tempfile.mkstemp(suffix=".tmp")
    os.close(fd)
    for attempt in range(retries):
        args = [
            "curl.exe", "-s", "--compressed",
            "-L", "--max-time", str(timeout),
            "-o", tmp_path,
            "-w", "%{http_code}",
            url,
        ]
        for header in headers or []:
            args.extend(["-H", header])
        try:
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=timeout + 5,
            )
            code = result.stdout.strip()
            if code == "200":
                return tmp_path
            if code in {"000", "", "429", "567"} or code.startswith("5"):
                wait = 5 * (2 ** attempt)
                LOGGER.warning("HTTP %s，%s 秒后重试 (%s/%s): %s", code or "(空)", wait, attempt + 1, retries, url)
                time.sleep(wait)
                continue
            LOGGER.warning("HTTP %s: %s", code, url)
            break
        except Exception as error:
            LOGGER.warning("请求失败: %s (%s)", error, url)
            if attempt + 1 < retries:
                time.sleep(3 * (attempt + 1))
                continue
            break
    if os.path.exists(tmp_path):
        os.unlink(tmp_path)
    return ""


def curl_json(url: str, params=None, headers=None, timeout: int = 30):
    if params:
        url = url + "?" + urllib.parse.urlencode(params, doseq=True)
    path = curl_to_file(url, headers, timeout)
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as error:
        LOGGER.warning("JSON 解析失败: %s (%s)", error, url)
        return None
    finally:
        if os.path.exists(path):
            os.unlink(path)


def curl_text(url: str, timeout: int = 30, headers=None) -> str:
    path = curl_to_file(url, headers or ["User-Agent: Mozilla/5.0"], timeout)
    if not path:
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    finally:
        if os.path.exists(path):
            os.unlink(path)


# ====== B站 Wiki ======

def bwiki_category_pages(category_name: str):
    """返回 (titles, errors)。"""
    titles = []
    errors = []
    continue_token = None
    while True:
        params = {
            "action": "query",
            "list": "categorymembers",
            "cmtitle": f"Category:{category_name}",
            "cmlimit": "500",
            "format": "json",
            "origin": "*",
        }
        if continue_token:
            params["cmcontinue"] = continue_token
        data = curl_json(BWIKI_API, params, headers=BWIKI_HEADERS)
        if not data or "query" not in data:
            errors.append(f"拉取分类失败: {category_name}")
            return titles, errors
        for item in data["query"].get("categorymembers", []):
            title = item.get("title")
            if title:
                titles.append(title)
        continue_token = data.get("continue", {}).get("cmcontinue")
        if not continue_token:
            break
        time.sleep(0.8)
    return titles, errors


def bwiki_revisions(titles):
    """批量获取 lastrevid；返回 (result, errors)。"""
    result = {}
    errors = []
    for start in range(0, len(titles), BWIKI_REVISION_BATCH):
        batch = titles[start:start + BWIKI_REVISION_BATCH]
        data = curl_json(
            BWIKI_API,
            {
                "action": "query",
                "prop": "revisions",
                "titles": "|".join(batch),
                "rvprop": "ids|timestamp|sha1",
                "rvslots": "main",
                "format": "json",
                "origin": "*",
            },
            headers=BWIKI_HEADERS,
        )
        if not data or "query" not in data:
            errors.append(f"获取修订版本失败，批次: {batch[:3]}")
            time.sleep(2.0)
            continue
        for page in data["query"].get("pages", {}).values():
            title = page.get("title")
            if not title or "missing" in page:
                continue
            revisions = page.get("revisions") or []
            if not revisions:
                continue
            revision = revisions[0]
            result[title] = {
                "lastrevid": revision.get("revid"),
                "timestamp": revision.get("timestamp"),
                "sha1": revision.get("sha1"),
            }
        time.sleep(1.0)
    return result, errors


def bwiki_raw(title: str) -> str:
    url = BWIKI_RAW + "?" + urllib.parse.urlencode({"title": title, "action": "raw"})
    return curl_text(url, headers=BWIKI_HEADERS)


def scan_bwiki_source(config, manifest, report):
    bwiki_conf = config["sources"]["bwiki"]
    if not bwiki_conf.get("enabled"):
        return
    categories = bwiki_conf.get("categories") or {}
    for category, category_name in categories.items():
        scan_bwiki_category(category, category_name, config, manifest, report)
    map_text_pages = bwiki_conf.get("map_text_pages") or []
    if map_text_pages:
        scan_bwiki_titles("map_text", map_text_pages, config, manifest, report)


def scan_bwiki_category(category, category_name, config, manifest, report):
    titles, errors = bwiki_category_pages(category_name)
    report["errors"].extend(errors)
    if not titles:
        report["errors"].append(f"B站分类 {category_name} 未获取到页面")
        return
    revisions, errors = bwiki_revisions(titles)
    report["errors"].extend(errors)
    LOGGER.info("B站 %s(%s): %s 个页面", category, category_name, len(revisions))
    _scan_bwiki_items(category, revisions, config, manifest, report)


def scan_bwiki_titles(category, titles, config, manifest, report):
    """批量扫描一组固定页面（例如地图文本区域页），只初始化一次基线。"""
    revisions, errors = bwiki_revisions(titles)
    report["errors"].extend(errors)
    missing = [title for title in titles if title not in revisions]
    if missing:
        report["errors"].append(f"B站页面未获取到修订: {missing[:5]}（共 {len(missing)} 个）")
    if revisions:
        _scan_bwiki_items(category, revisions, config, manifest, report)


def _scan_bwiki_items(category, revisions, config, manifest, report):
    initialized_key = f"bwiki:{category}"
    seen = manifest["bwiki"].setdefault(category, {})
    seed_only = not manifest["initialized"].get(initialized_key)

    for title, revision in revisions.items():
        old = seen.get(title)
        change_type = None
        if old is None:
            change_type = "seed" if seed_only else "new"
        elif str(old.get("lastrevid")) != str(revision.get("lastrevid")):
            change_type = "updated"
        seen[title] = {
            "title": title,
            "lastrevid": revision.get("lastrevid"),
            "timestamp": revision.get("timestamp"),
            "sha1": revision.get("sha1"),
            "checked_at": now_text(),
        }
        if change_type in ("new", "updated"):
            _validate_bwiki_change(category, title, change_type, config, manifest, report)

    if seed_only:
        manifest["initialized"][initialized_key] = True
        report["baseline_seeded"].append({"source": "bwiki", "category": category, "count": len(revisions)})
        LOGGER.info("B站 %s 首次建立基线: %s 条", category, len(revisions))


def _validate_bwiki_change(category, title, change_type, config, manifest, report):
    raw = bwiki_raw(title)
    item = {
        "source": "bwiki",
        "category": category,
        "id": title,
        "title": title,
        "change_type": change_type,
        "detected_at": now_text(),
    }
    if not raw:
        item["fetch_error"] = "抓取 raw 失败"
        item["validation"] = {"ok": False, "reasons": ["抓取 raw 失败"], "details": {}}
    else:
        item["validation"] = validate_bwiki(category, title, raw, config.get("validators", {}))
    report["changes"].append(item)
    if not item["validation"]["ok"]:
        _merge_pending(manifest, item)
        LOGGER.warning("B站未完成: %s/%s %s", category, title, item["validation"]["reasons"])


# ====== 米游社观测枢 ======

def load_mihoyo_module():
    path = os.path.join(PROJECT_DIR, "wiki_data_tools", "_fetch_mihoyo_channel.py")
    spec = importlib.util.spec_from_file_location("daily_mihoyo_channel", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["daily_mihoyo_channel"] = module
    spec.loader.exec_module(module)
    return module


def scan_mihoyo_source(config, manifest, report):
    mihoyo_conf = config["sources"]["mihoyo"]
    if not mihoyo_conf.get("enabled"):
        return
    helper = load_mihoyo_module()
    for channel_key, channel_id in (mihoyo_conf.get("channels") or {}).items():
        scan_mihoyo_channel(helper, channel_key, channel_id, config, manifest, report)


def scan_mihoyo_channel(helper, channel_key, channel_id, config, manifest, report):
    items = helper.fetch_channel_list(channel_id)
    if not items:
        report["errors"].append(f"米游社频道 {channel_id} 未获取到列表")
        return
    LOGGER.info("米游社 %s(%s): %s 张卡片", channel_key, channel_id, len(items))
    initialized_key = f"mihoyo:{channel_key}"
    seen = manifest["mihoyo"].setdefault(channel_key, {})
    seed_only = not manifest["initialized"].get(initialized_key)
    current_ids = set()

    for item in items:
        content_id = str(item.get("content_id"))
        current_ids.add(content_id)
        title = item.get("title") or content_id
        fingerprint = helper.card_fingerprint(item, channel_id)
        old = seen.get(content_id)
        change_type = None
        if old is None:
            change_type = "seed" if seed_only else "new"
        elif str(old.get("card_fingerprint")) != fingerprint:
            change_type = "updated"
        seen[content_id] = {
            "content_id": content_id,
            "title": title,
            "card_fingerprint": fingerprint,
            "checked_at": now_text(),
        }
        if change_type in ("new", "updated"):
            _validate_mihoyo_change(helper, channel_key, content_id, title, change_type, config, manifest, report)

    removed_ids = sorted(set(seen) - current_ids)
    if removed_ids:
        report["removed"].append({"source": "mihoyo", "category": channel_key, "ids": removed_ids[:50], "count": len(removed_ids)})
        for content_id in removed_ids:
            seen.pop(content_id, None)

    if seed_only:
        manifest["initialized"][initialized_key] = True
        report["baseline_seeded"].append({"source": "mihoyo", "category": channel_key, "count": len(items)})
        LOGGER.info("米游社 %s 首次建立基线: %s 条", channel_key, len(items))


def _validate_mihoyo_change(helper, channel_key, content_id, title, change_type, config, manifest, report):
    page = helper.fetch_entry_page(content_id)
    item = {
        "source": "mihoyo",
        "category": channel_key,
        "id": content_id,
        "title": title,
        "change_type": change_type,
        "detected_at": now_text(),
    }
    if not page:
        item["fetch_error"] = "抓取详情失败"
        item["validation"] = {"ok": False, "reasons": ["抓取详情失败"], "details": {}}
    else:
        item["validation"] = validate_mihoyo(channel_key, title, page, config.get("validators", {}))
    report["changes"].append(item)
    if not item["validation"]["ok"]:
        _merge_pending(manifest, item)
        LOGGER.warning("米游社未完成: %s/%s %s", channel_key, title, item["validation"]["reasons"])


# ====== pending 合并与复查 ======

def _merge_pending(manifest, item):
    key = f"{item['source']}:{item['category']}:{item['id']}"
    for existing in manifest["pending"]:
        if existing.get("key") == key:
            existing.update(item)
            existing["updated_at"] = now_text()
            return
    pending = dict(item)
    pending["key"] = key
    pending["updated_at"] = now_text()
    manifest["pending"].append(pending)


def recheck_pending(config, manifest, report):
    """复查历史 pending；如果 Wiki 已补齐则移出阻塞列表。"""
    helper = None
    remaining = []
    for pending in manifest["pending"]:
        source = pending.get("source")
        category = pending.get("category")
        title = pending.get("title") or pending.get("id")
        content_id = pending.get("id")
        item = dict(pending)
        item["updated_at"] = now_text()
        if source == "bwiki":
            raw = bwiki_raw(title)
            if raw:
                item["validation"] = validate_bwiki(category, title, raw, config.get("validators", {}))
                item.pop("fetch_error", None)
            else:
                item["validation"] = {"ok": False, "reasons": ["复查时抓取 raw 失败"], "details": {}}
                item["fetch_error"] = "复查抓取失败"
        elif source == "mihoyo":
            if helper is None:
                helper = load_mihoyo_module()
            page = helper.fetch_entry_page(content_id)
            if page:
                item["validation"] = validate_mihoyo(category, title, page, config.get("validators", {}))
                item.pop("fetch_error", None)
            else:
                item["validation"] = {"ok": False, "reasons": ["复查时抓取详情失败"], "details": {}}
                item["fetch_error"] = "复查抓取失败"
        else:
            item["validation"] = {"ok": False, "reasons": [f"未知来源: {source}"], "details": {}}
        if item["validation"]["ok"]:
            LOGGER.info("pending 已补齐，移出阻塞: %s", item.get("key"))
        else:
            remaining.append(item)
    manifest["pending"] = remaining


# ====== 报告与通知 ======

def build_report(manifest, report):
    blockers = [p for p in manifest["pending"] if not (p.get("validation") or {}).get("ok")]
    complete_changes = [c for c in report["changes"] if (c.get("validation") or {}).get("ok")]
    incomplete_changes = [c for c in report["changes"] if not (c.get("validation") or {}).get("ok")]
    has_scan_error = len(report["errors"]) > 0
    report["summary"] = {
        "baseline_seeded_count": sum(x.get("count", 0) for x in report["baseline_seeded"]),
        "change_count": len(report["changes"]),
        "complete_change_count": len(complete_changes),
        "incomplete_change_count": len(incomplete_changes),
        "pending_count": len(manifest["pending"]),
        "blocker_count": len(blockers),
        "gate": "blocked" if (blockers or has_scan_error) else "pass",
        "error_count": len(report["errors"]),
    }
    report["blockers"] = [
        {
            "key": p.get("key"),
            "title": p.get("title"),
            "reasons": (p.get("validation") or {}).get("reasons", []),
        }
        for p in blockers[:50]
    ]
    return report


def notify_windows(config, title: str, message: str):
    if not (config.get("notification") or {}).get("enabled", True):
        return
    script = os.path.join(SCRIPT_DIR, "notify_windows.ps1")
    if not os.path.exists(script):
        LOGGER.warning("通知脚本不存在: %s", script)
        return
    try:
        subprocess.run(
            [
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", script,
                "-Title", title,
                "-Message", message,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception as error:
        LOGGER.warning("Windows 通知失败: %s", error)


def format_notification(report) -> str:
    summary = report["summary"]
    if summary["gate"] == "blocked":
        if summary["blocker_count"] > 0:
            return (
                f"知识库更新暂缓：{summary['blocker_count']} 个条目缺字段。"
                f"新增/变化 {summary['change_count']} 个，扫描错误 {summary['error_count']} 个。"
            )
        return f"知识库检测未完成：扫描错误 {summary['error_count']} 个，本次不更新。"
    if summary["change_count"] == 0:
        return f"知识库无更新。新增/变化 0 个，错误 {summary['error_count']} 个。"
    return (
        f"知识库检测到 {summary['change_count']} 个新增/变化，"
        f"完整性通过，dry-run 未写入数据。"
    )


# ====== 主流程 ======

def main():
    parser = argparse.ArgumentParser(description="知识库自动更新检测器（Phase 1 dry-run）")
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--mode", default="dry-run", choices=["dry-run"],
                        help="Phase 1 只支持 dry-run")
    parser.add_argument("--no-notify", action="store_true")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    config = load_json(args.config, None)
    if not config:
        print(f"配置文件不存在或无法读取: {args.config}", file=sys.stderr)
        return 30

    logs_dir = os.path.join(PROJECT_DIR, config.get("paths", {}).get("logs_dir", "logs/kb_auto_update"))
    manifest_path = os.path.join(PROJECT_DIR, config.get("paths", {}).get("manifest", "logs/kb_auto_update/detection_manifest.json"))
    day = datetime.now().strftime("%Y-%m-%d")
    log_path = os.path.join(logs_dir, f"{day}.log")
    report_path = os.path.join(logs_dir, f"{day}.json")
    setup_logging(log_path)

    LOGGER.info("=" * 60)
    LOGGER.info("知识库自动更新 dry-run 开始")
    LOGGER.info("配置: %s", args.config)
    if args.mode != "dry-run":
        LOGGER.error("Phase 1 只允许 dry-run")
        return 30

    manifest = load_json(manifest_path, {
        "version": 1,
        "created_at": now_text(),
        "updated_at": now_text(),
        "bwiki": {},
        "mihoyo": {},
        "initialized": {},
        "pending": [],
    })
    manifest.setdefault("bwiki", {})
    manifest.setdefault("mihoyo", {})
    manifest.setdefault("initialized", {})
    manifest.setdefault("pending", [])

    report = {
        "date": day,
        "mode": "dry-run",
        "started_at": now_text(),
        "errors": [],
        "baseline_seeded": [],
        "changes": [],
        "removed": [],
    }

    try:
        scan_bwiki_source(config, manifest, report)
        scan_mihoyo_source(config, manifest, report)
        recheck_pending(config, manifest, report)
    except Exception as error:
        LOGGER.exception("扫描过程异常: %s", error)
        report["errors"].append(f"扫描异常: {error}")

    report["finished_at"] = now_text()
    report = build_report(manifest, report)
    manifest["updated_at"] = now_text()
    atomic_write_json(manifest_path, manifest)
    atomic_write_json(report_path, report)

    summary = report["summary"]
    LOGGER.info("检测完成: 变化 %s，完整 %s，不完整 %s，pending %s，gate=%s，错误 %s",
                summary["change_count"], summary["complete_change_count"],
                summary["incomplete_change_count"], summary["pending_count"],
                summary["gate"], summary["error_count"])
    LOGGER.info("报告: %s", report_path)

    if not args.no_notify:
        notify_windows(config, "原神知识库自动更新", format_notification(report))

    if summary["error_count"] > 0 and summary["change_count"] == 0:
        return 30
    if summary["gate"] == "blocked":
        return 20
    if summary["change_count"] > 0:
        return 10
    return 0


if __name__ == "__main__":
    sys.exit(main())
