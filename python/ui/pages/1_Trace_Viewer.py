"""
Trace 查看器 —— 把每次规划的链路追踪还原成可视化调用树。

展示内容: Agent 执行顺序与耗时、LLM 调用与 token 消耗、
RAG 检索策略与命中、ReplanAgent 的工具调用决策路径。
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st

from config.settings import settings

st.set_page_config(page_title="Trace 查看器", page_icon="🔍", layout="wide")
st.title("🔍 Trace 查看器")
st.caption("一次规划 = 一条 trace。每个 Agent / LLM 调用 / RAG 检索 / 工具执行都是树上的一个 span。")

# ── 选择 trace 文件 ────────────────────────────────

trace_dir = Path(settings.TRACE_DIR)
files = (
    sorted(trace_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if trace_dir.exists()
    else []
)
if not files:
    st.info("还没有 trace 记录 —— 先回主页跑一次规划吧。")
    st.stop()

labels = {
    f"{p.stem}（{datetime.fromtimestamp(p.stat().st_mtime):%m-%d %H:%M:%S}）": p
    for p in files[:50]
}
choice = st.selectbox("选择一次运行（按时间倒序, 最新在前）", list(labels))
path = labels[choice]

spans = [
    json.loads(line)
    for line in path.read_text(encoding="utf-8").splitlines()
    if line.strip()
]

# ── 汇总指标 ──────────────────────────────────────

llm_spans = [s for s in spans if s["kind"] == "generation"]
tool_spans = [s for s in spans if s["kind"] == "tool"]
total_tokens = sum(s["attributes"].get("total_tokens") or 0 for s in llm_spans)
roots = [s for s in spans if not s.get("parent_id")]
root_duration = max((s["duration_ms"] for s in roots), default=0)

c1, c2, c3, c4 = st.columns(4)
c1.metric("总耗时", f"{root_duration / 1000:.2f}s")
c2.metric("LLM 调用", f"{len(llm_spans)} 次")
c3.metric("Token 消耗", f"{total_tokens}" if total_tokens else "0 (mock)")
c4.metric("工具调用", f"{len(tool_spans)} 次")

# ── 构建并渲染调用树 ──────────────────────────────

by_id = {s["span_id"]: s for s in spans}
children: dict[str, list] = defaultdict(list)
top_level = []
for s in spans:
    pid = s.get("parent_id")
    if pid and pid in by_id:
        children[pid].append(s)
    else:
        top_level.append(s)
for lst in children.values():
    lst.sort(key=lambda s: s["start_time"])
top_level.sort(key=lambda s: s["start_time"])

KIND_ICON = {
    "trace": "🚀", "agent": "🤖", "generation": "🧠",
    "retrieval": "📚", "tool": "🔧", "internal": "⚙️",
}


def _attr_summary(s: dict) -> str:
    a = s.get("attributes", {})
    parts: list[str] = []
    if a.get("total_tokens"):
        parts.append(f"tokens={a['total_tokens']}")
    if a.get("model"):
        parts.append(str(a["model"]))
    if a.get("strategy"):
        parts.append(f"检索={a['strategy']}")
    if a.get("hits"):
        parts.append(f"命中={len(a['hits'])}块")
    if a.get("arguments"):
        parts.append(f"参数={json.dumps(a['arguments'], ensure_ascii=False)}")
    if a.get("tool_calls"):
        parts.append(f"→{','.join(a['tool_calls'])}")
    if a.get("result_state"):
        parts.append(str(a["result_state"]))
    if a.get("llm_fallback"):
        parts.append("⚠️ LLM降级")
    if s.get("error"):
        parts.append(f"错误: {s['error']}")
    return "  ".join(parts)


lines: list[str] = []


def _walk(s: dict, depth: int) -> None:
    status = "❌" if s["status"] == "error" else KIND_ICON.get(s["kind"], "•")
    indent = "    " * depth
    summary = _attr_summary(s)
    lines.append(f"{indent}{status} {s['name']:<32s} {s['duration_ms']:>8.1f}ms   {summary}")
    for child in children.get(s["span_id"], []):
        _walk(child, depth + 1)


for s in top_level:
    _walk(s, 0)

st.code("\n".join(lines), language=None)

st.caption(
    "🤖 Agent  🧠 LLM调用  📚 RAG检索  🔧 工具执行 —— "
    "ReplanAgent 的每次工具调用参数都在树上, 不同运行的决策路径可能不同。"
)

with st.expander("原始 span 数据 (JSON)"):
    st.json(spans)
