"""
Sunset Apricot 视觉主题 —— 纯 CSS 注入, 不含任何业务逻辑。

与 .streamlit/config.toml 配合使用:
  config.toml 负责 Streamlit 原生主题变量 (主色/底色/文字色),
  本模块负责 config.toml 覆盖不到的细节 (渐变按钮/卡片阴影/圆角/悬浮动效)。
"""

from __future__ import annotations

import streamlit as st

_CSS = """
<style>
/* ── 主按钮: 暖色渐变 + 悬浮上浮 ── */
.stButton > button[kind="primary"] {
    background: linear-gradient(135deg, #F2A65A 0%, #E07B39 55%, #D96C3F 100%);
    border: none;
    border-radius: 12px;
    box-shadow: 0 4px 14px rgba(224, 123, 57, 0.35);
    transition: transform 0.18s ease, box-shadow 0.18s ease;
    font-weight: 600;
    letter-spacing: 0.5px;
}
.stButton > button[kind="primary"]:hover {
    transform: translateY(-2px);
    box-shadow: 0 8px 22px rgba(224, 123, 57, 0.45);
}
.stButton > button[kind="primary"]:active {
    transform: translateY(0);
}

/* ── 指标卡片 (st.metric) ── */
[data-testid="stMetric"] {
    background: #FFFBF4;
    border: 1px solid #F0DFC8;
    border-radius: 14px;
    padding: 14px 16px;
    box-shadow: 0 2px 10px rgba(180, 120, 60, 0.08);
}

/* ── 侧边栏 ── */
[data-testid="stSidebar"] {
    background: #F8EFE3;
}

/* ── 标签页选中态 ── */
button[data-baseweb="tab"][aria-selected="true"] {
    color: #D96C3F;
    font-weight: 600;
}
[data-testid="stTabs"] [data-baseweb="tab-highlight"] {
    background-color: #E07B39;
}

/* ── 输入控件圆角 ── */
[data-testid="stTextInput"] input,
[data-testid="stNumberInput"] input,
[data-testid="stDateInput"] input,
.stTextArea textarea,
[data-testid="stSelectbox"] > div > div {
    border-radius: 10px;
}

/* ── 提示框 / 展开器 / 代码块 圆角统一 ── */
[data-testid="stAlert"] {
    border-radius: 12px;
}
[data-testid="stExpander"] {
    border-radius: 12px;
    border-color: #F0DFC8;
}
.stCode, pre {
    border-radius: 12px !important;
}

/* ── 标题: 陶土暖棕层级 ── */
h1 {
    color: #A8542F;
}
h2, h3 {
    color: #7A4A30;
}

/* ── 分隔线弱化 ── */
hr {
    border-color: #EBD9C0;
}
</style>
"""


def inject_theme() -> None:
    """注入自定义 CSS。在 st.set_page_config 之后调用一次即可。"""
    st.markdown(_CSS, unsafe_allow_html=True)
