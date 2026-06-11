"""
Destination Agent —— 目的地推荐 Agent（RAG 增强版）。

职责: 根据用户偏好推荐目的地，考虑季节、签证、安全性、性价比。
在 Pipeline 中处于第二个节点，接收 preferences，输出 DestinationRecommendation。

工作流程:
  1. RAG 检索: 用"旅行风格 + 兴趣 + 出行月份"组成查询, 从目的地知识库
     检索相关知识块 (mock / 真实模式都执行, BM25 离线可用)
  2. 真实 LLM 模式: 把候选目的地 + 检索到的参考资料注入 Prompt,
     调 call_llm_structured() 强制输出结构化推荐 —— 这就是完整的 RAG 链路
  3. 降级路径: LLM 失败 (超时/解析失败) 自动回退到多维加权评分,
     保证 Agent 永远有输出 —— LLM 不可靠时系统必须有兜底路径
"""

from __future__ import annotations

from datetime import datetime

from loguru import logger
from pydantic import BaseModel, Field

from config.settings import settings
from knowledge import get_knowledge_base
from knowledge.retriever import RetrievedChunk
from models.schemas import (
    Destination,
    DestinationRecommendation,
    PlanningState,
    TravelPlanState,
    UserPreferences,
)
from observability import get_tracer

from .base_agent import BaseAgent

MOCK_DESTINATIONS: list[dict] = [
    {
        "city": "东京",
        "country": "日本",
        "description": "传统与现代的完美融合，美食天堂",
        "best_season": "spring,autumn",
        "visa_required": True,
        "safety_score": 9.5,
        "cost_level": "high",
        "highlights": ["浅草寺", "涩谷十字路口", "筑地市场", "东京塔"],
    },
    {
        "city": "曼谷",
        "country": "泰国",
        "description": "热带风情，物美价廉的旅游胜地",
        "best_season": "winter",
        "visa_required": False,
        "safety_score": 7.5,
        "cost_level": "low",
        "highlights": ["大皇宫", "卧佛寺", "考山路", "暹罗广场"],
    },
    {
        "city": "巴黎",
        "country": "法国",
        "description": "浪漫之都，艺术与美食的殿堂",
        "best_season": "spring,summer",
        "visa_required": True,
        "safety_score": 8.0,
        "cost_level": "high",
        "highlights": ["埃菲尔铁塔", "卢浮宫", "香榭丽舍大街", "蒙马特高地"],
    },
    {
        "city": "清迈",
        "country": "泰国",
        "description": "宁静的兰纳古城，适合文化与休闲",
        "best_season": "winter",
        "visa_required": False,
        "safety_score": 8.5,
        "cost_level": "low",
        "highlights": ["双龙寺", "古城", "夜间动物园", "周末夜市"],
    },
    {
        "city": "首尔",
        "country": "韩国",
        "description": "潮流时尚与历史文化交汇",
        "best_season": "spring,autumn",
        "visa_required": False,
        "safety_score": 9.0,
        "cost_level": "medium",
        "highlights": ["景福宫", "明洞", "北村韩屋村", "南山塔"],
    },
    {
        "city": "大阪",
        "country": "日本",
        "description": "日本的厨房，环球影城所在地",
        "best_season": "spring,autumn",
        "visa_required": True,
        "safety_score": 9.5,
        "cost_level": "medium",
        "highlights": ["大阪城", "道顿堀", "环球影城", "黑门市场"],
    },
]


class _LLMDestinationOutput(BaseModel):
    """LLM 结构化输出的 Schema: 推荐 Top3 + 选中城市 + 推荐理由。"""

    destinations: list[Destination] = Field(..., min_length=1, max_length=3)
    selected_city: str = Field(..., description="从 destinations 中选出的最优城市名")
    reasoning: str = Field(..., description="结合参考资料给出的推荐理由, 100 字以内")


