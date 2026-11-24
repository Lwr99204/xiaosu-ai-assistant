"""本地 BGE-M3 向量化客户端（DEV_PLAN RAG 管道核心）。

本模块位于 RAG 子域内，不依赖 ``app.llm`` 或 ``app.tools``。对话模型与向量模型
使用各自的 Provider 契约和客户端，避免把云端 Chat 配置与本地 embedding 服务耦合。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

import httpx
from openai import AsyncOpenAI

from app.config import Settings
from app.core.errors import EmbeddingError
from app.core.retry import retry_async

DEFAULT_BATCH_SIZE = 16
DEFAULT_MAX_RETRIES = 1
_WARMUP_TEXT = "小苏知识库向量服务预热"


@runtime_checkable
class EmbeddingProvider(Protocol):
    """RAG 使用的独立向量化契约，与 ChatProvider 分离。"""

    model: str
    dim: int

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class BGEEmbedder:
    """通过 OpenAI 兼容端点调用本地 BGE-M3 服务。"""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        dim: int,
        api_key: str = "not-needed",
        timeout_s: float = 60.0,
        batch_size: int = DEFAULT_BATCH_SIZE,
        max_retries: int = DEFAULT_MAX_RETRIES,
        client: Any | None = None,
        health_client: Any | None = None,
    ) -> None:
        self.model = model
        self.dim = dim
        self._base_url = base_url.rstrip("/")
        self._health_url = self._base_url.removesuffix("/v1") + "/health"
        self._batch_size = max(1, batch_size)
        self._max_retries = max(0, max_retries)
        self._client = client or AsyncOpenAI(
            base_url=self._base_url,
            api_key=api_key,
            timeout=timeout_s,
            max_retries=0,
        )
        self._health_client = health_client or httpx.AsyncClient(timeout=timeout_s)
        self._owns_health_client = health_client is None

    async def check_ready(self) -> None:
        """确认服务可达且模型完成加载；失败信息包含可直接执行的启动命令。"""

        async def _request() -> None:
            try:
                response = await self._health_client.get(self._health_url)
                response.raise_for_status()
                payload = response.json()
            except Exception as exc:
                raise EmbeddingError(
                    "无法连接本地 embedding 服务；请设置 "
                    "BGE_MODEL_DIR=D:\\Models\\bge-m3 后运行 "
                    "uv run python dev_tools\\embedding_service.py",
                    cause=exc,
                ) from exc
            if not bool(payload.get("loaded")):
                raise EmbeddingError("embedding 模型仍在加载，请稍后重试")

        await retry_async(
            _request,
            operation="embedding.health",
            max_retries=self._max_retries,
            base_delay=0.2,
        )

    async def warmup(self) -> None:
        """启动期预热一次，避免首位用户承担 CUDA 初始化延迟。"""
        await self.check_ready()
        await self._embed_batches([_WARMUP_TEXT])

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """按配置批量向量化；每批失败重试一次，返回顺序与输入一致。"""
        if not texts:
            return []
        if any(not str(text).strip() for text in texts):
            raise EmbeddingError("向量化输入不能为空", retryable=False)
        await self.check_ready()
        return await self._embed_batches([str(text) for text in texts])

    async def _embed_batches(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = list(texts[start : start + self._batch_size])

            async def _request(batch: list[str] = batch) -> list[list[float]]:
                try:
                    response = await self._client.embeddings.create(
                        model=self.model,
                        input=batch,
                    )
                except Exception as exc:
                    raise EmbeddingError("向量化请求失败", cause=exc) from exc
                ordered = sorted(response.data, key=lambda item: int(item.index))
                result = [list(item.embedding) for item in ordered]
                if len(result) != len(batch):
                    raise EmbeddingError(
                        "embedding 返回数量与输入数量不一致",
                        retryable=False,
                        expected=len(batch),
                        actual=len(result),
                    )
                for vector in result:
                    if len(vector) != self.dim:
                        raise EmbeddingError(
                            f"embedding 维度不匹配：期望 {self.dim}，实际 {len(vector)}",
                            retryable=False,
                            user_facing_msg=(
                                "向量模型维度与配置不一致，请检查 EMBED_DIM 或重建索引"
                            ),
                        )
                return result

            vectors.extend(
                await retry_async(
                    _request,
                    operation=f"embedding.batch[{self.model}]",
                    max_retries=self._max_retries,
                    base_delay=0.2,
                )
            )
        return vectors

    async def close(self) -> None:
        if self._owns_health_client:
            await self._health_client.aclose()
        close = getattr(self._client, "close", None)
        if close is not None:
            await close()


def build_embedder(settings: Settings) -> BGEEmbedder:
    """从强类型配置构建客户端；维度与批量大小均不在调用点写死。"""
    return BGEEmbedder(
        base_url=settings.embed_base_url,
        model=settings.embed_model,
        dim=settings.embed_dim,
        api_key=settings.embed_api_key,
        timeout_s=settings.embed_timeout_s,
        batch_size=settings.embed_batch_size,
        max_retries=DEFAULT_MAX_RETRIES,
    )
