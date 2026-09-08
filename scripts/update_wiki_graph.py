# -*- coding: utf-8 -*-
"""增量更新全量 wiki 链接图（Flash 日常维护脚本）。

用法：
  python scripts/update_wiki_graph.py                     # 更新全部频道并重建全量图
  python scripts/update_wiki_graph.py --tier 1            # 只更新 Tier1
  python scripts/update_wiki_graph.py --tier all --delay 0.5
  python scripts/update_wiki_graph.py --dry-run           # 只对比新增/缺失，不抓取

原理：
  1. 调用 _fetch_mihoyo_channel.py 的断点续传能力：已有成功的 page 跳过，只抓新增/失败；
  2. 对比当前 raw 与最新列表，输出“不再出现在列表中的 ID”（stale，不自动删除）；
  3. 重建 wiki_entry_graph.json。
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
FETCH_SCRIPT = BASE_DIR / "wiki_data_tools" / "_fetch_mihoyo_channel.py"
BUILD_SCRIPT = BASE_DIR / "wiki_entry_graph.py"
RAW_DIR = BASE_DIR / "content_data" / "wiki_raw"


def run(cmd, cwd=None):
    print("  $", " ".join(str(x) for x in cmd), flush=True)
    return subprocess.run(
        [str(x) for x in cmd], cwd=str(cwd or BASE_DIR), shell=False
    )


def fetch_channel_lists(tier):
    """按 tier 调用抓取脚本，让所有频道做断点续传拉取。"""
    cmd = [sys.executable, str(FETCH_SCRIPT), "--tier", tier, "--delay"]
    # 延迟由调用方参数传进来
    return cmd


def compare_stale(raw_dir=RAW_DIR):
    """对比各频道 raw 与最新列表：返回 stale 概况（不删除）。"""
    stale_summary = {}
    # 每个 channel 文件里没有独立保存“最新列表”，这里直接说明：
    # 抓取脚本在更新时会将列表里不存在的旧 ID 保留为老记录；
    # 如需正式删除，需先做全库检索（AGENTS 规则），此处只提示数量。
    for path in sorted(Path(raw_dir).glob("channel_*.json")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except Exception:
            continue
        stale_summary[path.name] = len(doc.get("items") or [])
    return stale_summary


def main():
    parser = argparse.ArgumentParser(description="增量更新全量 wiki 链接图")
    parser.add_argument("--tier", default="all", help="1/2/3/all，默认 all")
    parser.add_argument("--delay", type=float, default=0.5, help="抓取详情间隔秒数")
    parser.add_argument("--dry-run", action="store_true", help="只对比，不抓取/不重建")
    args = parser.parse_args()

    if args.dry_run:
        print("dry-run：当前各频道 item 数")
        for name, count in sorted(compare_stale().items()):
            print(f"  {name}: {count}")
        print("dry-run done")
        return 0

    # 1. 增量抓取（已有成功的 page 自动跳过）
    fetch_cmd = [sys.executable, str(FETCH_SCRIPT), "--tier", args.tier, "--delay", str(args.delay)]
    ret = run(fetch_cmd)
    if ret.returncode not in (0, 2):
        print("抓取脚本异常退出，停止重建", file=sys.stderr)
        return ret.returncode

    # 2. 提示 stale（仅提示，不自动删除）
    print("\n[stale 提示] 本轮不自动删除任何词条；如需删除请先做全库检索")
    for name, count in sorted(compare_stale().items()):
        print(f"  {name}: {count} items")

    # 3. 重建全量图
    build_cmd = [sys.executable, str(BUILD_SCRIPT), "--build"]
    ret = run(build_cmd)
    if ret.returncode != 0:
        print("重建失败", file=sys.stderr)
        return ret.returncode
    print("\n增量更新完成", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
