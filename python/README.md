# Python 版本 - 多Agent智能旅游行程规划系统

> 完整的 7-Agent 实现：Pipeline 编排、并行执行、预算循环、RAG、SSE 实时事件流。

## 快速运行

```bash
# 安装依赖
pip install -r requirements.txt

# CLI 运行
python main.py
python main.py --budget 15000 --departure 上海 --start 2026-06-01 --end 2026-06-07

# WanderWarm Web (静态前端 + SSE 流式接口, http://localhost:8000)
uvicorn api.app:app --port 8000

# Streamlit 前端 + Trace Viewer (http://localhost:8501)
streamlit run ui/streamlit_app.py

# 运行测试 (56 个用例, 全程 mock 零 API 消耗)
python -m pytest tests/ -v
```

## 技术栈

- **Python 3.10+**
- **Pydantic v2** - 数据模型与校验 + LLM 结构化输出 Schema
- **asyncio** - 并行 Agent 执行 + contextvar 请求隔离
- **FastAPI** - REST API + SSE 流式接口 + 静态托管
- **原生 HTML/CSS/JS** - WanderWarm 演示前端（fetch + ReadableStream 消费 SSE）
- **Streamlit** - 备用前端 + Trace Viewer
- **loguru** - 日志
- **pytest** - 测试
- **自研链路追踪** - Span 树 + JSONL 导出，可选接入 Langfuse
- **RAG** - 目的地知识库 + BM25/向量双检索策略

## 模块说明

| 目录 | 说明 |
|------|------|
| `agents/` | 7 个 Agent 实现 + 基类（结构化输出 / function calling） |
| `orchestrator/` | Pipeline 编排 + 并行执行 + 预算循环（rule/agent 双模式） |
| `observability/` | 链路追踪：Tracer + JSONL/Langfuse 导出器 |
| `events/` | 实时事件系统：contextvar 发射器（默认 no-op，请求级隔离） |
| `knowledge/` | RAG 知识库：8 城市语料 + BM25/向量检索 |
| `models/` | Pydantic 数据模型 |
| `tools/` | Mock 搜索工具 + ReplanAgent 约束搜索 |
| `api/` | FastAPI：REST + SSE 流式接口（`streaming.py`） |
| `static/` | WanderWarm 前端（实时 Agent 时间线 / 流式摘要 / 动态渲染） |
| `ui/` | Streamlit 前端 + Trace Viewer 页 |
| `tests/` | 56 个测试用例（conftest 强制 mock，防误打真实 API） |
| `config/` | 配置管理 + 每请求 LLM 降级开关 |

## 五大核心能力

### 0. ReplanAgent —— 项目里唯一的真·Agent（`agents/replan_agent.py`）

`REPLAN_MODE=agent` 时，超预算的调整不再走写死的规则降级，而是交给
ReplanAgent 的 **function calling 工具调用循环**：LLM 看预算明细，自主决定
调 `search_flights / search_hotels / search_activities`（约束参数表达决策），
满意时调 `finalize_plan` 结束 —— **控制流在 LLM 手里，这是它与项目其余
Workflow 部分的本质区别**。

代码保留三项权力（Agent 安全栏）: 步数上限 `REPLAN_MAX_STEPS`、
预算由代码计算（不信 LLM 算术）、终态由代码验收。
LLM 异常时自动回退规则模式；mock 模式下恒走规则模式。

```bash
# 真实 LLM 集成测试（默认不跑, 显式运行）
python -m pytest tests/ -m real_llm -v
```

### 1. LLM 结构化输出（`agents/base_agent.py`）

`call_llm_structured()` 保证 LLM 输出可被程序消费的"三板斧"：

1. Prompt 注入 Pydantic 生成的 JSON Schema，约束输出格式
2. `extract_json()` 容错解析（剥离 ```` ```json ```` 围栏、前后闲聊文字）
3. 校验失败把错误信息喂回 LLM **自修复重试**（默认最多 2 次）

### 2. 可观测性（`observability/`）

一次规划 = 一条 trace。基类 `run()` 埋一次点，7 个 Agent 自动获得追踪；
LLM 调用记录 token 用量，RAG 记录检索策略与命中，Replan 工具调用逐步留痕。

- 本地：每次运行写入 `traces/<trace_id>.jsonl`（零依赖，默认开启）
- 可视化：Streamlit 的 **Trace Viewer** 页将 trace 渲染为调用树
- 云端：配置 `LANGFUSE_PUBLIC_KEY/SECRET_KEY` 后自动上报 Langfuse
  （需 `pip install 'langfuse>=2.39,<3'`）

### 3. RAG 目的地推荐（`knowledge/` + `agents/destination_agent.py`）

知识库为 8 个城市 × 6 小节（签证/季节/美食/玩乐/预算）的 Markdown 语料，
按二级标题切块。检索策略自动降级：

- 配置 `EMBEDDING_MODEL` + Key → 向量语义检索（带本地 embedding 缓存）
- 未配置 / 调用失败 → 本地 BM25 词法检索（中文按字符二元组分词，零成本离线可用）

真实 LLM 模式下，检索结果作为"参考资料"注入 Prompt 做检索增强推荐；
mock 模式下检索命中参与规则评分加权，推荐理由附知识库依据。

### 4. 实时事件流 + 真实模式钥匙（`events/` + `api/streaming.py`）

WanderWarm 前端通过 `POST /api/plan/stream` 实时观看规划全过程：

- **事件发射器**：业务代码经 contextvar 发射器发布事件，默认 no-op
  —— CLI / 普通 API / 测试零感知；SSE 端点为每个请求建独立 `asyncio.Queue`
- **LLM 流式摘要**：规划完成后 `stream=true` 逐字生成行程总结（mock 走本地模板）
- **稳定性**：并发上限 2、总超时 180s、行程 ≤14 天、客户端断开即取消
- **真实模式钥匙**：设置 `DEMO_ACCESS_CODE` 后，仅 URL 带 `?key=` 的请求走真实
  LLM，其余自动降级 mock —— 降级用 contextvar 每请求隔离，并发安全

## 环境变量

复制 `.env.example` 为 `.env` 进行配置。默认 Mock 模式不需要任何配置：
追踪走本地 JSONL、RAG 走 BM25、推荐走规则评分，零 API Key 可完整运行。

接入真实能力：

```bash
LLM_PROVIDER=deepseek                       # 任意 OpenAI 兼容服务
LLM_API_KEY=sk-...
LLM_BASE_URL=https://api.deepseek.com/v1    # 与 PROVIDER/MODEL 配套修改
LLM_MODEL=deepseek-chat
REPLAN_MODE=agent                           # 启用 ReplanAgent 自主调整
DEMO_ACCESS_CODE=自定义口令                  # 公网部署务必设置
EMBEDDING_MODEL=                            # 配置后 RAG 切换为向量检索
LANGFUSE_PUBLIC_KEY=                        # 配置后 trace 自动上报 Langfuse
LANGFUSE_SECRET_KEY=
```
