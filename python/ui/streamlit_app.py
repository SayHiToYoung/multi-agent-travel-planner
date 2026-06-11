"""
Streamlit 前端 —— 交互式行程规划界面。

运行方式:
  cd python
  streamlit run ui/streamlit_app.py

运行模式 (侧边栏切换):
  - Mock 演示模式: 零成本, 规则评分 + BM25 检索, 无需任何 API Key
  - 真实 LLM 模式: LLM+RAG 推荐 + ReplanAgent 工具调用循环;
    设置了 DEMO_ACCESS_CODE 环境变量时需输入访问码解锁 (公网部署防刷)
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st

from config.settings import settings
from models.schemas import TravelPlanState, TravelStyle, UserPreferences
from orchestrator.pipeline import TravelPlanningPipeline

# 直接读环境变量而非 settings.LLM_PROVIDER:
# Streamlit 每次交互都会重跑本脚本, 而 settings 是会被本页按运行模式改写的全局对象,
# 环境变量才是稳定的"真实配置"来源
_REAL_PROVIDER = os.getenv("LLM_PROVIDER", "mock")
_ACCESS_CODE = os.getenv("DEMO_ACCESS_CODE", "")

st.set_page_config(page_title="智能旅游行程规划", page_icon="✈️", layout="wide")

st.title("✈️ 多Agent智能旅游行程规划系统")
st.markdown("**7个AI Agent协作** | Pipeline编排 + 并行搜索 + RAG增强 + ReplanAgent自主调整 + 全链路Trace")

# ── 侧边栏: 运行模式 ──────────────────────────────

with st.sidebar:
    st.subheader("⚙️ 运行模式")
    real_available = _REAL_PROVIDER != "mock" and bool(settings.LLM_API_KEY)
    use_real = False
    if real_available:
        if _ACCESS_CODE:
            entered = st.text_input("访问码（解锁真实 LLM 模式）", type="password")
            if entered:
                use_real = entered == _ACCESS_CODE
                if not use_real:
                    st.error("访问码不正确")
        else:
            use_real = st.toggle("使用真实 LLM", value=True)

    if use_real:
        st.success(f"🧠 真实 LLM 模式\n\n模型: {settings.LLM_MODEL}\n\nLLM+RAG 推荐 + ReplanAgent 自主调整")
    else:
        st.info("🎭 Mock 演示模式（零成本）\n\n规则评分 + BM25 离线检索 + 规则降级")

    st.divider()
    st.caption("规划完成后, 可在 **Trace Viewer** 页查看本次运行的完整调用链 (各Agent耗时 / token消耗 / 工具调用路径)。")

# ── 主区域 ────────────────────────────────────────

st.divider()

col1, col2 = st.columns([1, 2])

with col1:
    st.subheader("📝 旅行偏好")
    budget = st.number_input("总预算（¥）", min_value=1000, max_value=500000, value=10000, step=1000)
    departure = st.text_input("出发城市", value="北京")
    start_date = st.date_input("出发日期")
    end_date = st.date_input("返回日期")
    style = st.selectbox("旅行风格", ["comfort", "budget", "luxury", "adventure", "cultural", "relaxation"],
                         format_func=lambda x: {"comfort": "舒适", "budget": "经济", "luxury": "豪华",
                                                "adventure": "探险", "cultural": "文化", "relaxation": "休闲"}[x])
    travelers = st.number_input("出行人数", min_value=1, max_value=10, value=1)
    interests = st.multiselect("兴趣标签", ["美食", "历史", "艺术", "自然", "购物", "摄影", "运动"])
    notes = st.text_area("额外备注", placeholder="例: 不吃辣、需要无障碍设施...")

    plan_btn = st.button("🚀 开始规划", type="primary", use_container_width=True)

with col2:
    if plan_btn:
        # 按本次选择的模式切换 provider (单进程 demo 场景下的简化做法)
        settings.LLM_PROVIDER = _REAL_PROVIDER if use_real else "mock"

        mode_label = "真实 LLM" if use_real else "Mock 演示"
        with st.spinner(f"7个Agent正在协作规划您的行程...（{mode_label}模式）"):
            prefs = UserPreferences(
                budget=float(budget),
                travel_style=TravelStyle(style),
                departure_city=departure,
                start_date=start_date.strftime("%Y-%m-%d"),
                end_date=end_date.strftime("%Y-%m-%d"),
                num_travelers=travelers,
                interests=interests,
                notes=notes,
            )
            pipeline = TravelPlanningPipeline()
            state: TravelPlanState = asyncio.run(pipeline.run(prefs))

        st.success(f"行程规划完成!（{mode_label}模式）")

        if state.selected_destination:
            d = state.selected_destination
            st.subheader(f"🌍 目的地: {d.city}, {d.country}")
            st.write(d.description)
            if d.highlights:
                st.write(f"**亮点:** {', '.join(d.highlights)}")
            if state.destination_rec and state.destination_rec.reasoning:
                st.caption(f"💡 推荐理由: {state.destination_rec.reasoning}")

        tab1, tab2, tab3, tab4 = st.tabs(["✈️ 航班", "🏨 酒店", "📅 行程", "💰 预算"])

        with tab1:
            if state.flight_result:
                fr = state.flight_result
                if fr.recommended_outbound:
                    o = fr.recommended_outbound
                    st.metric("去程推荐", f"{o.airline} {o.flight_no}", f"¥{o.price:.0f}")
                if fr.recommended_return:
                    r = fr.recommended_return
                    st.metric("返程推荐", f"{r.airline} {r.flight_no}", f"¥{r.price:.0f}")
                st.write(f"**航班总费用:** ¥{fr.total_flight_cost:.0f}")

        with tab2:
            if state.hotel_result and state.hotel_result.recommended:
                h = state.hotel_result.recommended
                st.metric("推荐酒店", h.name, f"⭐{h.star_rating}")
                st.write(f"¥{h.price_per_night:.0f}/晚 × {state.hotel_result.total_nights} 晚")
                st.write(f"**设施:** {', '.join(h.amenities)}")
                st.write(f"**酒店总费用:** ¥{state.hotel_result.total_hotel_cost:.0f}")

        with tab3:
            if state.activity_result:
                for day in state.activity_result.day_plans:
                    st.markdown(f"### {day.date} (¥{day.day_cost:.0f})")
                    for act in day.activities:
                        price_str = f"¥{act.price:.0f}" if act.price > 0 else "免费"
                        st.write(f"- **[{act.time_slot}]** {act.name} ({act.duration_hours}h) {price_str}")

        with tab4:
            if state.budget_breakdown:
                bb = state.budget_breakdown
                c1, c2, c3 = st.columns(3)
                c1.metric("航班", f"¥{bb.flight_cost:.0f}")
                c2.metric("酒店", f"¥{bb.hotel_cost:.0f}")
                c3.metric("活动", f"¥{bb.activity_cost:.0f}")

                st.divider()
                st.metric("总计 / 预算", f"¥{bb.total_cost:.0f} / ¥{bb.budget:.0f}",
                          delta=f"{'节省' if bb.remaining >= 0 else '超出'} ¥{abs(bb.remaining):.0f}",
                          delta_color="normal" if bb.remaining >= 0 else "inverse")

                if state.adjustment_round > 0:
                    adj_label = ("ReplanAgent 自主调整" if use_real and settings.REPLAN_MODE == "agent"
                                 else "规则渐进降级")
                    st.info(f"经过 {state.adjustment_round} 轮预算调整（{adj_label}）")

        if state.error_messages:
            for msg in state.error_messages:
                st.warning(msg)

        st.caption("🔍 想看这次规划的内部过程? 左侧切到 **Trace Viewer** 页, 查看 Agent 调用树与决策路径。")
    else:
        st.info("👈 请在左侧填写旅行偏好，然后点击\"开始规划\"")

        st.subheader("🏗️ 系统架构")
        st.markdown("""
        ```
        用户输入 → Preference Agent → Destination Agent (RAG增强)
        → [Flight + Hotel + Activity (并行搜索)]
        → Budget Agent (预算校验)
        ↓ 超预算?
        ReplanAgent (LLM工具调用循环, 自主决策调整)
        ↓
        输出最终行程 + 完整 Trace
        ```
        """)

        st.subheader("🤖 7个Agent")
        agents_info = {
            "Preference Agent": "收集/补全用户偏好（预算/风格/兴趣）",
            "Destination Agent": "RAG增强的目的地推荐（知识库检索 + LLM结构化输出, 失败回退规则评分）",
            "Flight Agent": "航班搜索比价（价格/时长/中转加权评分）",
            "Hotel Agent": "酒店匹配（风格-星级拟合 + 预算约束）",
            "Activity Agent": "每日行程生成（时间槽分配 + 兴趣匹配）",
            "Budget Agent": "预算校验与规则降级（Workflow 路径）",
            "Replan Agent": "超预算时 LLM 工具调用循环自主调整（Agent 路径, 真实 LLM 模式启用）",
        }
        for name, desc in agents_info.items():
            st.write(f"- **{name}**: {desc}")
