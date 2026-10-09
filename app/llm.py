# -*- coding: utf-8 -*-
"""LLM 实例池：按用途分级配置，统一重试封装。

分级设计（参照 DeepSeek/通义千问的多模型协同思路）：
- llm: 通用 LLM（qwen3.7-max, medium reasoning）
- plan_llm: L1 快速规划（qwen3.7-plus, 无 reasoning，省时，兼顾工具调用）
- plan_llm_l2: L2 复杂规划（qwen3.7-max, low reasoning）
- answer_llm_light/medium/deep: 按意图分级回答
- alias_judge_llm: 别名判断沙箱（deepseek-flash, 20 token 输出）
- assess_llm: L1/L2 路径分类器（deepseek-flash, 50 token 输出）

Qwen 实例统一使用 QwenFallbackChatOpenAI：
- 默认走 token-plan 新接口 + 新 Key；
- 新 Key 联不通/配额用光时，自动切换到原 DashScope 接口 + 旧 Key；
- answer_llm_light / 路由器原来用 qwen-plus，新接口不支持，因此主用 qwen3.6-flash，
  回退时恢复 qwen-plus（保持“一开始的样子”）。
"""
import logging
import threading
import time

from langchain_openai import ChatOpenAI

from app.config import (
    QWEN_API_KEY, QWEN_BASE_URL,
    QWEN_FALLBACK_API_KEY, QWEN_FALLBACK_BASE_URL,
    DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL,
)


# 进程级回退开关：一旦主 Key 调用失败，所有 Qwen 实例切到原 DashScope。
# 单向设计（不自动切回）是刻意选择：避免网关抖动时来回打两边、配额不可控；
# 需要回到主网关时重启进程即可（开关随进程重置）。
_QWEN_FALLBACK_ACTIVE = False
_QWEN_FALLBACK_LOCK = threading.RLock()
# 全局降级需要"连续 N 次"基础设施类失败：单次瞬时限流/超时只走退避重试，不降级整进程。
_QWEN_SWITCH_THRESHOLD = 2
_QWEN_SWITCH_STRIKES = 0

# 主备切换判定：请求本身有问题（确定性 4xx）换网关也没用，不触发全局降级；
# 其余（认证/配额/超时/连接/服务端错误、以及无法识别的异常）都按可切换处理，保住韧性。
_NON_SWITCHABLE_STATUS = frozenset({400, 404, 405, 413, 415, 422})
_NON_SWITCHABLE_HINTS = (
    "bad request", "invalid_request", "invalid request", "content_filter",
    "content filter", "context length", "maximum context", "length_finish",
    "unprocessable", "validation error",
)
# 可重试判定：限流/超时/连接/服务端错误可重试；确定性 4xx 立即失败，不空等。
_RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
# 文本级可重试信号：给不带状态码、也非 SDK 异常对象的第三方/httpx 错误兜底
_RETRYABLE_HINTS = (
    "rate", "throttle", "429", "timeout", "timed out", "connection",
    "unavailable", "overloaded", "bad gateway", "service unavailable",
)
# 主备切换白名单：只认"基础设施类"错误，未知异常不触发全局降级（防止畸形请求诱导降级）。
_SWITCHABLE_STATUS = frozenset({401, 403, 408, 409, 425, 429, 500, 502, 503, 504})
_SWITCHABLE_HINTS = (
    "rate", "throttle", "429", "401", "403", "unauthorized", "forbidden",
    "timeout", "timed out", "connection", "unavailable", "overloaded",
    "bad gateway", "internal server", "service unavailable",
)
_SWITCHABLE_TYPES = (
    "ratelimiterror", "authenticationerror", "permissiondeniederror",
    "apitimeouterror", "apiconnectionerror", "internalservererror",
    "serviceunavailableerror", "connecterror", "connecttimeout",
    "readtimeout", "remoteprotocolerror",
)
logger = logging.getLogger(__name__)


def _status_code(error: Exception):
    """取 HTTP 状态码：优先 openai SDK 的 status_code / response.status_code。"""
    for candidate in (
        getattr(error, "status_code", None),
        getattr(getattr(error, "response", None), "status_code", None),
    ):
        try:
            if candidate is not None:
                return int(candidate)
        except (TypeError, ValueError):
            continue
    return None


