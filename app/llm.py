# -*- coding: utf-8 -*-
"""LLM 实例池：按用途分级配置，统一重试封装。

分级设计（参照 DeepSeek/通义千问的多模型协同思路）：
- llm: 通用 LLM（qwen3.7-max, medium reasoning）
- plan_llm: L1 快速规划（qwen3.7-plus, 无 reasoning，省时，兼顾工具调用）
- plan_llm_l2: L2 复杂规划（qwen3.7-max, low reasoning）
- answer_llm_light/medium/deep: 按意图分级回答
- alias_judge_llm: 别名判断沙箱（deepseek-v4-flash-vision-exp, 20 token 输出）
- assess_llm: L1/L2 路径分类器（deepseek-v4-flash-vision-exp, 50 token 输出）

Qwen 实例统一使用 QwenFallbackChatOpenAI：
- 默认走 token-plan 新接口 + 新 Key；
- 新 Key 联不通/配额用光时，自动切换到原 DashScope 接口 + 旧 Key；
- answer_llm_light / 路由器原来用 qwen-plus，新接口不支持，因此主用 qwen3.6-flash，
  回退时恢复 qwen-plus（保持“一开始的样子”）。
"""
import time
from typing import List

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from app.config import (
    QWEN_API_KEY, QWEN_BASE_URL,
    QWEN_FALLBACK_API_KEY, QWEN_FALLBACK_BASE_URL,
    DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL,
)


# 进程级回退开关：一旦主 Key 调用失败，所有 Qwen 实例切到原 DashScope。
_QWEN_FALLBACK_ACTIVE = False


class QwenFallbackChatOpenAI(ChatOpenAI):
    """带自动回退的 Qwen ChatOpenAI。

    主配置：token-plan 新接口 + 新 Key。
    备用配置：原 DashScope 接口 + 旧 Key。
    任一次主调用失败后，整个进程内所有 Qwen 实例切到备用配置，
    避免后续请求继续打失效的 Key。
    内部维护主/备用两个真实 ChatOpenAI 客户端，切换时不会复用旧 client 缓存。
    """

    def __init__(self, *, fallback_model: str = None, **kwargs):
        super().__init__(**kwargs)
        self._primary_api_key = kwargs.get("api_key")
        self._primary_base_url = kwargs.get("base_url") or QWEN_BASE_URL
        self._primary_model = kwargs.get("model") or self.model_name
        self._fallback_api_key = QWEN_FALLBACK_API_KEY or self._primary_api_key
        self._fallback_base_url = QWEN_FALLBACK_BASE_URL or self._primary_base_url
        self._fallback_model = fallback_model or self._primary_model

        # 真实请求客户端：主备用分离，避免切换后仍使用旧 client。
        self._primary_client = ChatOpenAI(**kwargs)
        fallback_kwargs = dict(kwargs)
        fallback_kwargs["model"] = self._fallback_model
        fallback_kwargs["api_key"] = self._fallback_api_key
        fallback_kwargs["base_url"] = self._fallback_base_url
        self._fallback_client = ChatOpenAI(**fallback_kwargs)

        # 如果进程内已经触发过回退，新创建的实例也直接使用备用配置。
        if _QWEN_FALLBACK_ACTIVE:
            self._switch_to_fallback()

    def _switch_to_fallback(self) -> None:
        """标记全局回退，并把当前实例的展示字段切为原 DashScope 配置。"""
        global _QWEN_FALLBACK_ACTIVE
        _QWEN_FALLBACK_ACTIVE = True
        self.model_name = self._fallback_model
        self.openai_api_base = self._fallback_base_url
        self.openai_api_key = self._fallback_api_key

    def invoke(self, *args, **kwargs):
        # 已经回退时不再重复请求新接口。
        if _QWEN_FALLBACK_ACTIVE:
            return self._fallback_client.invoke(*args, **kwargs)
        try:
            return self._primary_client.invoke(*args, **kwargs)
        except Exception as e:
            if self._fallback_api_key and self._fallback_api_key != self._primary_api_key:
                print(f"  [QwenFallback] 主 Key/接口调用失败，切换备用 DashScope: {e}")
                self._switch_to_fallback()
                return self._fallback_client.invoke(*args, **kwargs)
            raise

    def stream(self, *args, **kwargs):
        """流式调用同样走主/备自动切换，保证主 Key 失效时流式不因 401 而退化。

        stream() 返回迭代器，实际报错发生在迭代过程中，因此用生成器包装，
        在主接口抛错时切到备用接口重新流式。
        """
        if _QWEN_FALLBACK_ACTIVE:
            yield from self._fallback_client.stream(*args, **kwargs)
            return
        try:
            yield from self._primary_client.stream(*args, **kwargs)
        except Exception as e:
            if self._fallback_api_key and self._fallback_api_key != self._primary_api_key:
                print(f"  [QwenFallback] 流式主 Key/接口调用失败，切换备用 DashScope: {e}")
                self._switch_to_fallback()
                yield from self._fallback_client.stream(*args, **kwargs)
            else:
                raise



