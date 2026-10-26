# -*- coding: utf-8 -*-
"""工作流拼装：构建 LangGraph StateGraph。

节点拓扑：
  rewrite_query → assess_query
    ├── L1 → fast_agent ⇄ tools → END
    └── L2 → plan_agent ⇄ tools → answer_agent → END

L1（快速路径）：合并 Plan/Answer，最多 MAX_FAST_ITERATIONS 轮工具调用
L2（完整路径）：Plan → Tool 循环 → Answer，最多 MAX_AGENT_ITERATIONS 轮迭代
"""

import os

from langgraph.graph import END, StateGraph

from app.agent import (
    answer_agent,
    assess_query,
    fast_agent,
    plan_agent,
    rewrite_query,
    route_after_assess,
    route_after_fast,
    route_after_plan,
    route_after_tools,
    tool_executor,
)
from app.agent.alias_identity import alias_identity_gate, initialize_request
from app.schema import GenshinAdvisorState
from app.statistics.routing import statistics_gate
from app.wiki_route import router_from_environment


def _initialize_adaptive_request(state):
    return dict(initialize_request(state), fast_finalized=False,
                fast_terminal_nonce=None, fast_exit_kind=None,
                answer_validation=None, answer_diagnostics=None, raw_draft=None)


def create_agent_workflow() -> StateGraph:
    """构建并编译 Agent 工作流。返回 CompiledGraph，可调用 .invoke() / .stream()。"""
    wiki_router = router_from_environment()
    workflow = StateGraph(GenshinAdvisorState)

    workflow.add_node("rewrite_query", rewrite_query)
    workflow.add_node("assess_query", assess_query)
    workflow.add_node("fast_agent", fast_agent)
    workflow.add_node("plan_agent", plan_agent)
    workflow.add_node("tools", tool_executor)
    workflow.add_node("answer_agent", answer_agent)
    workflow.add_node("statistics_gate", statistics_gate)
    adaptive_alias = os.environ.get('LLM_WIKI_MODE', 'off') == 'agent_evidence'
    if adaptive_alias:
        workflow.add_node("initialize_request", _initialize_adaptive_request)
        workflow.add_node("alias_identity_gate", alias_identity_gate)
        workflow.set_entry_point("initialize_request")
        workflow.add_edge("initialize_request", "statistics_gate")
    else:
        workflow.set_entry_point("statistics_gate")

    if wiki_router is None:
        legacy_entry = "rewrite_query"
    else:
        workflow.add_node("wiki_gate", wiki_router.node)
        legacy_entry = "wiki_gate"
        workflow.add_conditional_edges(
            "wiki_gate", lambda state: state["wiki_route"]["route"],
            {"wiki": END, "legacy": "rewrite_query"},
        )
    workflow.add_conditional_edges(
        "statistics_gate", lambda state: "statistics" if state.get("count_result") is not None else "legacy",
        {"statistics": END, "legacy": legacy_entry},
    )
    if adaptive_alias:
        workflow.add_edge("rewrite_query", "alias_identity_gate")
        workflow.add_conditional_edges(
            "alias_identity_gate",
            lambda state: "end" if state.get("termination_kind") in {"alias_identity", "cancelled"} else "assess",
            {"end": END, "assess": "assess_query"},
        )
    else:
        workflow.add_edge("rewrite_query", "assess_query")

    # 分类后路由：L1 → fast_agent，L2 → plan_agent
    workflow.add_conditional_edges(
        "assess_query",
        route_after_assess,
        {"fast_agent": "fast_agent", "plan_agent": "plan_agent"},
    )

    # L2 路径：plan_agent → tools 循环 → answer_agent
    workflow.add_conditional_edges(
        "plan_agent",
        route_after_plan,
        {"tools": "tools", "answer_agent": "answer_agent", **({"end": END} if adaptive_alias else {})},
    )
    workflow.add_conditional_edges(
        "tools",
        route_after_tools,
        {"plan_agent": "plan_agent", "fast_agent": "fast_agent", "answer_agent": "answer_agent", "end": END},
    )
    workflow.add_edge("answer_agent", END)

    # L1 路径：fast_agent → tools 循环 → END
    workflow.add_conditional_edges(
        "fast_agent",
        route_after_fast,
        {"tools": "tools", "end": END, "fast_agent": "fast_agent"},
    )

    return workflow.compile()
