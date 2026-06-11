"""
事件文案 —— 把各 Agent 的完成状态翻译成面向用户的一句话摘要。

集中在这里而不是写进各 Agent, 是为了让业务 Agent 完全不感知展示层。
"""

from __future__ import annotations

from models.schemas import TravelPlanState


def started_message(agent_name: str) -> str:
    return {
        "PreferenceAgent": "正在整理预算、日期与兴趣偏好…",
        "DestinationAgent": "正在检索目的地知识库并评估候选城市…",
        "FlightAgent": "正在搜索往返航班…",
        "HotelAgent": "正在筛选合适的酒店…",
        "ActivityAgent": "正在编排每日活动…",
        "BudgetAgent": "正在汇总费用并校验预算…",
        "ReplanAgent": "超出预算, AI 正在自主调整方案…",
    }.get(agent_name, "执行中…")


def completed_message(agent_name: str, state: TravelPlanState) -> str:
    try:
        return _completed(agent_name, state)
    except Exception:
        return "已完成"


def _completed(name: str, state: TravelPlanState) -> str:
    if name == "PreferenceAgent" and state.preferences:
        tags = "、".join(state.preferences.interests[:3]) or "通用偏好"
        return f"偏好画像已生成：{tags}"

    if name == "DestinationAgent" and state.selected_destination:
        d = state.selected_destination
        return f"推荐目的地：{d.city} · {d.country}"

    if name == "FlightAgent" and state.flight_result:
        fr = state.flight_result
        n = len(fr.outbound_flights) + len(fr.return_flights)
        return f"找到 {n} 组航班，推荐总价 ¥{fr.total_flight_cost:.0f}"

    if name == "HotelAgent" and state.hotel_result and state.hotel_result.recommended:
        h = state.hotel_result.recommended
        return f"推荐 {h.name}（¥{h.price_per_night:.0f}/晚）"

    if name == "ActivityAgent" and state.activity_result:
        return f"{len(state.activity_result.day_plans)} 天活动安排完成"

    if name == "BudgetAgent" and state.budget_breakdown:
        bb = state.budget_breakdown
        if bb.is_within_budget:
            return f"总费用 ¥{bb.total_cost:.0f}，在预算内"
        return f"超出预算 ¥{bb.over_budget_amount:.0f}，准备调整"

    if name == "ReplanAgent" and state.budget_breakdown:
        bb = state.budget_breakdown
        status = "已回到预算内" if bb.is_within_budget else "已尽力优化"
        return f"经 {state.adjustment_round} 步调整，总费用 ¥{bb.total_cost:.0f}，{status}"

    return "已完成"
