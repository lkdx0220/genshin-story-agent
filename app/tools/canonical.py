# -*- coding: utf-8 -*-
"""canonical Wiki 查询工具（Wave1 运行时接入）。

只读本地 ``wiki/characters/*.md``，返回带 source span citation 的候选证据。
不联网、不修改 wiki、不发布事实。
"""

from typing import Optional

from langchain_core.tools import tool

from app.canonical import canonical_query as _canonical_query


@tool("canonical_query")
def canonical_query_tool(question: str, snapshot: Optional[dict] = None, player_choice: Optional[str] = None) -> str:
    """查询 canonical Wiki 候选页（本地结构化事实 + 来源 citation）。

    适用问题：
    - 某角色的身份阶段、名称/别名；
    - 某角色与组织/他人的关系；
    - 某角色经历的版本、事件、时间线；
    - 记忆、失忆、资料记录；
    - 冲突候选。

    snapshot 可选：{"identity_version_id": "...", "story_event_id": "...",
    "position": "before|after|at|unknown", "game_version": "3.3",
    "game_version_label": "..."}。提供后只返回适用于该快照的 Fact。

    返回：带 ``[^source:URL#pNNN]`` 的证据文本；没有命中时返回显式缺口。
    """
    question = str(question or "").strip()
    if not question:
        raise ValueError("question 不能为空")
    return _canonical_query(question, snapshot=snapshot, player_choice=player_choice)
