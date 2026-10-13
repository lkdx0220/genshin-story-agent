"""生成 README 的「现状」区块（机器维护，禁止手改）。

用法：
    python scripts/gen_readme_status.py           # 就地刷新 README
    python scripts/gen_readme_status.py --check    # 只校验（CI 用；不一致退出码 1）
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
BEGIN = "<!-- GENERATED:STATUS"
END = "<!-- /GENERATED:STATUS -->"


def _count_tools() -> tuple[int, int]:
    # 返回 (注册总数, 隐藏数)；隐藏数解析不到时为 0，调用方据此避免输出假数字
    """工具总数与初始暴露数。

    主路径：AST 解析 app/tools/__init__.py 中名为 tools 的集合（本项目实际注册方式）；
    回退：统计 app/tools/*.py 里的 @tool 装饰器（装饰器式注册项目）。
    """
    import ast

    total = 0
    init = PROJ / "app" / "tools" / "__init__.py"
    try:
        tree = ast.parse(init.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.AnnAssign):
                names = [node.target.id] if isinstance(node.target, ast.Name) else []
                value = node.value
            elif isinstance(node, ast.Assign):
                names = [t.id for t in node.targets if isinstance(t, ast.Name)]
                value = node.value
            else:
                continue
            if "tools" not in names or value is None:
                continue
            if isinstance(value, (ast.List, ast.Tuple, ast.Set)):
                total = max(total, len(value.elts))
            elif isinstance(value, ast.Dict):
                total = max(total, len(value.keys))
    except Exception:
        total = 0
    if total == 0:
        for f in sorted((PROJ / "app" / "tools").glob("*.py")):
            total += len(re.findall(r"@tool\(", f.read_text(encoding="utf-8")))
    hidden = _count_hidden()
    return total, hidden


def _count_hidden() -> int:
    """初始不对模型暴露的工具数（_HIDDEN_TOOL_NAMES）。AST 解析，兼容 set/list/tuple/dict。"""
    import ast

    init = PROJ / "app" / "tools" / "__init__.py"
    try:
        tree = ast.parse(init.read_text(encoding="utf-8"))
    except Exception:
        return 0
    for node in tree.body:
        if isinstance(node, ast.AnnAssign):
            names = [node.target.id] if isinstance(node.target, ast.Name) else []
            value = node.value
        elif isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            value = node.value
        else:
            continue
        if "_HIDDEN_TOOL_NAMES" not in names or value is None:
            continue
        if isinstance(value, (ast.Set, ast.List, ast.Tuple)):
            return len(value.elts)
        if isinstance(value, ast.Dict):
            return len(value.keys)
    return 0


def _kb_stats() -> dict:
    kb = PROJ / "kb_vectors"
    out: dict = {}
    graph = kb / "wiki_entry_graph.json"
    if graph.exists():
        data = json.loads(graph.read_text(encoding="utf-8"))
        entries = data.get("entries") or []
        out["entries"] = len(entries)
        out["data_date"] = datetime.fromtimestamp(graph.stat().st_mtime).strftime("%Y-%m-%d")
    idx = kb / "wiki_entity_mention_index.json"
    if idx.exists():
        ents = (json.loads(idx.read_text(encoding="utf-8")).get("entities") or {})
        out["entities"] = len(ents)
        out["mentions"] = sum(len(v.get("mentions") or []) for v in ents.values())
    am = kb / "entity_alias_map.json"
    if am.exists():
        out["alias"] = len(json.loads(am.read_text(encoding="utf-8")).get("variant_to_canonical") or {})
    cc = kb / "cast_coverage.json"
    if cc.exists():
        cats = (json.loads(cc.read_text(encoding="utf-8")).get("categories") or {})
        out["cast"] = cats
    return out


def _latest_release() -> str:
    try:
        r = subprocess.run(["gh", "release", "list", "--limit", "1"],
                           capture_output=True, text=True, timeout=20,
                           encoding="utf-8", errors="replace")
        first = (r.stdout or "").strip().splitlines()
        if r.returncode == 0 and first:
            return first[0].split("\t")[2].strip()
    except Exception:
        pass
    return "kb-2026.09.10"  # 回退值：发新快照时请同步更新


def _rate(d: dict) -> str:
    try:
        return f"{int(d['non_empty'])}/{int(d['total'])} = {float(d['rate']):.1%}"
    except Exception:
        return "—"


def build_block() -> str:
    total, hidden = _count_tools()
    kb = _kb_stats()
    cats = kb.get("cast") or {}
    act = (cats.get("活动剧情") or {})
    egg = (cats.get("彩蛋剧情") or {})
    rows = [
        ("已注册工具",
         f"{total} / 初始暴露 {total - hidden}" if hidden else f"{total}（初始暴露数见 app/tools/__init__.py）"),
        ("词条条目", f"{kb.get('entries', '—'):,}" if isinstance(kb.get("entries"), int) else "—"),
        ("提及索引实体（有提及）/ 提及对",
         f"{kb.get('entities', '—'):,} / {kb.get('mentions', '—'):,}"
         if isinstance(kb.get("entities"), int) and isinstance(kb.get("mentions"), int) else "—"),
        ("别名归一映射", f"{kb.get('alias', '—'):,}" if isinstance(kb.get("alias"), int) else "—"),
        ("cast 覆盖（活动剧情）", _rate(act.get("raw") or act)),
        ("cast 覆盖（彩蛋剧情）",
         f"原始 {_rate(egg.get('raw') or {})}；有剧情/语音 {_rate(egg.get('narrative_only') or {})}"),
        ("工作区数据快照日期", kb.get("data_date") or "—"),
        ("最新 KB Release", f"{_latest_release()}（发布资产是快照，非当前工作区状态）"),
    ]
    lines = [BEGIN + " 由 scripts/gen_readme_status.py 生成，请勿手改；运行时重跑即可刷新 -->",
             "| 项 | 当前值 |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in rows]
    lines.append(END)
    return "\n".join(lines)


def main() -> int:
    readme = PROJ / "README.md"
    text = readme.read_text(encoding="utf-8")
    block = build_block()
    if BEGIN in text:
        start = text.index(BEGIN)
        stop = text.index(END, start) + len(END)
        new_text = text[:start] + block + text[stop:]
    else:
        anchor = "\n## 预构建知识库 Release"
        if anchor not in text:
            print("找不到插入锚点（## 预构建知识库 Release）", file=sys.stderr)
            return 2
        new_text = text.replace(anchor, "\n" + block + "\n" + anchor, 1)

    if "--check" in sys.argv:
        if new_text != text:
            print("README 现状区块与真源不一致：请运行 python scripts/gen_readme_status.py 后提交", file=sys.stderr)
            return 1
        print("README 现状区块与真源一致 ✓")
        return 0
    readme.write_text(new_text, encoding="utf-8")
    print("README 现状区块已刷新 ✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
