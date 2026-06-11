"""可观测性模块 —— get_tracer() 返回按 settings 配置好导出器的全局单例。"""

from __future__ import annotations

from .tracer import Span, Tracer

_tracer: Tracer | None = None


def get_tracer() -> Tracer:
    global _tracer
    if _tracer is None:
        from config.settings import settings

        from .exporters import JsonlExporter, build_langfuse_exporter

        _tracer = Tracer()
        if settings.TRACE_ENABLED:
            _tracer.add_exporter(JsonlExporter(settings.TRACE_DIR))
        if settings.LANGFUSE_PUBLIC_KEY and settings.LANGFUSE_SECRET_KEY:
            exporter = build_langfuse_exporter(
                settings.LANGFUSE_PUBLIC_KEY,
                settings.LANGFUSE_SECRET_KEY,
                settings.LANGFUSE_HOST,
            )
            if exporter:
                _tracer.add_exporter(exporter)
    return _tracer


__all__ = ["Span", "Tracer", "get_tracer"]