class DestinationAgent(BaseAgent):
    name = "DestinationAgent"

    async def execute(self, state: TravelPlanState) -> TravelPlanState:
        pref = state.preferences
        if pref is None:
            raise ValueError("缺少用户偏好")

        # ── 第 1 步: RAG 检索目的地知识 ──
        retrieved: list[RetrievedChunk] = []
        if settings.RAG_ENABLED:
            query = self._build_query(pref)
            try:
                retrieved = await get_knowledge_base().retrieve(query)
                logger.info(
                    f"[{self.name}] RAG 命中 {len(retrieved)} 个知识块: "
                    f"{[r.chunk.chunk_id for r in retrieved]}"
                )
            except Exception as exc:
                logger.warning(f"[{self.name}] RAG 检索失败, 跳过知识增强: {exc}")

        # ── 第 2 步: 真实 LLM 模式 → 检索增强的结构化推荐 ──
        if not self.is_mock:
            try:
                state.destination_rec = await self._recommend_with_llm(pref, retrieved)
                state.state = PlanningState.SEARCHING_PARALLEL
                sel = state.destination_rec.selected
                logger.info(f"[{self.name}] (LLM+RAG) 推荐目的地: {sel.city}, {sel.country}")
                return state
            except Exception as exc:
                logger.warning(f"[{self.name}] LLM 推荐失败, 回退规则评分: {exc}")
                span = get_tracer().current_span
                if span:
                    span.set(llm_fallback=str(exc))

        # ── 第 3 步: 规则评分路径 (mock 模式 / LLM 降级) ──
        state.destination_rec = self._recommend_with_rules(pref, retrieved)
        state.state = PlanningState.SEARCHING_PARALLEL
        sel = state.destination_rec.selected
        logger.info(f"[{self.name}] 推荐目的地: {sel.city}, {sel.country}")
        return state

    # ── RAG 查询构造 ─────────────────────────────

    @staticmethod
    def _build_query(pref: UserPreferences) -> str:
        try:
            month = datetime.strptime(pref.start_date, "%Y-%m-%d").month
        except (ValueError, TypeError):
            month = 6
        style_zh = {
            "budget": "预算紧张 性价比 便宜",
            "comfort": "舒适 中等预算",
            "luxury": "奢华 高端 品质",
            "adventure": "冒险 户外 徒步",
            "cultural": "文化 历史 古迹 博物馆",
            "relaxation": "放松 度假 休闲",
        }.get(pref.travel_style.value, "")
        parts = [f"{month}月出行", style_zh, *pref.interests, pref.notes]
        return " ".join(p for p in parts if p)

    # ── LLM 推荐路径 ─────────────────────────────

    async def _recommend_with_llm(
        self, pref: UserPreferences, retrieved: list[RetrievedChunk]
    ) -> DestinationRecommendation:
        candidates = "\n".join(
            f"- {d['city']}（{d['country']}）: {d['description']}, "
            f"消费水平 {d['cost_level']}, 安全评分 {d['safety_score']}, "
            f"{'需要' if d['visa_required'] else '免'}签证"
            for d in MOCK_DESTINATIONS
        )
        context = "\n\n".join(
            f"【{r.chunk.metadata.get('city', '')} - {r.chunk.metadata.get('section', '')}】\n{r.chunk.text}"
            for r in retrieved
        ) or "（无）"

        prompt = f"""请为以下用户从候选城市中推荐最合适的 1-3 个旅行目的地。

用户偏好:
- 预算: ¥{pref.budget:.0f}（{pref.num_travelers} 人）
- 出发城市: {pref.departure_city}, 日期: {pref.start_date} 至 {pref.end_date}
- 旅行风格: {pref.travel_style.value}
- 兴趣: {', '.join(pref.interests) or '未指定'}
- 备注: {pref.notes or '无'}

候选城市:
{candidates}

参考资料（来自目的地知识库, 请优先依据这些事实推荐, 并在 reasoning 中引用关键信息）:
{context}
"""
        output = await self.call_llm_structured(
            prompt,
            _LLMDestinationOutput,
            system_prompt="你是资深旅行规划师, 推荐必须基于参考资料中的事实, 不要编造。",
        )
        selected = next(
            (d for d in output.destinations if d.city == output.selected_city),
            output.destinations[0],
        )
        return DestinationRecommendation(
            destinations=output.destinations,
            selected=selected,
            reasoning=output.reasoning,
        )

    # ── 规则评分路径 ─────────────────────────────

    def _recommend_with_rules(
        self, pref: UserPreferences, retrieved: list[RetrievedChunk]
    ) -> DestinationRecommendation:
        scored = []
        for d_data in MOCK_DESTINATIONS:
            dest = Destination(**d_data)
            score = self._score_destination(dest, pref.budget, pref.travel_style.value, pref.start_date)
            # RAG 加成: 知识库检索命中该城市, 说明与用户兴趣相关
            score += sum(5.0 for r in retrieved if r.chunk.metadata.get("city") == dest.city)
            scored.append((score, dest))

        scored.sort(key=lambda x: x[0], reverse=True)
        top3 = [d for _, d in scored[:3]]
        selected = top3[0]

        reasoning = f"根据您 ¥{pref.budget:.0f} 的预算和 {pref.travel_style.value} 风格，推荐 {selected.city}"
        hit_sections = [
            r.chunk.metadata.get("section", "")
            for r in retrieved
            if r.chunk.metadata.get("city") == selected.city
        ]
        if hit_sections:
            reasoning += f"（知识库依据: {selected.city} 的 {'、'.join(hit_sections)} 与您的偏好高度相关）"

        return DestinationRecommendation(
            destinations=top3,
            selected=selected,
            reasoning=reasoning,
        )

    @staticmethod
    def _score_destination(dest: Destination, budget: float, style: str, start_date: str) -> float:
        score = 0.0

        cost_budget_map = {"low": 8000, "medium": 15000, "high": 25000}
        est_cost = cost_budget_map.get(dest.cost_level, 15000)
        if budget >= est_cost:
            score += 30
        elif budget >= est_cost * 0.7:
            score += 15

        score += dest.safety_score * 3

        try:
            month = datetime.strptime(start_date, "%Y-%m-%d").month
        except (ValueError, TypeError):
            month = 6

        season_map = {12: "winter", 1: "winter", 2: "winter",
                      3: "spring", 4: "spring", 5: "spring",
                      6: "summer", 7: "summer", 8: "summer",
                      9: "autumn", 10: "autumn", 11: "autumn"}
        current_season = season_map.get(month, "summer")
        if current_season in dest.best_season:
            score += 20

        style_cost_pref = {"budget": "low", "comfort": "medium", "luxury": "high",
                           "adventure": "low", "cultural": "medium", "relaxation": "medium"}
        if style_cost_pref.get(style) == dest.cost_level:
            score += 15

        if not dest.visa_required:
            score += 10

        return score
