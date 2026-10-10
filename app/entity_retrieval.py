# -*- coding: utf-8 -*-
"""实体检索原语 v2：把"谁参与过什么"变成集合运算（LLM Wiki 结构化层下放 L2 的第一步）。

v2 相对 v1 的两处关键修正（来自人工抽查反馈）：
1. **语境污染必须剔除**：任务文本里的「任务概述 / 前情提要 / B站词条元数据」写的是**上一章**剧情
   或元信息，既不能算提及、也不能当证据（实测 780/2354 个任务的 story_text 含此类区域）。
   → 统一走 `story_body()` 得到"本任务剧情正文"，提及次数/台词/动作/证据全部只在正文上统计。
2. **出场人物表是权威关系**：B站词条在每个任务开头列出「出场人物：A、B、C」，
   这就是"谁参与了本段剧情"的一手数据，不该由启发式去猜。
   → 有表时以表为准（在表内 = 强证据；不在表内 = 直接排除）；无表时才退回"有台词"推定。

数据源（只读，进程内缓存）：
- `kb_vectors/wiki_entity_mention_index.json`：实体 → 被哪些条目提及（提供候选集合）
- `kb_vectors/wiki_entry_graph.json`：词条元数据与正文
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

_BASE_DIR = Path(__file__).resolve().parents[1]
GRAPH_PATH = _BASE_DIR / "kb_vectors" / "wiki_entry_graph.json"
MENTION_PATH = _BASE_DIR / "kb_vectors" / "wiki_entity_mention_index.json"

_CHARACTER_TYPES = {"character", "npc", "character_anecdote"}
_VARIANT_RE = re.compile(r"【[^】]*】")
_SPEAKER_RE = re.compile(r"(?:^|\n)\s*\*?\s*([\u4e00-\u9fff·]{2,10})\s*[：:]")
# 动作词只收"物理动作"：说/道/问/喊/答 这类转述动词会命中"行秋说想约大家一起聚一聚"
# （香菱转述行秋的话，行秋并不在场）→ 实测误判，故剔除。
_ACTION_WORDS = ("笑", "点头", "摇头", "皱眉", "递", "接", "走", "跑", "站", "坐",
                 "抬头", "低头", "沉默", "叹气", "解释", "提醒")

# 出场人物表（权威）：B站词条元数据里的「出场人物：A、B、C」
_CAST_RE = re.compile(r"出场人物\s*[:：]\s*([^/\n]{2,200})")
# 元数据行（逐行剔除）：这些是页面信息框字段，不是剧情正文
_META_LINE_RE = re.compile(
    r"^\s*(任务名称|任务描述|任务条件|前置任务|后续任务|出场人物|任务地区|所属版本|任务编号"
    r"|相关活动|开放等级|任务类型|起始NPC|结束NPC|起始npc|结束npc|chapter_name|act_name"
    r"|时间|奖励|任务过程|任务概述)\s*[:：]"
)

# 证据分级
LEVEL_CAST = "强·出场人物表"
LEVEL_LINE = "强·有台词"
LEVEL_ACTION = "中·有动作"
LEVEL_WEAK = "弱·仅文字提及"
LEVEL_ORDER = {LEVEL_CAST: 3, LEVEL_LINE: 3, LEVEL_ACTION: 2, LEVEL_WEAK: 1}


@dataclass
class EntityHit:
    """一条"实体出现在该任务"的命中记录。"""

    title: str
    task_type: str
    count: int
    level: str
    evidence: str
    cast_size: int = 0  # 该任务出场人物表的大小（0 = 无表，走推定）

    @property
    def weight(self) -> int:
        return LEVEL_ORDER.get(self.level, 0)


@dataclass
class EntityDocsResult:
    query_kind: str  # "docs" | "intersect"
    display: str
    hits: List[EntityHit] = field(default_factory=list)
    total_rows: int = 0
    deduped_rows: int = 0
    scanned_tasks: int = 0
    cast_tasks: int = 0  # 其中带权威出场人物表的任务数
    others: List[str] = field(default_factory=list)

    @property
    def coverage_note(self) -> str:
        """覆盖度声明：所有否定结论都必须带上。"""
        ratio = f"{self.cast_tasks}/{self.scanned_tasks}"
        return (f"覆盖度：本次在已收录的 {self.scanned_tasks} 个任务条目中比对（其中 {ratio} 个带权威"
                f"出场人物表），原始命中 {self.total_rows} 条、双源去重后 {len(self.hits)} 条。"
                f"未出现 ≠ 游戏内不存在，只表示当前知识库未收录（或缺出场人物表、无法权威判定）。")


@lru_cache(maxsize=1)
def _graph_and_index():
    graph = json.loads(GRAPH_PATH.read_text(encoding="utf-8"))
    mention = json.loads(MENTION_PATH.read_text(encoding="utf-8"))
    by_id = {str(e.get("entry_id")): e for e in graph.get("entries") or []}
    return by_id, mention


def _base_name(name: str) -> str:
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


def parse_cast(full_text: str) -> set:
    """解析权威出场人物表；丢弃明显错位的取值（实测存在 `出场人物：所属版本=1.0` 这类脏数据）。"""
    names = set()
    for m in _CAST_RE.finditer(str(full_text or "")):
        for chunk in re.split(r"[、,，/]", m.group(1)):
            name = chunk.strip().strip("[]【】")
            if not name or len(name) > 12:
                continue
            if "=" in name or "版本" in name or "任务" in name:
                continue
            names.add(name)
    return names


def story_body(entry: dict) -> str:
    """只取"本任务剧情正文"：剔除元数据块与前情提要/任务概述（它们写的是上一章剧情）。"""
    text = str(entry.get("story_text") or "") or str(entry.get("full_text") or "")
    # mihoyo 风格：[任务概述] … [任务过程] 之后才是本任务正文
    idx = text.find("[任务过程]")
    if idx >= 0:
        text = text[idx + len("[任务过程]"):]
    # bwiki 风格：正文从【B站词条元数据】块（以"任务编号"结尾）之后开始
    marker = text.find("【B站词条元数据】")
    if marker >= 0:
        end = text.find("任务编号", marker)
        text = text[text.find("\n", end) + 1:] if end > 0 else text[marker:]
    lines = [line for line in text.splitlines() if not _META_LINE_RE.match(line)]
    return "\n".join(lines)


def _speaker_hits(body: str, names: Sequence[str]) -> int:
    return sum(1 for m in _SPEAKER_RE.finditer(body) if m.group(1) in names)


def _action_hits(body: str, names: Sequence[str]) -> int:
    total = 0
    for name in names:
        for word in _ACTION_WORDS:
            total += len(re.findall(re.escape(name) + r"[^\n。！？]{0,6}?" + word, body))
    return total


def _snippet(body: str, names: Sequence[str], width: int = 170) -> str:
    """证据片段：优先该角色的台词行（强/中证据），否则首次提及上下文（弱证据）。"""
    for match in _SPEAKER_RE.finditer(body):
        if match.group(1) in names:
            start = match.start()
            return body[start:start + width].replace("\n", " ").strip()
    for name in names:
        idx = body.find(name)
        if idx >= 0:
            return body[max(0, idx - 25): idx + width].replace("\n", " ").strip()
    return body[:width].replace("\n", " ").strip()


def grade_body(entry: dict, names: Sequence[str], cast: set) -> Tuple[Optional[str], str, int, int]:
    """返回 (等级 | None, 证据, 正文提及次数, 表大小)。None = 不算参与（不产出行）。

    三种 None：
    - 有出场人物表但表里没这个角色（权威排除）；
    - 正文净化后提及数为 0（提及只存在于前情提要/元数据里——人工抽查反馈：证据里根本没有人，
      这种条目必须丢弃，否则会给出"没提到他"的假证据）。
    """
    body = story_body(entry)
    count = sum(body.count(n) for n in names if n)
    if cast:
        if any(n in cast for n in names):
            evidence = _snippet(body, names) if count else f"出场人物表（{len(cast)} 人）：{'、'.join(sorted(cast)[:12])}"
            return LEVEL_CAST, evidence, count, len(cast)
        return None, "", count, len(cast)
    if count == 0:
        return None, "", 0, 0  # 正文里没提到 → 前情提要/标题里的提及不算证据
    if _speaker_hits(body, names) > 0:
        return LEVEL_LINE, _snippet(body, names), count, 0
    if _action_hits(body, names) > 0:
        return LEVEL_ACTION, _snippet(body, names), count, 0
    return LEVEL_WEAK, _snippet(body, names), count, 0


def _entity_tasks(name: str) -> Tuple[str, Dict[str, dict]]:
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


@lru_cache(maxsize=1)
def _title_index() -> Dict[str, dict]:
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


def _norm_title(title: str) -> str:
    return re.sub(r"[\s「」（）()·：:，,。！？?！\-—0-9]", "", str(title or ""))


def dedupe_variants(rows: List[EntityHit], mention: dict, jaccard: float = 0.7,
                    min_ratio: float = 0.6) -> List[EntityHit]:
    """双源去重（与已验证探针口径一致）：标题同族 + 长度量级相当 + 实体画像 Jaccard 高。

    要点（踩过坑）：必须按正文长度降序比较；必须有长度量级门槛；主信号用实体画像
    （实测同任务两源正文重合仅 19.7%，画像 Jaccard 0.87）。
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
                continue
            a = set(map(str, inverted.get(t2i.get(short.title, "")) or []))
            b = set(map(str, inverted.get(t2i.get(long.title, "")) or []))
            if len(a & b) / max(1, len(a | b)) >= jaccard:
                dropped.add(short.title)
                break
    return [r for r in rows if r.title not in dropped]


