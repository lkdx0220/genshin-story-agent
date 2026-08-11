# -*- coding: utf-8 -*-
"""进度事件钩子：供工具执行器和长任务工具向 Web 前端推送进度。

_progress_hook 由 Web API 注入；_cancel_events 由 tool_executor/agent 节点共享。
放在独立模块避免 app.tools 和 app.agent 之间循环导入。
"""
import threading
from typing import Dict


# 进度回调钩子（由 Web API 注入；CLI 模式下保持 None）
_progress_hook = None

# 取消信号字典：run_id → threading.Event（线程安全，避免模块级变量竞态）
_cancel_events: Dict[str, threading.Event] = {}


def _emit_progress(event_type: str, data: dict):
    """向 Web 前端推送进度事件。_progress_hook 未注入时静默忽略。"""
    if _progress_hook:
        try:
            _progress_hook({"type": event_type, **data})
        except Exception:
            pass


def set_progress_hook(hook):
    """注入进度回调（由 Web API 调用）。"""
    global _progress_hook
    _progress_hook = hook


def get_progress_hook():
    """获取当前进度回调（供测试或诊断用）。"""
    return _progress_hook
