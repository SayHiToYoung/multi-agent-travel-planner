"""
Embedding 客户端 —— 调用 OpenAI 兼容的 /embeddings 接口。

设计要点:
  - 为什么要本地缓存 embedding? 知识库文档不变, 每次启动重新嵌入纯属浪费 token
  - 缓存 key = 模型名 + 文本内容哈希, 模型或文档一变自动失效
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
from loguru import logger


class ApiEmbedder:
    def __init__(self, model: str, base_url: str, api_key: str, cache_path: str | None = None) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._cache_path = Path(cache_path) if cache_path else None
        self._cache: dict[str, list[float]] = self._load_cache()

    def _cache_key(self, text: str) -> str:
        return hashlib.sha256(f"{self.model}::{text}".encode("utf-8")).hexdigest()

    def _load_cache(self) -> dict[str, list[float]]:
        if self._cache_path and self._cache_path.exists():
            try:
                return json.loads(self._cache_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning(f"[Embedder] 缓存读取失败, 重建: {exc}")
        return {}

    def _save_cache(self) -> None:
        if self._cache_path:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._cache_path.write_text(
                json.dumps(self._cache, ensure_ascii=False), encoding="utf-8"
            )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """批量嵌入, 命中缓存的不再请求。"""
        results: dict[int, list[float]] = {}
        missing: list[tuple[int, str]] = []
        for i, text in enumerate(texts):
            cached = self._cache.get(self._cache_key(text))
            if cached is not None:
                results[i] = cached
            else:
                missing.append((i, text))

        if missing:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"{self.base_url}/embeddings",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={"model": self.model, "input": [t for _, t in missing]},
                )
                resp.raise_for_status()
                data = resp.json()["data"]
            for (i, text), item in zip(missing, data):
                vector = item["embedding"]
                results[i] = vector
                self._cache[self._cache_key(text)] = vector
            self._save_cache()

        return [results[i] for i in range(len(texts))]
