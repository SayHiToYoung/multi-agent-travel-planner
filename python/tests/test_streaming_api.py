"""SSE 流式接口测试 —— 事件信封、顺序、终止保证、输入校验 (全程 mock, 零 API 消耗)。"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.app import app
from events import Event, QueueEmitter, emit, reset_emitter, set_emitter
from events.emitter import NoopEmitter


def _payload(**overrides) -> dict:
    base = dict(
        budget=20000,
        departure_city="上海",
        start_date="2026-07-15",
        end_date="2026-07-19",
        travel_style="comfort",
        num_travelers=2,
        interests=["美食", "历史"],
        notes="",
    )
    base.update(overrides)
    return base


async def _collect_events(payload: dict) -> tuple[int, list[dict]]:
    """请求流式接口并解析全部 SSE 事件。"""
    transport = httpx.ASGITransport(app=app)
    events: list[dict] = []
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream("POST", "/api/plan/stream", json=payload, timeout=60) as resp:
            status = resp.status_code
            if status != 200:
                await resp.aread()
                return status, []
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    events.append(json.loads(line[6:]))
    return status, events


# ━━━━━━ 事件系统单元 ━━━━━━


async def test_emit_is_noop_by_default():
    emit("agent_started", agent="X")  # 无发射器时不抛异常、无副作用


async def test_queue_emitter_receives_events():
    queue: asyncio.Queue = asyncio.Queue()
    token = set_emitter(QueueEmitter(queue))
    try:
        emit("agent_completed", agent="TestAgent", message="done", data={"k": 1})
    finally:
        reset_emitter(token)
    event: Event = queue.get_nowait()
    assert event.type == "agent_completed"
    assert event.agent == "TestAgent"
    assert event.data == {"k": 1}
    assert event.timestamp  # 信封字段齐全


async def test_emitter_isolation_after_reset():
    queue: asyncio.Queue = asyncio.Queue()
    token = set_emitter(QueueEmitter(queue))
    reset_emitter(token)
    emit("agent_started", agent="X")
    assert queue.empty()  # reset 后回到 no-op


# ━━━━━━ 流式接口端到端 (mock) ━━━━━━


async def test_stream_happy_path_event_order():
    status, events = await _collect_events(_payload())
    assert status == 200
    types = [e["type"] for e in events]

    assert types[0] == "pipeline_started"
    assert types[-1] == "stream_closed"
    assert types.count("plan_completed") == 1
    assert types.count("stream_closed") == 1

    # Agent 生命周期事件成对出现且先 started 后 completed
    assert types.index("agent_started") < types.index("agent_completed")
    started_agents = {e["agent"] for e in events if e["type"] == "agent_started"}
    assert {"PreferenceAgent", "DestinationAgent", "FlightAgent",
            "HotelAgent", "ActivityAgent", "BudgetAgent"} <= started_agents

    # RAG 与摘要流存在
    assert "rag_result" in types
    assert "summary_delta" in types
    # plan_completed 在 summary 之后、stream_closed 之前
    assert types.index("plan_completed") > types.index("summary_delta")
    assert types.index("plan_completed") < types.index("stream_closed")


async def test_stream_plan_payload_is_renderable():
    _, events = await _collect_events(_payload())
    plan = next(e["data"] for e in events if e["type"] == "plan_completed")

    assert plan["destination"]["city"]
    assert plan["flight"]["outbound"]["airline"]
    assert plan["hotel"]["name"]
    assert len(plan["days"]) == 4
    assert plan["budget"]["budget"] == 20000
    assert isinstance(plan["budget"]["within_budget"], bool)


async def test_stream_rejects_overlong_trip():
    status, _ = await _collect_events(_payload(end_date="2026-08-15"))
    assert status == 400


async def test_stream_rejects_inverted_dates():
    status, _ = await _collect_events(_payload(end_date="2026-07-10"))
    assert status == 400


async def test_stream_rejects_too_many_interests():
    status, _ = await _collect_events(_payload(interests=[f"兴趣{i}" for i in range(9)]))
    assert status == 422  # pydantic 校验


async def test_stream_rejects_bad_style():
    status, _ = await _collect_events(_payload(travel_style="extreme"))
    assert status == 422


async def test_static_index_served():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/")
        assert resp.status_code == 200
        assert "WanderWarm" in resp.text
        # API 路由不被静态挂载吞掉
        health = await client.get("/api/health")
        assert health.status_code == 200
