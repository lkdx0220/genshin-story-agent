# -*- coding: utf-8 -*-
"""进度事件钩子：供工具执行器和长任务工具向 Web 前端推送进度。

进度钩子按线程隔离（gunicorn 多线程下每个请求各持一份，避免并发串台）；
_cancel_events 由 tool_executor/agent 节点共享。
放在独立模块避免 app.tools 和 app.agent 之间循环导入。
"""
import logging
import threading
from typing import Dict

# 进度钩子异常只记日志、不向上抛：进度属非关键遥测，不能因推送失败中断回答。
_logger = logging.getLogger(__name__)


# 进度回调钩子（thread-local；CLI 模式或未注入的线程保持未设置）
_tls = threading.local()

# 取消信号字典：run_id → threading.Event（线程安全，避免模块级变量竞态）
_cancel_events: Dict[str, threading.Event] = {}


def _emit_progress(event_type: str, data: dict):
    """向 Web 前端推送进度事件。当前线程未注入钩子时静默忽略。

    事件类型由代码强制写入并覆盖 payload 同名键，避免调用方数据篡改事件类型。
    """
    hook = getattr(_tls, "hook", None)
    if hook:
        try:
            hook({**data, "type": event_type})
        except Exception as e:
            # 只记异常类型，不落异常文本（可能夹带内部数据）
            _logger.warning("进度钩子回调异常（已忽略）: %s", type(e).__name__)


def set_progress_hook(hook):
    """注入进度回调（由 Web API 调用；仅对当前线程生效）。"""
    _tls.hook = hook


def get_progress_hook():
    """获取当前线程的进度回调（供测试或诊断用）。"""
    return getattr(_tls, "hook", None)
