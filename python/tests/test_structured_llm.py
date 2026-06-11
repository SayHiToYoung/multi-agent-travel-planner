"""结构化 LLM 输出测试 —— JSON 提取容错 + Pydantic 校验 + 自修复重试链路。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.base_agent import BaseAgent, extract_json
from models.schemas import TravelPlanState


class _City(BaseModel):
    city: str
    score: float


class _StubAgent(BaseAgent):
    """用脚本化的 LLM 响应序列代替真实 API。"""

    name = "StubAgent"

    def __init__(self, responses: list[str]) -> None:
        super().__init__()
        self._llm_provider = "stub"  # 非 mock, 走 _real_llm
        self._responses = list(responses)
        self.call_count = 0

    async def execute(self, state: TravelPlanState) -> TravelPlanState:
        return state

    async def _real_llm(self, prompt: str, system_prompt: str = "") -> str:
        self.call_count += 1
        return self._responses.pop(0)


# ━━━━━━ extract_json ━━━━━━


def test_extract_json_plain():
    assert extract_json('{"a": 1}') == '{"a": 1}'


def test_extract_json_with_fence():
    raw = '好的，以下是结果:\n```json\n{"city": "东京", "score": 9.5}\n```\n希望对你有帮助!'
    assert extract_json(raw) == '{"city": "东京", "score": 9.5}'


def test_extract_json_with_surrounding_text():
    raw = '推荐结果为 {"city": "首尔", "score": 8.8} 以上。'
    assert extract_json(raw) == '{"city": "首尔", "score": 8.8}'


# ━━━━━━ call_llm_structured ━━━━━━


async def test_structured_output_success_first_try():
    agent = _StubAgent(['{"city": "东京", "score": 9.5}'])
    result = await agent.call_llm_structured("推荐城市", _City)
    assert result.city == "东京"
    assert agent.call_count == 1


async def test_structured_output_repairs_invalid_json():
    """第一次输出非法 → 错误信息喂回 → 第二次修复成功。"""
    agent = _StubAgent([
        "对不起，我无法以 JSON 回答",          # 第 1 次: 解析失败
        '{"city": "大阪", "score": "很高"}',   # 第 2 次: 类型校验失败
        '{"city": "大阪", "score": 9.0}',      # 第 3 次: 成功
    ])
    result = await agent.call_llm_structured("推荐城市", _City, max_repair=2)
    assert result.city == "大阪"
    assert agent.call_count == 3


async def test_structured_output_raises_after_max_repair():
    agent = _StubAgent(["bad", "bad", "bad"])
    with pytest.raises(ValueError, match="结构化输出"):
        await agent.call_llm_structured("推荐城市", _City, max_repair=2)
    assert agent.call_count == 3


async def test_structured_output_rejected_in_mock_mode():
    agent = _StubAgent([])
    agent._llm_provider = "mock"
    with pytest.raises(RuntimeError, match="mock"):
        await agent.call_llm_structured("推荐城市", _City)
