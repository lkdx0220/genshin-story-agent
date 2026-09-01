# -*- coding: utf-8 -*-
"""可选的 Trace 事件记录器（默认关闭）。

作用：
- 不改变 Agent 原有行为；
- 当外部调用 enable_trace(sink) 时，把 Agent 关键节点事件以结构化 dict 推给 sink；
- 供 Agent Trace Inspector 等外部观测工具消费。

事件格式：
{
    "event": "tool_start",
    "timestamp": 1234567890.123,
    "data": {...}
}
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Dict, Optional

_trace_lock = threading.Lock()
_trace_sink: Optional[Callable[[Dict], None]] = None
_trace_enabled: bool = False


def enable_trace(sink: Callable[[Dict], None]) -> None:
    """开启 Trace 记录。sink 回调接收一个 dict 事件。"""
    global _trace_enabled, _trace_sink
    with _trace_lock:
        _trace_sink = sink
        _trace_enabled = True


def disable_trace() -> None:
    global _trace_enabled, _trace_sink
    with _trace_lock:
        _trace_enabled = False
        _trace_sink = None


def is_trace_enabled() -> bool:
    return _trace_enabled


def emit(event: str, data: Optional[Dict] = None) -> None:
    """发送一个 Trace 事件；未启用时零开销。"""
    if not _trace_enabled:
        return
    payload = {
        "event": event,
        "timestamp": time.time(),
        "data": data or {},
    }
    try:
        with _trace_lock:
            sink = _trace_sink
        if sink is not None:
            sink(payload)
    except Exception:
        # Trace 记录器绝不能影响 Agent 正常运行
        pass
