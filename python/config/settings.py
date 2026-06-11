"""
全局配置管理 —— 通过环境变量加载，绝不硬编码密钥。
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent


def _env_bool(key: str, default: str = "true") -> bool:
    return os.getenv(key, default).strip().lower() in ("1", "true", "yes", "on")


class Settings:
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "mock")
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
    LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "https://api.minimax.chat/v1")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "MiniMax-M2.7")
    LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", "0.7"))
    LLM_MAX_TOKENS: int = int(os.getenv("LLM_MAX_TOKENS", "4096"))
    # 结构化输出解析失败后, 携带错误信息让 LLM 重新生成的最大次数
    LLM_STRUCTURED_MAX_REPAIR: int = int(os.getenv("LLM_STRUCTURED_MAX_REPAIR", "2"))

    # ── 超预算调整策略 ──
    # rule  = 写死的渐进降级 (Workflow 模式, 默认)
    # agent = ReplanAgent 工具调用循环, LLM 自主决策 (需真实 LLM, mock 下自动回退 rule)
    REPLAN_MODE: str = os.getenv("REPLAN_MODE", "rule")
    REPLAN_MAX_STEPS: int = int(os.getenv("REPLAN_MAX_STEPS", "6"))

    # ── 可观测性 ──
    TRACE_ENABLED: bool = _env_bool("TRACE_ENABLED", "true")
    TRACE_DIR: str = os.getenv("TRACE_DIR", str(BASE_DIR / "traces"))
    LANGFUSE_PUBLIC_KEY: str = os.getenv("LANGFUSE_PUBLIC_KEY", "")
    LANGFUSE_SECRET_KEY: str = os.getenv("LANGFUSE_SECRET_KEY", "")
    LANGFUSE_HOST: str = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")

    # ── RAG ──
    RAG_ENABLED: bool = _env_bool("RAG_ENABLED", "true")
    RAG_TOP_K: int = int(os.getenv("RAG_TOP_K", "4"))
    # 配置了 EMBEDDING_MODEL + key 才走向量检索, 否则回退本地 BM25 (零依赖零成本)
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "")
    EMBEDDING_BASE_URL: str = os.getenv("EMBEDDING_BASE_URL", "") or os.getenv("LLM_BASE_URL", "https://api.minimax.chat/v1")
    EMBEDDING_API_KEY: str = os.getenv("EMBEDDING_API_KEY", "") or os.getenv("LLM_API_KEY", "")

    BUDGET_MAX_RETRIES: int = int(os.getenv("BUDGET_MAX_RETRIES", "3"))
    PARALLEL_TIMEOUT: int = int(os.getenv("PARALLEL_TIMEOUT", "30"))

    API_HOST: str = os.getenv("API_HOST", "0.0.0.0")
    API_PORT: int = int(os.getenv("API_PORT", "8000"))

    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")


settings = Settings()
