"""
Replan Agent —— 超预算时由 LLM 自主决策调整方案的真·Agent。

与项目其余部分 (Workflow) 的本质区别:
  - Workflow: 砍谁/砍多少/何时停, 由代码的 if/else 决定 (BudgetAgent 规则降级)
  - Agent:    LLM 在工具调用循环中自主决定 —— 调哪个工具、传什么约束参数、
              何时调用 finalize_plan 结束, 每次运行的决策路径都可能不同

代码保留的三项权力 (Agent 安全栏, 必讲):
  1. 步数上限 REPLAN_MAX_STEPS —— 防失控循环
  2. 预算由 compute_breakdown() 计算 —— LLM 负责决策, 代码负责算术
  3. 终态由代码验收 —— 循环怎么结束都重算一次预算, 超支则附警告,
     永远返回可用方案而不是失败
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from config.settings import settings
from models.schemas import BudgetBreakdown, PlanningState, TravelPlanState
from observability import get_tracer

from .base_agent import BaseAgent
from .budget_agent import compute_breakdown

# OpenAI function calling 协议的工具定义。
# 参数 Schema 就是 LLM 的"决策表达空间": 它砍谁、砍到什么程度, 全在参数里。
REPLAN_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_flights",
            "description": "重新搜索往返航班。可设置单程票价上限或允许更多中转来换取低价。",
            "parameters": {
                "type": "object",
                "properties": {
                    "max_price": {"type": "number", "description": "单程票价上限（人民币元/人）"},
                    "max_stops": {"type": "integer", "description": "可接受的最大中转次数 (0=仅直飞)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_hotels",
            "description": "重新搜索酒店。可设置每晚价格上限和最低星级，在满足约束的酒店中自动选口碑最好的。",
            "parameters": {
                "type": "object",
                "properties": {
                    "max_price_per_night": {"type": "number", "description": "每晚价格上限（人民币元/间）"},
                    "min_star": {"type": "number", "description": "可接受的最低星级 (1-5)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_activities",
            "description": "重新安排每日活动。设置每人每天的活动预算上限，超出的活动会被删减。",
            "parameters": {
                "type": "object",
                "properties": {
                    "daily_budget_per_person": {"type": "number", "description": "每人每天活动预算上限（人民币元）"},
                },
                "required": ["daily_budget_per_person"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finalize_plan",
            "description": "接受当前方案并结束调整。当总费用已在预算内、或你判断继续调整只会损害体验时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "description": "做出此决定的简要理由"},
                },
                "required": ["reason"],
            },
        },
    },
]

_SYSTEM_PROMPT = """你是旅行预算优化专家。当前行程超出了用户预算，你的任务是通过调用工具调整方案，\
把总费用降到预算内，同时尽量减少旅行体验的损失。