def _error_text(error: Exception) -> str:
    return f"{type(error).__name__} {error}".lower()


def _error_summary(error: Exception) -> str:
    """日志用的错误摘要：只保留类型与状态码，绝不带供应方原文。

    供应方报错体可能回显 Authorization 头、Key 片段或请求负载，正则脱敏总有绕过形态；
    这里干脆不记录原文。原文仍随异常向上抛出，排障与审计不受影响。
    """
    code = _status_code(error)
    return f"{type(error).__name__}(status={code})" if code is not None else type(error).__name__


def _should_switch(error: Exception) -> bool:
    """是否应切换主备：只认基础设施类错误，未知异常不触发全局降级。"""
    code = _status_code(error)
    if code is not None:
        return code in _SWITCHABLE_STATUS
    text = _error_text(error)
    return any(hint in text for hint in _SWITCHABLE_HINTS) or any(
        name in text for name in _SWITCHABLE_TYPES
    )


def _should_retry(error: Exception, attempt: int = 0) -> bool:
    """是否值得重试：限流/超时/连接/服务端错误重试；确定性 4xx 立即失败。

    完全无法识别的异常（既无状态码、也不匹配已知基础设施类）只重试一次，
    避免把工作线程长时间钉在 sleep 上。
    """
    code = _status_code(error)
    if code is not None:
        return code in _RETRYABLE_STATUS
    text = _error_text(error)
    if any(hint in text for hint in _NON_SWITCHABLE_HINTS):
        return False  # 请求本身有问题，重试无用
    if any(name in text for name in _SWITCHABLE_TYPES):
        return True  # 已知基础设施类异常（结构化类名）→ 按原策略重试
    if any(hint in text for hint in _RETRYABLE_HINTS):
        return True  # 文本可识别的限流/超时/连接类错误 → 按原策略重试（线性退避）
    return attempt == 0  # 完全无法识别的异常：只给一次重试机会，避免长时间占用线程


