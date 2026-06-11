"""
Span 导出器实现。

  - JsonlExporter:    零依赖, 每个 trace 写一个 JSONL 文件, 本地即可分析
  - LangfuseExporter: 可选, 配置 LANGFUSE_PUBLIC_KEY/SECRET_KEY 后自动启用,
                      在 Langfuse 控制台可视化整条 Agent 调用链与 token 消耗

设计要点:
  - 为什么导出器要 best-effort (吞掉异常)? —— 可观测性组件绝不能拖垮业务主链路
  - Langfuse 的 trace/span/generation 三级模型与本地 Span.kind 的映射关系
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from .tracer import Span


class JsonlExporter:
    """把 span 追加写入 traces/<trace_id>.jsonl, 一行一个 span。"""

    def __init__(self, directory: str) -> None:
        self._dir = Path(directory)

    def export(self, span: Span) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / f"{span.trace_id}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(span.to_dict(), ensure_ascii=False, default=str) + "\n")


class LangfuseExporter:
    """把 span 上报到 Langfuse (使用 v2 SDK 的无状态低层 API)。"""

    def __init__(self, public_key: str, secret_key: str, host: str) -> None:
        from langfuse import Langfuse  # 延迟导入: 未安装时整个模块仍可用

        self._client = Langfuse(public_key=public_key, secret_key=secret_key, host=host)

    def export(self, span: Span) -> None:
        common = dict(
            id=span.span_id,
            trace_id=span.trace_id,
            name=span.name,
            start_time=datetime.fromtimestamp(span.start_time, tz=timezone.utc),
            end_time=datetime.fromtimestamp(span.end_time or span.start_time, tz=timezone.utc),
            metadata=span.attributes,
            level="ERROR" if span.status == "error" else "DEFAULT",
            status_message=span.error,
        )
        if span.parent_id:
            common["parent_observation_id"] = span.parent_id

        if span.kind == "generation":
            usage = {
                "input": span.attributes.get("prompt_tokens"),
                "output": span.attributes.get("completion_tokens"),
            }
            self._client.generation(
                **common,
                model=span.attributes.get("model"),
                usage=usage if any(v is not None for v in usage.values()) else None,
            )
        else:
            self._client.span(**common)

        # 根 span 结束 = 一次规划完成, 补充 trace 元信息并立即上报
        if span.parent_id is None:
            self._client.trace(id=span.trace_id, name=span.name, metadata=span.attributes)
            self._client.flush()


def build_langfuse_exporter(public_key: str, secret_key: str, host: str):
    """工厂: 未安装 langfuse 包时返回 None 并告警, 不影响主流程。"""
    try:
        return LangfuseExporter(public_key, secret_key, host)
    except ImportError:
        logger.warning("[Observability] 已配置 Langfuse 密钥但未安装 langfuse 包, "
                       "请执行: pip install 'langfuse>=2.39,<3'")
        return None
