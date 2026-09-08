# -*- coding: utf-8 -*-
"""全量 wiki 链接图增量维护脚本（知识库侧）。

用法：
  python scripts/update_wiki_graph.py --check          # 只检测变更，不抓取不重建
  python scripts/update_wiki_graph.py --apply          # 抓 new/updated 详情 + 重建图 + 写报告
  python scripts/update_wiki_graph.py --report         # 打印上次 --apply 报告
  python scripts/update_wiki_graph.py --tier 1 --check # 只检测 Tier1

设计：
  manifest 记录每个 content_id 的 card_fingerprint + page_hash。
  --check 只读列表并对比 manifest，不写指纹。
  --apply 先检测，再把 new/updated 的 ID 交给 _fetch_mihoyo_channel.py --ids 增量抓取，
          然后更新 manifest、重建图、保存报告。
  不自动删除词条：列表里消失的 ID 在 manifest 里标成 stale。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
FETCH_SCRIPT = BASE_DIR / "wiki_data_tools" / "_fetch_mihoyo_channel.py"
BUILD_SCRIPT = BASE_DIR / "wiki_entry_graph.py"
RAW_DIR = BASE_DIR / "content_data" / "wiki_raw"
GRAPH_PATH = BASE_DIR / "kb_vectors" / "wiki_entry_graph.json"
IDS_FILE = RAW_DIR / "_pending_ids.json"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(BASE_DIR / "wiki_data_tools"))
from _fetch_mihoyo_channel import (  # noqa: E402
    MANIFEST_PATH,
    REPORT_PATH,
    CHANNEL_NAMES,
    card_fingerprint,
    fetch_channel_list,
    load_manifest,
    raw_page_hash_map,
    save_manifest,
    save_report,
    select_channels,
)


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def log(msg):
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def run(cmd, cwd=None):
    print("  $", " ".join(str(x) for x in cmd), flush=True)
    return subprocess.run(
        [str(x) for x in cmd], cwd=str(cwd or BASE_DIR), shell=False
    )


def backup_file(path: Path):
    """apply 前备份 graph/manifest，避免误操作后无法回滚。"""
    if path.exists():
        bak = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, bak)
        print(f"  备份: {path.name} -> {bak.name}")


def fetch_current_state(channel_id: int, channel_name: str):
    """拉取频道最新列表，返回 {content_id: {card_fingerprint, page_hash, title}}；列表失败返回 None。"""
    items = fetch_channel_list(channel_id)
    if not items:
        log(f"  警告：频道 {channel_id} ({channel_name}) 列表获取失败或为空，跳过")
        return None
    page_map = raw_page_hash_map(channel_id)
    current = {}
    for it in items:
        cid = str(it.get("content_id"))
        current[cid] = {
            "title": str(it.get("title") or ""),
            "card_fingerprint": card_fingerprint(it, channel_id),
            "page_hash": page_map.get(cid, ""),
        }
    return current


def build_initial_manifest(channels) -> dict:
    """没有 manifest 时，用当前列表 + 现有 raw 的 page_hash 初始化基线。"""
    manifest = {
        "version": 1,
        "created_at": now(),
        "last_check_at": now(),
        "channels": {},
    }
    for channel_id, graph_type in channels:
        channel_name = CHANNEL_NAMES.get(channel_id, str(channel_id))
        current = fetch_current_state(channel_id, channel_name)
        if current is None:
            continue
        items = {}
        for cid, data in current.items():
            items[cid] = {
                "title": data["title"],
                "card_fingerprint": data["card_fingerprint"],
                "page_hash": data["page_hash"],
                "fetched_at": now(),
                "status": "ok" if data["page_hash"] else "error",
            }
        manifest["channels"][str(channel_id)] = {
            "graph_type": graph_type,
            "card_count": len(items),
            "items": items,
        }
    save_manifest(manifest)
    return manifest


def compare_manifest(manifest, channels) -> dict:
    """对比 manifest 与最新列表，只读不写。返回报告 + 每个频道的当前状态。"""
    report = {
        "checked_at": now(),
        "channels": {},
        "total_new": 0,
        "total_updated": 0,
        "total_removed": 0,
        "states": {},
    }
    manifest_channels = manifest.get("channels", {})
    for channel_id, graph_type in channels:
        channel_name = CHANNEL_NAMES.get(channel_id, str(channel_id))
        current = fetch_current_state(channel_id, channel_name)
        if current is None:
            report["channels"][str(channel_id)] = {
                "graph_type": graph_type,
                "error": "list_fetch_failed",
            }
            continue
        old_items = manifest_channels.get(str(channel_id), {}).get("items", {})
        new_ids, updated_ids, removed_ids = [], [], []
        unchanged_count = 0
        for cid, data in current.items():
            old = old_items.get(cid)
            if not old:
                new_ids.append(cid)
            elif old.get("card_fingerprint") != data["card_fingerprint"]:
                updated_ids.append(cid)
            elif old.get("status") in ("error", "stale") or not old.get("page_hash") or not data.get("page_hash"):
                # 详情缺失/上次抓取失败/列表重新出现：即使卡片指纹没变也要补抓。
                updated_ids.append(cid)
            else:
                unchanged_count += 1
        for cid, old in old_items.items():
            if cid not in current and old.get("status") != "stale":
                removed_ids.append(cid)
        report["channels"][str(channel_id)] = {
            "graph_type": graph_type,
            "new": new_ids,
            "updated": updated_ids,
            "removed": removed_ids,
            "unchanged": unchanged_count,
            "current_count": len(current),
        }
        report["states"][str(channel_id)] = current
        report["total_new"] += len(new_ids)
        report["total_updated"] += len(updated_ids)
        report["total_removed"] += len(removed_ids)
    return report


def rebuild_manifest_after_fetch(manifest, channels) -> dict:
    """抓取完成后，用最新列表 + 最新 raw page_hash 重建 manifest；列表消失的标 stale。"""
    new_manifest = {
        "version": 1,
        "created_at": manifest.get("created_at", now()),
        "last_check_at": now(),
        "last_apply_at": now(),
        "channels": {},
    }
    for channel_id, graph_type in channels:
        channel_name = CHANNEL_NAMES.get(channel_id, str(channel_id))
        current = fetch_current_state(channel_id, channel_name)
        old_channel = manifest.get("channels", {}).get(str(channel_id), {})
        old_items = old_channel.get("items", {})
        if current is None:
            new_manifest["channels"][str(channel_id)] = old_channel
            continue
        items = {}
        for cid, data in current.items():
            old = old_items.get(cid, {})
            items[cid] = {
                "title": data["title"],
                "card_fingerprint": data["card_fingerprint"],
                "page_hash": data["page_hash"],
                "fetched_at": old.get("fetched_at", now()),
                "status": "ok" if data["page_hash"] else "error",
            }
        # 保留列表里已不存在的旧词条，标 stale，不删除。
        for cid, old in old_items.items():
            if cid not in items:
                stale = dict(old)
                stale["status"] = "stale"
                items[cid] = stale
        new_manifest["channels"][str(channel_id)] = {
            "graph_type": graph_type,
            "card_count": len(current),
            "items": items,
        }
    save_manifest(new_manifest)
    return new_manifest


def print_check_report(report: dict):
    print("\n===== 变更检测报告 =====")
    print(f"检测时间: {report.get('checked_at')}")
    print(f"总计: new={report['total_new']} updated={report['total_updated']} removed={report['total_removed']}")
    for cid, ch in sorted(report.get("channels", {}).items(), key=lambda x: int(x[0])):
        if "error" in ch:
            print(f"  频道 {cid}: 列表获取失败")
            continue
        print(
            f"  频道 {cid:>3} ({ch.get('graph_type')}): "
            f"new={len(ch['new'])} updated={len(ch['updated'])} removed={len(ch['removed'])} "
            f"unchanged={ch['unchanged']}"
        )
        if ch["new"]:
            print(f"       new: {ch['new'][:10]}{' ...' if len(ch['new']) > 10 else ''}")
        if ch["updated"]:
            print(f"       updated: {ch['updated'][:10]}{' ...' if len(ch['updated']) > 10 else ''}")
        if ch["removed"]:
            print(f"       removed(标stale): {ch['removed'][:10]}{' ...' if len(ch['removed']) > 10 else ''}")


def collect_changed_ids(report: dict) -> list:
    """把报告中的 new+updated 展平为 ID 列表。"""
    ids = []
    for ch in report.get("channels", {}).values():
        if "error" in ch:
            continue
        ids.extend(ch.get("new", []))
        ids.extend(ch.get("updated", []))
    return sorted(set(ids))


def cmd_check(args):
    manifest = load_manifest()
    channels = select_channels(args.channel, args.tier)
    log(f"检查 {len(channels)} 个频道")
    if manifest is None:
        log("首次运行：初始化 manifest 基线（拉列表，不抓详情）...")
        manifest = build_initial_manifest(channels)
        print_check_report({"checked_at": now(), "channels": {}, "total_new": 0, "total_updated": 0, "total_removed": 0})
        print("\nmanifest 初始化完成，后续 --check 将基于此基线对比。")
        return 0
    report = compare_manifest(manifest, channels)
    print_check_report(report)
    return 0


def cmd_apply(args):
    manifest = load_manifest()
    channels = select_channels(args.channel, args.tier)
    if manifest is None:
        log("首次运行：初始化 manifest 基线...")
        manifest = build_initial_manifest(channels)
        report = compare_manifest(manifest, channels)
        print_check_report(report)
    else:
        report = compare_manifest(manifest, channels)
        print_check_report(report)

    changed_ids = collect_changed_ids(report)
    log(f"待抓取 ID 数: {len(changed_ids)}")

    if changed_ids:
        backup_file(GRAPH_PATH)
        backup_file(Path(MANIFEST_PATH))
        # 写临时 ID 文件给 _fetch_mihoyo_channel.py --ids
        IDS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(IDS_FILE, "w", encoding="utf-8") as f:
            json.dump({"ids": changed_ids, "generated_at": now()}, f, ensure_ascii=False, indent=2)
        fetch_cmd = [
            sys.executable, str(FETCH_SCRIPT),
            "--ids", str(IDS_FILE),
            "--force",
            "--tier", args.tier,
            "--delay", str(args.delay),
        ]
        ret = run(fetch_cmd)
        if ret.returncode not in (0, 2):
            print("增量抓取失败，停止 apply", file=sys.stderr)
            return ret.returncode
        manifest = rebuild_manifest_after_fetch(manifest, channels)

    # 即使没有 changed_ids，也重建 manifest 的 last_apply_at？保持只写一次即可。
    if changed_ids:
        pass
    else:
        manifest["last_check_at"] = now()
        manifest["last_apply_at"] = now()
        save_manifest(manifest)

    # 重建全量图（当前规模本地构建足够，不做 patch）。
    log("重建全量 wiki 链接图...")
    build_cmd = [sys.executable, str(BUILD_SCRIPT), "--build"]
    ret = run(build_cmd)
    if ret.returncode != 0:
        print("重建失败", file=sys.stderr)
        return ret.returncode

    report["applied_at"] = now()
    report["applied_ids"] = changed_ids
    report["stale_ids"] = []
    for ch in report.get("channels", {}).values():
        if "error" in ch:
            continue
        report["stale_ids"].extend(ch.get("removed", []))
    # states 是内部临时数据，不写入报告，避免 -report 输出/文件过大。
    report.pop("states", None)
    save_report(report)
    log("apply 完成")
    return 0


def cmd_report(args):
    report = load_manifest(REPORT_PATH)
    if not report:
        print("还没有 --apply 报告。请先运行 python scripts/update_wiki_graph.py --apply")
        return 0
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def main():
    parser = argparse.ArgumentParser(description="全量 wiki 链接图增量维护")
    parser.add_argument("--channel", type=int, action="append", default=None, help="指定频道 ID，可多次传")
    parser.add_argument("--tier", default="all", help="1/2/3/all，默认 all")
    parser.add_argument("--delay", type=float, default=0.5, help="抓取详情间隔秒数")
    parser.add_argument("--check", action="store_true", help="只检测变更，不抓取不重建")
    parser.add_argument("--apply", action="store_true", help="抓 new/updated 详情 + 重建图 + 写报告")
    parser.add_argument("--report", action="store_true", help="打印上次 apply 报告")
    parser.add_argument("--dry-run", action="store_true", help="等价 --check")
    args = parser.parse_args()

    if args.dry_run:
        args.check = True
    if args.check:
        return cmd_check(args)
    if args.apply:
        return cmd_apply(args)
    if args.report:
        return cmd_report(args)

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