# ====== 通用 LLM（默认）======
llm = QwenFallbackChatOpenAI(
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
plan_llm = QwenFallbackChatOpenAI(
    model="qwen3.7-plus",
    api_key=QWEN_API_KEY,
    base_url=QWEN_BASE_URL,
    temperature=0.1,
    max_tokens=4096,
    request_timeout=60,
    # token-plan 模型默认会先思考，L1 快速路径关闭思考保持轻量。
    extra_body={"enable_thinking": False},
)

# ====== Plan L2 专用 LLM（复杂题，plan_agent）：轻度思考，确保多子问题拆解和工具选择准确性 ======
plan_llm_l2 = QwenFallbackChatOpenAI(
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
# 新 token-plan 接口不支持 qwen-plus，主用 qwen3.6-flash；回退时恢复 qwen-plus。
answer_llm_light = QwenFallbackChatOpenAI(
    model="qwen3.6-flash",
    api_key=QWEN_API_KEY,
    base_url=QWEN_BASE_URL,
    temperature=0.2,
    max_tokens=4096,
    request_timeout=60,
    fallback_model="qwen-plus",
    # token-plan 的 qwen3.6-flash 默认也会思考，轻量回答关闭思考。
    extra_body={"enable_thinking": False},
)
# 中等（搜索/世界观/书籍）：轻度思考
answer_llm_medium = QwenFallbackChatOpenAI(
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
answer_llm_deep = QwenFallbackChatOpenAI(
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
# L3 全景/超长文综合：使用更强的 qwen3.8-max，回退到 qwen3.7-max。
answer_llm_l3 = QwenFallbackChatOpenAI(
    model="qwen3.8-max",
    api_key=QWEN_API_KEY,
    base_url=QWEN_BASE_URL,
    temperature=0.2,
    max_tokens=65536,
    request_timeout=240,
    fallback_model="qwen3.7-max",
    model_kwargs={
        "reasoning_effort": "medium",
    },
)

# L3 分段生成专用：qwen3.8-max 关闭思考，单节输出短，避免长文生成超时。
answer_llm_l3_fast = QwenFallbackChatOpenAI(
    model="qwen3.8-max",
    api_key=QWEN_API_KEY,
    base_url=QWEN_BASE_URL,
    temperature=0.3,
    max_tokens=4096,
    request_timeout=240,
    fallback_model="qwen3.7-max",
    extra_body={
        "enable_thinking": False,
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
    model="deepseek-v4-flash-vision-exp",
    api_key=DEEPSEEK_API_KEY,
    base_url=DEEPSEEK_BASE_URL,
    temperature=0,
    max_tokens=100,
    request_timeout=10,
)

# ====== 查询分类器（L1/L2 判断，轻量模型）======
assess_llm = ChatOpenAI(
    model="deepseek-v4-flash-vision-exp",
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