规则:
1. 每次工具调用后，你会收到执行结果和由系统计算的最新预算明细（以系统计算为准，不要自己估算）。
2. 优先调整对体验影响小、且超支贡献大的项目；用约束参数精确表达你的意图。
3. 不要重复执行参数几乎相同的调用；如果工具提示约束无法满足，请放宽参数或换一个方向。
4. 当总费用已在预算内，或你判断继续调整得不偿失时，调用 finalize_plan 结束。"""


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


class ReplanAgent(BaseAgent):
    name = "ReplanAgent"

    def __init__(self, max_steps: int | None = None) -> None:
        super().__init__()
        self.max_steps = max_steps or settings.REPLAN_MAX_STEPS

    async def execute(self, state: TravelPlanState) -> TravelPlanState:
        breakdown = compute_breakdown(state)
        state.budget_breakdown = breakdown
        if breakdown.is_within_budget:
            state.state = PlanningState.COMPLETED
            return state

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": self._initial_situation(state, breakdown)},
        ]

        tracer = get_tracer()
        steps_used = 0
        finalized = False

        for step in range(1, self.max_steps + 1):
            message = await self.call_llm_with_tools(messages, REPLAN_TOOLS)
            tool_calls = message.get("tool_calls") or []

            assistant_msg: dict[str, Any] = {"role": "assistant", "content": message.get("content") or ""}
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls
            messages.append(assistant_msg)

            if not tool_calls:
                # 软终止: 模型违反协议输出了纯文本, 视为放弃调整
                logger.warning(f"[{self.name}] 模型未调用工具, 软终止: {message.get('content')!r:.200}")
                break

            steps_used = step
            state.adjustment_round = step

            for call in tool_calls:
                tool_name = call["function"]["name"]
                async with tracer.span(f"tool:{tool_name}", kind="tool") as span:
                    try:
                        args = json.loads(call["function"].get("arguments") or "{}")
                    except json.JSONDecodeError as exc:
                        args = {}
                        result = f"参数解析失败: {exc}。请重新调用并提供合法 JSON 参数。"
                    else:
                        if tool_name == "finalize_plan":
                            finalized = True
                            result = f"已接受当前方案。理由: {args.get('reason', '')}"
                        else:
                            result = self._execute_tool(tool_name, args, state)
                            breakdown = compute_breakdown(state)  # 算术归代码
                            state.budget_breakdown = breakdown
                            result += "\n" + self._budget_line(breakdown)
                    span.set(arguments=args, result=result)
                logger.info(f"[{self.name}] 第{step}步 → {tool_name}({args})")
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})
                if finalized:
                    break

            if finalized:
                break
            if breakdown.is_within_budget:
                # 代码层提前停止: 目标已达成, 省一轮 LLM 调用
                logger.info(f"[{self.name}] 预算已达标, 提前结束循环")
                break

        # ── 终态验收: 无论循环怎么结束, 由代码重算并定论 ──
        breakdown = compute_breakdown(state)
        state.budget_breakdown = breakdown
        state.state = PlanningState.COMPLETED
        span = tracer.current_span
        if span:
            span.set(steps_used=steps_used, finalized=finalized,
                     within_budget=breakdown.is_within_budget)
        if breakdown.is_within_budget:
            logger.info(
                f"[{self.name}] 调整完成: {steps_used} 步, "
                f"总费用 ¥{breakdown.total_cost:.0f} / 预算 ¥{breakdown.budget:.0f}"
            )
        else:
            state.error_messages.append(
                f"ReplanAgent 经 {steps_used} 步调整后仍超预算 ¥{breakdown.over_budget_amount:.0f}，返回当前最优方案"
            )
            logger.warning(f"[{self.name}] 未能降到预算内, 返回当前最优方案")
        return state

    # ── 工具分发 ─────────────────────────────

    @staticmethod
    def _execute_tool(name: str, args: dict[str, Any], state: TravelPlanState) -> str:
        from tools.replan_search import (
            search_activities_with_constraints,
            search_flights_with_constraints,
            search_hotels_with_constraints,
        )

        try:
            if name == "search_flights":
                return search_flights_with_constraints(
                    state,
                    max_price=_to_float(args.get("max_price")),
                    max_stops=int(args["max_stops"]) if args.get("max_stops") is not None else None,
                )
            if name == "search_hotels":
                return search_hotels_with_constraints(
                    state,
                    max_price_per_night=_to_float(args.get("max_price_per_night")),
                    min_star=_to_float(args.get("min_star")),
                )
            if name == "search_activities":
                budget = _to_float(args.get("daily_budget_per_person"))
                if budget is None:
                    return "缺少必填参数 daily_budget_per_person, 请重新调用。"
                return search_activities_with_constraints(state, daily_budget_per_person=budget)
            return f"未知工具: {name}。可用工具: search_flights / search_hotels / search_activities / finalize_plan。"
        except (ValueError, TypeError) as exc:
            # 参数错误作为 observation 喂回, 让 LLM 自己修正, 不中断循环
            return f"工具执行失败: {exc}。请检查参数后重试。"

    # ── Prompt 构造 ─────────────────────────────

    @staticmethod
    def _budget_line(b: BudgetBreakdown) -> str:
        status = "已在预算内 ✓" if b.is_within_budget else f"仍超支 ¥{b.over_budget_amount:.0f}"
        return (
            f"[系统计算] 航班 ¥{b.flight_cost:.0f} + 酒店 ¥{b.hotel_cost:.0f} "
            f"+ 活动 ¥{b.activity_cost:.0f} = 总计 ¥{b.total_cost:.0f} / 预算 ¥{b.budget:.0f}（{status}）"
        )

    def _initial_situation(self, state: TravelPlanState, b: BudgetBreakdown) -> str:
        pref = state.preferences
        dest = state.selected_destination
        lines = [
            f"目的地: {dest.city}, {dest.country}" if dest else "目的地: 未知",
            f"出行: {pref.start_date} 至 {pref.end_date}, {pref.num_travelers} 人, 风格 {pref.travel_style.value}",
        ]
        fr = state.flight_result
        if fr and fr.recommended_outbound and fr.recommended_return:
            lines.append(
                f"当前航班: 去程 ¥{fr.recommended_outbound.price:.0f} ({fr.recommended_outbound.stops}次中转), "
                f"返程 ¥{fr.recommended_return.price:.0f} ({fr.recommended_return.stops}次中转)"
            )
        hr = state.hotel_result
        if hr and hr.recommended:
            lines.append(
                f"当前酒店: {hr.recommended.name} ({hr.recommended.star_rating}星) "
                f"¥{hr.recommended.price_per_night:.0f}/晚 × {hr.total_nights}晚"
            )
        ar = state.activity_result
        if ar:
            lines.append(f"当前活动: {len(ar.day_plans)} 天行程, 共 ¥{ar.total_activity_cost:.0f}")
        lines.append(self._budget_line(b))
        lines.append("请开始调整。")
        return "\n".join(lines)
