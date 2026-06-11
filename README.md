# 多Agent智能旅游行程规划系统

> **生产级多Agent系统设计** —— Python实现，含完整的架构设计、RAG增强、LLM工具调用、全链路可观测性。

---

## 这个项目是什么？

这是一个 **7个AI Agent协作** 的智能旅游行程规划系统。你输入预算、出发城市、日期、旅行风格，系统自动帮你规划完整行程——包括目的地推荐、航班比价、酒店匹配、每日活动安排，并且自动控制预算。

**核心亮点**:
- 7个Agent各司其职（Preference/Destination/Flight/Hotel/Activity/Budget/Replan），通过Pipeline + 并行 + 预算循环协作
- 航班/酒店/活动 **三Agent并行搜索**，互不依赖的搜索阶段延迟降至串行的约1/3
- **ReplanAgent 真·Agent实现**：LLM工具调用循环自主调整超预算方案，而非写死的规则
- **RAG增强的目的地推荐**：双路检索（向量+BM25），支持离线运行，零依赖
- 超预算自动触发 **渐进式降级循环**（最多3轮调整）
- **全链路可观测性**：Trace记录每个Agent/LLM/工具调用

---

## 系统架构

```
用户输入
  │
  ▼
┌────────────────┐
│ Preference     │  收集用户偏好（预算/风格/时间/禁忌）
│ Agent          │
└───────┬────────┘
        │
        ▼
┌────────────────┐
│ Destination    │  推荐目的地（RAG增强，多维度评分）
│ Agent (RAG)    │
└───────┬────────┘
        │
        ├──────────────────┬──────────────────┐
        ▼                  ▼                  ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│ Flight Agent │  │ Hotel Agent  │  │ Activity     │  ← 三个Agent并行执行
│ (航班搜索)    │  │ (酒店搜索)    │  │ Agent(活动)  │
└──────┬───────┘  └──────┬───────┘  └──────┬───────┘
       │                 │                 │
       └─────────────────┼─────────────────┘
                         │
                         ▼
               ┌────────────────┐
               │ Budget Agent   │  预算校验
               └───────┬────────┘
                       │
                ┌──────┴──────┐
                │             │
             通过？         超预算？
                │             │
                ▼             ▼
            输出行程     ReplanAgent
                        (LLM工具调用循环)
                              │
                        ┌─────┴──────┐
                        │            │
                    通过？    超预算/失败？
                        │            │
                        ▼            ▼
                    完成      回到并行搜索
                              (最多3轮)
```

**编排模式**: Pipeline（串行）+ 并行（asyncio.gather）+ 预算循环（while loop）

---

## 快速开始（5分钟上手）

### 前置条件

- Python 3.10+
- pip（Python包管理器）

### 安装与运行

```bash
# 1. 克隆项目
git clone https://github.com/SayHiToYoung/multi-agent-travel-planner.git
cd multi-agent-travel-planner

# 2. 进入Python目录
cd python

# 3. 安装依赖
pip install -r requirements.txt

# 4. 运行CLI演示（不需要任何API Key！）
python main.py

# 5. 自定义参数
python main.py --budget 15000 --departure 上海 --start 2026-06-01 --end 2026-06-07 --style luxury --travelers 2

# 6. 启动API服务
python -m api.app
# 访问 http://localhost:8000/docs 查看API文档

# 7. 启动Streamlit前端
streamlit run ui/streamlit_app.py
# 访问 http://localhost:8501

# 8. 运行测试
python -m pytest tests/ -v
```

---

## 运行效果展示

### CLI 输出示例

```
============================================================
📋 行程规划结果
============================================================

🌍 目的地: 首尔, 韩国
   潮流时尚与历史文化交汇
   亮点: 景福宫, 明洞, 北村韩屋村, 南山塔

✈️  去程: 东方航空 MU1903 ¥1578
✈️  返程: 南方航空 CZ6372 ¥1703

🏨 酒店: 首尔精品设计酒店 (4.0星)
   ¥395/晚 × 4 晚
   设施: WiFi, 早餐, 酒吧

📅 每日行程:

  2026-06-01 (日花费: ¥730)
    [morning  ] 博物馆参观 (3.0h) ¥80
    [afternoon] 温泉/SPA体验 (2.0h) ¥350
    [evening  ] 文化演出 (2.0h) ¥300

💰 预算明细:
   航班: ¥3281
   酒店: ¥1580
   活动: ¥2270
   ─────────────
   总计: ¥7131 / 预算: ¥10000
   ✅ 预算内
```

