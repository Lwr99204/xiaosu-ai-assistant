"""检索排序、过滤和信度判定单测。"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from app.rag.chunking import Chunk
from app.rag.retriever import Retriever, hit_to_mapping
from app.rag.vector_store import Hit

pytestmark = pytest.mark.allow_hosts("127.0.0.1")


class _Embedder:
    model = "fake"
    dim = 2

    def __init__(self) -> None:
        self.inputs: list[list[str]] = []

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.inputs.append(list(texts))
        return [[1.0, 0.0] for _ in texts]


class _Store:
    def __init__(self, hits: list[Hit]) -> None:
        self.hits = hits
        self.requested_top_k = 0

    async def query(self, _vector: Sequence[float], *, top_k: int, where=None) -> list[Hit]:
        self.requested_top_k = top_k
        return self.hits[:top_k]


def _hit(doc_id: str, index: int, score: float) -> Hit:
    text = f"{doc_id} 第 {index} 块制度正文"
    chunk = Chunk(
        chunk_id=f"{doc_id}#{index:04d}",
        doc_id=doc_id,
        index=index,
        text=text,
        heading_path=("第一章", "年假"),
        char_start=index * 100,
        char_end=index * 100 + len(text),
        content_hash="v1",
    )
    return Hit(chunk=chunk, score=score, snippet=text, filename=doc_id)


@pytest.mark.asyncio
async def test_search_oversamples_caps_each_doc_and_filters_threshold() -> None:
    hits = [
        _hit("a.md", 0, 0.90),
        _hit("a.md", 1, 0.82),
        _hit("a.md", 2, 0.74),
        _hit("a.md", 3, 0.70),
        _hit("b.md", 0, 0.61),
        _hit("c.md", 0, 0.54),
    ]
    store = _Store(hits)
    retriever = Retriever(embedder=_Embedder(), store=store, min_spread=0.01)
    result = await retriever.search("年假", top_k=3)
    assert store.requested_top_k == 6
    assert [(hit.chunk.doc_id, hit.chunk.index) for hit in result.hits] == [
        ("a.md", 0),
        ("a.md", 1),
        ("a.md", 2),
    ]
    assert all(hit.score >= 0.55 for hit in result.hits)
    assert result.reliable is True


@pytest.mark.asyncio
async def test_search_marks_close_scores_as_unreliable() -> None:
    store = _Store([_hit("a.md", 0, 0.70), _hit("b.md", 0, 0.69), _hit("c.md", 0, 0.68)])
    result = await Retriever(embedder=_Embedder(), store=store).search("制度", top_k=3)
    assert result.hits
    assert result.reliable is False
    assert result.reason == "low_spread"
    assert await Retriever(embedder=_Embedder(), store=store).search_for_tool("制度", 3) == []


@pytest.mark.asyncio
async def test_search_returns_no_hit_below_absolute_threshold() -> None:
    result = await Retriever(
        embedder=_Embedder(),
        store=_Store([_hit("a.md", 0, 0.54)]),
    ).search("不存在的制度", top_k=1)
    assert result.empty
    assert result.reliable is False
    assert result.reason == "no_hit"


@pytest.mark.asyncio
async def test_single_hit_is_reliable_and_mapping_keeps_offsets() -> None:
    hit = _hit("员工手册.md", 2, 0.77)
    result = await Retriever(embedder=_Embedder(), store=_Store([hit])).search("年假", top_k=1)
    assert result.reliable is True
    mapping = hit_to_mapping(result.hits[0])
    assert mapping["filename"] == "员工手册.md"
    assert mapping["heading_path"] == "第一章 › 年假"
    assert mapping["char_start"] == 200
    assert mapping["char_end"] == 200 + len(hit.chunk.text)
    assert mapping["score"] == 0.77
