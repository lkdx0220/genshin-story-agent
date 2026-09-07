# -*- coding: utf-8 -*-
"""本地 wiki 词条链接图（轻量 GraphRAG 数据层，试点版）。

设计目标：
- 不靠 LLM 抽三元组，直接复用米游社观测枢词条内部自带的 data-entry-id 超链接；
- 每个词条保存：全文、类型、地区、别名、它链接到的其它词条；
- Agent 可以像 WorkBuddy 那样顺着链接做多跳检索。

本模块只处理数据结构、构建与查询，不负责网络抓取。
"""
from __future__ import annotations

import argparse
import html
import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from wiki_graph_channels import CHANNEL_TYPE_MAP

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT = BASE_DIR / "kb_vectors" / "wiki_entry_graph.json"
PILOT_OUTPUT = BASE_DIR / "kb_vectors" / "wiki_entry_graph_pilot.json"

TASK_RAW = BASE_DIR / "content_data" / "mihoyo_tasks_raw.json"
MAP_TEXT_RAW = BASE_DIR / "content_data" / "mihoyo_map_text_raw_full.json"
MISSING_RAW = BASE_DIR / "content_data" / "wiki_missing_entries_raw.json"
WIKI_RAW_DIR = BASE_DIR / "content_data" / "wiki_raw"

SCHEMA_VERSION = 1

# 试点范围：灰眸四线任务 ID 及其关联地区。
PILOT_TASK_IDS = {"509533", "509538", "509592", "509591"}
# 试点图缺失、已单独补抓的高价值词条（见 scripts/fetch_wiki_missing_entries.py）。
PILOT_MISSING_IDS = {"509399", "509397", "509511"}
PILOT_REGIONS = {
    "至冬", "白冕宫", "奥古洛夫镇", "焰羽谷", "赫斯珀利德斯之馆", "白桦雪葬地",
}

_LINK_RE_1 = re.compile(
    r'data-entry-id=["\'](\d+)["\'][^>]*data-entry-name=["\']([^"\']+)["\']'
)
_LINK_RE_2 = re.compile(
    r'data-entry-name=["\']([^"\']+)["\'][^>]*data-entry-id=["\'](\d+)["\']'
)


