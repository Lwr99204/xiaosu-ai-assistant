"""Chroma 向量存储与版本切换单测。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.errors import RAGError
from app.rag.chunking import Chunk
from app.rag.vector_store import ChromaStore

pytestmark = pytest.mark.allow_hosts("127.0.0.1")


def _chunk(doc_id: str, content_hash: str, text: str, index: int = 0) -> Chunk:
    return Chunk(
        chunk_id=f"{doc_id}#{index:04d}",
        doc_id=doc_id,
        index=index,
        text=text,
        heading_path=("制度",),
        char_start=0,
        char_end=len(text),
        content_hash=content_hash,
    )


@pytest.mark.asyncio
async def test_cosine_score_and_location_metadata_round_trip(tmp_path: Path) -> None:
    store = ChromaStore(path=tmp_path / "chroma", embed_model="test", embed_dim=3)
    chunk = _chunk("员工手册.md", "v1", "每年享受十天年假")
    await store.upsert([chunk], [[1.0, 0.0, 0.0]], filenames={chunk.doc_id: "员工手册.md"})
    await store.activate_version(chunk.doc_id, "v1")
    hits = await store.query([1.0, 0.0, 0.0], top_k=1)
    assert len(hits) == 1
    assert hits[0].score == pytest.approx(1.0)
    assert hits[0].chunk.char_start == 0
    assert hits[0].chunk.char_end == len(chunk.text)
    assert hits[0].chunk.content_hash == "v1"
    assert hits[0].filename == "员工手册.md"
    await store.close()


@pytest.mark.asyncio
async def test_staged_version_is_invisible_until_atomic_activation(tmp_path: Path) -> None:
    store = ChromaStore(path=tmp_path / "chroma", embed_model="test", embed_dim=2)
    old = _chunk("faq.md", "old", "旧版年假")
    new = _chunk("faq.md", "new", "新版年假")
    await store.upsert([old], [[1.0, 0.0]], filenames={old.doc_id: "FAQ.md"})
    await store.activate_version(old.doc_id, "old")
    await store.upsert([new], [[0.0, 1.0]], filenames={new.doc_id: "FAQ.md"})

    before = await store.query([0.0, 1.0], top_k=2)
    assert [hit.chunk.content_hash for hit in before] == ["old"]

    previous = await store.activate_version(new.doc_id, "new")
    after = await store.query([0.0, 1.0], top_k=2)
    assert previous == "old"
    assert [hit.chunk.content_hash for hit in after] == ["new"]
    assert await store.delete_version(old.doc_id, "old") == 1
    await store.close()


@pytest.mark.asyncio
async def test_failed_activation_keeps_old_version_visible(tmp_path: Path) -> None:
    store = ChromaStore(path=tmp_path / "chroma", embed_model="test", embed_dim=2)
    old = _chunk("faq.md", "old", "旧版内容")
    await store.upsert([old], [[1.0, 0.0]], filenames={old.doc_id: "FAQ.md"})
    await store.activate_version(old.doc_id, "old")
    with pytest.raises(RAGError, match="没有向量"):
        await store.activate_version(old.doc_id, "missing")
    hits = await store.query([1.0, 0.0], top_k=1)
    assert hits[0].chunk.content_hash == "old"
    await store.close()


@pytest.mark.asyncio
async def test_reopen_rejects_model_or_dimension_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "chroma"
    first = ChromaStore(path=path, embed_model="model-a", embed_dim=2)
    assert await first.count() == 0
    await first.close()
    second = ChromaStore(path=path, embed_model="model-b", embed_dim=2)
    with pytest.raises(RAGError) as info:
        await second.count()
    assert info.value.code == "VECTOR_INDEX_INCOMPATIBLE"
    await second.close()
