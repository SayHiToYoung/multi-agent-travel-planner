"""
预算循环控制器 —— 反复执行"并行搜索 → 预算校验"直到预算通过或达到上限。

两种超预算调整策略 (REPLAN_MODE, Agent vs Workflow 的对照实现):
  - rule  (默认): 写死的渐进式降级 —— 第1轮砍活动 → 第2轮降酒店 → 第3轮换航班,
                  控制流完全在代码手里, 属于 Evaluator-Optimizer Workflow 模式
  - agent: ReplanAgent 工具调用循环 —— 砍谁/砍多少/何时停由 LLM 自主决策,
           需要真实 LLM; mock 模式或 Agent 异常时自动回退 rule (优雅降级)

设计要点:
  - 循环终止条件: ① 预算通过 ② 达到最大调整次数 ③ 出现不可恢复错误
  - 为什么不无限循环？ → 用户体验差、Token 消耗大、可能陷入震荡
  - 与 LangGraph 的对应: 本质上是 conditional_edge + cycle，状态机的 ADJUSTING 节点
"""

from __future__ import annotations

from loguru import logger

from agents.budget_agent import BudgetAgent
from agents.replan_agent import ReplanAgent
from config.settings import is_force_mock, settings
from models.schemas import PlanningState, TravelPlanState
from observability import get_tracer

from .parallel import ParallelExecutor


class BudgetLoopController:
    """执行"并行搜索 + 预算校验"循环，最多 max_retries 轮。"""

    def __init__(
        self,
        parallel_executor: ParallelExecutor,
        budget_agent: BudgetAgent | None = None,
        max_retries: int | None = None,
        replan_agent: ReplanAgent | None = None,
    ):
        self.parallel_executor = parallel_executor
        self.budget_agent = budget_agent or BudgetAgent()           # rule 模式: 校验+规则降级
        self.budget_checker = BudgetAgent(auto_adjust=False)        # agent 模式: 只校验不降级
        self.replan_agent = replan_agent or ReplanAgent()
        self.max_retries = max_retries or settings.BUDGET_MAX_RETRIES

    @staticmethod
    def _agent_mode_enabled() -> bool:
        return (
            settings.REPLAN_MODE == "agent"
            and settings.LLM_PROVIDER != "mock"
            and not is_force_mock()
        )

    async def run(self, state: TravelPlanState) -> TravelPlanState:
        state.max_adjustments = self.max_retries

        if self._agent_mode_enabled():
            state = await self._run_agent_mode(state)
            if state.state in (PlanningState.COMPLETED, PlanningState.FAILED):
                return state
            # ReplanAgent 异常退出 (状态停在 ADJUSTING) → 回退规则模式兜底
            logger.warning("[BudgetLoop] Agent 模式未完成, 回退规则模式")
            state.adjustment_round = 0

        return await self._run_rule_mode(state)

    # ── Agent 模式: 初搜 → 校验 → LLM 工具调用循环 ──────────

    async def _run_agent_mode(self, state: TravelPlanState) -> TravelPlanState:
        tracer = get_tracer()
        async with tracer.span("budget_loop:agent_mode") as span:
            state = await self.parallel_executor.run(state)
            state.state = PlanningState.BUDGET_CHECKING
            state = await self.budget_checker.run(state)

            if state.state == PlanningState.ADJUSTING:
                logger.info("[BudgetLoop] 超预算, 交由 ReplanAgent 自主调整")
                state = await self.replan_agent.run(state)

            span.set(result_state=state.state.value, rounds=state.adjustment_round)
        return state

    # ── 规则模式: 原有 Workflow 循环 ─────────────────────

    async def _run_rule_mode(self, state: TravelPlanState) -> TravelPlanState:
        tracer = get_tracer()
        for attempt in range(self.max_retries + 1):
            label = "初始搜索" if attempt == 0 else f"第 {attempt} 轮调整"
            logger.info(f"[BudgetLoop] ── {label} ──")

            async with tracer.span(f"budget_loop:round_{attempt}", round=attempt) as span:
                if attempt == 0 or state.state == PlanningState.ADJUSTING:
                    state = await self.parallel_executor.run(state)

                state.state = PlanningState.BUDGET_CHECKING
                state = await self.budget_agent.run(state)
                span.set(result_state=state.state.value)

            if state.state == PlanningState.COMPLETED:
                logger.info(f"[BudgetLoop] 在第 {attempt} 轮完成 (共尝试 {attempt + 1} 次)")
                return state

            if state.state == PlanningState.FAILED:
                logger.error("[BudgetLoop] 规划失败，退出循环")
                return state

        logger.warning(f"[BudgetLoop] 达到最大重试次数 {self.max_retries}")
        state.state = PlanningState.COMPLETED
        return state
