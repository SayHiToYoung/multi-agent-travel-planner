"""可观测性模块测试 —— Tracer 的 span 树、错误标记、JSONL 导出、Agent 自动埋点。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models.schemas import PlanningState, TravelPlanState, TravelStyle, UserPreferences
from observability import get_tracer
from observability.exporters import JsonlExporter
from observability.tracer import Span, Tracer


class _ListExporter:
    """测试用导出器: 把完成的 span 收进列表。"""

    def __init__(self) -> None:
        self.spans: list[Span] = []

    def export(self, span: Span) -> None:
        self.spans.append(span)


async def test_span_nesting_and_trace_id():
    tracer = Tracer()
    exporter = _ListExporter()
    tracer.add_exporter(exporter)

    async with tracer.span("root") as root:
        async with tracer.span("child") as child:
            assert child.parent_id == root.span_id
            assert child.trace_id == root.trace_id

    # 子 span 先结束先导出
    assert [s.name for s in exporter.spans] == ["child", "root"]
    assert exporter.spans[1].parent_id is None
    assert all(s.status == "ok" for s in exporter.spans)
    assert all(s.end_time is not None for s in exporter.spans)


async def test_span_marks_error_on_exception():
    tracer = Tracer()
    exporter = _ListExporter()
    tracer.add_exporter(exporter)

    with pytest.raises(ValueError):
        async with tracer.span("boom"):
            raise ValueError("出错了")

    assert exporter.spans[0].status == "error"
    assert "出错了" in exporter.spans[0].error


async def test_jsonl_exporter_writes_trace_file(tmp_path):
    tracer = Tracer()
    tracer.add_exporter(JsonlExporter(str(tmp_path)))

    async with tracer.span("root", foo="bar") as root:
        trace_id = root.trace_id

    lines = (tmp_path / f"{trace_id}.jsonl").read_text(encoding="utf-8").strip().split("\n")
    record = json.loads(lines[0])
    assert record["name"] == "root"
    assert record["attributes"]["foo"] == "bar"
    assert record["duration_ms"] >= 0


async def test_agent_run_creates_span():
    """BaseAgent.run() 模板方法应自动产生 agent span (含失败兜底标记)。"""
    from agents.preference_agent import PreferenceAgent

    tracer = get_tracer()
    exporter = _ListExporter()
    tracer.add_exporter(exporter)
    try:
        state = TravelPlanState(
            preferences=UserPreferences(
                budget=10000,
                travel_style=TravelStyle.COMFORT,
                departure_city="北京",
                start_date="2026-05-01",
                end_date="2026-05-05",
            )
        )
        await PreferenceAgent().run(state)
    finally:
        tracer.remove_exporter(exporter)

    agent_spans = [s for s in exporter.spans if s.kind == "agent"]
    assert len(agent_spans) == 1
    assert agent_spans[0].name == "agent:PreferenceAgent"
    assert agent_spans[0].attributes["state"] == PlanningState.RECOMMENDING_DESTINATIONS.value


async def test_pipeline_emits_full_trace():
    """完整跑一次 pipeline, 应产生 根span + 各Agent + 并行 + 预算轮次 的 span 树。"""
    from orchestrator.pipeline import quick_plan

    tracer = get_tracer()
    exporter = _ListExporter()
    tracer.add_exporter(exporter)
    try:
        await quick_plan(budget=20000)
    finally:
        tracer.remove_exporter(exporter)

    names = [s.name for s in exporter.spans]
    assert "pipeline:travel_planning" in names
    assert "parallel:search" in names
    assert "budget_loop:round_0" in names
    assert any(n.startswith("agent:") for n in names)
    # 同一条 trace
    assert len({s.trace_id for s in exporter.spans}) == 1
