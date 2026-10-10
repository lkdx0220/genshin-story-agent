# -*- coding: utf-8 -*-
"""实体检索原语：把"谁参与过什么"变成集合运算（LLM Wiki 结构化层从 L3 下放到 L2 的第一步）。

数据源（只读，进程内缓存）：
- `kb_vectors/wiki_entity_mention_index.json`：实体 → 被哪些条目提及（含次数）——**关系来源**
- `kb_vectors/wiki_entry_graph.json`：词条元数据（entry_type / filters 任务类型 / story_text）

处理链：实体归一 → 文档级集合 → 双源去重（实体画像 Jaccard）→ 证据分级 → 覆盖度声明。

为什么不追求"图上的参与边"：词条图的 links 是 wiki 页面超链接（角色页往往为空），
真正可用的关系是**反向提及索引**；分级则回答"是出场还是被提到"。
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

# 项目根（app/ 的上一级）；不依赖 app.config（那里没有 BASE_DIR）
_BASE_DIR = Path(__file__).resolve().parents[1]

GRAPH_PATH = _BASE_DIR / "kb_vectors" / "wiki_entry_graph.json"
MENTION_PATH = _BASE_DIR / "kb_vectors" / "wiki_entity_mention_index.json"

# 角色类实体（其余类型不参与实体检索：任务/物品同名词条会造成误召回）
_CHARACTER_TYPES = {"character", "npc", "character_anecdote"}

_VARIANT_RE = re.compile(r"【[^】]*】")
_SPEAKER_RE = re.compile(r"(?:^|\n)\s*\*?\s*([\u4e00-\u9fff·]{2,10})\s*[：:]")
_ACTION_WORDS = ("说", "问", "笑", "道", "喊", "答", "看", "点头", "摇头", "皱眉", "递", "接",
                 "走", "跑", "站", "坐", "抬头", "低头", "沉默", "叹气", "解释", "提醒")

# 证据分级（回答"出场 vs 仅被提及"）
LEVEL_STRONG = "强·有台词"
LEVEL_MEDIUM = "中·有动作"
LEVEL_WEAK = "弱·仅文字提及"
LEVEL_ORDER = {LEVEL_STRONG: 3, LEVEL_MEDIUM: 2, LEVEL_WEAK: 1}


@dataclass
class EntityHit:
    """一条"实体出现在该任务"的命中记录。"""

    title: str
    task_type: str
    count: int
    level: str
    evidence: str
    variants: Tuple[str, ...] = ()

    @property
    def weight(self) -> int:
        return LEVEL_ORDER.get(self.level, 0)


@dataclass
class EntityDocsResult:
    """实体检索结果（含覆盖度声明，供回答"没参与"时使用）。"""

    query_kind: str  # "docs"（单实体）| "intersect"（双实体）
    display: str
    hits: List[EntityHit] = field(default_factory=list)
    total_rows: int = 0
    deduped_rows: int = 0
    scanned_tasks: int = 0
    others: List[str] = field(default_factory=list)  # intersect 时的另一个实体名

    @property
    def coverage_note(self) -> str:
        """覆盖度声明：所有否定结论都必须带上它。"""
        return (f"覆盖度：本次在已收录的 {self.scanned_tasks} 个任务条目中比对，"
                f"原始命中 {self.total_rows} 条、双源去重后 {len(self.hits)} 条。"
                f"未出现 ≠ 游戏内不存在，只表示当前知识库未收录相关剧情。")


@lru_cache(maxsize=1)
def _graph_and_index():
    graph = json.loads(GRAPH_PATH.read_text(encoding="utf-8"))
    mention = json.loads(MENTION_PATH.read_text(encoding="utf-8"))
    by_id = {str(e.get("entry_id")): e for e in graph.get("entries") or []}
    return by_id, mention


def _base_name(name: str) -> str:
    """实体归一：去掉【…】后缀与 · 之后修饰（如 行秋【逸闻】 → 行秋）。"""
    cleaned = _VARIANT_RE.sub("", str(name or "")).strip()
    return cleaned.split("·")[0].strip() if cleaned else cleaned


def _task_type_of(entry: dict) -> str:
    for f in entry.get("filters") or []:
        if f.startswith("任务类型/"):
            return f.split("/", 1)[1]
    return ""


def normalize_entity(name: str) -> Tuple[str, List[str]]:
    """返回 (基准名, [变体实体 id])；只认角色类实体。"""
    _, mention = _graph_and_index()
    bucket: Dict[str, List[str]] = defaultdict(list)
    for eid, item in (mention.get("entities") or {}).items():
        if str(item.get("type")) not in _CHARACTER_TYPES:
            continue
        bucket[_base_name(str(item.get("name") or ""))].append(eid)
    query = _base_name(name)
    if query in bucket:
        return query, bucket[query]
    loose = [(k, v) for k, v in bucket.items() if query and query in k]
    if loose:
        best = max(loose, key=lambda kv: len(kv[1]))
        return best[0], best[1]
    return query, []


def _entity_tasks(name: str) -> Tuple[str, Dict[str, dict]]:
    """实体 → {条目 id: {count, variants}}，只保留 task 类型条目。"""
    by_id, mention = _graph_and_index()
    base, eids = normalize_entity(name)
    tasks: Dict[str, dict] = {}
    for eid in eids:
        for m in (mention["entities"][eid] or {}).get("mentions") or []:
            sid = str(m.get("entry_id"))
            entry = by_id.get(sid)
            if not entry or entry.get("entry_type") != "task":
                continue
            slot = tasks.setdefault(sid, {"count": 0, "variants": set()})
            slot["count"] += int(m.get("count") or 0)
            slot["variants"].add(str((mention["entities"][eid] or {}).get("name") or ""))
    return base, tasks


def _speaker_hits(entry: dict, names: Sequence[str]) -> int:
    text = entry.get("story_text") or ""
    return sum(1 for m in _SPEAKER_RE.finditer(text) if m.group(1) in names)


def _action_hits(entry: dict, names: Sequence[str]) -> int:
    text = entry.get("story_text") or ""
    total = 0
    for name in names:
        for word in _ACTION_WORDS:
            total += len(re.findall(re.escape(name) + r"[^\n。！？]{0,6}?" + word, text))
    return total


def _snippet(entry: dict, names: Sequence[str], width: int = 110) -> str:
    """挑证据片段：**优先该角色的台词行**，没有台词再退回首次提及上下文。

    为什么要按这个顺序：'强·有台词' 的条目若只截首次提及，常截到"别人提到他"的那句
    （如"果然和钟离说的一样"），人工抽检和 LLM 都会误判为"只是被提到"。
    """
    text = entry.get("story_text") or entry.get("full_text") or ""
    for match in _SPEAKER_RE.finditer(text):
        if match.group(1) in names:
            start = match.start()
            return text[start:start + width].replace("\n", " ")
    for name in names:
        idx = text.find(name)
        if idx >= 0:
            return text[max(0, idx - 25): idx + width].replace("\n", " ")
    return text[:width].replace("\n", " ")


def grade_entry(entry: dict, names: Sequence[str]) -> str:
    """分级：有台词 > 有动作 > 仅文字提及。"""
    if _speaker_hits(entry, names) > 0:
        return LEVEL_STRONG
    if _action_hits(entry, names) > 0:
        return LEVEL_MEDIUM
    return LEVEL_WEAK


def _norm_title(title: str) -> str:
    return re.sub(r"[\s「」（）()·：:，,。！？?！\-—0-9]", "", str(title or ""))


@lru_cache(maxsize=1)
def _title_index() -> Dict[str, dict]:
    """标题 → 词条（用于取正文长度与实体画像，避免每次线性扫描全图）。"""
    by_id, _ = _graph_and_index()
    idx: Dict[str, dict] = {}
    for entry in by_id.values():
        title = str(entry.get("title") or "")
        if title:
            idx.setdefault(title, entry)
    return idx


@lru_cache(maxsize=1)
def _title_to_id() -> Dict[str, str]:
    by_id, _ = _graph_and_index()
    idx: Dict[str, str] = {}
    for sid, entry in by_id.items():
        title = str(entry.get("title") or "")
        if title:
            idx.setdefault(title, sid)
    return idx


def dedupe_variants(rows: List[EntityHit], mention: dict, jaccard: float = 0.7,
                    min_ratio: float = 0.6) -> List[EntityHit]:
    """双源去重（与已验证的探针口径一致）：标题同族 + **长度量级相当** + 实体画像 Jaccard 高。

    要点（踩过坑）：
    - 必须按正文长度降序比较，否则"短 ⊂ 长"的方向不成立，会把同系列的**不同幕**合并；
    - 必须有长度量级门槛（实测同一任务的两种转写长度相近，而不同幕差异大）；
    - 主信号用实体画像而非文本覆盖度：实测两源同一任务正文重合仅 19.7%，画像 Jaccard 0.87。
    """
    inverted = mention.get("inverted") or {}
    idx, t2i = _title_index(), _title_to_id()
    order = sorted(rows, key=lambda r: -len(str((idx.get(r.title) or {}).get("story_text") or "")))
    dropped: set = set()
    for i, short in enumerate(order):
        if short.title in dropped:
            continue
        s_norm = _norm_title(short.title)
        s_text = str((idx.get(short.title) or {}).get("story_text") or "")
        if len(s_norm) < 3 or not s_text:
            continue
        for long in order[:i]:
            if long.title in dropped:
                continue
            l_norm = _norm_title(long.title)
            l_text = str((idx.get(long.title) or {}).get("story_text") or "")
            if not (s_norm in l_norm or (len(l_norm) >= 3 and l_norm in s_norm)):
                continue
            longest = max(1, max(len(s_text), len(l_text)))
            if min(len(s_text), len(l_text)) / longest < min_ratio:
                continue  # 长度量级差太多 → 不是同一任务的两种转写
            a = set(map(str, inverted.get(t2i.get(short.title, "")) or []))
            b = set(map(str, inverted.get(t2i.get(long.title, "")) or []))
            if len(a & b) / max(1, len(a | b)) >= jaccard:
                dropped.add(short.title)
                break
    return [r for r in rows if r.title not in dropped]


def _collect_hits(name: str, task_type: Optional[str]) -> Tuple[str, List[EntityHit], int, int]:
    by_id, mention = _graph_and_index()
    base, tasks = _entity_tasks(name)
    rows: List[EntityHit] = []
    for sid, info in tasks.items():
        entry = by_id.get(sid) or {}
        ttype = _task_type_of(entry)
        if task_type and task_type not in ttype:
            continue
        names = [base] + [v for v in info["variants"] if v]
        rows.append(
            EntityHit(
                title=str(entry.get("title") or ""),
                task_type=ttype or "?",
                count=int(info["count"]),
                level=grade_entry(entry, names),
                evidence=_snippet(entry, names),
                variants=tuple(sorted(info["variants"])),
            )
        )
    total = len(rows)
    rows = dedupe_variants(rows, mention)
    rows.sort(key=lambda r: (-r.weight, -r.count))
    return base, rows, total, total - len(rows)


def entity_docs(entity: str, task_type: Optional[str] = None, min_level: Optional[str] = None,
                top_k: int = 8) -> EntityDocsResult:
    """单实体检索：该实体参与/被提及的剧情文档（按证据强度排序）。"""
    base, rows, total, deduped = _collect_hits(entity, task_type)
    if min_level:
        floor = LEVEL_ORDER.get(min_level, 0)
        rows = [r for r in rows if r.weight >= floor]
    scanned = len(_entity_tasks(entity)[1])
    return EntityDocsResult("docs", base, rows[:top_k], total, deduped, scanned)


def entity_intersect(entity_a: str, entity_b: str, task_type: Optional[str] = None,
                     top_k: int = 8) -> EntityDocsResult:
    """双实体求交：两人共同出现（或被同时提及）的任务。"""
    base_a, rows_a, total_a, _ = _collect_hits(entity_a, task_type)
    base_b, rows_b, total_b, _ = _collect_hits(entity_b, task_type)
    key = lambda r: r.title
    map_a, map_b = {key(r): r for r in rows_a}, {key(r): r for r in rows_b}
    common = [map_a[k] for k in map_a.keys() & map_b.keys()]
    # 交集用"两侧证据都更强"的那条，证据片段取 A 侧
    merged = []
    for hit in common:
        other = map_b[hit.title]
        stronger = hit if hit.weight >= other.weight else EntityHit(
            title=hit.title, task_type=hit.task_type, count=max(hit.count, other.count),
            level=other.level, evidence=hit.evidence, variants=hit.variants,
        )
        merged.append(stronger)
    merged.sort(key=lambda r: (-r.weight, -(r.count)))
    result = EntityDocsResult("intersect", f"{base_a} ∩ {base_b}", merged[:top_k],
                              total_a + total_b, 0, len(_entity_tasks(entity_a)[1]))
    result.others = [base_b]
    return result


def format_entity_docs(result: EntityDocsResult) -> str:
    """渲染成给 LLM 的文本：分级 + 证据片段 + 覆盖度声明（否定结论必须带上）。"""
    if result.query_kind == "intersect":
        head = f"===== 实体检索（交集）「{result.display}」({len(result.hits)}个共同任务) ====="
    else:
        head = f"===== 实体检索「{result.display}」({len(result.hits)}条剧情文档) ====="
    lines = [head]
    if not result.hits:
        lines.append("（本次未检出命中）")
    for i, hit in enumerate(result.hits, 1):
        lines.append(f"{i}. [{hit.level}] {hit.title}（{hit.task_type}，提及 {hit.count} 次）")
        lines.append(f"   证据：{hit.evidence[:160]}")
    lines.append(result.coverage_note)
    return "\n".join(lines)