---

## 项目结构

```
.
├── README.md                    ← 你正在看的文件
│
├── python/                      ← Python 实现（主力版本）
│   ├── main.py                  ← CLI 入口
│   ├── requirements.txt
│   │
│   ├── config/
│   │   └── settings.py          ← 配置管理（支持环境变量覆盖）
│   │
│   ├── models/
│   │   └── schemas.py           ← Pydantic数据模型（7种状态、所有DTO）
│   │
│   ├── agents/                  ← 7个 Agent
│   │   ├── base_agent.py        ← Agent基类（模板方法模式）
│   │   ├── preference_agent.py  ← 偏好收集
│   │   ├── destination_agent.py ← 目的地推荐 (RAG增强)
│   │   ├── flight_agent.py      ← 航班搜索
│   │   ├── hotel_agent.py       ← 酒店搜索
│   │   ├── activity_agent.py    ← 活动推荐
│   │   ├── budget_agent.py      ← 预算校验与规则降级
│   │   └── replan_agent.py      ← ReplanAgent (LLM工具调用循环)
│   │
│   ├── orchestrator/            ← 编排层
│   │   ├── pipeline.py          ← Pipeline编排器
│   │   ├── parallel.py          ← 并行执行器 (asyncio.gather)
│   │   └── budget_loop.py       ← 预算循环控制
│   │
│   ├── knowledge/               ← RAG 知识库与检索
│   │   ├── knowledge_base.py    ← 知识库装配层（分块+策略选择）
│   │   ├── retriever.py         ← BM25词法检索 + 向量检索
│   │   ├── embedder.py          ← Embedding客户端 + 缓存
│   │   └── data/                ← 目的地知识库 (Markdown)
│   │       ├── tokyo.md
│   │       ├── osaka.md
│   │       └── ...
│   │
│   ├── tools/                   ← Mock搜索工具
│   │   ├── flight_search.py
│   │   ├── hotel_search.py
│   │   ├── activity_search.py
│   │   ├── weather_api.py
│   │   └── replan_search.py     ← ReplanAgent用的约束搜索
│   │
│   ├── observability/           ← 可观测性
│   │   ├── tracer.py            ← Trace记录（每个Agent/LLM/工具都有span）
│   │   └── exporters.py         ← Trace导出器
│   │
│   ├── api/
│   │   └── app.py               ← FastAPI 后端
│   │
│   ├── ui/
│   │   └── streamlit_app.py     ← Streamlit 前端
│   │
│   └── tests/                   ← 单元测试
│       ├── test_agents.py
│       ├── test_rag.py
│       ├── test_replan_agent.py
│       ├── test_structured_llm.py
│       ├── test_observability.py
│       └── conftest.py
```

---

## 7个Agent详解

| # | Agent | 职责 | 输入 | 输出 | 核心特性 |
|---|-------|------|------|------|---------|
| 1 | **PreferenceAgent** | 收集/补充用户偏好 | 原始用户输入 | enriched preferences | 校验预算、补齐默认值 |
| 2 | **DestinationAgent** | 推荐目的地 (RAG增强) | UserPreferences | Top3城市 + 推荐理由 | 双路检索、多维度评分 |
| 3 | **FlightAgent** | 航班搜索比价 | 出发/目的地+日期 | 往返航班 + 推荐 | 时长+中转+价格评分 |
| 4 | **HotelAgent** | 酒店匹配 | 目的地+日期+风格 | 酒店列表 + 推荐 | 风格匹配、房间计算 |
| 5 | **ActivityAgent** | 生成每日行程 | 目的地+天数+兴趣 | 每日活动计划 | 时间槽分配、预算约束 |
| 6 | **BudgetAgent** | 预算校验与规则降级 | 所有费用 | 预算明细 + 调整 | Rule模式（写死的渐进式降级） |
| 7 | **ReplanAgent** | 智能预算调整 (LLM驱动) | 所有费用+状态 | 调整方案/完成 | **真·Agent**（工具调用循环） |

---

## 核心技术亮点

### 1. 并行编排 (asyncio.gather)

三个搜索Agent互不依赖，通过 `asyncio.gather` 并发执行：
- 航班/酒店/活动同时搜索，搜索阶段延迟降至串行的约1/3
- `asyncio.wait_for` 单Agent超时控制
- `return_exceptions=True` 容错设计，单个失败不影响其他