class _TextExtractor(HTMLParser):
    """把 HTML 转为可读纯文本，块级标签换行。"""

    BLOCK_TAGS = {"p", "div", "li", "br", "tr", "h1", "h2", "h3", "h4", "h5", "section", "table"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        text = data.strip()
        if text:
            self.parts.append(text)


@dataclass
class WikiLink:
    target_id: str
    target_name: str
    context: str = ""


@dataclass
class WikiEntry:
    entry_id: str
    title: str
    entry_type: str
    full_text: str
    aliases: List[str] = field(default_factory=list)
    region: str = ""
    filters: List[str] = field(default_factory=list)
    links: List[WikiLink] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "title": self.title,
            "entry_type": self.entry_type,
            "full_text": self.full_text,
            "aliases": self.aliases,
            "region": self.region,
            "filters": self.filters,
            "links": [
                {"target_id": l.target_id, "target_name": l.target_name, "context": l.context}
                for l in self.links
            ],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WikiEntry":
        return cls(
            entry_id=str(data.get("entry_id", "")),
            title=str(data.get("title", "")),
            entry_type=str(data.get("entry_type", "")),
            full_text=str(data.get("full_text", "")),
            aliases=list(data.get("aliases") or []),
            region=str(data.get("region", "")),
            filters=list(data.get("filters") or []),
            links=[
                WikiLink(
                    target_id=str(l.get("target_id", "")),
                    target_name=str(l.get("target_name", "")),
                    context=str(l.get("context", "")),
                )
                for l in data.get("links") or []
            ],
        )


@dataclass
class WikiEntryGraph:
    entries: Dict[str, WikiEntry] = field(default_factory=dict)

    def add(self, entry: WikiEntry) -> None:
        if entry.entry_id:
            self.entries[entry.entry_id] = entry

    def get(self, entry_id: str) -> Optional[WikiEntry]:
        return self.entries.get(str(entry_id))

    def search(self, keyword: str, limit: int = 20) -> List[WikiEntry]:
        """本地词条搜索：标题精确优先，其次标题/别名/全文子串，再退化为字符交集。"""
        kw = (keyword or "").strip()
        if not kw:
            return []
        results: List[Tuple[int, WikiEntry]] = []
        seen = set()
        for e in self.entries.values():
            if e.entry_id in seen:
                continue
            score = 0
            if e.title == kw:
                score = 100
            elif kw in e.title:
                score = 80
            elif any(kw in a for a in e.aliases):
                score = 70
            elif kw in e.full_text:
                score = 40
            else:
                # 中文空格分词粗匹配：全部字符都出现时给低分。
                terms = [t for t in re.split(r"[\s,，、]+", kw) if t]
                if terms and all(t in e.title + e.full_text for t in terms):
                    score = 20
            if score > 0:
                seen.add(e.entry_id)
                results.append((score, e))
        results.sort(key=lambda x: (-x[0], x[1].title))
        return [e for _score, e in results[:limit]]

    def expand(self, entry_id: str, limit: int = 20) -> List[Tuple[WikiEntry, WikiLink]]:
        """返回某词条链接到的其它词条；目标不在本地索引中的也会列出。"""
        entry = self.get(entry_id)
        if entry is None:
            return []
        out: List[Tuple[WikiEntry, WikiLink]] = []
        for link in entry.links[:limit]:
            target = self.get(link.target_id)
            out.append((target, link))
        return out

    def backlinks(self, entry_id: str) -> List[Tuple[WikiEntry, WikiLink]]:
        """返回哪些本地词条链接到了 entry_id（反向引用）。

        用于从任务词条出发找到引用它的地图文本/圣遗物等相邻词条：
        数据里常见的是 map_text -> task 的显式链接，任务词条自己的出边
        往往只有任务互链和奖励道具，反向引用能补全“任务 ← 地图文本”这一跳。
        """
        out: List[Tuple[WikiEntry, WikiLink]] = []
        for e in self.entries.values():
            if e.entry_id == str(entry_id):
                continue
            for link in e.links:
                if link.target_id == str(entry_id):
                    out.append((e, link))
                    break
        return out

    def missing_targets(self, limit: int = 100) -> Dict[str, str]:
        """统计所有链接中、本地索引缺失的目标词条 ID。"""
        missing: Dict[str, str] = {}
        for e in self.entries.values():
            for link in e.links:
                if link.target_id not in self.entries and link.target_id not in missing:
                    missing[link.target_id] = link.target_name
                    if len(missing) >= limit:
                        return missing
        return missing

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "entries": [e.to_dict() for e in sorted(self.entries.values(), key=lambda x: x.entry_id)],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WikiEntryGraph":
        graph = cls()
        for item in data.get("entries") or []:
            entry = WikiEntry.from_dict(item)
            graph.add(entry)
        return graph

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path) -> "WikiEntryGraph":
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_dict(json.load(f))


# ====== 解析工具 ======


