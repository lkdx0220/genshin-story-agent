# -*- coding: utf-8 -*-
"""本地 wiki 词条链接图（轻量 GraphRAG 数据层，试点版）。

设计目标：
- 不靠 LLM 抽三元组，直接复用米游社观测枢词条内部自带的 data-entry-id 超链接；
- 每个词条保存：全文（展示用）、剧情文本 story_text（链接/提及索引用）、类型、地区、别名、它链接到的其它词条；
- Agent 可以像 WorkBuddy 那样顺着链接做多跳检索。

本模块只处理数据结构、构建与查询，不负责网络抓取。
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import time
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from wiki_graph_channels import CHANNEL_TYPE_MAP
from character_aliases import CHARACTER_ALIASES

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT = BASE_DIR / "kb_vectors" / "wiki_entry_graph.json"
PILOT_OUTPUT = BASE_DIR / "kb_vectors" / "wiki_entry_graph_pilot.json"

TASK_RAW = BASE_DIR / "content_data" / "mihoyo_tasks_raw.json"
MAP_TEXT_RAW = BASE_DIR / "content_data" / "mihoyo_map_text_raw_full.json"
MISSING_RAW = BASE_DIR / "content_data" / "wiki_missing_entries_raw.json"
WIKI_RAW_DIR = BASE_DIR / "content_data" / "wiki_raw"

SCHEMA_VERSION = 4

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
    link_type: str = ""


@dataclass
class WikiEntry:
    entry_id: str
    title: str
    entry_type: str
    full_text: str
    story_text: str = ""
    aliases: List[str] = field(default_factory=list)
    region: str = ""
    filters: List[str] = field(default_factory=list)
    links: List[WikiLink] = field(default_factory=list)
    # v2 元数据：来源、时间、指纹、状态
    source_channel: int = 0
    fetched_at: str = ""
    content_hash: str = ""
    status: str = "ok"
    updated_at: str = ""
    # v4：来源标记。mihoyo=米游社观测枢；bwiki=B站 wiki 等补充源
    source: str = "mihoyo"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "title": self.title,
            "entry_type": self.entry_type,
            "full_text": self.full_text,
            "story_text": self.story_text,
            "aliases": self.aliases,
            "region": self.region,
            "filters": self.filters,
            "links": [
                {
                    "target_id": l.target_id,
                    "target_name": l.target_name,
                    "context": l.context,
                    "link_type": l.link_type,
                }
                for l in self.links
            ],
            "source_channel": self.source_channel,
            "fetched_at": self.fetched_at,
            "content_hash": self.content_hash,
            "status": self.status,
            "updated_at": self.updated_at,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WikiEntry":
        return cls(
            entry_id=str(data.get("entry_id", "")),
            title=str(data.get("title", "")),
            entry_type=str(data.get("entry_type", "")),
            full_text=str(data.get("full_text", "")),
            story_text=str(data.get("story_text", "")),
            aliases=list(data.get("aliases") or []),
            region=str(data.get("region", "")),
            filters=list(data.get("filters") or []),
            links=[
                WikiLink(
                    target_id=str(l.get("target_id", "")),
                    target_name=str(l.get("target_name", "")),
                    context=str(l.get("context", "")),
                    link_type=str(l.get("link_type", "")),
                )
                for l in data.get("links") or []
            ],
            source_channel=int(data.get("source_channel", 0) or 0),
            fetched_at=str(data.get("fetched_at", "")),
            content_hash=str(data.get("content_hash", "")),
            status=str(data.get("status", "ok")),
            updated_at=str(data.get("updated_at", "")),
            source=str(data.get("source", "mihoyo") or "mihoyo"),
        )


@dataclass
class WikiEntryGraph:
    entries: Dict[str, WikiEntry] = field(default_factory=dict)
    meta: Dict[str, Any] = field(default_factory=dict)
    # 反向链接内存索引：target_id -> [(来源词条, 该来源的第一条指向链接), ...]。
    # 延迟构建，add() 时置脏；图规模只有一万多词条，构建一次即可复用。
    _backlinks: Dict[str, List[Tuple[WikiEntry, WikiLink]]] = field(
        default_factory=dict, init=False, repr=False
    )
    _backlinks_dirty: bool = field(default=True, init=False, repr=False)

    def add(self, entry: WikiEntry) -> None:
        if entry.entry_id:
            self.entries[entry.entry_id] = entry
            self._backlinks_dirty = True

    def get(self, entry_id: str) -> Optional[WikiEntry]:
        return self.entries.get(str(entry_id))

    def search(self, keyword: str, limit: int = 20) -> List[WikiEntry]:
        """本地词条搜索：标题精确优先，其次标题/别名/剧情文本子串，再退化为字符交集。

        只搜索 story_text，不搜索 full_text：full_text 仍包含玩法推荐/装备展示等模块，
        如果参与搜索会把“推荐角色”当成剧情相关词条返回。
        """
        kw = (keyword or "").strip()
        if not kw:
            return []
        results: List[Tuple[int, WikiEntry]] = []
        seen = set()
        for e in self.entries.values():
            if e.entry_id in seen:
                continue
            score = 0
            story = e.story_text or ""
            if e.title == kw:
                score = 100
            elif kw in e.title:
                score = 80
            elif any(kw in a for a in e.aliases):
                score = 70
            elif kw in story:
                score = 40
            else:
                # 中文空格分词粗匹配：全部字符都出现时给低分。
                terms = [t for t in re.split(r"[\s,，、]+", kw) if t]
                if terms and all(t in e.title + story for t in terms):
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

    def _ensure_backlinks(self) -> None:
        """构建反向链接索引，语义与旧的全图扫描完全一致。

        规则：
        - 跳过自链接（来源词条 == 目标词条）；
        - 同一个来源词条指向同一目标多次时，只保留第一条链接；
        - 来源顺序沿用 entries 的插入顺序。
        """
        if not self._backlinks_dirty:
            return
        index: Dict[str, List[Tuple[WikiEntry, WikiLink]]] = {}
        for entry in self.entries.values():
            seen_targets = set()
            for link in entry.links:
                if entry.entry_id == link.target_id:
                    continue
                if link.target_id in seen_targets:
                    continue
                seen_targets.add(link.target_id)
                index.setdefault(link.target_id, []).append((entry, link))
        self._backlinks = index
        self._backlinks_dirty = False

    def backlinks(self, entry_id: str) -> List[Tuple[WikiEntry, WikiLink]]:
        """返回哪些本地词条链接到了 entry_id（反向引用）。

        用于从任务词条出发找到引用它的地图文本/圣遗物等相邻词条：
        数据里常见的是 map_text -> task 的显式链接，任务词条自己的出边
        往往只有任务互链和奖励道具，反向引用能补全“任务 ← 地图文本”这一跳。
        """
        self._ensure_backlinks()
        return list(self._backlinks.get(str(entry_id), []))

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
            "meta": dict(self.meta or {}),
            "entries": [e.to_dict() for e in sorted(self.entries.values(), key=lambda x: x.entry_id)],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WikiEntryGraph":
        graph = cls(meta=dict(data.get("meta") or {}))
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


_NOISE_TOKENS = {"None", "null", "left", "right", "center", "top", "bottom", "mid", "true", "false"}
_URL_LINE_RE = re.compile(r"^https?://\S+$")
_DIALOG_ID_LINE_RE = re.compile(r"^[A-Za-z0-9]{8,}T\d+$")


def _clean_plain_text(text: str) -> str:
    """清洗已从 HTML 提取的纯文本：去 URL/接口 ID/布局词，合并重复连续行。"""
    if not text:
        return ""
    text = re.sub(r"\n{3,}", "\n\n", text)
    lines: List[str] = []
    prev = None
    for line in text.splitlines():
        line = re.sub(r"https?://\S+", "", line).strip()
        if not line:
            continue
        if _URL_LINE_RE.match(line):
            continue
        if _DIALOG_ID_LINE_RE.match(line):
            continue
        if line in _NOISE_TOKENS:
            continue
        if line == prev:
            continue
        lines.append(line)
        prev = line
    return "\n".join(lines)


def _data_to_text(data: Any) -> str:
    """组件 data 可能是 HTML 字符串，也可能是 JSON 字符串/嵌套结构。"""
    texts: List[str] = []

    def walk(obj: Any):
        if isinstance(obj, str):
            stripped = obj.strip()
            if not stripped:
                return
            if _URL_LINE_RE.match(stripped):
                return
            if _DIALOG_ID_LINE_RE.match(stripped):
                return
            if stripped in _NOISE_TOKENS:
                return
            try:
                parsed = json.loads(stripped)
            except Exception:
                parsed = None
            if isinstance(parsed, (dict, list)):
                walk(parsed)
            elif "<" in stripped and ">" in stripped:
                texts.append(_clean_plain_text(_strip_html(stripped)))
            else:
                texts.append(_clean_plain_text(html.unescape(stripped)))
        elif isinstance(obj, dict):
            for key, value in obj.items():
                walk(value)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(data)
    return "\n".join(t for t in texts if t).strip()


def _extract_dialogue_text(data: Any) -> str:
    """对话类模块专用解析：按 root_id/child_ids 顺序遍历 contents，
    只保留 option 和 dialogue，不把接口 ID 当作正文。"""
    if isinstance(data, str):
        try:
            obj = json.loads(data)
        except Exception:
            return _data_to_text(data)
    else:
        obj = data
    if not isinstance(obj, dict):
        return _data_to_text(data)

    out: List[str] = []
    seen: set = set()

    def emit(node: Any):
        if not isinstance(node, dict):
            return
        option = str(node.get("option") or "").strip()
        dialogue = node.get("dialogue")
        if option and option not in _NOISE_TOKENS:
            option_text = _strip_html(option) if "<" in option else option
            out.append("【选项】" + _clean_plain_text(option_text))
        if isinstance(dialogue, str) and dialogue.strip():
            dialogue_text = _strip_html(dialogue) if "<" in dialogue else dialogue
            dialogue_text = _clean_plain_text(dialogue_text)
            if dialogue_text:
                out.append(dialogue_text)

    def walk_tree(tree: Dict[str, Any]):
        contents = tree.get("contents") or obj.get("contents") or {}
        child_ids = tree.get("child_ids") or {}
        if not isinstance(contents, dict) or not contents:
            return
        root = str(tree.get("root_id") or "")

        def visit(node_id: str):
            if not node_id or node_id in seen:
                return
            seen.add(node_id)
            node = contents.get(node_id)
            if isinstance(node, dict):
                emit(node)
            for child in child_ids.get(node_id) or []:
                visit(str(child))

        if root:
            visit(root)
        else:
            for node in contents.values():
                if isinstance(node, dict):
                    emit(node)

    trees = obj.get("list") or []
    if isinstance(trees, list) and trees:
        for tree in trees:
            if isinstance(tree, dict):
                walk_tree(tree)
    else:
        contents = obj.get("contents") or {}
        if isinstance(contents, dict):
            for node in contents.values():
                if isinstance(node, dict):
                    emit(node)
    return "\n".join(out).strip()


# ====== 剧情文本提取规则 ======
# story_text 只保留叙事类模块；玩法/数值/推荐类模块必须排除，避免玩法推荐边污染剧情图。
# 规则顺序：先黑名单，再白名单；部分叙事型词条允许未命中黑白名单的模块名（如书籍卷名、地图告示标题）。
_STORY_MODULE_BLACKLIST = (
    "推荐", "装备描述", "装备展示", "成长", "数值", "属性", "基础", "突破",
    "天赋", "命之座", "配队", "强化", "获取", "材料", "商品", "商店", "数据",
    "图鉴", "攻略", "玩法", "奖励", "纪行", "位置", "地点", "分布", "关卡",
    "挑战", "成就", "地图", "图片", "展示", "配音", "CV", "名片", "料理",
    "食材", "食物", "合成", "锻造", "价格", "出售", "兑换", "来源", "获得",
    "使用", "效果", "消耗", "冷却", "时间轴", "宣发", "媒体", "语音", "词条",
    "导航", "战斗单位", "补充说明", "文字说明", "图片说明", "详细说明",
    "关卡说明", "挑战说明", "纪行说明", "秘境信息", "秘境位置", "更多信息",
    "详细信息", "活动说明", "游戏内活动说明", "玩法说明", "活动攻略",
    "玩家攻略", "活动商店", "活动公告", "活动详情", "活动奖励", "活动玩法",
    "活动流程", "任务奖励", "任务条件", "任务流程", "任务目标", "任务与奖励",
    "委托奖励", "挑战目标", "挑战阵容", "挑战特殊效果", "地图说明", "地图展示",
    "地图位置", "地图点位", "角色展示", "NPC展示", "衣装展示", "实机展示",
    "动作展示", "立绘展示", "洞天预览", "外观", "图标", "海报", "入口",
)
_STORY_MODULE_WHITELIST = (
    "相关故事", "背景故事", "角色故事", "衣装故事", "故事",
    "剧情对话", "剧情彩蛋", "剧情说明", "剧情",
    "任务概述", "任务过程", "任务对话", "任务剧情",
    "NPC对话", "对话", "交互文本",
    "物品描述", "更多描述", "角色详细",
    "神之眼", "月之轮", "神之心", "星之楔",
    "角色关系网",
    "简介", "重要事件", "成员", "部族成员", "主要人物", "逸闻", "趣闻",
    "重大事迹", "逸闻与事迹",
    "生之花", "死之羽", "时之沙", "空之杯", "理之冠",
    "阅读", "书籍内容", "从石碑上抄下来的文字", "鼓谱",
    "旅行者的笔记", "解读",
    "逸闻纪事", "彩蛋", "生日", "角色洞天对话", "角色赠礼",
    "好感套装对话", "赠礼对话", "特殊对话", "纪念留影",
    "相关任务", "相关角色及任务",
    "公告板内容", "告示板内容", "留言板内容", "告示", "留言", "通知",
    "日志", "手记", "笔记", "秘闻",
    "月下纪闻", "聚所纪事", "世界任务", "纪闻",
    "行迹", "见闻", "影域",
    "秘境详情", "简述", "臻冰造物", "活动简述",
)
# 这些类型的词条以叙事为主：未命中黑白名单的模块名也纳入 story_text。
# book 需要卷名/自定义书名模块，map_text 需要告示/广告板自定义标题，
# organization 需要各分会/部门小节；其余类型只认白名单，避免把玩法模块带进来。
_UNKNOWN_MODULE_STORY_TYPES = {"book", "map_text", "organization"}


def _is_story_module(module_name: str, entry_type: str) -> bool:
    """判断一个模块是否属于剧情类；玩法模块优先排除。"""
    name = (module_name or "").strip()
    if not name:
        return False
    if any(bad in name for bad in _STORY_MODULE_BLACKLIST):
        return False
    if any(good in name for good in _STORY_MODULE_WHITELIST):
        return True
    return entry_type in _UNKNOWN_MODULE_STORY_TYPES


def _iter_story_modules(page: Dict[str, Any], entry_type: str):
    """按顺序产出剧情模块的 (模块名, 模块对象)。"""
    for module in page.get("modules") or []:
        module_name = str(module.get("name") or "").strip()
        if _is_story_module(module_name, entry_type):
            yield module_name, module


def _iter_story_strings(page: Dict[str, Any], entry_type: str):
    """递归产出剧情模块组件里的所有字符串，供链接提取使用。"""
    for _module_name, module in _iter_story_modules(page, entry_type):
        yield from _iter_strings(module.get("components") or [])


def _extract_story_text(page: Dict[str, Any], entry_type: str) -> str:
    """把 page.modules 中剧情类模块重建为纯文本，供实体提及索引和链接提取使用。"""
    chunks: List[str] = []
    for module_name, module in _iter_story_modules(page, entry_type):
        module_texts: List[str] = []
        for comp in module.get("components") or []:
            data = comp.get("data")
            if "对话" in module_name:
                text = _extract_dialogue_text(data)
            else:
                text = _data_to_text(data)
            if text:
                module_texts.append(text)
        body = "\n".join(module_texts).strip()
        if body:
            chunks.append(f"[{module_name}]\n{body}")
    return "\n\n".join(chunks).strip()


def _extract_full_text(page: Dict[str, Any]) -> str:
    """把 page.modules 重建为纯文本。对话模块走有序遍历，其余模块走通用清洗。"""
    chunks: List[str] = []
    for module in page.get("modules") or []:
        module_name = str(module.get("name") or "").strip()
        module_texts: List[str] = []
        for comp in module.get("components") or []:
            data = comp.get("data")
            if "对话" in module_name:
                text = _extract_dialogue_text(data)
            else:
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


def _link_context(normalized: str, match_start: int, match_end: int, width: int = 140) -> str:
    """取链接锚点的纯文本上下文，帮助 expand 展示“为什么链接到这里”。"""
    # 优先取整段 <a>...</a> 的锚文本；窗口从半截标签开始会得到大量 HTML 碎片。
    a_start = normalized.rfind("<a", 0, match_start)
    if a_start >= 0:
        a_end = normalized.find("</a>", match_end)
        if a_end >= 0:
            anchor_text = _strip_html(normalized[a_start:a_end + 4])
            anchor_text = re.sub(r"\s+", " ", anchor_text).strip()
            if anchor_text:
                return anchor_text[:width]
    window = normalized[max(0, match_start - width):match_end + width]
    text = _strip_html(window)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:width]


def _extract_links(page: Dict[str, Any], entry_type: str) -> List[WikiLink]:
    """只从剧情模块组件里提取 data-entry-id / data-entry-name 链接，跳过玩法模块。"""
    found: Dict[str, WikiLink] = {}
    names_by_id: Dict[str, set] = {}
    for text in _iter_story_strings(page, entry_type):
        if not isinstance(text, str):
            continue
        # 组件 data 是双重 JSON 编码字符串，内部引号形如 \"，
        # 先把字面反斜杠引号还原成普通引号，再按 HTML 属性提取链接。
        normalized = text.replace('\\"', '"')
        for match in _LINK_RE_1.finditer(normalized):
            target_id, target_name = match.group(1), match.group(2)
            found.setdefault(
                target_id,
                WikiLink(target_id, target_name, _link_context(normalized, match.start(), match.end())),
            )
            names_by_id.setdefault(target_id, set()).add(target_name)
        for match in _LINK_RE_2.finditer(normalized):
            target_name, target_id = match.group(1), match.group(2)
            found.setdefault(
                target_id,
                WikiLink(target_id, target_name, _link_context(normalized, match.start(), match.end())),
            )
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
        # 频道 filter 里最常见的地区前缀：角色/NPC/组织/地图文本/食物等用“地区/”。
        if f.startswith("地区/"):
            return f.split("/", 1)[1]
        if f.startswith("地图/"):
            return f.split("/", 1)[1]
        if f.startswith("任务区域/"):
            return f.split("/", 1)[1]
    return ""


def _infer_region_from_title(title: str) -> str:
    """地图文本标题常带【地区】后缀（如 蓝藻【奥古洛夫镇】），作为 region 兜底。"""
    m = re.search(r"【([^】]+)】\s*$", title or "")
    return m.group(1) if m else ""


def _derive_title_aliases(title: str, region: str) -> List[str]:
    """从标题本身派生低风险别名：地区前缀、【地区】/括号后缀去掉后的简名。"""
    out: List[str] = []

    def add(candidate: str):
        candidate = candidate.strip().strip("「」").strip()
        if candidate and candidate != title and candidate not in out:
            out.append(candidate)

    title = (title or "").strip()
    if not title:
        return out
    if region and title.startswith(region + " "):
        add(title[len(region) + 1:])
    m = re.match(r"^(.*?)(?:【[^】]*】|（[^（）]*）|\([^()]*\))\s*$", title)
    if m:
        add(m.group(1))
    return out


def build_entry_from_raw(
    item: Dict[str, Any],
    default_type: str = "unknown",
    source_channel: int = 0,
    fetched_at: str = "",
) -> Optional[WikiEntry]:
    page = item.get("page")
    if not isinstance(page, dict) or not page.get("id"):
        return None
    filters = _parse_filters(item)
    content_hash = hashlib.sha1(
        json.dumps(page, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    entry_type = _infer_type(filters, page) if default_type == "unknown" else default_type
    aliases: List[str] = []
    for alias_source in (item.get("alias_name"), page.get("alias_name")):
        alias = str(alias_source or "").strip()
        if alias and alias not in ("None", ""):
            aliases.append(alias)
    # page.alias_name 可能包含多个名称（逗号/顿号分隔），拆分入库。
    expanded_aliases: List[str] = []
    for alias in aliases:
        for part in re.split(r"[,，、]", alias):
            part = part.strip()
            if part and part not in expanded_aliases:
                expanded_aliases.append(part)
    title = str(page.get("name") or item.get("title") or "")
    region = _infer_region(filters) or _infer_region_from_title(title)

    # 标题派生别名：任务去掉地区前缀、地图文本/NPC 去掉【地区】后缀等。
    for alias in _derive_title_aliases(title, region):
        if alias not in expanded_aliases:
            expanded_aliases.append(alias)

    # 观测枢 entry_page 的 alias_name 基本都是空串，角色别名从项目自身维护的
    # CHARACTER_ALIASES 反查补齐（规范名 -> 别名列表）。
    if entry_type == "character" and title in CHARACTER_ALIASES:
        for alias in CHARACTER_ALIASES[title]:
            alias_clean = alias.strip("「」").strip()
            if alias_clean and alias_clean != title and alias_clean not in expanded_aliases:
                expanded_aliases.append(alias_clean)

    return WikiEntry(
        entry_id=str(page.get("id")),
        title=title,
        entry_type=entry_type,
        full_text=_extract_full_text(page),
        story_text=_extract_story_text(page, entry_type),
        aliases=expanded_aliases,
        region=region,
        filters=filters,
        links=_extract_links(page, entry_type),
        source_channel=source_channel,
        fetched_at=fetched_at,
        content_hash=content_hash,
        status="ok",
        updated_at=time.strftime("%Y-%m-%d %H:%M:%S"),
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


def _norm_meta_key(text: str) -> str:
    """元数据匹配用的归一化：去空白、书名号、引号、标点。"""
    s = re.sub(r"<[^>]+>", "", str(text or ""))
    s = re.sub(r"[\s\u3000「」『』《》【】（）()\[\]{}:：,，。.、;；\-—·!！?？\"'“”‘’]", "", s)
    return s


_BWIKI_ALLOWED_TYPES = (
    "世界任务", "传说任务", "魔神任务", "活动剧情", "部族纪闻", "委托任务",
    "地图事件", "其他任务", "隐藏任务", "伴月纪闻", "游逸旅闻", "彩蛋剧情",
)
_BWIKI_EXCLUDE_TITLE_KEYWORDS = ("额外对话/彩蛋", "教程", "活动说明", "NPC对话")
_ACT_NAME_RE = re.compile(r"第[一二三四五六七八九十0-9]+(?:幕|回|章)|尾声|序奏|序章|间章|幕间")


def _parse_title_meta(title: str):
    """从标题拆出 (chapter, act, task)；没幕级标记时只返回 (title, '', '')。"""
    t = str(title or "").strip()
    m = _ACT_NAME_RE.search(t)
    if not m:
        return _norm_meta_key(t), "", ""
    act = m.group(0)
    chapter = t[: m.start()].strip(" 　-—_·「」『』《》")
    task = t[m.end():].strip(" 　-—_·「」『』《》")
    return _norm_meta_key(chapter), _norm_meta_key(act), _norm_meta_key(task or t)


def _load_bwiki_entries() -> List[Dict[str, Any]]:
    """读取全部 content_data/quests_*.json，整理为可合并的 B 站任务条目。

    过滤规则：只保留任务/剧情类条目；丢弃额外对话、教程、活动说明等非剧情页。
    优先使用原始 text；原始 text 为空时用 quests_processed.json 的 chunks 兜底。
    """
    content_dir = BASE_DIR / "content_data"
    processed: Dict[str, Any] = {}
    processed_path = content_dir / "quests_processed.json"
    if processed_path.exists():
        try:
            processed = json.loads(processed_path.read_text(encoding="utf-8"))
        except Exception:
            processed = {}

    def _processed_text(item: Dict[str, Any]) -> str:
        parts: List[str] = []
        summary = item.get("summary")
        if isinstance(summary, str) and summary.strip():
            parts.append(summary.strip())
        for key in ("chunks_bm25", "chunks_vec"):
            value = item.get(key)
            if isinstance(value, list):
                parts.extend(str(x) for x in value if isinstance(x, str))
        return "\n".join(parts).strip()

    processed_text = {}
    for title, item in processed.items():
        if not isinstance(item, dict):
            continue
        text = _processed_text(item)
        if text:
            processed_text[_norm_meta_key(title)] = text

    entries: Dict[tuple, Dict[str, Any]] = {}
    for path in sorted(content_dir.glob("quests_*.json")):
        if path.name == "quests_processed.json":
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        items = data if isinstance(data, list) else (data.get("items") or list(data.values()))
        for item in items:
            if not isinstance(item, dict):
                continue
            md = item.get("metadata") or {}
            title = str(item.get("title") or md.get("任务名称") or "").strip()
            task = str(md.get("任务名称") or title).strip()
            entry_type = str(md.get("任务类型") or item.get("category") or "").strip()
            if not title or not task:
                continue
            if any(k in title for k in _BWIKI_EXCLUDE_TITLE_KEYWORDS):
                continue
            if any(k in entry_type for k in ("额外", "教程", "说明")):
                continue
            if entry_type and not any(t in entry_type for t in _BWIKI_ALLOWED_TYPES):
                continue
            chapter = str(md.get("chapter_name") or md.get("系列任务") or "").split(",")[0].strip()
            act = str(md.get("act_name") or "").strip()
            text = str(item.get("text") or "").strip()
            if not text:
                text = processed_text.get(_norm_meta_key(title), "")
            if not text:
                continue
            key = (_norm_meta_key(chapter), _norm_meta_key(act), _norm_meta_key(task))
            row = {
                "title": title,
                "task": task,
                "entry_type": entry_type,
                "chapter": chapter,
                "act": act,
                "region": str(md.get("任务区域") or md.get("地区") or "").strip(),
                "text": text,
                "file": path.name,
            }
            old = entries.get(key)
            if old is None or len(text) > len(old["text"]):
                entries[key] = row

    # quests_processed.json 中 raw 文件没有覆盖到的任务，用 chunks 补建。
    for title, item in processed.items():
        if not isinstance(item, dict):
            continue
        entry_type = str(item.get("category") or "").strip()
        if any(k in entry_type for k in ("额外", "教程", "说明")):
            continue
        if entry_type and not any(t in entry_type for t in _BWIKI_ALLOWED_TYPES):
            continue
        text = _processed_text(item)
        if not text:
            continue
        task = str(item.get("title") or title).strip()
        key = ("", "", _norm_meta_key(task))
        if key in entries:
            continue
        entries[key] = {
            "title": task,
            "task": task,
            "entry_type": entry_type,
            "chapter": "",
            "act": "",
            "region": "",
            "text": text,
            "file": "quests_processed.json",
        }
    # 同一任务若已有带 chapter/act 的条目，丢弃无元数据的重复条目，避免建出重复节点。
    metadata_tasks = {
        _norm_meta_key(row["task"])
        for row in entries.values()
        if row["chapter"] or row["act"]
    }
    if metadata_tasks:
        for key in list(entries.keys()):
            row = entries[key]
            if (not row["chapter"] and not row["act"]) and _norm_meta_key(row["task"]) in metadata_tasks:
                del entries[key]
    return list(entries.values())


def _build_graph_key_index(graph: WikiEntryGraph) -> Dict[tuple, List[WikiEntry]]:
    index: Dict[tuple, List[WikiEntry]] = {}
    for entry in graph.entries.values():
        if entry.entry_type not in ("task", "activity"):
            continue
        chapter, act, task = _parse_title_meta(entry.title)
        keys = [
            (chapter, act, task),
            (chapter, "", task),
            ("", "", task),
            ("", "", _norm_meta_key(entry.title)),
        ]
        for key in keys:
            if not key[2]:
                continue
            index.setdefault(key, []).append(entry)
    return index


def merge_bwiki_entries(graph: WikiEntryGraph) -> Dict[str, int]:
    """把 B 站 wiki 语料合并进图。

    规则：
    - 元数据 (chapter+act+task) 精确匹配且只命中一个图节点：B 站文本更长则替换主文本；
    - 其他多命中/弱命中：不覆盖图节点，新建 B 站节点并写别名；
    - 图里没有的：按 task 新建节点，source=bwiki。
    """
    entries = _load_bwiki_entries()
    index = _build_graph_key_index(graph)
    stats = {
        "loaded": len(entries),
        "merged": 0,
        "replaced": 0,
        "created": 0,
        "skipped": 0,
    }
    for row in entries:
        text = row["text"]
        chapter, act, task = row["chapter"], row["act"], row["task"]
        keys = [
            (_norm_meta_key(chapter), _norm_meta_key(act), _norm_meta_key(task)),
            (_norm_meta_key(chapter), "", _norm_meta_key(task)),
            ("", "", _norm_meta_key(task)),
            ("", "", _norm_meta_key(row["title"])),
        ]
        matched: List[WikiEntry] = []
        used_key = None
        for key in keys:
            if key[2] and key in index:
                matched = index[key]
                used_key = key
                break
        if matched:
            # 单节点命中即可合并：任务名一致时，B 站文本更长就替换主文本；多节点命中则新建节点，避免误伤。
            if len(matched) == 1:
                entry = matched[0]
                old_text = entry.story_text or entry.full_text or ""
                if len(text) > len(old_text):
                    entry.story_text = text
                    entry.full_text = text
                    entry.source = "bwiki"
                    stats["replaced"] += 1
                for alias in (chapter, act, task, row["title"]):
                    alias = str(alias or "").strip()
                    if alias and alias not in entry.aliases:
                        entry.aliases.append(alias)
                stats["merged"] += 1
                continue
            # 一对多/弱匹配：保留图节点，落成新的 B 站节点。
        entry_id = "bwiki_" + hashlib.sha1(
            f"{chapter}|{act}|{task}|{row['title']}".encode("utf-8")
        ).hexdigest()[:12]
        if entry_id in graph.entries:
            stats["skipped"] += 1
            continue
        aliases = []
        for alias in (row["title"], task, f"{chapter} {act} {task}", f"{chapter} {task}", chapter, act):
            alias = str(alias or "").strip()
            if alias and alias not in aliases:
                aliases.append(alias)
        entry_type = "task"
        if not act and not chapter and row["title"].startswith("「") and row["title"].endswith("」"):
            entry_type = "activity"
        filters = [f"来源/bwiki"]
        if row["entry_type"]:
            filters.append(f"任务类型/{row['entry_type']}")
        graph.add(
            WikiEntry(
                entry_id=entry_id,
                title=row["title"] if entry_type == "activity" else task,
                entry_type=entry_type,
                full_text=text,
                story_text=text,
                aliases=aliases,
                region=row["region"],
                filters=filters,
                source_channel=-1,
                fetched_at=time.strftime("%Y-%m-%d %H:%M:%S"),
                content_hash=hashlib.sha1(text.encode("utf-8")).hexdigest()[:16],
                source="bwiki",
            )
        )
        stats["created"] += 1
    return stats


def build_full_graph(wiki_raw_dir: Path = WIKI_RAW_DIR) -> WikiEntryGraph:
    """全量图构建：读取 content_data/wiki_raw/channel_*.json 全部频道词条。

    类型来自文件 meta 的 channel_id -> CHANNEL_TYPE_MAP；缺失的旧 raw 与补抓词条
    也一并合并，保证试点期间手工抓的词条不丢。
    """
    graph = WikiEntryGraph()
    # (entry_type, doc, source_channel, fetched_at)
    docs: List[Tuple[Optional[str], dict, int, str]] = []
    sources_meta: List[Dict[str, Any]] = []

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
            fetched_at = str(doc.get("fetched_at") or "")
            item_count = len(doc.get("items") or [])
            docs.append((entry_type, doc, channel_id, fetched_at))
            sources_meta.append({
                "channel_id": channel_id,
                "graph_type": entry_type,
                "item_count": item_count,
                "fetched_at": fetched_at,
            })

    # 2) 旧 raw 与手工补抓词条（graph.add 按 ID 覆盖，重复无副作用）。
    for default_type, path, source_channel in (
        ("task", TASK_RAW, 0),
        ("map_text", MAP_TEXT_RAW, 0),
        ("unknown", MISSING_RAW, 0),
    ):
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                doc = json.load(f)
            docs.append((default_type, doc, source_channel, str(doc.get("fetched_at") or "")))

    for default_type, doc, source_channel, fetched_at in docs:
        for item in doc.get("items") or []:
            entry = build_entry_from_raw(
                item,
                default_type=default_type or "unknown",
                source_channel=source_channel,
                fetched_at=fetched_at,
            )
            if entry is None:
                continue
            graph.add(entry)

    bwiki_stats = merge_bwiki_entries(graph)
    graph.meta = {
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "entry_count": len(graph.entries),
        "link_count": sum(len(e.links) for e in graph.entries.values()),
        "sources": sources_meta,
        "bwiki": bwiki_stats,
        "dirty_links": [],
    }
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