### 2. ReplanAgent (真·Agent)

LLM工具调用循环，自主决策调整方案：
```
LLM查看预算明细 
  → 调用search_flights/search_hotels/search_activities之一
  → 代码执行工具，计算新预算
  → 反馈给LLM继续迭代
  → LLM调用finalize_plan结束
```

关键安全栏：
- 步数限制（REPLAN_MAX_STEPS，默认6步）
- 预算计算权由代码负责（不信任LLM算术，每步重算后回灌）
- 终态验收（循环无论怎么结束，都由代码重算预算并定论）
- LLM异常时自动降级回Rule模式

### 3. RAG增强的目的地推荐

**分块策略**：按Markdown二级标题切块，支持源追溯
```
# 东京（日本）
## 美食          ← 一个chunk（chunk_id: tokyo::美食）
## 景点          ← 一个chunk（chunk_id: tokyo::景点）
## 交通          ← 一个chunk（chunk_id: tokyo::交通）
```

**Query改写**：组合时间+风格+兴趣
- 出行月份提取 → 考虑季节因素
- 旅行风格转中文 → `cultural → "文化 历史 古迹 博物馆"`
- 融合用户兴趣和备注
- 结果：更特异化的检索query

**双路检索**（优雅降级）：
1. **向量检索**（配置EMBEDDING_MODEL时）
   - 调用OpenAI兼容/embeddings接口
   - 余弦相似度排序
   - Embedding缓存（cache key = sha256(model + text)）

2. **BM25词法检索**（向量失败/离线时）
   - 零依赖、完全离线可用
   - 中英混合分词（单字+二元组）
   - 无需jieba，bigram对短查询足够

为什么不用向量库（Chroma/Qdrant）？
- 知识库仅50个chunk，O(n)暴力检索<1ms足够
- 引入向量库属于过度设计
- 但接口抽象了，万一扩展也方便

### 4. 预算循环

**Rule模式**（默认）：渐进式降级
- 第1轮：砍活动30%
- 第2轮：降酒店星级
- 第3轮：换经济航班
- 确保有进展，避免无限循环

**Agent模式**（REPLAN_MODE=agent且LLM可用时）：
- ReplanAgent自主调整
- 异常自动降级到Rule模式

### 5. 全链路可观测性

每个操作都记录Trace Span：
```
pipeline:travel_planning (根span)
  ├─ agent:PreferenceAgent
  ├─ agent:DestinationAgent
  │  ├─ rag:retrieve (记录检索策略、命中chunk、相似度)
  │  └─ llm:call_structured
  ├─ parallel:search
  │  ├─ agent:FlightAgent
  │  ├─ agent:HotelAgent
  │  └─ agent:ActivityAgent
  ├─ agent:BudgetAgent
  └─ agent:ReplanAgent
     ├─ tool:search_flights
     ├─ tool:search_hotels
     └─ tool:search_activities
```

Trace导出到 `traces/{trace_id}.jsonl`，支持后续分析

---

## 配置说明

### 环境变量（在 `python/.env` 中配置）

```bash
# LLM配置
LLM_PROVIDER=mock                 # mock / minimax / openai
LLM_API_KEY=your-api-key          # 可选，mock模式不需要

# Embedding配置（用于RAG向量检索）
EMBEDDING_MODEL=text-embedding-3-small  # 可选
EMBEDDING_API_KEY=your-api-key         # 可选
EMBEDDING_BASE_URL=https://api.openai.com/v1  # 可选

# 预算循环配置
BUDGET_MAX_RETRIES=3              # 规则模式最多调整轮数
REPLAN_MAX_STEPS=6                # ReplanAgent最多步数
REPLAN_MODE=rule                  # rule / agent

# 其他
RAG_ENABLED=true                  # 是否启用RAG
RAG_TOP_K=4                        # 检索top-k
PARALLEL_TIMEOUT=30               # 并行Agent超时（秒）
```

**零成本运行**：
- LLM_PROVIDER=mock → 所有数据模拟生成，无需API Key
- RAG_ENABLED=true → BM25完全离线，不需要向量库

---

## API接口文档

### POST /api/plan

**请求**:
```json
{
  "budget": 10000,
  "departure_city": "北京",
  "start_date": "2026-06-01",
  "end_date": "2026-06-05",
  "travel_style": "comfort",
  "num_travelers": 1,
  "interests": ["美食", "历史"],
  "notes": ""
}
```

