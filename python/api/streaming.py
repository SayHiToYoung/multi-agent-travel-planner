"""
SSE 流式规划接口 —— POST /api/plan/stream

设计要点:
  - 每个请求一个独立的 asyncio.Queue 事件发射器 (contextvar 注入),
    pipeline 在兄弟任务中执行, 响应生成器消费队列 → 请求间完全隔离
  - 稳定性控制: 并发信号量 / 总超时 / 输入边界校验 / 客户端断开即取消任务
  - 摘要流: 规划完成后用 LLM 流式生成面向用户的行程总结 (mock 模式走本地模板)
  - 事件只含执行事实, 不含 prompt/密钥/思维链/原始异常
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import AsyncIterator

from loguru import logger
from pydantic import BaseModel, Field, field_validator

from config.settings import reset_force_mock, set_force_mock, settings
from events import Event, QueueEmitter, reset_emitter, set_emitter
from models.schemas import TravelPlanState, TravelStyle, UserPreferences

# ── 稳定性参数 ────────────────────────────────────

MAX_CONCURRENT_PLANS = 2          # 同时进行的规划数 (保护 LLM 余额与小服务器)
TOTAL_TIMEOUT_SECONDS = 180       # 单次规划总超时
KEEPALIVE_SECONDS = 15            # SSE 保活注释间隔

_semaphore = asyncio.Semaphore(MAX_CONCURRENT_PLANS)


# ── 请求模型 (带输入边界) ──────────────────────────

class StreamPlanRequest(BaseModel):
    budget: float = Field(..., gt=0, le=1_000_000)
    departure_city: str = Field(..., min_length=1, max_length=30)
    start_date: str
    end_date: str
    travel_style: str = "comfort"
    num_travelers: int = Field(1, ge=1, le=10)
    interests: list[str] = Field(default_factory=list, max_length=8)
    notes: str = Field("", max_length=500)
    access_key: str = Field("", max_length=64, description="真实模式钥匙 (URL ?key= 透传)")

    @field_validator("interests")
    @classmethod
    def _interest_length(cls, v: list[str]) -> list[str]:
        for item in v:
            if len(item) > 30:
                raise ValueError("单个兴趣标签不超过 30 字")
        return v

    @field_validator("travel_style")
    @classmethod
    def _valid_style(cls, v: str) -> str:
        TravelStyle(v)  # 非法值抛 ValueError
        return v

    def trip_days(self) -> int:
        d1 = datetime.strptime(self.start_date, "%Y-%m-%d")
        d2 = datetime.strptime(self.end_date, "%Y-%m-%d")
        return (d2 - d1).days

    def validate_trip(self) -> str | None:
        """返回用户可读的错误信息, 合法返回 None。"""
        try:
            days = self.trip_days()
        except ValueError:
            return "日期格式应为 YYYY-MM-DD"
        if days < 1:
            return "返回日期需要晚于出发日期"
        if days > 14:
            return "演示版最长支持 14 天行程"
        return None


# ── 真实模式门禁 ──────────────────────────────────

def real_mode_allowed(access_key: str) -> bool:
    """是否允许本次请求使用真实 LLM。

    规则: 未配置真实 LLM → 永远 mock;
          未设置 DEMO_ACCESS_CODE → 对所有人开放 (本地开发);
          设置了 → 仅持有钥匙 (URL ?key=) 的请求走真实 LLM, 其余降级 mock。
    """
    if settings.LLM_PROVIDER == "mock" or not settings.LLM_API_KEY:
        return False
    if not settings.DEMO_ACCESS_CODE:
        return True
    return access_key == settings.DEMO_ACCESS_CODE


# ── 事件序列化 ────────────────────────────────────

def sse_format(event: Event) -> str:
    return f"data: {event.model_dump_json()}\n\n"


def _ev(type: str, message: str = "", agent: str | None = None, data: dict | None = None) -> Event:
    return Event(type=type, agent=agent, message=message, data=data or {})


def serialize_state(state: TravelPlanState) -> dict:
    """把最终 state 压成前端渲染所需的最小 JSON。"""
    dest = state.selected_destination
    fr, hr, ar, bb = state.flight_result, state.hotel_result, state.activity_result, state.budget_breakdown

    def flight(f):
        if not f:
            return None
        return {"airline": f.airline, "flight_no": f.flight_no,
                "departure_city": f.departure_city, "arrival_city": f.arrival_city,
                "price": f.price, "stops": f.stops}

    return {
        "destination": {
            "city": dest.city, "country": dest.country,
            "description": dest.description, "highlights": dest.highlights,
        } if dest else None,
        "reasoning": state.destination_rec.reasoning if state.destination_rec else "",
        "flight": {
            "outbound": flight(fr.recommended_outbound) if fr else None,
            "return": flight(fr.recommended_return) if fr else None,
            "total": fr.total_flight_cost if fr else 0,
        },
        "hotel": {
            "name": hr.recommended.name, "star_rating": hr.recommended.star_rating,
            "user_rating": hr.recommended.user_rating,
            "price_per_night": hr.recommended.price_per_night,
            "nights": hr.total_nights, "total": hr.total_hotel_cost,
        } if hr and hr.recommended else None,
        "days": [
            {"date": d.date, "day_cost": d.day_cost,
             "activities": [{"time_slot": a.time_slot, "name": a.name,
                             "duration_hours": a.duration_hours, "price": a.price}
                            for a in d.activities]}
            for d in ar.day_plans
        ] if ar else [],
        "budget": {
            "flight": bb.flight_cost, "hotel": bb.hotel_cost, "activity": bb.activity_cost,
            "total": bb.total_cost, "budget": bb.budget, "remaining": bb.remaining,
            "within_budget": bb.is_within_budget,
        } if bb else None,
        "adjustment_rounds": state.adjustment_round,
        "warnings": state.error_messages,
    }


# ── 摘要流 (LLM streaming / mock 模板) ─────────────

_SUMMARY_SYSTEM = "你是温暖的旅行文案师。基于给定的行程事实写一段 120-180 字的中文总结，分 2-3 个自然段，语气亲切克制，不要使用列表和表情符号，不要编造数据。"


def _mock_summary(plan: dict) -> str:
    dest = plan.get("destination") or {}
    budget = plan.get("budget") or {}
    days = len(plan.get("days") or [])
    city = dest.get("city", "目的地")
    line3 = (
        f"行程总费用约 ¥{budget.get('total', 0):.0f}，控制在 ¥{budget.get('budget', 0):.0f} 预算内。"
        if budget.get("within_budget")
        else f"行程总费用约 ¥{budget.get('total', 0):.0f}，已尽力贴近你的预算。"
    )
    return (
        f"我为你选择了{city}作为这次旅程的目的地。{dest.get('description', '')}\n\n"
        f"这是一段 {days} 天的行程，航班、酒店与每日活动都已安排妥当，"
        f"节奏松紧适度，给临时的惊喜留了空间。\n\n{line3}"
    )


async def stream_summary(plan: dict, use_real: bool) -> AsyncIterator[str]:
    """逐段产出面向用户的总结文本。真实模式走 LLM 流式接口, mock 走本地模板。"""
    if not use_real:
        text = _mock_summary(plan)
        for i in range(0, len(text), 6):
            yield text[i:i + 6]
            await asyncio.sleep(0.02)  # 仅 mock: 模拟打字节奏
        return

    import httpx

    facts = json.dumps(
        {k: plan.get(k) for k in ("destination", "reasoning", "budget", "hotel")},
        ensure_ascii=False,
    )
    payload = {
        "model": settings.LLM_MODEL,
        "messages": [
            {"role": "system", "content": _SUMMARY_SYSTEM},
            {"role": "user", "content": f"行程事实:\n{facts}\n\n请写总结。"},
        ],
        "temperature": 0.7,
        "max_tokens": 500,
        "stream": True,
    }
    headers = {"Authorization": f"Bearer {settings.LLM_API_KEY}"}
    async with httpx.AsyncClient(timeout=60) as client:
        async with client.stream(
            "POST", f"{settings.LLM_BASE_URL}/chat/completions",
            json=payload, headers=headers,
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: ") or line.endswith("[DONE]"):
                    continue
                try:
                    delta = json.loads(line[6:])["choices"][0]["delta"].get("content") or ""
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
                if delta:
                    yield delta


# ── SSE 响应生成器 ────────────────────────────────

async def plan_event_stream(req: StreamPlanRequest) -> AsyncIterator[str]:
    if _semaphore.locked():
        yield sse_format(_ev("error", message="当前体验人数较多，请稍后再试", data={"reason": "service_busy"}))
        yield sse_format(_ev("stream_closed"))
        return

    async with _semaphore:
        queue: asyncio.Queue[Event] = asyncio.Queue()
        token = set_emitter(QueueEmitter(queue))
        use_real = real_mode_allowed(req.access_key)
        mock_token = None if use_real else set_force_mock()
        pipeline_task: asyncio.Task | None = None
        try:
            yield sse_format(_ev(
                "pipeline_started",
                message="已收到你的旅行期待，7 个 Agent 开始协作",
                data={"mode": "real" if use_real else "mock"},
            ))

            prefs = UserPreferences(
                budget=req.budget,
                travel_style=TravelStyle(req.travel_style),
                departure_city=req.departure_city,
                start_date=req.start_date,
                end_date=req.end_date,
                num_travelers=req.num_travelers,
                interests=req.interests,
                notes=req.notes,
            )

            from orchestrator.pipeline import TravelPlanningPipeline
            pipeline_task = asyncio.create_task(TravelPlanningPipeline().run(prefs))

            started = asyncio.get_event_loop().time()
            while True:
                if asyncio.get_event_loop().time() - started > TOTAL_TIMEOUT_SECONDS:
                    pipeline_task.cancel()
                    yield sse_format(_ev("error", message="本次规划超时了，请稍后重试", data={"reason": "timeout"}))
                    return
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_SECONDS)
                    yield sse_format(event)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                if pipeline_task.done() and queue.empty():
                    break

            state = pipeline_task.result()
            plan = serialize_state(state)

            # 摘要流: LLM 失败不致命, 跳过即可
            try:
                async for delta in stream_summary(plan, use_real):
                    yield sse_format(_ev("summary_delta", data={"delta": delta}))
            except Exception as exc:
                logger.warning(f"[Stream] 摘要生成失败, 跳过: {exc}")

            yield sse_format(_ev("plan_completed", message="规划完成", data=plan))

        except asyncio.CancelledError:
            # 客户端断开: 终止 pipeline, 不再产出
            raise
        except Exception as exc:
            logger.error(f"[Stream] 规划失败: {exc}")
            yield sse_format(_ev("error", message="这次规划没有成功，请稍后重试"))
        finally:
            if pipeline_task and not pipeline_task.done():
                pipeline_task.cancel()
            if mock_token is not None:
                reset_force_mock(mock_token)
            reset_emitter(token)
            yield sse_format(_ev("stream_closed"))