def _strip_html(text: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(text)
    except Exception:
        pass
    out = "".join(parser.parts)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def _iter_strings(obj: Any):
    """递归产出对象中的所有字符串。"""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _iter_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_strings(v)


def _data_to_text(data: Any) -> str:
    """组件 data 可能是 HTML 字符串，也可能是 JSON 字符串/嵌套结构。"""
    texts: List[str] = []

    def walk(obj: Any):
        if isinstance(obj, str):
            stripped = obj.strip()
            if not stripped:
                return
            try:
                parsed = json.loads(stripped)
            except Exception:
                parsed = None
            if isinstance(parsed, (dict, list)):
                walk(parsed)
            elif "<" in stripped and ">" in stripped:
                texts.append(_strip_html(stripped))
            else:
                texts.append(html.unescape(stripped))
        elif isinstance(obj, dict):
            # key: value 结构保留 key 作为弱标注，方便 Agent 知道这是哪个字段。
            for key, value in obj.items():
                if isinstance(value, (dict, list)):
                    walk(value)
                else:
                    walk(value)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(data)
    return "\n".join(t for t in texts if t).strip()


def _extract_full_text(page: Dict[str, Any]) -> str:
    """把 page.modules 重建为纯文本。"""
    chunks: List[str] = []
    for module in page.get("modules") or []:
        module_name = str(module.get("name") or "").strip()
        module_texts: List[str] = []
        for comp in module.get("components") or []:
            data = comp.get("data")
            if isinstance(data, str):
                text = _data_to_text(data)
                if text:
                    module_texts.append(text)
        body = "\n".join(module_texts).strip()
        if not body:
            continue
        if module_name:
            chunks.append(f"[{module_name}]\n{body}")
        else:
            chunks.append(body)
    return "\n\n".join(chunks).strip()


def _extract_links(page: Dict[str, Any]) -> List[WikiLink]:
    """从 page 的所有字符串里提取 data-entry-id / data-entry-name 链接。"""
    found: Dict[str, WikiLink] = {}
    names_by_id: Dict[str, set] = {}
    for text in _iter_strings(page):
        if not isinstance(text, str):
            continue
        # 组件 data 是双重 JSON 编码字符串，内部引号形如 \"，
        # 先把字面反斜杠引号还原成普通引号，再按 HTML 属性提取链接。
        normalized = text.replace('\\"', '"')
        for target_id, target_name in _LINK_RE_1.findall(normalized):
            found.setdefault(target_id, WikiLink(target_id, target_name))
            names_by_id.setdefault(target_id, set()).add(target_name)
        for target_name, target_id in _LINK_RE_2.findall(normalized):
            found.setdefault(target_id, WikiLink(target_id, target_name))
            names_by_id.setdefault(target_id, set()).add(target_name)
    # 剔除自链接
    own_id = str(page.get("id") or "")
    # 数据清洗：观测枢里存在同一个 data-entry-id 对应多个不同 data-entry-name 的
    # 脏链接（实测 509226 同时被标成石兽如幽灵般端坐/普洛克路斯忒斯的寝床/
    # 望向入梦的白冕等多个名字，而 entry_page 接口显示 509226 实际是「旅行者·冰」）。
    # 这种 ID 不是真实词条 ID，必须整条丢弃，否则图会指向错误词条。
    return [
        link for tid, link in sorted(found.items())
        if tid != own_id and len(names_by_id.get(tid, set())) == 1
    ]


def _parse_filters(item: Dict[str, Any]) -> List[str]:
    filters = item.get("filters") or {}
    raw = filters.get("filters_text", "")
    if not raw:
        return []
    try:
        arr = json.loads(raw)
    except Exception:
        return []
    return [str(x) for x in arr if isinstance(x, str)]


def _infer_type(filters: List[str], page: Dict[str, Any]) -> str:
    for f in filters:
        if f.startswith("任务类型/"):
            return "task"
        if f.startswith("地图/"):
            return "map_text"
        if f.startswith("图鉴类型/") or f.startswith("图鉴分类/"):
            return "collection"
    # 兜底：有任务概述/任务过程模块的按任务处理。
    names = {str(m.get("name") or "") for m in page.get("modules") or []}
    if "任务过程" in names or "任务概述" in names:
        return "task"
    if "地图说明" in names:
        return "map_text"
    return "unknown"


def _infer_region(filters: List[str]) -> str:
    for f in filters:
        if f.startswith("地图/"):
            return f.split("/", 1)[1]
        if f.startswith("任务区域/"):
            return f.split("/", 1)[1]
    return ""


def _infer_region_from_title(title: str) -> str:
    """地图文本标题常带【地区】后缀（如 蓝藻【奥古洛夫镇】），作为 region 兜底。"""
    m = re.search(r"【([^】]+)】\s*$", title or "")
    return m.group(1) if m else ""


def build_entry_from_raw(item: Dict[str, Any], default_type: str = "unknown") -> Optional[WikiEntry]:
    page = item.get("page")
    if not isinstance(page, dict) or not page.get("id"):
        return None
    filters = _parse_filters(item)
    entry_type = _infer_type(filters, page) if default_type == "unknown" else default_type
    aliases: List[str] = []
    alias = str(item.get("alias_name") or "").strip()
    if alias and alias not in ("None", ""):
        aliases.append(alias)
    title = str(page.get("name") or item.get("title") or "")
    region = _infer_region(filters) or _infer_region_from_title(title)
    return WikiEntry(
        entry_id=str(page.get("id")),
        title=title,
        entry_type=entry_type,
        full_text=_extract_full_text(page),
        aliases=aliases,
        region=region,
        filters=filters,
        links=_extract_links(page),
    )


def build_graph(
    task_raw_path: Path = TASK_RAW,
    map_text_raw_path: Path = MAP_TEXT_RAW,
    missing_raw_path: Path = MISSING_RAW,
    pilot_only: bool = True,
) -> WikiEntryGraph:
    """试点图构建（至冬灰眸四线），保持与前期验证一致。"""
    graph = WikiEntryGraph()

    sources = []
    if task_raw_path.exists():
        with open(task_raw_path, "r", encoding="utf-8") as f:
            sources.append(("task", json.load(f)))
    if map_text_raw_path.exists():
        with open(map_text_raw_path, "r", encoding="utf-8") as f:
            sources.append(("map_text", json.load(f)))
    if missing_raw_path.exists():
        with open(missing_raw_path, "r", encoding="utf-8") as f:
            # 单独补抓的词条没有默认类型，交给 _infer_type 从模块名推断。
            sources.append(("unknown", json.load(f)))

    for default_type, doc in sources:
        for item in doc.get("items") or []:
            entry = build_entry_from_raw(item, default_type=default_type)
            if entry is None:
                continue
            if pilot_only:
                if entry.entry_id in PILOT_TASK_IDS or entry.entry_id in PILOT_MISSING_IDS:
                    graph.add(entry)
                    continue
                if entry.entry_type == "map_text" and any(
                    region in (entry.region or entry.title) for region in PILOT_REGIONS
                ):
                    graph.add(entry)
                    continue
            else:
                graph.add(entry)

    return graph


def build_full_graph(wiki_raw_dir: Path = WIKI_RAW_DIR) -> WikiEntryGraph:
    """全量图构建：读取 content_data/wiki_raw/channel_*.json 全部频道词条。

    类型来自文件 meta 的 channel_id -> CHANNEL_TYPE_MAP；缺失的旧 raw 与补抓词条
    也一并合并，保证试点期间手工抓的词条不丢。
    """
    graph = WikiEntryGraph()
    docs: List[Tuple[Optional[str], dict]] = []

    # 1) 全量频道 raw（主数据源）。
    if wiki_raw_dir.exists():
        for path in sorted(wiki_raw_dir.glob("channel_*.json")):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    doc = json.load(f)
            except Exception:
                continue
            channel_id = int(doc.get("channel_id") or 0)
            entry_type = doc.get("graph_type") or CHANNEL_TYPE_MAP.get(channel_id) or "unknown"
            docs.append((entry_type, doc))

    # 2) 旧 raw 与手工补抓词条（graph.add 按 ID 覆盖，重复无副作用）。
    for default_type, path in (("task", TASK_RAW), ("map_text", MAP_TEXT_RAW), ("unknown", MISSING_RAW)):
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                docs.append((default_type, json.load(f)))

    for default_type, doc in docs:
        for item in doc.get("items") or []:
            entry = build_entry_from_raw(item, default_type=default_type or "unknown")
            if entry is None:
                continue
            graph.add(entry)

    return graph


def print_stats(graph: WikiEntryGraph) -> None:
    types: Dict[str, int] = {}
    link_total = 0
    for e in graph.entries.values():
        types[e.entry_type] = types.get(e.entry_type, 0) + 1
        link_total += len(e.links)
    print(f"entries={len(graph.entries)}")
    print(f"types={types}")
    print(f"links_total={link_total}")
    missing = graph.missing_targets()
    print(f"missing_targets={len(missing)}")
    for tid, name in sorted(missing.items(), key=lambda x: x[0])[:40]:
        print(f"  missing {tid} {name}")


def main() -> int:
    parser = argparse.ArgumentParser(description="本地 wiki 词条链接图构建/查询")
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--scope", default="full", choices=["pilot", "full"],
                        help="full=全量频道图（默认）；pilot=至冬灰眸四线试点图")
    parser.add_argument("--all", action="store_true", help="兼容旧参数：等价 --scope full")
    parser.add_argument("--out", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--get")
    parser.add_argument("--search")
    parser.add_argument("--expand")
    parser.add_argument("--stats", action="store_true")
    args = parser.parse_args()

    out = Path(args.out)
    if args.build:
        if args.scope == "full" or args.all:
            graph = build_full_graph()
            out = Path(args.out) if args.out != str(DEFAULT_OUTPUT) else DEFAULT_OUTPUT
        else:
            graph = build_graph(pilot_only=True)
            out = PILOT_OUTPUT
        graph.save(out)
        print(f"saved {out}")
        print_stats(graph)
        return 0

    if not out.exists():
        print(f"未找到索引文件: {out}。请先运行 --build")
        return 1
    graph = WikiEntryGraph.load(out)

    if args.get:
        e = graph.get(args.get)
        if e is None:
            print("not found")
            return 1
        print(json.dumps(e.to_dict(), ensure_ascii=False, indent=2))
        return 0
    if args.search:
        for e in graph.search(args.search):
            print(f"{e.entry_id} | {e.entry_type} | {e.title} | {len(e.full_text)}字 | links={len(e.links)}")
        return 0
    if args.expand:
        rows = graph.expand(args.expand)
        if not rows:
            print("not found or no links")
            return 1
        for entry, link in rows:
            target = entry.title if entry else "(索引缺失)"
            print(f"{link.target_id} | {link.target_name} | target={target}")
        return 0
    if args.stats:
        print_stats(graph)
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