def _collect_rows(name: str, task_type: Optional[str]) -> Tuple[str, List[EntityHit], int, int, int]:
    by_id, mention = _graph_and_index()
    base, tasks = _entity_tasks(name)
    rows: List[EntityHit] = []
    cast_tasks = 0
    for sid, info in tasks.items():
        entry = by_id.get(sid) or {}
        ttype = _task_type_of(entry)
        if task_type and task_type not in ttype:
            continue
        names = [base] + [v for v in info["variants"] if v]
        cast = parse_cast(str(entry.get("full_text") or ""))
        if cast:
            cast_tasks += 1
        level, evidence, count, cast_size = grade_body(entry, names, cast)
        if level is None:
            continue  # 权威排除：有表但表中无此角色
        rows.append(EntityHit(title=str(entry.get("title") or ""), task_type=ttype or "?",
                              count=count, level=level, evidence=evidence, cast_size=cast_size))
    total = len(rows)
    rows = dedupe_variants(rows, mention)
    rows.sort(key=lambda r: (-r.weight, -r.count))
    return base, rows, total, total - len(rows), cast_tasks


def entity_docs(entity: str, task_type: Optional[str] = None, min_level: Optional[str] = None,
                top_k: int = 8) -> EntityDocsResult:
    """单实体检索：该实体参与/被提及的剧情文档（权威出场人物表优先，按证据强度排序）。"""
    base, rows, total, deduped, cast_tasks = _collect_rows(entity, task_type)
    if min_level:
        floor = LEVEL_ORDER.get(min_level, 0)
        rows = [r for r in rows if r.weight >= floor]
    scanned = len(_entity_tasks(entity)[1])
    return EntityDocsResult("docs", base, rows[:top_k], total, deduped, scanned, cast_tasks)