class QwenFallbackChatOpenAI(ChatOpenAI):
    """带自动回退的 Qwen ChatOpenAI。

    主配置：token-plan 新接口 + 新 Key。
    备用配置：原 DashScope 接口 + 旧 Key。
    任一次主调用失败后，整个进程内所有 Qwen 实例切到备用配置，
    避免后续请求继续打失效的 Key。
    内部维护主/备用两个真实 ChatOpenAI 客户端，切换时不会复用旧 client 缓存。

    切换边界（2026-10 安全审查加固）：只对认证/配额/超时/连接/服务端错误切换；
    "请求本身有问题"的确定性错误（400/404/422、内容过滤、超长上下文）不触发全局降级，
    避免外力诱发一次 400 就把整个进程切到备用模型。流式只有"尚未产出任何片段"时才允许
    切备用重流，已产出的片段无法撤回。切换过程加锁且幂等，不再改写模型的密钥/网关公开属性。
    """

    def __init__(self, *, fallback_model: str = None, **kwargs):
        super().__init__(**kwargs)
        primary_api_key = kwargs.get("api_key")
        primary_base_url = kwargs.get("base_url") or QWEN_BASE_URL
        fallback_api_key = QWEN_FALLBACK_API_KEY or primary_api_key
        fallback_base_url = QWEN_FALLBACK_BASE_URL or primary_base_url
        self._fallback_model = fallback_model or kwargs.get("model") or self.model_name

        # 密钥只在构造期以局部变量流转：本类不再额外驻留密钥属性（密钥由底层
        # ChatOpenAI 客户端以 SecretStr 持有，这是发请求的必要条件），
        # 从而避免本对象的 __dict__/repr/序列化带出密钥。
        self._fallback_available = bool(fallback_api_key) and fallback_api_key != primary_api_key

        # 真实请求客户端：主备用分离，避免切换后仍使用旧 client。
        self._primary_client = ChatOpenAI(**kwargs)
        if self._fallback_available:
            fallback_kwargs = dict(kwargs)
            fallback_kwargs["model"] = self._fallback_model
            fallback_kwargs["api_key"] = fallback_api_key
            fallback_kwargs["base_url"] = fallback_base_url
            self._fallback_client = ChatOpenAI(**fallback_kwargs)
        else:
            # 没有独立备用 Key 时不留备用客户端（避免用主 Key 再建一个同配置对象）。
            self._fallback_client = self._primary_client

        # 如果进程内已经触发过回退，新创建的实例也直接使用备用配置。
        if _QWEN_FALLBACK_ACTIVE:
            self._switch_to_fallback()

    def _fallback_ready(self) -> bool:
        """备用配置是否可用：构造期已确认存在独立于主 Key 的备用 Key。"""
        return self._fallback_available

    def _switch_to_fallback(self) -> None:
        """标记全局回退（加锁、幂等），并更新用于展示的模型名。

        不再改写 openai_api_key / openai_api_base：真实请求由 _fallback_client 承载，
        把密钥写进模型对象的公开属性既没必要，也会让并发读者看到"写一半"的字段状态。
        """
        global _QWEN_FALLBACK_ACTIVE
        with _QWEN_FALLBACK_LOCK:
            _QWEN_FALLBACK_ACTIVE = True
            self.model_name = self._fallback_model

    def _note_primary_failure(self, error: Exception) -> bool:
        """记一次主网关失败；连续失败达到阈值才真正全局降级。返回是否已切换。"""
        global _QWEN_SWITCH_STRIKES
        with _QWEN_FALLBACK_LOCK:
            if _QWEN_FALLBACK_ACTIVE:
                return True
            _QWEN_SWITCH_STRIKES += 1
            if _QWEN_SWITCH_STRIKES < _QWEN_SWITCH_THRESHOLD:
                return False
            self._switch_to_fallback()
        return True

    def _note_primary_success(self) -> None:
        """主网关恢复正常：清零连续失败计数（避免偶发错误累积成降级）。"""
        global _QWEN_SWITCH_STRIKES
        if _QWEN_SWITCH_STRIKES:
            with _QWEN_FALLBACK_LOCK:
                _QWEN_SWITCH_STRIKES = 0

    def invoke(self, *args, **kwargs):
        # 已经回退时不再重复请求新接口。
        if _QWEN_FALLBACK_ACTIVE:
            return self._fallback_client.invoke(*args, **kwargs)
        try:
            result = self._primary_client.invoke(*args, **kwargs)
        except Exception as error:
            if not self._fallback_ready() or not _should_switch(error) or not self._note_primary_failure(error):
                raise
            logger.warning("[QwenFallback] 主网关连续失败，切换备用 DashScope：%s", _error_summary(error))
            return self._fallback_client.invoke(*args, **kwargs)
        self._note_primary_success()
        return result

    def stream(self, *args, **kwargs):
        """流式调用同样走主/备自动切换，保证主 Key 失效时流式不因 401 而退化。

        stream() 返回迭代器，实际报错发生在迭代过程中，因此用生成器包装。
        只有"尚未产出任何片段"时才允许切备用重流：已经 yield 出去的片段无法撤回，
        重流会让用户看到重复或前后拼接的内容，此时直接抛错。
        """
        if _QWEN_FALLBACK_ACTIVE:
            yield from self._fallback_client.stream(*args, **kwargs)
            return
        emitted = False
        try:
            for chunk in self._primary_client.stream(*args, **kwargs):
                if not emitted:
                    emitted = True
                    self._note_primary_success()
                yield chunk
        except Exception as error:
            if emitted or not self._fallback_ready() or not _should_switch(error) or not self._note_primary_failure(error):
                raise
            logger.warning("[QwenFallback] 流式主网关连续失败，切换备用 DashScope：%s", _error_summary(error))
            yield from self._fallback_client.stream(*args, **kwargs)



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

# 机制名相关性裁判：只输出“是/否”，qwen3.6-flash 关闭思考；
# 非法输出由调用方按“否”处理，避免无关机制名被注入 Answer 提示词。
mechanism_judge_llm = QwenFallbackChatOpenAI(
    model="qwen3.6-flash",
    api_key=QWEN_API_KEY,
    base_url=QWEN_BASE_URL,
    temperature=0,
    max_tokens=8,
    request_timeout=30,
    fallback_model="qwen-plus",
    extra_body={"enable_thinking": False},
)

# 指代消解专用：只做"把指代词换成实体"的短任务，关思考、限输出长度。
coref_llm = QwenFallbackChatOpenAI(
    model="qwen3.6-flash",
    api_key=QWEN_API_KEY,
    base_url=QWEN_BASE_URL,
    temperature=0,
    max_tokens=200,
    request_timeout=30,
    fallback_model="qwen-plus",
    extra_body={"enable_thinking": False},
)

