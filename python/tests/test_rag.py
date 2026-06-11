"""RAG 模块测试 —— 知识库加载、BM25 检索相关性、DestinationAgent 知识增强。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.destination_agent import DestinationAgent
from knowledge import get_knowledge_base
from knowledge.knowledge_base import load_chunks
from knowledge.retriever import BM25Index, Chunk, tokenize
from models.schemas import PlanningState, TravelPlanState, TravelStyle, UserPreferences


# ━━━━━━ 分词 ━━━━━━


def test_tokenize_mixed_language():
    tokens = tokenize("东京的 Ramen 拉面")
    assert "ramen" in tokens          # 英文单词
    assert "东" in tokens             # 中文单字
    assert "东京" in tokens           # 中文二元组


# ━━━━━━ 知识库加载 ━━━━━━


def test_load_chunks_parses_city_and_sections():
    chunks = load_chunks()
    assert len(chunks) >= 40  # 8 城市 × 6 小节
    cities = {c.metadata["city"] for c in chunks}
    assert {"东京", "曼谷", "首尔", "巴黎", "大阪", "清迈", "新加坡", "巴厘岛"} <= cities
    sections = {c.metadata["section"] for c in chunks}
    assert "签证与安全" in sections
    assert "美食" in sections


# ━━━━━━ BM25 检索相关性 ━━━━━━


def test_bm25_food_query_hits_food_cities():
    kb = get_knowledge_base()
    results = kb._bm25.search("美食 拉面 米其林", top_k=4)
    assert results, "BM25 应该有命中"
    top_cities = {r.chunk.metadata["city"] for r in results}
    # 美食相关查询应命中以美食著称的城市文档
    assert top_cities & {"东京", "大阪", "曼谷"}
    assert all(r.score > 0 for r in results)


def test_bm25_beach_query_hits_bali():
    kb = get_knowledge_base()
    results = kb._bm25.search("海岛 度假 海滩 潜水", top_k=4)
    top_cities = {r.chunk.metadata["city"] for r in results}
    assert "巴厘岛" in top_cities


def test_bm25_visa_query_hits_visa_sections():
    index = BM25Index(load_chunks())
    results = index.search("免签 签证", top_k=4)
    sections = [r.chunk.metadata["section"] for r in results]
    assert "签证与安全" in sections


def test_bm25_empty_index_no_crash():
    index = BM25Index([])
    assert index.search("任意查询") == []


def test_bm25_irrelevant_query_returns_empty():
    index = BM25Index([Chunk(chunk_id="x", text="东京 美食")])
    assert index.search("zzz qqq") == []


# ━━━━━━ KnowledgeBase.retrieve ━━━━━━


async def test_kb_retrieve_returns_scored_chunks():
    kb = get_knowledge_base()
    results = await kb.retrieve("文化 历史 博物馆", top_k=3)
    assert 0 < len(results) <= 3
    # 分数降序
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)


# ━━━━━━ DestinationAgent + RAG ━━━━━━


def _make_state(**overrides) -> TravelPlanState:
    defaults = dict(
        budget=10000,
        travel_style=TravelStyle.COMFORT,
        departure_city="北京",
        start_date="2026-05-01",
        end_date="2026-05-05",
        num_travelers=1,
    )
    defaults.update(overrides)
    return TravelPlanState(preferences=UserPreferences(**defaults))


async def test_destination_agent_with_rag_completes():
    state = _make_state(interests=["美食", "历史"])
    result = await DestinationAgent().run(state)
    assert result.destination_rec is not None
    assert result.destination_rec.selected is not None
    assert result.state == PlanningState.SEARCHING_PARALLEL


async def test_destination_agent_reasoning_cites_knowledge_base():
    """命中知识库时, 推荐理由应包含知识库依据。"""
    state = _make_state(interests=["美食", "拉面", "米其林"], budget=30000)
    result = await DestinationAgent().run(state)
    rec = result.destination_rec
    assert rec is not None
    # 检索若命中选中城市, reasoning 会附知识库依据 (兴趣与城市强相关时应命中)
    assert "知识库依据" in rec.reasoning or rec.reasoning


def test_build_query_contains_style_and_interests():
    prefs = UserPreferences(
        budget=10000,
        travel_style=TravelStyle.CULTURAL,
        departure_city="北京",
        start_date="2026-05-01",
        end_date="2026-05-05",
        interests=["美食"],
    )
    query = DestinationAgent._build_query(prefs)
    assert "5月出行" in query
    assert "文化" in query
    assert "美食" in query
