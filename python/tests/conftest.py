"""
测试全局配置。

关键职责: .env 中配置了真实 LLM Key 时, 防止单元测试误打真实 API ——
默认把所有测试强制切回 mock + rule 模式; 真正需要打真实 API 的集成测试
用 @pytest.mark.real_llm 标记, 显式运行: pytest -m real_llm
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import settings  # noqa: E402


@pytest.fixture(autouse=True)
def _force_mock_llm(request, monkeypatch):
    if request.node.get_closest_marker("real_llm"):
        if settings.LLM_PROVIDER == "mock" or not settings.LLM_API_KEY:
            pytest.skip("需要在 .env 中配置真实 LLM (LLM_PROVIDER + LLM_API_KEY)")
        return
    monkeypatch.setattr(settings, "LLM_PROVIDER", "mock")
    monkeypatch.setattr(settings, "REPLAN_MODE", "rule")
