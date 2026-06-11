"""
目的地知识库 —— RAG 的装配层: 加载文档 → 分块 → 选检索策略 → 提供 retrieve()。

文档格式 (knowledge/data/*.md):
  # 城市名（国家）          ← 一级标题 = 城市
  ## 小节标题               ← 二级标题切块, 一节一个 chunk
  正文...

检索策略 (优雅降级):
  - 配置了 EMBEDDING_MODEL + API Key → 向量语义检索
  - 否则 / 向量检索异常        → 本地 BM25 词法检索 (零依赖, 离线可用)
"""

from __future__ import annotations

import re
from pathlib import Path

from loguru import logger

from config.settings import settings
from observability import get_tracer

from .embedder import ApiEmbedder
from .retriever import BM25Index, Chunk, RetrievedChunk, VectorIndex

DATA_DIR = Path(__file__).resolve().parent / "data"

_H1 = re.compile(r"^#\s+(.+)$", re.MULTILINE)


def load_chunks(data_dir: Path = DATA_DIR) -> list[Chunk]:
    """读取所有城市文档, 按二级标题切块。"""
    chunks: list[Chunk] = []
    for path in sorted(data_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        m = _H1.search(text)
        city_title = m.group(1).strip() if m else path.stem
        city = re.split(r"[（(]", city_title)[0].strip()

        body = text[m.end():] if m else text
        sections = re.split(r"^##\s+", body, flags=re.MULTILINE)
        for section in sections:
            section = section.strip()
            if not section:
                continue
            lines = section.split("\n", 1)
            title = lines[0].strip()
            content = lines[1].strip() if len(lines) > 1 else ""
            if not content:
                continue
            chunks.append(
                Chunk(
                    chunk_id=f"{path.stem}::{title}",
                    text=f"{city_title} {title}\n{content}",
                    metadata={"city": city, "section": title, "source": path.name},
                )
            )
    return chunks


class KnowledgeBase:
    """对外只暴露一个 retrieve(); 内部自动选择/降级检索策略。"""

    def __init__(self, data_dir: Path = DATA_DIR) -> None:
        self.chunks = load_chunks(data_dir)
        self._bm25 = BM25Index(self.chunks)
        self._vector_index: VectorIndex | None = None
        self._embedder: ApiEmbedder | None = None
        self._vector_disabled = False
        logger.info(f"[KnowledgeBase] 已加载 {len(self.chunks)} 个知识块")

    @property
    def _vector_enabled(self) -> bool:
        return bool(
            settings.EMBEDDING_MODEL
            and settings.EMBEDDING_API_KEY
            and not self._vector_disabled
        )

    async def _ensure_vector_index(self) -> VectorIndex:
        if self._vector_index is None:
            self._embedder = ApiEmbedder(
                model=settings.EMBEDDING_MODEL,
                base_url=settings.EMBEDDING_BASE_URL,
                api_key=settings.EMBEDDING_API_KEY,
                cache_path=str(DATA_DIR / ".embeddings_cache.json"),
            )
            vectors = await self._embedder.embed([c.text for c in self.chunks])
            self._vector_index = VectorIndex(self.chunks, vectors)
            logger.info("[KnowledgeBase] 向量索引构建完成")
        return self._vector_index

    async def retrieve(self, query: str, top_k: int | None = None) -> list[RetrievedChunk]:
        top_k = top_k or settings.RAG_TOP_K
        tracer = get_tracer()
        async with tracer.span("rag:retrieve", kind="retrieval", query=query, top_k=top_k) as span:
            results: list[RetrievedChunk]
            if self._vector_enabled:
                try:
                    index = await self._ensure_vector_index()
                    assert self._embedder is not None
                    query_vector = (await self._embedder.embed([query]))[0]
                    results = index.search_by_vector(query_vector, top_k)
                    span.set(strategy="vector")
                except Exception as exc:
                    logger.warning(f"[KnowledgeBase] 向量检索失败, 本次会话回退 BM25: {exc}")
                    self._vector_disabled = True
                    results = self._bm25.search(query, top_k)
                    span.set(strategy="bm25_fallback", fallback_reason=str(exc))
            else:
                results = self._bm25.search(query, top_k)
                span.set(strategy="bm25")

            span.set(hits=[(r.chunk.chunk_id, r.score) for r in results])

            from events import emit  # 延迟导入避免环
            emit("rag_result",
                 agent="DestinationAgent",
                 message=f"知识库命中 {len(results)} 条资料",
                 data={"hits": [r.chunk.chunk_id for r in results]})
            return results


_kb: KnowledgeBase | None = None


def get_knowledge_base() -> KnowledgeBase:
    global _kb
    if _kb is None:
        _kb = KnowledgeBase()
    return _kb
