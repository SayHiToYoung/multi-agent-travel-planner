"""
事件模型 —— 前端实时展示用的结构化事件信封。

设计要点:
  - 事件只包含"执行事实": 状态、决策摘要、耗时、结果
  - 绝不包含: prompt 原文、密钥、模型思维链、原始异常堆栈
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field

# 支持的事件类型 (与前端 renderer 的约定)
EVENT_TYPES = (
    "pipeline_started",   # 请求已受理, pipeline 启动
    "agent_started",      # 某个 Agent 开始执行
    "agent_completed",    # 某个 Agent 成功完成
    "agent_failed",       # 某个 Agent 失败/降级
    "rag_result",         # RAG 检索命中摘要
    "tool_called",        # ReplanAgent 工具调用摘要
    "budget_adjusted",    # 预算校验/调整结果
    "summary_delta",      # 面向用户的行程总结增量文本
    "plan_completed",     # 最终完整结果
    "error",              # 用户可读的致命错误
    "stream_closed",      # 显式终止事件
)


class Event(BaseModel):
    type: str
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    agent: Optional[str] = None
    message: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
