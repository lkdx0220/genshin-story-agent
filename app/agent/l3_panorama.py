# -*- coding: utf-8 -*-
"""L3 全景题分段生成器 v1。

设计目标：
- 单次调用 qwen3.8-max 生成 1.2~1.8 万字会超时（实测 >10 分钟）；
- 改为代码先按“任务线 / 实体档案 / 地图文本 / 跨线综合”分桶，
  每个桶单独调用 qwen3.8-max，关闭 thinking，单节 1200~3000 字；
- 多个 section 并行生成，按固定顺序回收并流式输出，保证前端看到的内容与最终答案顺序一致；
- 所有评价只给“直接证据/动机/内在矛盾/一句评价”的通用范式，不注入任何具体角色结论，避免题目级过拟合。

v1 的边界：
- 不做 Embedding 语义重叠切片，先用证据文本自身的实体标题做确定性关联；
- 覆盖率只做日志检查，暂不自动重生成缺失 section；
- 解析失败时返回空字符串，由调用方回退到原来的单次 L3 调用。
"""
from __future__ import annotations

import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from app.llm import answer_llm_l3_fast

_FULL_TEXT_HEADER = "[全景全文读取]"
_MAP_SECTION_MARKER = "\n\n===== 相关地图文本全文 ====="
_ENTITY_SECTION_MARKER = "\n\n===== 相关角色/组织/圣遗物/地点档案全文 ====="

_TASK_BLOCK_RE = re.compile(
    r"===== 任务 \d+: (.+?) \(ID (\d+)\) 共 \d+ 字 =====\n(.*?)(?=\n===== 任务 |\n\n===== 相关地图文本全文|\Z)",
    re.S,
)
_MAP_BLOCK_RE = re.compile(
    r"----- (.+?) \(ID (\d+)\) 共 \d+ 字 -----\n(.*?)(?=\n----- |\Z)",
    re.S,
)
_ENTITY_BLOCK_RE = re.compile(
    r"----- (.+?) \((.+?), ID (\d+)\) 共 \d+ 字 -----\n(.*?)(?=\n----- |\Z)",
    re.S,
)

_EMIT: Optional[Callable[[str, dict], None]] = None
_SECTION_WORKERS = 4
_ENTITY_PER_SECTION = 15
_MAX_ENTITY_CHARS = 3000
_MAX_TASK_RELATED_ENTITY_CHARS = 24000


@dataclass
class PanoramaSection:
    key: str
    title: str
    kind: str
    text: str
    instruction: str


_COMMON_SYSTEM = """你是原神剧情取证分析师。你只能使用下面提供的证据，不得使用任何外部知识。

铁律：
1. 每一条事实性陈述都必须能在证据原文中找到依据；证据没有写的，写“证据未覆盖”，不要推测。
2. 禁止用“总之/综上所述”式空泛总结替代具体剧情。
3. 禁止用“黑暗/邪恶/外来力量/外部敌人”这类概括词替代具体的组织、角色、机制或事件。
4. 角色评价必须包含四段：直接证据、动机、内在矛盾、一句不超过 50 字的评价；不能只复述剧情。
5. 只输出正文，不输出“根据证据/综上所述”等元话语，不写“本节”之类的标题。"""

_TASK_INSTRUCTION = """请按以下结构撰写本节：
一、任务线概述（触发条件、地区、核心冲突）
二、关键事件与流程（按时间顺序）
三、关键角色与关系（谁做了什么，为什么）
四、与其它任务线的交叉线索
字数 1200~2000 字，只写证据支持的内容。"""

_ENTITY_INSTRUCTION = """请对证据中出现的每一个角色/组织/造物分别成节：
### 名称
- 直接证据：引用或概括证据原文中的行为/台词
- 动机：角色为什么这样做
- 内在矛盾：角色身上的悖论或张力
- 评价：一句不超过 50 字、有洞察力的评价
字数 1500~2500 字，禁止把多个角色合并成一段。"""

_MAP_INSTRUCTION = """请逐张地图文本说明：
- 地图文本名称
- 原文要点（地点、人物、事件）
- 它与剧情的关系
字数 1000~1800 字，不要漏掉证据中给出的地图文本。"""

_SYNTHESIS_INSTRUCTION = """请撰写跨线综合：
一、四条任务线之间的关系（谁连接了谁，伏笔如何收束）
二、核心角色的跨线对照（同一角色在不同任务线中的选择与变化）
三、主题与结局（用证据说明，不要空泛抒情）
字数 2000~3000 字，只写证据支持的内容。"""


def _emit_progress(event: str, data: dict) -> None:
    if _EMIT is not None:
        try:
            _EMIT(event, data)
        except Exception:
            pass