# 意图 → Answer LLM 档位：取最高优先级的意图
# 这里只存"档位名"，取用时现查模块全局。不要改成直接存 LLM 实例：
# 字典按值捕获实例后，任何"重绑 answer_llm_*"的配置覆盖（临时换模型、灰度切换）
# 都传导不进来，会出现"换了模型但路由仍走旧实例"的静默污染。
_INTENT_ANSWER_LEVEL = {
    "D": "deep",      # 剧情任务
    "F": "deep",      # 溯源追踪
    "C2": "deep",     # 世界观设定
    "E": "medium",    # 书籍文献
    "A": "medium",    # 搜索检索
    "B": "light",     # 角色查询
}

_ANSWER_INTENT_PRIORITY = ("D", "F", "C2", "E", "A", "B")


def _answer_llm_for_level(level: str) -> ChatOpenAI:
    """按档位名取 Answer LLM：显式分支，不用 globals() 动态拼名字。"""
    if level == "deep":
        return answer_llm_deep
    if level == "medium":
        return answer_llm_medium
    return answer_llm_light


def _select_answer_llm(intent_labels: list) -> ChatOpenAI:
    """根据意图标签选择最合适的 Answer LLM。
    多个意图取最高优先级（Deep > Medium > Light）。
    每次调用现查模块全局，保证外部重绑 answer_llm_* 立即生效。"""
    if not intent_labels:
        return answer_llm_deep  # 未知意图用深度
    # 按优先级查找：先找 D/F/C2，再找 E/A，最后 B
    for priority_key in _ANSWER_INTENT_PRIORITY:
        if priority_key in intent_labels:
            return _answer_llm_for_level(_INTENT_ANSWER_LEVEL[priority_key])
    return answer_llm_deep


# ====== DeepSeek 别名判断（安全沙箱，不计入 agent 轮次）======
alias_judge_llm = ChatOpenAI(
    model="deepseek-flash",
    api_key=DEEPSEEK_API_KEY,
    base_url=DEEPSEEK_BASE_URL,
    temperature=0,
    max_tokens=100,
    request_timeout=10,
)

# ====== 查询分类器（L1/L2 判断，轻量模型）======
assess_llm = ChatOpenAI(
    model="deepseek-flash",
    api_key=DEEPSEEK_API_KEY,
    base_url=DEEPSEEK_BASE_URL,
    temperature=0,
    max_tokens=50,
    request_timeout=10,
)


def _retry_wait_seconds(error: Exception, attempt: int):
    """返回 (等待秒数, 是否限流)：限流线性退避 5s/10s/15s，其他异常固定 2s。"""
    text = str(error).lower()
    is_rate_limited = "rate" in text or "throttle" in text or "429" in text
    return ((attempt + 1) * 5, True) if is_rate_limited else (2, False)


def llm_invoke_with_retry(messages, max_retries=3, llm_instance=None):
    """带重试的 LLM 调用，处理 API 限流。

    只对可重试错误（限流/超时/连接/服务端错误）重试；确定性 4xx（400/404/422 等）
    立即抛出，不浪费等待时间。退避策略：限流线性 5s/10s/15s，其他可重试错误固定 2s。
    最后一次尝试失败时直接抛出原异常，绝不静默返回 None：调用方要么拿到响应，
    要么拿到异常（交接给主备切换或上层错误处理）。
    llm_instance: 指定 LLM 实例，不传则用默认 llm。"""
    if max_retries < 1:
        raise ValueError("max_retries 必须 >= 1")
    target_llm = llm_instance if llm_instance is not None else llm
    for attempt in range(max_retries):
        try:
            return target_llm.invoke(messages)
        except Exception as error:
            if attempt == max_retries - 1 or not _should_retry(error, attempt):
                raise
            wait, rate_limited = _retry_wait_seconds(error, attempt)
            tag = "[限流]" if rate_limited else "[重试]"
            logger.warning("%s 等待 %ss 后重试（第 %s/%s 次失败：%s）", tag, wait, attempt + 1, max_retries, _error_summary(error))
            time.sleep(wait)
