"""
轻量级链路追踪 —— 为多 Agent 系统提供可观测性。

设计要点:
  - Span 树模型: 一次规划 = 一个 trace, trace 内的每个 Agent/LLM 调用 = 一个 span,
    通过 parent_id 构成调用树 —— 与 OpenTelemetry / Langfuse 的概念一一对应
  - contextvars 维护"当前 Span": asyncio 创建子任务时自动拷贝上下文,
    因此三个并行 Agent 各自的 span 能正确挂到同一个父节点下, 互不串扰
  - 导出器 (Exporter) 模式: 本地 JSONL 始终可用 (零依赖),
    Langfuse 等云端平台按需插拔 —— 业务代码不感知导出目标
"""

from __future__ import annotations

import contextvars
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional, Protocol

from loguru import logger

_current_span: contextvars.ContextVar[Optional["Span"]] = contextvars.ContextVar(
    "current_span", default=None
)


def _new_id() -> str:
    return uuid.uuid4().hex[:16]


@dataclass
class Span:
    """一次操作的追踪记录 (Agent 执行 / LLM 调用 / 检索等)。"""

    name: str
    kind: str = "internal"  # internal / agent / generation / retrieval
    trace_id: str = ""
    span_id: str = field(default_factory=_new_id)
    parent_id: Optional[str] = None
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    status: str = "ok"  # ok / error
    error: Optional[str] = None
    attributes: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_ms(self) -> float:
        end = self.end_time if self.end_time is not None else time.time()
        return (end - self.start_time) * 1000

    def set(self, **attrs: Any) -> None:
        """追加属性 (token 数、模型名、检索命中等)。"""
        self.attributes.update(attrs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_id": self.parent_id,
            "name": self.name,
            "kind": self.kind,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": round(self.duration_ms, 2),
            "status": self.status,
            "error": self.error,
            "attributes": self.attributes,
        }


class SpanExporter(Protocol):
    """导出器接口: span 结束时被调用一次。"""

    def export(self, span: Span) -> None: ...


class Tracer:
    """管理 span 生命周期, 并将完成的 span 分发给所有导出器。"""

    def __init__(self) -> None:
        self._exporters: list[SpanExporter] = []

    def add_exporter(self, exporter: SpanExporter) -> None:
        self._exporters.append(exporter)

    def remove_exporter(self, exporter: SpanExporter) -> None:
        if exporter in self._exporters:
            self._exporters.remove(exporter)

    @property
    def current_span(self) -> Optional[Span]:
        return _current_span.get()

    @asynccontextmanager
    async def span(self, name: str, kind: str = "internal", **attributes: Any) -> AsyncIterator[Span]:
        """开启一个 span; 无父节点时自动成为新 trace 的根。"""
        parent = _current_span.get()
        span = Span(
            name=name,
            kind=kind,
            trace_id=parent.trace_id if parent else _new_id(),
            parent_id=parent.span_id if parent else None,
            attributes=dict(attributes),
        )
        token = _current_span.set(span)
        try:
            yield span
        except Exception as exc:
            span.status = "error"
            span.error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            span.end_time = time.time()
            _current_span.reset(token)
            self._export(span)

    def _export(self, span: Span) -> None:
        for exporter in self._exporters:
            try:
                exporter.export(span)
            except Exception as exc:
                logger.warning(f"[Tracer] 导出器 {type(exporter).__name__} 失败: {exc}")
