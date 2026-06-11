"""
ReplanAgent 的工具集 —— 带约束参数的定向重搜。

设计要点:
  - 工具必须有"决策杠杆": 约束参数 (max_price / min_star / daily_budget) 就是
    LLM 表达决策的方式 —— 砍谁、砍到什么程度, 全在它生成的参数里。
    无参数的搜索工具对 Agent 毫无意义 (调一百次结果分布都一样)。
  - 约束不可满足时不报错, 而是选最接近的结果并在返回摘要中明确告知
    —— 让 LLM 拿到"约束太紧"的信号后自行放宽, 而不是循环卡死。
  - 工具直接写回 TravelPlanState (与并行 Agent 行为一致),
    给 LLM 的只是摘要文本 —— observation 给摘要不给原始 JSON, 控制 token。

注: 复用各 Agent 的 mock 生成器, 保证重搜价格分布与初始搜索一致;
函数内延迟导入以避免 tools ↔ agents 的循环依赖。
"""

from __future__ import annotations

from models.schemas import (
    ActivitySearchResult,
    FlightSearchResult,
    HotelSearchResult,
    TravelPlanState,
)


def _require_context(state: TravelPlanState):
    pref = state.preferences
    dest = state.selected_destination
    if pref is None or dest is None:
        raise ValueError("缺少偏好或目的地信息")
    return pref, dest


def search_flights_with_constraints(
    state: TravelPlanState,
    max_price: float | None = None,
    max_stops: int | None = None,
) -> str:
    """重搜往返航班, 约束: 单程票价上限 / 最大中转次数。"""
    from agents.flight_agent import _generate_mock_flights

    pref, dest = _require_context(state)
    outbound = _generate_mock_flights(pref.departure_city, dest.city, pref.start_date, count=8)
    returns = _generate_mock_flights(dest.city, pref.departure_city, pref.end_date, count=8)

    def pick(flights):
        candidates = [
            f for f in flights
            if (max_price is None or f.price <= max_price)
            and (max_stops is None or f.stops <= max_stops)
        ]
        relaxed = not candidates
        pool = candidates or flights
        return min(pool, key=lambda f: f.price), relaxed

    rec_out, relaxed_out = pick(outbound)
    rec_ret, relaxed_ret = pick(returns)
    total = (rec_out.price + rec_ret.price) * pref.num_travelers

    state.flight_result = FlightSearchResult(
        outbound_flights=outbound,
        return_flights=returns,
        recommended_outbound=rec_out,
        recommended_return=rec_ret,
        total_flight_cost=total,
    )

    summary = (
        f"航班已更新: 去程 {rec_out.airline} ¥{rec_out.price:.0f} ({rec_out.stops}次中转), "
        f"返程 {rec_ret.airline} ¥{rec_ret.price:.0f} ({rec_ret.stops}次中转), "
        f"航班总费用 ¥{total:.0f} ({pref.num_travelers}人)。"
    )
    if relaxed_out or relaxed_ret:
        summary += " 注意: 部分航段未找到满足约束的航班, 已选用最便宜可用项, 可考虑放宽约束。"
    return summary


def search_hotels_with_constraints(
    state: TravelPlanState,
    max_price_per_night: float | None = None,
    min_star: float | None = None,
) -> str:
    """重搜酒店, 约束: 每晚价格上限 / 最低星级。在满足约束的酒店中选口碑最好的。"""
    from agents.hotel_agent import HotelAgent

    pref, dest = _require_context(state)
    hotels = HotelAgent._generate_hotels(dest.city, pref.travel_style.value)

    candidates = [
        h for h in hotels
        if (max_price_per_night is None or h.price_per_night <= max_price_per_night)
        and (min_star is None or h.star_rating >= min_star)
    ]
    relaxed = not candidates
    rec = (
        max(candidates, key=lambda h: h.user_rating)
        if candidates
        else min(hotels, key=lambda h: h.price_per_night)
    )

    nights = HotelAgent._calc_nights(pref.start_date, pref.end_date)
    rooms = max(1, (pref.num_travelers + 1) // 2)
    total = rec.price_per_night * nights * rooms

    state.hotel_result = HotelSearchResult(
        hotels=hotels,
        recommended=rec,
        total_nights=nights,
        total_hotel_cost=total,
    )

    summary = (
        f"酒店已更新: {rec.name} ({rec.star_rating}星, 用户评分 {rec.user_rating}), "
        f"¥{rec.price_per_night:.0f}/晚 × {nights}晚 × {rooms}间 = ¥{total:.0f}。"
    )
    if relaxed:
        summary += " 注意: 没有酒店同时满足价格与星级约束, 已选用最便宜酒店, 可考虑放宽约束。"
    return summary


def search_activities_with_constraints(
    state: TravelPlanState,
    daily_budget_per_person: float,
) -> str:
    """重排每日活动, 约束: 每人每天活动预算上限。超出时从最贵的活动开始删减。"""
    from agents.activity_agent import ActivityAgent

    pref, dest = _require_context(state)
    days = ActivityAgent._get_travel_days(pref.start_date, pref.end_date)
    pool = ActivityAgent._get_activity_pool(dest.city)

    day_plans = []
    total = 0.0
    dropped = 0
    for date_str in days:
        plan = ActivityAgent._plan_one_day(date_str, pool, daily_budget_per_person, pref.interests)
        # 强制执行预算上限: 从最贵的活动开始删, 至少保留一个
        activities = sorted(plan.activities, key=lambda a: a.price)
        while len(activities) > 1 and sum(a.price for a in activities) > daily_budget_per_person:
            activities.pop()
            dropped += 1
        plan.activities = sorted(activities, key=lambda a: ["morning", "afternoon", "evening"].index(a.time_slot or "morning"))
        plan.day_cost = sum(a.price for a in plan.activities) * pref.num_travelers
        total += plan.day_cost
        day_plans.append(plan)

    state.activity_result = ActivitySearchResult(
        day_plans=day_plans,
        total_activity_cost=total,
    )

    summary = (
        f"活动已重排: {len(day_plans)}天行程, 每人每天预算上限 ¥{daily_budget_per_person:.0f}, "
        f"活动总费用 ¥{total:.0f} ({pref.num_travelers}人)。"
    )
    if dropped:
        summary += f" 为满足预算共删减了 {dropped} 个活动。"
    return summary
