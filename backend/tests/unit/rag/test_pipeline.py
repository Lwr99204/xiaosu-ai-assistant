"""RAG 索引编排与替换回滚单测。"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from app.config import Settings
from app.rag.pipeline import RAGPipeline

pytestmark = pytest.mark.allow_hosts("127.0.0.1")


class _Embedder:
    model = "fake"
    dim = 2

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [[1.0, float(index)] for index, _ in enumerate(texts)]

    async def warmup(self) -> None:
        return None


class _Store:
    def __init__(self, *, fail_upsert: bool = False) -> None:
        self.active = "old-hash"
        self.fail_upsert = fail_upsert
        self.upserted = []
        self.deleted: list[str] = []

    async def upsert(self, chunks, vectors, *, filenames) -> None:
        if self.fail_upsert:
            raise RuntimeError("write failed")
        self.upserted = list(chunks)
        assert len(chunks) == len(vectors)
        assert filenames[chunks[0].doc_id] == "制度.md"

    async def activate_version(self, _doc_id: str, content_hash: str) -> str:
        previous = self.active
        self.active = content_hash
        return previous

    async def delete_version(self, _doc_id: str, content_hash: str) -> int:
        self.deleted.append(content_hash)
        return 1

    async def delete_by_doc(self, doc_id: str) -> int:
        self.deleted.append(doc_id)
        return 3

    async def query(self, vector, *, top_k: int, where=None):
        return []

    async def count(self) -> int:
        return len(self.upserted)


def _settings() -> Settings:
    return Settings(_env_file=None, llm_provider="mock", llm_api_key="sk-test-1")


@pytest.mark.asyncio
async def test_index_file_writes_activates_then_cleans_old_version(tmp_path: Path) -> None:
    path = tmp_path / "制度.md"
    path.write_text("# 年假制度\n员工每年享受十天年假。", encoding="utf-8")
    store = _Store()
    pipeline = RAGPipeline(settings=_settings(), embedder=_Embedder(), store=store)
    result = await pipeline.index_file(path)
    assert result.doc_id == "制度.md"
    assert result.chunk_count == 1
    assert result.previous_hash == "old-hash"
    assert store.active == result.content_hash
    assert store.deleted == ["old-hash"]
    assert store.upserted[0].text.startswith("# 年假制度")


@pytest.mark.asyncio
async def test_index_failure_keeps_old_version_active(tmp_path: Path) -> None:
    path = tmp_path / "制度.md"
    path.write_text("# 新版制度\n新内容", encoding="utf-8")
    store = _Store(fail_upsert=True)
    pipeline = RAGPipeline(settings=_settings(), embedder=_Embedder(), store=store)
    with pytest.raises(RuntimeError, match="write failed"):
        await pipeline.index_file(path)
    assert store.active == "old-hash"
    assert store.deleted == []


@pytest.mark.asyncio
async def test_retrieve_rejects_empty_query() -> None:
    pipeline = RAGPipeline(settings=_settings(), embedder=_Embedder(), store=_Store())
    with pytest.raises(Exception, match="检索问题不能为空"):
        await pipeline.retrieve("   ")


@pytest.mark.asyncio
async def test_verify_ready_warms_embedder_and_counts_vectors() -> None:
    store = _Store()
    pipeline = RAGPipeline(settings=_settings(), embedder=_Embedder(), store=store)
    assert await pipeline.verify_ready() == 0


@pytest.mark.asyncio
async def test_delete_document_delegates_to_vector_store() -> None:
    store = _Store()
    pipeline = RAGPipeline(settings=_settings(), embedder=_Embedder(), store=store)
    assert await pipeline.delete_document("制度.md") == 3
    assert store.deleted == ["制度.md"]

    with pytest.raises(Exception, match="文档 ID 不能为空"):
        await pipeline.delete_document("  ")
