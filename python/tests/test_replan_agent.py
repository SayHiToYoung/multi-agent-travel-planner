"""
ReplanAgent 测试 —— 工具约束、Agent 循环机制 (脚本化桩 LLM)、降级回退、真实集成。

桩测试思路: 用脚本化的 assistant 消息序列代替真实 LLM,
验证循环机制本身 (终止条件/步数上限/状态写回), 不依赖网络与模型行为。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents import ActivityAgent, FlightAgent, HotelAgent
from agents.replan_agent import REPLAN_TOOLS, ReplanAgent
from config.settings import settings
from models.schemas import (
    ActivitySearchResult,
    Destination,
    DestinationRecommendation,
    Flight,
    FlightSearchResult,
    Hotel,
    HotelSearchResult,
    PlanningState,
    TravelPlanState,
    TravelStyle,
    UserPreferences,
)
from orchestrator.budget_loop import BudgetLoopController
from orchestrator.parallel import ParallelExecutor
from tools.replan_search import (
    search_activities_with_constraints,
    search_flights_with_constraints,
    search_hotels_with_constraints,
)


def _over_budget_state(budget: float = 10000, travelers: int = 1) -> TravelPlanState:
    """构造一个固定超预算的状态: 航班6000 + 酒店3200 + 活动2000 = 11200。"""
    prefs = UserPreferences(
        budget=budget,
        travel_style=TravelStyle.COMFORT,
        departure_city="北京",
        start_date="2026-05-01",
        end_date="2026-05-05",
        num_travelers=travelers,
    )
    state = TravelPlanState(preferences=prefs)
    dest = Destination(city="首尔", country="韩国")
    state.destination_rec = DestinationRecommendation(destinations=[dest], selected=dest)

    def flight(price: float) -> Flight:
        return Flight(
            airline="测试航空", flight_no="TS1234", departure_city="北京", arrival_city="首尔",
            departure_time="2026-05-01T08:00", arrival_time="2026-05-01T11:00",
            price=price, duration_hours=3.0,
        )

    state.flight_result = FlightSearchResult(
        recommended_outbound=flight(3000), recommended_return=flight(3000),
        total_flight_cost=6000 * travelers,
    )
    state.hotel_result = HotelSearchResult(
        recommended=Hotel(name="测试酒店", city="首尔", price_per_night=800),
        total_nights=4, total_hotel_cost=3200,
    )
    state.activity_result = ActivitySearchResult(day_plans=[], total_activity_cost=2000)
    state.state = PlanningState.ADJUSTING
    return state


def _tool_call(call_id: str, name: str, arguments: str) -> dict:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


class _ScriptedReplanAgent(ReplanAgent):
    """脚本化 LLM: 依次返回预设的 assistant 消息。"""

    def __init__(self, script: list[dict], max_steps: int | None = None) -> None:
        super().__init__(max_steps=max_steps)
        self._llm_provider = "stub"
        self.script = list(script)
        self.llm_calls = 0
        self.messages_seen: list[list[dict]] = []

    async def call_llm_with_tools(self, messages, tools, temperature=None):
        self.messages_seen.append(list(messages))
        self.llm_calls += 1
        return self.script.pop(0)


# ━━━━━━ 工具层: 约束参数必须生效 ━━━━━━


async def test_hotel_tool_respects_price_cap():
    state = _over_budget_state()
    summary = search_hotels_with_constraints(state, max_price_per_night=300)
    rec = state.hotel_result.recommended
    assert rec is not None
    assert rec.price_per_night <= 300 or "放宽约束" in summary
    # 总价由代码按 晚数×间数 重算
    assert state.hotel_result.total_hotel_cost == rec.price_per_night * 4 * 1
    assert "酒店已更新" in summary


async def test_flight_tool_respects_constraints_or_reports():
    state = _over_budget_state()
    summary = search_flights_with_constraints(state, max_price=1500, max_stops=2)
    fr = state.flight_result
    satisfied = (
        fr.recommended_outbound.price <= 1500 and fr.recommended_return.price <= 1500
    )
    assert satisfied or "放宽约束" in summary
    assert fr.total_flight_cost == (
        fr.recommended_outbound.price + fr.recommended_return.price
    ) * state.preferences.num_travelers


async def test_activity_tool_enforces_daily_budget():
    state = _over_budget_state()
    search_activities_with_constraints(state, daily_budget_per_person=100)
    for day in state.activity_result.day_plans:
        per_person = sum(a.price for a in day.activities)
        assert per_person <= 100 or len(day.activities) == 1
    assert len(state.activity_result.day_plans) == 4


# ━━━━━━ Agent 循环机制 ━━━━━━


async def test_replan_loop_stops_when_within_budget():
    """一次工具调用解决问题 → 代码提前终止, 不再请求 LLM。"""
    # 活动砍到每天≤50: 总费用 6000+3200+≤200 < 10000
    script = [{
        "role": "assistant", "content": None,
        "tool_calls": [_tool_call("c1", "search_activities", '{"daily_budget_per_person": 50}')],
    }]
    agent = _ScriptedReplanAgent(script)
    state = await agent.run(_over_budget_state(budget=10000))

    assert agent.llm_calls == 1          # 达标后没有发起第二次 LLM 调用
    assert state.state == PlanningState.COMPLETED
    assert state.budget_breakdown.is_within_budget
    assert state.adjustment_round == 1


async def test_replan_loop_finalize_terminates_even_over_budget():
    script = [{
        "role": "assistant", "content": None,
        "tool_calls": [_tool_call("c1", "finalize_plan", '{"reason": "继续调整损害体验"}')],
    }]
    agent = _ScriptedReplanAgent(script)
    state = await agent.run(_over_budget_state(budget=1000))  # 无法达标

    assert agent.llm_calls == 1
    assert state.state == PlanningState.COMPLETED
    assert not state.budget_breakdown.is_within_budget
    assert any("仍超预算" in m for m in state.error_messages)  # 代码终审附警告


async def test_replan_loop_respects_max_steps():
    """预算 100 永远无法达标 → 循环必须被步数上限终止。"""
    step_msg = {
        "role": "assistant", "content": None,
        "tool_calls": [_tool_call("c1", "search_hotels", '{"max_price_per_night": 100}')],
    }
    agent = _ScriptedReplanAgent([dict(step_msg) for _ in range(5)], max_steps=2)
    state = await agent.run(_over_budget_state(budget=100))

    assert agent.llm_calls == 2          # 被上限掐断
    assert state.state == PlanningState.COMPLETED
    assert any("仍超预算" in m for m in state.error_messages)


async def test_replan_loop_soft_stop_on_text_response():
    """模型违反协议输出纯文本 → 软终止, 不崩溃。"""
    script = [{"role": "assistant", "content": "我觉得没法再调整了。", "tool_calls": None}]
    agent = _ScriptedReplanAgent(script)
    state = await agent.run(_over_budget_state(budget=1000))

    assert state.state == PlanningState.COMPLETED
    assert any("仍超预算" in m for m in state.error_messages)


async def test_replan_loop_feeds_tool_result_back():
    """第二次 LLM 调用的 messages 里必须包含第一次的工具结果 (observation 回灌)。"""
    script = [
        {"role": "assistant", "content": None,
         "tool_calls": [_tool_call("c1", "search_hotels", '{"max_price_per_night": 100}')]},
        {"role": "assistant", "content": None,
         "tool_calls": [_tool_call("c2", "finalize_plan", '{"reason": "ok"}')]},
    ]
    agent = _ScriptedReplanAgent(script)
    await agent.run(_over_budget_state(budget=100))

    second_call_messages = agent.messages_seen[1]
    roles = [m["role"] for m in second_call_messages]
    assert roles == ["system", "user", "assistant", "tool"]
    assert "[系统计算]" in second_call_messages[-1]["content"]  # 预算明细由代码算并喂回


async def test_replan_unknown_tool_feeds_error_back():
    script = [
        {"role": "assistant", "content": None,
         "tool_calls": [_tool_call("c1", "book_spaceship", "{}")]},
        {"role": "assistant", "content": None,
         "tool_calls": [_tool_call("c2", "finalize_plan", '{"reason": "ok"}')]},
    ]
    agent = _ScriptedReplanAgent(script)
    state = await agent.run(_over_budget_state(budget=1000))

    assert state.state == PlanningState.COMPLETED
    tool_reply = agent.messages_seen[1][-1]["content"]
    assert "未知工具" in tool_reply


# ━━━━━━ 控制器: 模式开关与降级回退 ━━━━━━


class _ExplodingReplanAgent(ReplanAgent):
    async def execute(self, state):
        raise RuntimeError("模拟 LLM API 故障")


async def test_budget_loop_falls_back_to_rule_mode(monkeypatch):
    """Agent 模式异常 → 自动回退规则模式, 最终仍产出完整方案。"""
    monkeypatch.setattr(settings, "REPLAN_MODE", "agent")
    monkeypatch.setattr(settings, "LLM_PROVIDER", "deepseek")  # 非 mock 才会进 agent 模式

    executor = ParallelExecutor(agents=[FlightAgent(), HotelAgent(), ActivityAgent()])
    controller = BudgetLoopController(
        parallel_executor=executor,
        replan_agent=_ExplodingReplanAgent(),
    )
    state = _over_budget_state(budget=2000, travelers=2)
    state.state = PlanningState.SEARCHING_PARALLEL
    result = await controller.run(state)

    assert result.state == PlanningState.COMPLETED
    assert result.budget_breakdown is not None
    assert any("ReplanAgent" in m for m in result.error_messages)  # 故障被记录


async def test_budget_loop_stays_rule_mode_under_mock():
    """mock 模式下即使 REPLAN_MODE=agent 也走规则路径 (不会调 LLM)。"""
    assert settings.LLM_PROVIDER == "mock"  # conftest 已强制
    assert BudgetLoopController._agent_mode_enabled() is False


# ━━━━━━ 真实 LLM 集成测试 (pytest -m real_llm 显式运行) ━━━━━━


@pytest.mark.real_llm
async def test_replan_agent_with_real_llm(monkeypatch):
    monkeypatch.setattr(settings, "REPLAN_MODE", "agent")
    from orchestrator.pipeline import quick_plan

    # 故意给一个大概率超预算的场景
    state = await quick_plan(budget=6000, style="luxury", travelers=2)

    assert state.state == PlanningState.COMPLETED
    assert state.budget_breakdown is not None
    # Agent 至少行动过一轮, 或初始就在预算内 (随机数据下小概率)
    assert state.adjustment_round >= 1 or state.budget_breakdown.is_within_budget
