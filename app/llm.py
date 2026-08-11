# -*- coding: utf-8 -*-
"""LLM 实例池：按用途分级配置，统一重试封装。

分级设计（参照 DeepSeek/通义千问的多模型协同思路）：
- llm: 通用 LLM（qwen3.7-max, medium reasoning）
- plan_llm: L1 快速规划（qwen-plus, 无 reasoning，省时）
- plan_llm_l2: L2 复杂规划（qwen3.7-max, low reasoning）
- answer_llm_light/medium/deep: 按意图分级回答
- alias_judge_llm: 别名判断沙箱（deepseek-v4-flash, 20 token 输出）
- assess_llm: L1/L2 路径分类器（deepseek-v4-flash, 50 token 输出）
"""
import time
from typing import List

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from app.config import QWEN_API_KEY, QWEN_BASE_URL, DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL

# ====== 通用 LLM（默认）======
llm = ChatOpenAI(
    model="qwen3.7-max",
    api_key=QWEN_API_KEY,
    base_url=QWEN_BASE_URL,
    temperature=0.2,
    max_tokens=65536,
    request_timeout=180,
    model_kwargs={
        "reasoning_effort": "medium",
    },
)

# ====== Plan L1 专用 LLM（简单题，fast_agent）：不需要深度思考，快速决策 ======
plan_llm = ChatOpenAI(
    model="qwen-plus",
    api_key=QWEN_API_KEY,
    base_url=QWEN_BASE_URL,
    temperature=0.1,
    max_tokens=4096,
    request_timeout=60,
)

# ====== Plan L2 专用 LLM（复杂题，plan_agent）：轻度思考，确保多子问题拆解和工具选择准确性 ======
plan_llm_l2 = ChatOpenAI(
    model="qwen3.7-max",
    api_key=QWEN_API_KEY,
    base_url=QWEN_BASE_URL,
    temperature=0.1,
    max_tokens=32768,
    request_timeout=120,
    model_kwargs={
        "reasoning_effort": "low",
    },
)

# ====== 按意图分级配置 Answer LLM ======
# 轻量（简单事实/角色查询）：不开思考，快速响应
answer_llm_light = ChatOpenAI(
    model="qwen-plus",
    api_key=QWEN_API_KEY,
    base_url=QWEN_BASE_URL,
    temperature=0.2,
    max_tokens=4096,
    request_timeout=60,
)
# 中等（搜索/世界观/书籍）：轻度思考
answer_llm_medium = ChatOpenAI(
    model="qwen3.7-max",
    api_key=QWEN_API_KEY,
    base_url=QWEN_BASE_URL,
    temperature=0.2,
    max_tokens=32768,
    request_timeout=120,
    model_kwargs={
        "reasoning_effort": "low",
    },
)
# 深度（剧情任务/溯源追踪）：充分思考
answer_llm_deep = ChatOpenAI(
    model="qwen3.7-max",
    api_key=QWEN_API_KEY,
    base_url=QWEN_BASE_URL,
    temperature=0.2,
    max_tokens=65536,
    request_timeout=180,
    model_kwargs={
        "reasoning_effort": "medium",
    },
)

# 意图 → Answer LLM 映射：取最高优先级的意图
INTENT_LLM_MAP = {
    "D": answer_llm_deep,     # 剧情任务
    "F": answer_llm_deep,     # 溯源追踪
    "C2": answer_llm_deep,    # 世界观设定
    "E": answer_llm_medium,   # 书籍文献
    "A": answer_llm_medium,   # 搜索检索
    "B": answer_llm_light,    # 角色查询
}


def _select_answer_llm(intent_labels: list) -> ChatOpenAI:
    """根据意图标签选择最合适的 Answer LLM。
    多个意图取最高优先级（Deep > Medium > Light）。"""
    if not intent_labels:
        return answer_llm_deep  # 未知意图用深度
    # 按优先级查找：先找 D/F/C2，再 E/A，最后 B
    for priority_key in ("D", "F", "C2", "E", "A", "B"):
        if priority_key in intent_labels:
            return INTENT_LLM_MAP[priority_key]
    return answer_llm_deep


# ====== DeepSeek 别名判断（安全沙箱，不计入 agent 轮次）======
alias_judge_llm = ChatOpenAI(
    model="deepseek-v4-flash",
    api_key=DEEPSEEK_API_KEY,
    base_url=DEEPSEEK_BASE_URL,
    temperature=0,
    max_tokens=20,
    request_timeout=10,
)

# ====== 查询分类器（L1/L2 判断，轻量模型）======
assess_llm = ChatOpenAI(
    model="deepseek-v4-flash",
    api_key=DEEPSEEK_API_KEY,
    base_url=DEEPSEEK_BASE_URL,
    temperature=0,
    max_tokens=50,
    request_timeout=10,
)


def llm_invoke_with_retry(messages, max_retries=3, llm_instance=None):
    """带重试的 LLM 调用，处理 API 限流。
    llm_instance: 指定 LLM 实例，不传则用默认 llm。"""
    target_llm = llm_instance if llm_instance is not None else llm
    for attempt in range(max_retries):
        try:
            return target_llm.invoke(messages)
        except Exception as e:
            if "rate" in str(e).lower() or "throttle" in str(e).lower() or "429" in str(e):
                wait = (attempt + 1) * 5
                print(f"  [限流] 等待 {wait}s 后重试...")
                time.sleep(wait)
            elif attempt < max_retries - 1:
                time.sleep(2)
            else:
                raise
