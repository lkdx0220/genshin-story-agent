# -*- coding: utf-8 -*-
"""实体查询工具骨架（第 ③ 步 3a：离线固化，**尚未注册进工具表**）。

设计要点（来自人工抽查反馈定下的契约）：
1. **结论区只放"强"证据**（出场人物表 / 正文台词）；中·有动作、弱·仅文字提及一律进"线索区"，
   并明确标注不可作结论——避免把"被转述提及"当成出场。
2. **否定结论必带覆盖度声明**（未收录 ≠ 游戏内不存在；并说明有多少任务带权威出场人物表）。
3. 参数全部走 `vocab` 枚举（任务类型 / 证据等级 / 关系），非法取值直接报错而不是静默降级。

注意：本模块**不在** `app/tools/__init__.py` 的导入链里，因此不会被注册给 LLM。
第 ③ 步 3b 才会加 `@tool` 包装并挂进工具表 + 路由。
"""
from __future__ import annotations

from typing import List, Optional

from langchain_core.tools import tool

from app.entity_retrieval import (
    LEVEL_ACTION,
    LEVEL_CAST,
    LEVEL_LINE,
    LEVEL_ORDER,
    LEVEL_WEAK,
    EntityDocsResult,
    EntityHit,
    entity_docs,
    entity_intersect,
)
from app.tools.vocab import (
    ENTITY_RELATIONS,
    EVIDENCE_LEVELS,
    TASK_TYPES,
    EntityEvidenceLevel,
    EntityRelation,
    TaskType,
)

_STRONG_FLOOR = LEVEL_ORDER[LEVEL_LINE]  # 强 = 3（出场人物表 / 有台词）；中=2；弱=1

_RELATION_LABELS = {
    "participated": "参与/出现在哪些剧情",
    "co_occur": "两人共同出场",
}


def _validate(entity: str, relation: str, target: str, task_type: str, min_level: str) -> None:
    """参数校验：非法取值直接报错（不静默降级，便于 3b 的工具调用被模型纠正）。"""
    if not str(entity or "").strip():
        raise ValueError("entity 不能为空")
    if relation not in ENTITY_RELATIONS:
        raise ValueError(f"relation 只能是 {list(ENTITY_RELATIONS)}，收到 {relation!r}")
    if relation == "co_occur" and not str(target or "").strip():
        raise ValueError("relation=co_occur 时必须提供 target（第二个人物名）")
    if task_type and task_type not in TASK_TYPES:
        raise ValueError(f"task_type 取值非法：{task_type!r}")
    if min_level and min_level not in EVIDENCE_LEVELS:
        raise ValueError(f"min_level 取值非法：{min_level!r}")


def _render(result: EntityDocsResult, relation: str) -> str:
    """渲染：结论区（强）/ 线索区（中·弱）/ 覆盖度声明。"""
    strong = [h for h in result.hits if h.weight >= _STRONG_FLOOR]
    weak = [h for h in result.hits if h.weight < _STRONG_FLOOR]
    head = (f"===== 实体查询「{result.display}」（{_RELATION_LABELS.get(relation, relation)}，"
            f"共 {len(result.hits)} 条命中）=====")
    lines = [head, "", "【结论区｜强证据（出场人物表或正文台词），可作结论】"]
    if strong:
        for i, hit in enumerate(strong, 1):
            lines.append(_format_hit(i, hit))
    else:
        lines.append("（无强证据命中）")
    if weak:
        lines += ["", "【线索区｜中·有动作 / 弱·仅文字提及，仅供参考，**不可**当作出场结论】"]
        for i, hit in enumerate(weak, 1):
            lines.append(_format_hit(i, hit))
    lines += ["", result.coverage_note]
    return "\n".join(lines)


def _format_hit(index: int, hit: EntityHit) -> str:
    src = f"出场人物表 {hit.cast_size} 人" if hit.cast_size else "正文推定"
    return (f"{index}. [{hit.level}] {hit.title}（{hit.task_type}，正文提及 {hit.count} 次，{src}）\n"
            f"   证据：{hit.evidence[:200]}")


def entity_query(entity: str, relation: str = "participated", target: str = "",
                 task_type: str = "", min_level: str = "", top_k: int = 8) -> str:
    """查询人物参与过的剧情（relation=participated）或两人共同出场的剧情（relation=co_occur）。

    参数：
    - entity：人物名（如「胡桃」；支持去括号/变体归一）
    - relation：participated（默认）| co_occur
    - target：relation=co_occur 时的第二个人物名
    - task_type：限定任务类型（见 vocab.TaskType，如「魔神任务」）；空=不限
    - min_level：限定最低证据等级（见 vocab.EntityEvidenceLevel）；空=不限
    - top_k：返回条数上限（结论区+线索区合计）

    返回：已渲染文本（结论区 / 线索区 / 覆盖度声明）。
    """
    _validate(entity, relation, target, task_type, min_level)
    ttype = task_type or None
    min_level_arg = min_level or None
    if int(top_k) <= 0:
        raise ValueError("top_k 必须为正整数")
    if relation == "co_occur":
        result = entity_intersect(entity, target, task_type=ttype, top_k=int(top_k))
    else:
        result = entity_docs(entity, task_type=ttype, min_level=min_level_arg, top_k=int(top_k))
    return _render(result, relation)


# 3b 才会用到的工具描述（先写好，便于评审文案）
ENTITY_QUERY_DESCRIPTION = (
    "查询人物与剧情的参与关系：某角色参与/出现在哪些任务（relation=participated），"
    "或两个人有哪些共同出场的剧情（relation=co_occur，需给 target）。"
    "证据分级：【强】= 出场人物表或正文台词，可作结论；【中/弱】= 动作线索或仅被提及，不可当出场。"
    "只能回答知识库已收录的内容，回答否定结论时必须带上覆盖度声明。"
)


@tool("entity_query")
def entity_query_tool(entity: str, relation: EntityRelation = "participated", target: str = "",
                      task_type: Optional[TaskType] = None,
                      min_level: Optional[EntityEvidenceLevel] = None, top_k: int = 8) -> str:
    """查询人物与剧情的参与关系（本地知识库，非联网）。

    - relation=participated：某角色参与/出现在哪些任务（默认）
    - relation=co_occur：两个人有哪些共同出场的剧情（必须提供 target）

    证据分级：【强·出场人物表】或【强·有台词】可作结论；【中·有动作】【弱·仅文字提及】只作线索，
    不可当作出场。只能回答知识库已收录的内容；给出否定结论时必须带上返回文本里的覆盖度声明。
    """
    return entity_query(entity, relation=relation, target=target, task_type=task_type or "",
                        min_level=min_level or "", top_k=top_k)