def _find_panorama_text(messages) -> str:
    for msg in messages:
        if isinstance(msg, ToolMessage):
            content = msg.content if hasattr(msg, "content") else str(msg)
            if content.strip().startswith(_FULL_TEXT_HEADER):
                return content
    return ""


def _split_evidence(text: str):
    """把全景全文 ToolMessage 拆成 task/map/entity 三类。"""
    if not text:
        return [], [], []

    map_idx = text.find(_MAP_SECTION_MARKER)
    entity_idx = text.find(_ENTITY_SECTION_MARKER)
    if map_idx < 0:
        map_idx = len(text)
    if entity_idx < 0:
        entity_idx = len(text)

    task_part = text[:map_idx]
    map_part = text[map_idx:entity_idx] if entity_idx > map_idx else ""
    entity_part = text[entity_idx:] if entity_idx > map_idx else ""

    tasks = [(m.group(1).strip(), m.group(2).strip(), m.group(3).strip()) for m in _TASK_BLOCK_RE.finditer(task_part)]
    maps = [(m.group(1).strip(), m.group(2).strip(), m.group(3).strip()) for m in _MAP_BLOCK_RE.finditer(map_part)]
    entities = [
        (m.group(1).strip(), m.group(2).strip(), m.group(3).strip(), m.group(4).strip())
        for m in _ENTITY_BLOCK_RE.finditer(entity_part)
    ]
    return tasks, maps, entities


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[本节证据过长，已截断]"


def _build_task_sections(tasks, maps, entities) -> List[PanoramaSection]:
    sections: List[PanoramaSection] = []
    map_text = "\n\n".join(
        f"----- {title} (ID {eid}) -----\n{content}" for title, eid, content in maps
    )
    for title, eid, content in tasks:
        # 第一版不做 Embedding 语义切片：任务正文里出现过的实体标题，
        # 才作为该任务线的相关实体证据；跨线实体在多个任务正文里出现时会自然重复进入多个桶。
        related = []
        for etitle, etype, eeid, econtent in entities:
            if len(etitle) >= 2 and (etitle in content or any(etitle in m[2] for m in maps)):
                related.append((etitle, etype, eeid, econtent))
        related_text = "\n\n".join(
            f"----- {etitle} ({etype}, ID {eeid}) -----\n{_truncate(econtent, _MAX_ENTITY_CHARS)}"
            for etitle, etype, eeid, econtent in related
        )
        evidence_parts = [f"【任务全文：{title}】\n{content}"]
        if map_text:
            evidence_parts.append("【相关地图文本】\n" + map_text)
        if related_text:
            evidence_parts.append("【该任务线相关实体档案】\n" + _truncate(related_text, _MAX_TASK_RELATED_ENTITY_CHARS))
        sections.append(
            PanoramaSection(
                key=f"task_{eid}",
                title=f"任务线：{title}",
                kind="task",
                text="\n\n".join(evidence_parts),
                instruction=_TASK_INSTRUCTION,
            )
        )
    return sections


def _build_entity_sections(entities) -> List[PanoramaSection]:
    sections: List[PanoramaSection] = []
    if not entities:
        return sections
    for i in range(0, len(entities), _ENTITY_PER_SECTION):
        chunk = entities[i:i + _ENTITY_PER_SECTION]
        body = "\n\n".join(
            f"----- {etitle} ({etype}, ID {eeid}) -----\n{_truncate(econtent, _MAX_ENTITY_CHARS)}"
            for etitle, etype, eeid, econtent in chunk
        )
        names = "、".join(t for t, _, _, _ in chunk)
        sections.append(
            PanoramaSection(
                key=f"entity_{i // _ENTITY_PER_SECTION + 1}",
                title=f"角色/组织/造物档案：{names}",
                kind="entity",
                text=body,
                instruction=_ENTITY_INSTRUCTION,
            )
        )
    return sections


def _build_map_section(maps) -> List[PanoramaSection]:
    if not maps:
        return []
    body = "\n\n".join(f"----- {title} (ID {eid}) -----\n{content}" for title, eid, content in maps)
    return [
        PanoramaSection(
            key="map_all",
            title="地图文本与世界观",
            kind="map",
            text=body,
            instruction=_MAP_INSTRUCTION,
        )
    ]


