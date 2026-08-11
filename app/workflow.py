# -*- coding: utf-8 -*-
"""工作流拼装：构建 LangGraph StateGraph。

节点拓扑：
  rewrite_query → assess_query
    ├── L1 → fast_agent ⇄ tools → END
    └── L2 → plan_agent ⇄ tools → answer_agent → END

L1（快速路径）：合并 Plan/Answer，最多 MAX_FAST_ITERATIONS 轮工具调用
L2（完整路径）：Plan → Tool 循环 → Answer，最多 MAX_AGENT_ITERATIONS 轮迭代
"""
from langgraph.graph import StateGraph, END

from app.schema import GenshinAdvisorState
from app.agent import (
    rewrite_query,
    assess_query,
    route_after_assess,
    fast_agent,
    route_after_fast,
    plan_agent,
    route_after_plan,
    route_after_tools,
    answer_agent,
    tool_executor,
)


def create_agent_workflow() -> StateGraph:
    """构建并编译 Agent 工作流。返回 CompiledGraph，可调用 .invoke() / .stream()。"""
    workflow = StateGraph(GenshinAdvisorState)

    workflow.add_node("rewrite_query", rewrite_query)
    workflow.add_node("assess_query", assess_query)
    workflow.add_node("fast_agent", fast_agent)
    workflow.add_node("plan_agent", plan_agent)
    workflow.add_node("tools", tool_executor)
    workflow.add_node("answer_agent", answer_agent)

    workflow.set_entry_point("rewrite_query")
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
        {"tools": "tools", "answer_agent": "answer_agent"},
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
