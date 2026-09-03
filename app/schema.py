# -*- coding: utf-8 -*-
"""Schema：Agent 状态、系统 Prompt 加载。

GenshinAdvisorState 是 LangGraph 各节点之间共享的 state 类型，
所有节点函数签名都以它为输入输出。
"""
import os
from typing import Dict, List, Tuple, Any, Optional, Annotated, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

from app.config import PROMPTS_DIR


def _load_prompt(filename: str) -> str:
    """加载 prompts/ 目录下的系统 prompt 文本。"""
    filepath = os.path.join(PROMPTS_DIR, filename)
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read()
    return ""


# ====== 系统 Prompt（4 个）======
AGENT_SYSTEM_PROMPT_PLAN = _load_prompt("system/agent_system_v4_plan.txt")
AGENT_SYSTEM_PROMPT_ANSWER = _load_prompt("system/agent_system_v4_answer.txt")
AGENT_SYSTEM_PROMPT_FAST = _load_prompt("system/agent_fast_answer.txt")
HELP_TEXT = _load_prompt("help.txt")


# ====== Agent 状态 ======
class GenshinAdvisorState(TypedDict):
    user_query: str
    rewritten_query: Optional[str]
    alias_notes: Optional[str]
    alias_pairs: Optional[List[Tuple[str, str]]]   # 结构化别名映射 [(别名, 规范名)]，供确定性身份直答使用
    conversation_history: Optional[List[Dict[str, str]]]
    conversation_summary: Optional[str]
    messages: Annotated[List[BaseMessage], add_messages]
    final_response: Optional[str]
    iteration: Optional[int]
    plan_retry: Optional[int]             # 规划阶段无工具调用时的强制重试计数
    plan_exit_reason: Optional[str]       # 无工具调用进入回答阶段的原因：alias_identity/math_greeting/prior_tool_result/retry_exhausted
    execution_plan: Optional[str]          # 规划阶段生成的执行报告，供回答阶段使用
    intent_labels: Optional[List[str]]    # 路由器输出的意图标签，如 ["B", "D"]
    run_id: Optional[str]                 # 运行标识，用于取消信号查找
    execution_mode: Optional[str]         # L1（快速路径）或 L2（完整路径）
    fast_iteration: Optional[int]         # 快速路径的迭代计数
