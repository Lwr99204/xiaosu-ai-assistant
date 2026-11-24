"""检索层：查询向量化 → 过采样 → 同文档限额 → 阈值过滤 → 相对间隔判据（TDD §4.4.4）。

五步的顺序是刻意的：

1. 先按 top_k * oversample 过采样再做限额。直接取 top_k 再限额，会让命中数少于
   top_k，白白丢掉其他文档里相关的内容。
2. score = 1 - 余弦距离。ChromaStore 已显式声明 hnsw:space=cosine，距离落在 [0, 2]，
   换算后 score 落在 [-1, 1]，与 retrieval_min_score=0.55 同一口径。
3. 阈值过滤放在限额之后。限额解决的是「别让单篇长文刷屏」，阈值解决的是「压根不相关」，
   顺序颠倒会让不相关的块先占掉限额名额。
4. 相对间隔判据（TDD R16③）：top1 与命中均分咬得太近，说明这一堆内容在向量空间里
   彼此分不出高下，检索结果不可信，按没查到处理，交给 L2 拒答话术。

本模块不 import app.tools（TDD §3.1）。search_for_tool 只是产出符合
tools.knowledge.KnowledgeSearchFn 约定的普通 mapping，注入动作由 services 层完成。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.config import Settings
from app.core.errors import EmbeddingError
from app.core.retry import retry_async
from app.rag.embedder import EmbeddingProvider
from app.rag.vector_store import HEADING_SEP, SNIPPET_MAX_CHARS, Hit, VectorStore, make_snippet

logger = logging.getLogger(__name__)

#: 过采样倍数。取 2 够覆盖单文档限额筛掉的量，又不至于让 Chroma 做无谓计算。
DEFAULT_OVERSAMPLE = 2
#: 同一文档最多保留的块数，防止一篇长文占满 top_k。
DEFAULT_MAX_PER_DOC = 3
#: top1 与命中均分的最小间隔，低于它判定检索不可靠。
DEFAULT_MIN_SPREAD = 0.05
#: 喂给模型的摘要长度。比引用展示用的 200 字更长，年假三档天数这类细节才不会被截掉。
DEFAULT_TOOL_SNIPPET_CHARS = 300
DEFAULT_TOP_K = 8
DEFAULT_MIN_SCORE = 0.55


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    """一次检索的完整结论。

    reliable=False 时调用方应当按「没查到」处理，不要把 hits 交给模型，否则模型会
    基于一堆低分噪声编出看似有据的答案，而 L3 引用后校验对此无能为力（编号是合法的）。
    """

    hits: tuple[Hit, ...]
    reliable: bool
    top_score: float
    spread: float
    reason: str = ""

    @property
    def empty(self) -> bool:
        return not self.hits


def hit_to_mapping(hit: Hit, snippet_chars: int = SNIPPET_MAX_CHARS) -> dict[str, Any]:
    """把 Hit 转成引用视图与工具输出共用的 mapping。

    heading_path 输出成 HEADING_SEP 连接的字符串而不是列表：knowledge.format_references
    对这个字段直接 str()，传列表会渲染成带方括号和引号的样子。
    """
    chunk = hit.chunk
    return {
        "doc_id": chunk.doc_id,
        "filename": hit.filename or chunk.doc_id,
        "heading_path": HEADING_SEP.join(chunk.heading_path),
        "snippet": make_snippet(chunk.text, snippet_chars),
        "char_start": chunk.char_start,
        "char_end": chunk.char_end,
        "page": chunk.page,
        "score": round(hit.score, 4),
    }


def _cap_per_doc(hits: Sequence[Hit], max_per_doc: int) -> list[Hit]:
    """按文档限额保留命中，保持原有相关性顺序。"""
    kept: list[Hit] = []
    seen: dict[str, int] = {}
    for hit in hits:
        used = seen.get(hit.chunk.doc_id, 0)
        if used < max_per_doc:
            seen[hit.chunk.doc_id] = used + 1
            kept.append(hit)
    return kept


def _judge(hits: tuple[Hit, ...], *, min_spread: float) -> RetrievalResult:
    """按阈值过滤后的命中集合判定信度。"""
    if not hits:
        return RetrievalResult(
            hits=(),
            reliable=False,
            top_score=0.0,
            spread=0.0,
            reason="no_hit",
        )
    top = hits[0].score
    if len(hits) == 1:
        # 只有一条命中时均分就是它自己，间隔恒为 0，不能据此判不可靠。
        return RetrievalResult(hits=hits, reliable=True, top_score=top, spread=0.0)
    mean = sum(hit.score for hit in hits) / len(hits)
    spread = top - mean
    reliable = spread >= min_spread
    return RetrievalResult(
        hits=hits,
        reliable=reliable,
        top_score=top,
        spread=spread,
        reason="" if reliable else "low_spread",
    )


class Retriever:
    """把 EmbeddingProvider 与 VectorStore 组合成一次可判信度的检索。"""

    def __init__(
        self,
        *,
        embedder: EmbeddingProvider,
        store: VectorStore,
        top_k: int = DEFAULT_TOP_K,
        min_score: float = DEFAULT_MIN_SCORE,
        max_per_doc: int = DEFAULT_MAX_PER_DOC,
        oversample: int = DEFAULT_OVERSAMPLE,
        min_spread: float = DEFAULT_MIN_SPREAD,
        snippet_chars: int = DEFAULT_TOOL_SNIPPET_CHARS,
        max_retries: int = 1,
    ) -> None:
        self._embedder = embedder
        self._store = store
        self._top_k = top_k
        self._min_score = min_score
        self._max_per_doc = max_per_doc
        self._oversample = max(1, oversample)
        self._min_spread = min_spread
        self._snippet_chars = snippet_chars
        self._max_retries = max_retries

    async def search(self, query: str, *, top_k: int | None = None) -> RetrievalResult:
        """执行一次检索并给出信度结论。"""
        limit = top_k if top_k and top_k > 0 else self._top_k
        embedding = await self._embed_query(query)
        raw = await self._store.query(embedding, top_k=limit * self._oversample)
        capped = _cap_per_doc(raw, self._max_per_doc)
        passed = [hit for hit in capped if hit.score >= self._min_score]
        return _judge(tuple(passed[:limit]), min_spread=self._min_spread)

    async def search_for_tool(self, query: str, top_k: int) -> list[dict[str, Any]]:
        """产出 tools.knowledge.KnowledgeSearchFn 约定的命中列表。

        不可靠或空命中一律返回空列表：knowledge 工具会把它渲染成「没有检索到相关内容」，
        模型据 system prompt 第 3 条给出不知道话术，这就是 L2 阈值拒答的落点。
        """
        result = await self.search(query, top_k=top_k)
        if not result.reliable:
            logger.info(
                "knowledge_retrieval_unreliable reason=%s top_score=%.4f spread=%.4f kept=%d",
                result.reason,
                result.top_score,
                result.spread,
                len(result.hits),
            )
            return []
        return [hit_to_mapping(hit, self._snippet_chars) for hit in result.hits]

    async def _embed_query(self, query: str) -> list[float]:
        vectors = await retry_async(
            lambda: self._embedder.embed([query]),
            operation="embed_query",
            max_retries=self._max_retries,
        )
        if not vectors:
            raise EmbeddingError("向量化服务返回空结果", retryable=True)
        return vectors[0]


def build_retriever(
    settings: Settings,
    *,
    embedder: EmbeddingProvider,
    store: VectorStore,
) -> Retriever:
    """组合根用的工厂，top_k 与阈值全部取自 settings，避免两处各写一份默认值。"""
    return Retriever(
        embedder=embedder,
        store=store,
        top_k=settings.retrieval_top_k,
        min_score=settings.retrieval_min_score,
    )