def _build_synthesis_section(tasks, maps, entities) -> List[PanoramaSection]:
    if not tasks:
        return []
    task_body = "\n\n".join(f"----- 任务：{title} (ID {eid}) -----\n{content}" for title, eid, content in tasks)
    map_body = "\n\n".join(f"----- 地图：{title} (ID {eid}) -----\n{content}" for title, eid, content in maps)
    entity_body = "\n\n".join(
        f"----- {etitle} ({etype}, ID {eeid}) -----\n{_truncate(econtent, _MAX_ENTITY_CHARS)}"
        for etitle, etype, eeid, econtent in entities
    )
    evidence = "【全部任务线】\n" + task_body
    if map_body:
        evidence += "\n\n【全部地图文本】\n" + map_body
    if entity_body:
        evidence += "\n\n【全部实体档案】\n" + entity_body
    return [
        PanoramaSection(
            key="synthesis",
            title="跨线综合与角色评价",
            kind="synthesis",
            text=_truncate(evidence, 130000),
            instruction=_SYNTHESIS_INSTRUCTION,
        )
    ]


def build_sections(messages, original_query: str) -> List[PanoramaSection]:
    text = _find_panorama_text(messages)
    tasks, maps, entities = _split_evidence(text)
    if not tasks:
        return []
    sections: List[PanoramaSection] = []
    sections.extend(_build_task_sections(tasks, maps, entities))
    sections.extend(_build_entity_sections(entities))
    sections.extend(_build_map_section(maps))
    sections.extend(_build_synthesis_section(tasks, maps, entities))
    return sections


def _generate_one(section: PanoramaSection) -> str:
    start = time.time()
    messages = [
        SystemMessage(content=_COMMON_SYSTEM),
        HumanMessage(content=(
            f"【本节主题】{section.title}\n\n"
            f"【证据】\n{section.text}\n\n"
            f"{section.instruction}\n\n"
            f"【当前任务】只输出本节正文，不要输出“好的”“以下是”等开场白。"
        )),
    ]
    try:
        response = answer_llm_l3_fast.invoke(messages)
        content = (response.content if hasattr(response, "content") else str(response)) or ""
        content = content.strip()
        print(f"  -> [L3分段] {section.key} 完成 {len(content)} 字，用时 {time.time() - start:.1f}s")
        return content
    except Exception as e:
        print(f"  -> [L3分段] {section.key} 失败：{e}")
        return f"[本节生成失败：{e}]"


def _ordered_parallel(sections: List[PanoramaSection]) -> List[str]:
    """并行生成，但按 section 原始顺序回收，保证流式顺序和最终答案一致。"""
    results: List[str] = [""] * len(sections)
    with ThreadPoolExecutor(max_workers=min(_SECTION_WORKERS, max(1, len(sections)))) as pool:
        futures = [pool.submit(_generate_one, section) for section in sections]
        for i, future in enumerate(futures):
            try:
                results[i] = future.result()
            except Exception as e:
                results[i] = f"[本节生成失败：{e}]"
            # 每完成一个 section 就推送一次，前端可以按顺序边生成边看。
            header = f"\n\n## {sections[i].title}\n\n"
            _emit_progress("answer_delta", {"delta": header + results[i]})
    return results


def _assemble(sections: List[PanoramaSection], results: List[str], original_query: str) -> str:
    parts = [f"# {original_query}\n"]
    for section, content in zip(sections, results):
        parts.append(f"\n\n## {section.title}\n\n{content.strip()}")
    return "".join(parts).strip()


def _log_coverage(sections, final_answer: str) -> None:
    entity_titles = []
    for section in sections:
        if section.kind != "entity":
            continue
        for line in section.text.splitlines():
            m = re.match(r"----- (.+?) \((.+?), ID (\d+)\) -----", line.strip())
            if m:
                entity_titles.append(m.group(1).strip())
    if not entity_titles:
        return
    missing = [t for t in entity_titles if t not in final_answer]
    if missing:
        print(f"  -> [L3覆盖检查] 未在答案中出现（v1 仅记录，不重生成）：{missing}")
    else:
        print(f"  -> [L3覆盖检查] {len(entity_titles)} 个实体全部出现")


def generate_panorama_answer(messages, original_query: str, emit: Optional[Callable[[str, dict], None]] = None) -> str:
    """L3 全景题入口：返回最终答案字符串；解析失败返回空字符串由调用方回退。"""
    global _EMIT
    _EMIT = emit
    sections = build_sections(messages, original_query)
    if not sections:
        return ""
    # 冒烟测试用：L3_PANORAMA_LIMIT=1 时只生成第一节，避免每次测试都跑完整 8 节。
    limit = int(os.getenv("L3_PANORAMA_LIMIT", "0") or "0")
    if limit > 0:
        sections = sections[:limit]
    print(f"  -> [L3分段] 共 {len(sections)} 节：{[s.key for s in sections]}")
    _emit_progress("answer_delta", {"delta": f"# {original_query}\n"})
    results = _ordered_parallel(sections)
    final_answer = _assemble(sections, results, original_query)
    _log_coverage(sections, final_answer)
    print(f"  -> [L3分段] 拼装完成，总长 {len(final_answer)} 字")
    return final_answer
