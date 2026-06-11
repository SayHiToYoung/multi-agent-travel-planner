"""
检索器实现 —— RAG 的 "R"。

两种实现 (检索方案选型):
  - BM25Index:   词法检索, 零依赖零成本, 中文用"字符二元组"分词
                 (无需 jieba: 对短查询场景 bigram 召回已足够)
  - VectorIndex: 语义检索, 调 OpenAI 兼容 /embeddings 接口, 余弦相似度排序
                 配置了 EMBEDDING_MODEL 后自动启用, 失败回退 BM25

为什么不上 Chroma/FAISS? —— 知识库仅几十个 chunk, 内存暴力检索 O(n) 足够,
引入向量数据库属于过度设计; 但接口抽象 (search) 保留了换库的空间。
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Sequence


@dataclass
class Chunk:
    """知识库的最小检索单元 (= 一个城市文档的一个小节)。"""

    chunk_id: str
    text: str
    metadata: dict = field(default_factory=dict)  # {"city": ..., "section": ...}


@dataclass
class RetrievedChunk:
    chunk: Chunk
    score: float


_ASCII_WORD = re.compile(r"[a-z0-9]+")
_CJK = re.compile(r"[一-鿿]")


def tokenize(text: str) -> list[str]:
    """中英混合分词: 英文按单词, 中文按 单字 + 二元组。"""
    text = text.lower()
    tokens = _ASCII_WORD.findall(text)
    cjk_chars = _CJK.findall(text)
    tokens.extend(cjk_chars)
    tokens.extend(a + b for a, b in zip(cjk_chars, cjk_chars[1:]))
    return tokens


class BM25Index:
    """经典 BM25 (k1=1.5, b=0.75) 的最小实现, ~40 行。"""

    def __init__(self, chunks: Sequence[Chunk], k1: float = 1.5, b: float = 0.75) -> None:
        self.chunks = list(chunks)
        self.k1, self.b = k1, b
        self._doc_tokens = [Counter(tokenize(c.text)) for c in self.chunks]
        self._doc_lens = [sum(tc.values()) for tc in self._doc_tokens]
        self._avg_len = (sum(self._doc_lens) / len(self._doc_lens)) if self.chunks else 0.0
        # 文档频率 → IDF
        df: Counter = Counter()
        for tc in self._doc_tokens:
            df.update(tc.keys())
        n = len(self.chunks)
        self._idf = {
            term: math.log((n - d + 0.5) / (d + 0.5) + 1.0) for term, d in df.items()
        }

    def search(self, query: str, top_k: int = 4) -> list[RetrievedChunk]:
        q_terms = tokenize(query)
        scored: list[RetrievedChunk] = []
        for chunk, tc, dl in zip(self.chunks, self._doc_tokens, self._doc_lens):
            score = 0.0
            for term in q_terms:
                tf = tc.get(term, 0)
                if tf == 0:
                    continue
                idf = self._idf.get(term, 0.0)
                norm = tf * (self.k1 + 1) / (tf + self.k1 * (1 - self.b + self.b * dl / self._avg_len))
                score += idf * norm
            if score > 0:
                scored.append(RetrievedChunk(chunk=chunk, score=round(score, 4)))
        scored.sort(key=lambda r: r.score, reverse=True)
        return scored[:top_k]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class VectorIndex:
    """内存向量索引: 余弦相似度暴力检索。向量由外部 (Embedder) 提供。"""

    def __init__(self, chunks: Sequence[Chunk], vectors: Sequence[Sequence[float]]) -> None:
        if len(chunks) != len(vectors):
            raise ValueError("chunks 与 vectors 数量不一致")
        self.chunks = list(chunks)
        self.vectors = [list(v) for v in vectors]

    def search_by_vector(self, query_vector: Sequence[float], top_k: int = 4) -> list[RetrievedChunk]:
        scored = [
            RetrievedChunk(chunk=c, score=round(cosine(query_vector, v), 4))
            for c, v in zip(self.chunks, self.vectors)
        ]
        scored.sort(key=lambda r: r.score, reverse=True)
        return scored[:top_k]
