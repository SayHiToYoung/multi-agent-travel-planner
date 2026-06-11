"""
Agent 基类 —— 所有 Agent 继承此类，统一接口与生命周期。

设计思路:
  - 模板方法模式: run() 定义骨架流程，子类只需实现 execute()
  - 统一日志、错误处理与链路追踪: 在基类埋一次点, 6 个 Agent 全部自动获得 trace
  - 支持 mock / 真实 LLM 两种模式
  - 结构化输出: call_llm_structured() 用 JSON Schema 约束 + Pydantic 校验
    + 错误反馈自修复重试, 保证 LLM 输出可被程序消费
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any, Optional, Type, TypeVar

from loguru import logger
from pydantic import BaseModel, ValidationError

from config.settings import settings
from models.schemas import TravelPlanState
from observability import get_tracer

T = TypeVar("T", bound=BaseModel)


def extract_json(text: str) -> str:
    """从 LLM 输出中提取 JSON: 剥离 ```json 围栏 / 前后闲聊文字。"""
    text = text.strip()
    if "```" in text:
        # 取第一个围栏内的内容
        parts = text.split("```")
        if len(parts) >= 3:
            text = parts[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
    # 截取最外层 {...} 或 [...]
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start, end = text.find(open_ch), text.rfind(close_ch)
        if start != -1 and end > start:
            return text[start : end + 1]
    return text


class BaseAgent(ABC):
    """所有 Agent 的抽象基类。"""

    name: str = "BaseAgent"

    def __init__(self) -> None:
        self._llm_provider = settings.LLM_PROVIDER

    @property
    def is_mock(self) -> bool:
        return self._llm_provider == "mock"

    # ── 模板方法: 子类不要覆盖 ──────────────────────

    async def run(self, state: TravelPlanState) -> TravelPlanState:
        """执行 Agent 的完整生命周期: 追踪 → 执行 → 日志/错误兜底。"""
        tracer = get_tracer()
        async with tracer.span(f"agent:{self.name}", kind="agent") as span:
            logger.info(f"[{self.name}] 开始执行...")
            try:
                state = await self.execute(state)
                logger.info(f"[{self.name}] 执行完成")
            except Exception as exc:
                span.status = "error"
                span.error = f"{type(exc).__name__}: {exc}"
                logger.error(f"[{self.name}] 执行失败: {exc}")
                state.error_messages.append(f"{self.name}: {str(exc)}")
            span.set(state=state.state.value)
        return state

    # ── 子类必须实现 ────────────────────────────

    @abstractmethod
    async def execute(self, state: TravelPlanState) -> TravelPlanState:
        """核心业务逻辑，子类实现。"""
        ...

    # ── LLM 调用辅助 ─────────────────────────────

    async def call_llm(self, prompt: str, system_prompt: str = "") -> str:
        """调用 LLM，支持 mock 和真实两种模式。"""
        if self.is_mock:
            return self._mock_llm(prompt)
        return await self._real_llm(prompt, system_prompt)

    async def call_llm_structured(
        self,
        prompt: str,
        schema: Type[T],
        system_prompt: str = "",
        max_repair: Optional[int] = None,
    ) -> T:
        """调用 LLM 并强制返回符合 Pydantic Schema 的结构化对象。

        可靠性设计:
          1. Prompt 中注入 JSON Schema, 明确约束输出格式
          2. 解析前先 extract_json 容错 (围栏/闲聊文字)
          3. Pydantic 校验失败 → 把错误信息喂回 LLM 自修复, 最多重试 max_repair 次
        """
        if self.is_mock:
            raise RuntimeError("mock 模式不支持结构化 LLM 调用, 请走规则路径")

        max_repair = max_repair if max_repair is not None else settings.LLM_STRUCTURED_MAX_REPAIR
        schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
        full_system = (
            (system_prompt + "\n\n" if system_prompt else "")
            + "你必须只输出一个 JSON 对象, 不要输出任何解释、Markdown 或其他文字。"
            + f"\nJSON 必须严格符合以下 JSON Schema:\n{schema_json}"
        )

        current_prompt = prompt
        last_error: Exception | None = None
        for attempt in range(max_repair + 1):
            raw = await self._real_llm(current_prompt, full_system)
            try:
                result = schema.model_validate_json(extract_json(raw))
                span = get_tracer().current_span
                if span:
                    span.set(structured_repair_rounds=attempt)
                return result
            except (ValidationError, ValueError) as exc:
                last_error = exc
                logger.warning(f"[{self.name}] 结构化输出解析失败 (第 {attempt + 1} 次): {exc}")
                current_prompt = (
                    f"{prompt}\n\n你上一次的输出无法通过校验, 错误信息:\n{exc}\n"
                    "请修正并重新输出严格合法的 JSON。"
                )
        raise ValueError(f"结构化输出在 {max_repair + 1} 次尝试后仍无法解析: {last_error}")

    async def call_llm_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        temperature: Optional[float] = None,
    ) -> dict[str, Any]:
        """带工具定义调用 LLM (OpenAI function calling 协议), 返回 assistant 消息。

        与 call_llm 的区别:
          - 传入完整 messages 历史而非单条 prompt —— Agent 循环需要累积
            assistant(tool_calls) / tool(result) 消息, 这是协议规定的格式
          - 返回原始 message dict (含 content 与 tool_calls), 由调用方决定下一步
            —— "调不调工具、调哪个"是 LLM 的输出, 不在这里解析语义
        """
        if self.is_mock:
            raise RuntimeError("mock 模式不支持工具调用, 请走规则路径")

        import httpx

        payload: dict[str, Any] = {
            "model": settings.LLM_MODEL,
            "messages": messages,
            "tools": tools,
            # 决策型任务用低温度, 提升工具选择的稳定性
            "temperature": temperature if temperature is not None else 0.2,
            "max_tokens": settings.LLM_MAX_TOKENS,
        }
        headers = {
            "Authorization": f"Bearer {settings.LLM_API_KEY}",
            "Content-Type": "application/json",
        }

        tracer = get_tracer()
        async with tracer.span(
            f"llm:{self.name}", kind="generation", model=settings.LLM_MODEL, with_tools=True
        ) as span:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"{settings.LLM_BASE_URL}/chat/completions",
                    json=payload,
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
                usage = data.get("usage") or {}
                message = data["choices"][0]["message"]
                span.set(
                    prompt_tokens=usage.get("prompt_tokens"),
                    completion_tokens=usage.get("completion_tokens"),
                    total_tokens=usage.get("total_tokens"),
                    tool_calls=[
                        tc["function"]["name"] for tc in (message.get("tool_calls") or [])
                    ],
                )
                return message

    def _mock_llm(self, prompt: str) -> str:
        """Mock LLM，返回固定结构化响应，用于零成本演示。"""
        return json.dumps({"response": f"[MOCK] {self.name} processed the request."})

    async def _real_llm(self, prompt: str, system_prompt: str = "") -> str:
        """调用真实 LLM API（MiniMax M2.7 / OpenAI 兼容接口），并记录 generation span。"""
        import httpx

        headers = {
            "Authorization": f"Bearer {settings.LLM_API_KEY}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": settings.LLM_MODEL,
            "messages": [],
            "temperature": settings.LLM_TEMPERATURE,
            "max_tokens": settings.LLM_MAX_TOKENS,
        }
        if system_prompt:
            payload["messages"].append({"role": "system", "content": system_prompt})
        payload["messages"].append({"role": "user", "content": prompt})

        tracer = get_tracer()
        async with tracer.span(f"llm:{self.name}", kind="generation", model=settings.LLM_MODEL) as span:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"{settings.LLM_BASE_URL}/chat/completions",
                    json=payload,
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
                usage = data.get("usage") or {}
                span.set(
                    prompt_tokens=usage.get("prompt_tokens"),
                    completion_tokens=usage.get("completion_tokens"),
                    total_tokens=usage.get("total_tokens"),
                )
                return data["choices"][0]["message"]["content"]
