"""本地 BGE embedding 客户端单测。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.config import Settings
from app.core.errors import EmbeddingError
from app.rag.embedder import BGEEmbedder, build_embedder

pytestmark = pytest.mark.allow_hosts("127.0.0.1")


class _HealthResponse:
    def __init__(self, *, loaded: bool = True) -> None:
        self._loaded = loaded

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return {"status": "ok", "model": "bge-m3", "loaded": self._loaded}


class _HealthClient:
    def __init__(self, states: list[bool] | None = None) -> None:
        self.states = states or [True]
        self.calls = 0

    async def get(self, _url: str) -> _HealthResponse:
        state = self.states[min(self.calls, len(self.states) - 1)]
        self.calls += 1
        return _HealthResponse(loaded=state)


class _Embeddings:
    def __init__(self, dim: int, *, fail_once: bool = False) -> None:
        self.dim = dim
        self.fail_once = fail_once
        self.batches: list[list[str]] = []

    async def create(self, *, model: str, input: list[str]) -> SimpleNamespace:
        assert model == "bge-m3"
        self.batches.append(input)
        if self.fail_once:
            self.fail_once = False
            raise ConnectionError("temporary")
        data = [
            SimpleNamespace(index=i, embedding=[float(i)] * self.dim) for i in range(len(input))
        ]
        return SimpleNamespace(data=data)


def _client(embeddings: _Embeddings) -> SimpleNamespace:
    return SimpleNamespace(embeddings=embeddings)


@pytest.mark.asyncio
async def test_embed_batches_by_16_and_preserves_order() -> None:
    api = _Embeddings(4)
    embedder = BGEEmbedder(
        base_url="http://127.0.0.1:8001/v1",
        model="bge-m3",
        dim=4,
        batch_size=16,
        client=_client(api),
        health_client=_HealthClient(),
    )
    vectors = await embedder.embed([f"text-{i}" for i in range(33)])
    assert [len(batch) for batch in api.batches] == [16, 16, 1]
    assert len(vectors) == 33
    assert all(len(vector) == 4 for vector in vectors)


@pytest.mark.asyncio
async def test_embed_retries_one_failed_batch() -> None:
    api = _Embeddings(3, fail_once=True)
    embedder = BGEEmbedder(
        base_url="http://127.0.0.1:8001/v1",
        model="bge-m3",
        dim=3,
        client=_client(api),
        health_client=_HealthClient(),
    )
    vectors = await embedder.embed(["年假"])
    assert len(api.batches) == 2
    assert vectors == [[0.0, 0.0, 0.0]]


@pytest.mark.asyncio
async def test_health_waits_once_while_model_is_loading() -> None:
    health = _HealthClient([False, True])
    embedder = BGEEmbedder(
        base_url="http://127.0.0.1:8001/v1",
        model="bge-m3",
        dim=3,
        client=_client(_Embeddings(3)),
        health_client=health,
    )
    await embedder.check_ready()
    assert health.calls == 2


@pytest.mark.asyncio
async def test_embed_rejects_wrong_dimension_without_retry() -> None:
    api = _Embeddings(2)
    embedder = BGEEmbedder(
        base_url="http://127.0.0.1:8001/v1",
        model="bge-m3",
        dim=3,
        client=_client(api),
        health_client=_HealthClient(),
    )
    with pytest.raises(EmbeddingError, match="维度不匹配") as info:
        await embedder.embed(["年假"])
    assert info.value.retryable is False
    assert len(api.batches) == 1


def test_build_embedder_reads_dimension_and_batch_size_from_settings() -> None:
    settings = Settings(
        _env_file=None,
        llm_provider="mock",
        llm_api_key="sk-test-1",
        embed_dim=768,
        embed_batch_size=7,
    )
    embedder = build_embedder(settings)
    assert embedder.dim == 768
    assert embedder._batch_size == 7