def entity_intersect(entity_a: str, entity_b: str, task_type: Optional[str] = None,
                     top_k: int = 8, require_both_strong: bool = True) -> EntityDocsResult:
    """双实体求交：两人**都在场**的任务。

    require_both_strong=True（默认）：只保留**双方都有强证据**（出场人物表或正文台词）的任务。
    实测教训：放宽到"中·有动作"会把"只有一方在场、另一方被转述提及"的条目混进交集
    （如「往生堂三日无主」里香菱转述"行秋说想约大家一起聚一聚"，行秋并不在场）。
    """
    base_a, rows_a, total_a, _, cast_a = _collect_rows(entity_a, task_type)
    base_b, rows_b, total_b, _, cast_b = _collect_rows(entity_b, task_type)
    map_a, map_b = {r.title: r for r in rows_a}, {r.title: r for r in rows_b}
    merged: List[EntityHit] = []
    floor = LEVEL_ORDER[LEVEL_LINE] if require_both_strong else LEVEL_ORDER[LEVEL_ACTION]
    for title in map_a.keys() & map_b.keys():
        hit_a, hit_b = map_a[title], map_b[title]
        if min(hit_a.weight, hit_b.weight) < floor:
            continue  # 至少要"强"（表/台词），避免"一方仅被提及/被转述"
        merged.append(hit_a if hit_a.weight >= hit_b.weight else hit_b)
    merged.sort(key=lambda r: (-r.weight, -r.count))
    result = EntityDocsResult("intersect", f"{base_a} ∩ {base_b}", merged[:top_k],
                              total_a + total_b, 0, len(_entity_tasks(entity_a)[1]),
                              min(cast_a, cast_b))
    result.others = [base_b]
    return result


def format_entity_docs(result: EntityDocsResult) -> str:
    """渲染成给 LLM 的文本：分级 + 证据片段 + 覆盖度声明（否定结论必须带上）。"""
    if result.query_kind == "intersect":
        head = f"===== 实体检索（交集）「{result.display}」({len(result.hits)}个双方在场的任务) ====="
    else:
        head = f"===== 实体检索「{result.display}」({len(result.hits)}条剧情文档) ====="
    lines = [head]
    if not result.hits:
        lines.append("（本次未检出命中）")
    for i, hit in enumerate(result.hits, 1):
        src = f"出场人物表 {hit.cast_size} 人" if hit.cast_size else "正文推定"
        lines.append(f"{i}. [{hit.level}] {hit.title}（{hit.task_type}，正文提及 {hit.count} 次，{src}）")
        lines.append(f"   证据：{hit.evidence[:170]}")
    lines.append(result.coverage_note)
    return "\n".join(lines)