**响应**:
```json
{
  "destination": "首尔",
  "country": "韩国",
  "flight_cost": 3281,
  "hotel_cost": 1580,
  "activity_cost": 2270,
  "total_cost": 7131,
  "budget": 10000,
  "within_budget": true,
  "adjustment_rounds": 0,
  "hotel_name": "首尔精品设计酒店",
  "days": 4,
  "highlights": ["景福宫", "明洞", "北村韩屋村", "南山塔"]
}
```

### GET /api/health

```json
{"status": "ok", "service": "travel-planner"}
```

---

## 常见问题

### Q: 需要API Key吗？

**不需要！** 系统默认使用Mock模式，所有数据都是模拟生成的，可以零成本完整运行。

如果想接入真实LLM，设置环境变量即可：
```bash
export LLM_PROVIDER=minimax
export LLM_API_KEY=your-api-key
```

### Q: 数据是真实的吗？

Mock模式下的航班/酒店/活动数据是模拟的，但数据结构和业务逻辑与真实场景一致。系统架构支持接入真实API（Amadeus/Booking/Google Places）。

### Q: 怎么修改知识库？

在 `python/knowledge/data/` 下编辑Markdown文件即可。格式：
```markdown
# 城市名（国家）
## 美食
内容...
## 景点
内容...
```

重启程序时会自动重新加载和分块。

---

## 测试

```bash
cd python

# 运行所有测试
python -m pytest tests/ -v

# 运行特定测试
python -m pytest tests/test_agents.py -v
python -m pytest tests/test_rag.py -v
python -m pytest tests/test_replan_agent.py -v

# 查看覆盖率
python -m pytest tests/ --cov=. --cov-report=html
```

测试覆盖：
- Agent单元测试（PreferenceAgent / DestinationAgent / 等）
- RAG检索测试（BM25 / 向量检索）
- ReplanAgent工具调用循环
- 结构化LLM输出解析
- 可观测性Trace记录

---

## 架构设计决策

### 为什么选Pipeline而不是DAG？

| 维度 | Pipeline | DAG |
|------|----------|-----|
| **复杂度** | 简单，易实现 | 复杂，学习曲线陡 |
| **流程** | 线性+分叉+循环 | 任意依赖关系 |
| **本项目** | ✓（流程很线性：偏好→目的地→[并行]→循环） | ✗ |
| **库** | 自实现 (~100行) | LangGraph (~重型依赖) |

### 为什么不用LangChain/CrewAI？

这些框架很好，但对于这个项目来说是过度设计：
- LangChain：太多magic，不利于学习
- CrewAI：抽象层太多，调试困难
- 自实现Pipeline：轻量、可控、易于理解

### Rule模式 vs Agent模式

| 模式 | 决策者 | 调整策略 | 场景 |
|------|--------|---------|------|
| Rule | 代码 (if/else) | 写死：活动→酒店→航班 | 默认、稳定、可预测 |
| Agent | LLM (工具调用) | 自主决策（每次可能不同） | 高级、灵活、需要真实LLM |

---

## 技术栈

| 组件 | 技术 | 说明 |
|------|------|------|
| **框架** | FastAPI + asyncio | 异步Web框架 + 原生协程 |
| **数据** | Pydantic v2 | 严格类型校验、自动文档生成 |
| **并行** | asyncio.gather | IO密集适用，轻量级 |
| **RAG** | BM25 + 向量嵌入 | 双路检索、零依赖降级 |
| **LLM** | OpenAI兼容 | 支持自定义/本地LLM |
| **可观测** | 自定义Tracer | 链路追踪 |
| **前端** | Streamlit | 快速交互式UI |
| **测试** | pytest | 单元测试框架 |

---

## 学习价值

这个项目涵盖的技术点：

- **系统设计**：Pipeline编排、状态管理、容错设计
- **并发编程**：asyncio、gather、超时控制
- **LLM应用**：Prompt工程、结构化输出、工具调用循环
- **RAG**：文档分块、检索算法、向量与词法混合
- **软件工程**：设计模式、测试覆盖、可观测性
- **工程实践**：配置管理、错误处理、优雅降级

---

## License

MIT License - 自由使用、修改、分发。

---

## 快速链接

- 本地运行：`cd python && python main.py`
- API文档：启动API后访问 `http://localhost:8000/docs`
- Streamlit UI：`streamlit run ui/streamlit_app.py`
- 运行测试：`python -m pytest tests/ -v`
