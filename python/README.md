# Python 版本 - 多Agent智能旅游行程规划系统

> 主力版本，包含完整的 6-Agent 实现、Pipeline 编排、并行执行、预算循环。

## 快速运行

```bash
# 安装依赖
pip install -r requirements.txt

# CLI 运行
python main.py
python main.py --budget 15000 --departure 上海 --start 2026-06-01 --end 2026-06-07

# 启动 API 服务 (http://localhost:8000)
python -m api.app

# 启动 Streamlit 前端
streamlit run ui/streamlit_app.py

# 运行测试
python -m pytest tests/ -v
```

## 技术栈

- **Python 3.10+**
- **Pydantic v2** - 数据模型与校验 + LLM 结构化输出 Schema
- **asyncio** - 并行 Agent 执行
- **FastAPI** - REST API
- **Streamlit** - 交互式前端
- **loguru** - 日志
- **pytest** - 测试
- **自研链路追踪** - Span 树 + JSONL 导出，可选接入 Langfuse
- **RAG** - 目的地知识库 + BM25/向量双检索策略

## 模块说明

| 目录 | 说明 |
|------|------|
| `agents/` | 6 个 Agent 实现 + 基类（含结构化 LLM 输出） |
| `orchestrator/` | Pipeline 编排 + 并行执行 + 预算循环 |
| `observability/` | 链路追踪：Tracer + JSONL/Langfuse 导出器 |
| `knowledge/` | RAG 知识库：8 城市语料 + BM25/向量检索 |
| `models/` | Pydantic 数据模型 |
| `tools/` | Mock 搜索工具（航班/酒店/活动/天气） |
| `api/` | FastAPI REST API |
| `ui/` | Streamlit 前端 |
| `tests/` | 单元测试（33 个测试用例） |
| `config/` | 配置管理 |

## 四大核心能力

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

一次规划 = 一条 trace。基类 `run()` 埋一次点，6 个 Agent 自动获得追踪；
LLM 调用记录 token 用量，RAG 记录检索策略与命中。

- 本地：每次运行写入 `traces/<trace_id>.jsonl`（零依赖，默认开启）
- 云端：配置 `LANGFUSE_PUBLIC_KEY/SECRET_KEY` 后自动上报 Langfuse
  （需 `pip install 'langfuse>=2.39,<3'`）

### 3. RAG 目的地推荐（`knowledge/` + `agents/destination_agent.py`）

知识库为 8 个城市 × 6 小节（签证/季节/美食/玩乐/预算）的 Markdown 语料，
按二级标题切块。检索策略自动降级：

- 配置 `EMBEDDING_MODEL` + Key → 向量语义检索（带本地 embedding 缓存）
- 未配置 / 调用失败 → 本地 BM25 词法检索（中文按字符二元组分词，零成本离线可用）

真实 LLM 模式下，检索结果作为"参考资料"注入 Prompt 做检索增强推荐；
mock 模式下检索命中参与规则评分加权，推荐理由附知识库依据。

## 环境变量

复制 `.env.example` 为 `.env` 进行配置。默认 Mock 模式不需要任何配置：
追踪走本地 JSONL、RAG 走 BM25、推荐走规则评分，零 API Key 可完整运行。

接入真实能力：

```bash
LLM_PROVIDER=minimax          # 任意 OpenAI 兼容服务
LLM_API_KEY=sk-...
EMBEDDING_MODEL=embo-01       # 配置后 RAG 切换为向量检索
LANGFUSE_PUBLIC_KEY=pk-...    # 配置后 trace 自动上报 Langfuse
LANGFUSE_SECRET_KEY=sk-...
```
