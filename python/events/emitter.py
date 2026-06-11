"""
事件发射器 —— 业务代码与 HTTP/SSE 解耦的桥。

设计要点 (与 observability.tracer 同一模式):
  - contextvars 保存"当前请求的发射器", 默认 no-op:
    CLI / 普通 API / 测试调用时业务代码发事件 = 空操作, 零影响
  - SSE 端点为每个请求创建独立的 asyncio.Queue 发射器:
    请求间天然隔离, 无全局状态, 客户端断开即随任务取消
  - 发射永远 best-effort: 事件系统绝不能拖垮业务主链路
"""

from __future__ import annotations

import contextvars
from typing import Any, Optional, Protocol

from loguru import logger

from .models import Event


class EventEmitter(Protocol):
    def emit(self, event: Event) -> None: ...


class NoopEmitter:
    def emit(self, event: Event) -> None:
        pass


class QueueEmitter:
    """把事件放进 asyncio.Queue, 由 SSE 响应生成器消费。"""

    def __init__(self, queue) -> None:
        self._queue = queue

    def emit(self, event: Event) -> None:
        self._queue.put_nowait(event)


_NOOP = NoopEmitter()
_current: contextvars.ContextVar[EventEmitter] = contextvars.ContextVar(
    "event_emitter", default=_NOOP
)


def set_emitter(emitter: EventEmitter) -> contextvars.Token:
    return _current.set(emitter)


def reset_emitter(token: contextvars.Token) -> None:
    _current.reset(token)


def emit(
    type: str,
    agent: Optional[str] = None,
    message: str = "",
    data: Optional[dict[str, Any]] = None,
) -> None:
    """业务代码的唯一入口: 一行发事件, 不感知消费者。"""
    try:
        _current.get().emit(Event(type=type, agent=agent, message=message, data=data or {}))
    except Exception as exc:
        logger.warning(f"[Events] 事件发射失败 (已忽略): {exc}")
