"""RAG 最小闭环：文档索引与查询检索编排。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings, get_settings
from app.core.errors import RAGError
from app.rag.chunking import split_chunks
from app.rag.embedder import EmbeddingProvider, build_embedder
from app.rag.loaders import parse_file
from app.rag.retriever import RetrievalResult, Retriever, build_retriever
from app.rag.vector_store import VectorStore, build_vector_store

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class IndexResult:
    doc_id: str
    filename: str
    content_hash: str
    chunk_count: int
    previous_hash: str | None = None


class RAGPipeline:
    """组合加载器、分块器、向量化、存储与检索器。"""

    def __init__(
        self,
        *,
        settings: Settings,
        embedder: EmbeddingProvider,
        store: VectorStore,
        retriever: Retriever | None = None,
    ) -> None:
        self._settings = settings
        self._embedder = embedder
        self._store = store
        self._retriever = retriever or build_retriever(
            settings,
            embedder=embedder,
            store=store,
        )

    async def index_file(self, path: Path | str) -> IndexResult:
        """索引单文件；新版本完整写入后才切换，失败时旧版本继续生效。"""
        document = parse_file(path)
        chunks = split_chunks(
            document,
            max_chars=self._settings.chunk_max_chars,
            overlap_chars=self._settings.chunk_overlap_chars,
        )
        if not chunks:
            raise RAGError(
                "文档没有可索引内容",
                retryable=False,
                doc_id=document.doc_id,
            )

        vectors = await self._embedder.embed([chunk.text for chunk in chunks])
        staged = False
        try:
            await self._store.upsert(
                chunks,
                vectors,
                filenames={document.doc_id: document.filename},
            )
            staged = True
            previous = await self._activate(document.doc_id, document.content_hash)
        except Exception:
            if staged:
                await self._discard_staged(document.doc_id, document.content_hash)
            raise

        if previous and previous != document.content_hash:
            try:
                await self._delete_version(document.doc_id, previous)
            except Exception:
                # manifest 已指向新版本，旧向量仅占磁盘且不会被检索；不回滚成功切换。
                logger.exception(
                    "旧文档向量清理失败 doc_id=%s old_hash=%s",
                    document.doc_id,
                    previous,
                )
        return IndexResult(
            doc_id=document.doc_id,
            filename=document.filename,
            content_hash=document.content_hash,
            chunk_count=len(chunks),
            previous_hash=previous,
        )

    async def retrieve(self, query: str, *, top_k: int | None = None) -> RetrievalResult:
        if not query.strip():
            raise RAGError("检索问题不能为空", retryable=False)
        return await self._retriever.search(query, top_k=top_k)

    async def delete_document(self, doc_id: str) -> int:
        """删除文档全部向量并撤销 active 版本，供文档服务统一编排。"""
        normalized = doc_id.strip()
        if not normalized:
            raise RAGError("文档 ID 不能为空", retryable=False)
        return await self._store.delete_by_doc(normalized)

    async def search_for_tool(self, query: str, top_k: int) -> list[dict[str, object]]:
        """供 services 组合根注入 knowledge 工具，不让 tools 反向依赖 rag。"""
        return await self._retriever.search_for_tool(query, top_k)

    async def warmup(self) -> None:
        """预热 embedding，避免首位用户承担模型初始化延迟。"""
        warmup = getattr(self._embedder, "warmup", None)
        if warmup is not None:
            await warmup()

    async def verify_ready(self) -> int:
        """启动期预热 embedding 并校验向量 collection，返回当前块数。"""
        await self.warmup()
        return await self._store.count()

    async def close(self) -> None:
        for component in (self._embedder, self._store):
            close = getattr(component, "close", None)
            if close is not None:
                await close()

    async def _activate(self, doc_id: str, content_hash: str) -> str | None:
        activate = getattr(self._store, "activate_version", None)
        if activate is None:
            raise RAGError("向量存储不支持原子版本切换", retryable=False)
        return await activate(doc_id, content_hash)

    async def _delete_version(self, doc_id: str, content_hash: str) -> int:
        delete = getattr(self._store, "delete_version", None)
        if delete is None:
            raise RAGError("向量存储不支持版本清理", retryable=False)
        return int(await delete(doc_id, content_hash))

    async def _discard_staged(self, doc_id: str, content_hash: str) -> None:
        try:
            await self._delete_version(doc_id, content_hash)
        except Exception:
            logger.exception(
                "暂存向量清理失败 doc_id=%s content_hash=%s",
                doc_id,
                content_hash,
            )


def build_pipeline(settings: Settings | None = None) -> RAGPipeline:
    resolved = settings or get_settings()
    embedder = build_embedder(resolved)
    store = build_vector_store(resolved)
    return RAGPipeline(settings=resolved, embedder=embedder, store=store)


async def index_file(path: Path | str, *, settings: Settings | None = None) -> IndexResult:
    """脚本友好入口：构建管道、索引单文件并释放资源。"""
    pipeline = build_pipeline(settings)
    try:
        return await pipeline.index_file(path)
    finally:
        await pipeline.close()


async def retrieve(
    query: str,
    *,
    top_k: int | None = None,
    settings: Settings | None = None,
) -> RetrievalResult:
    """脚本友好入口：从现有持久化索引检索并释放资源。"""
    pipeline = build_pipeline(settings)
    try:
        return await pipeline.retrieve(query, top_k=top_k)
    finally:
        await pipeline.close()
