"""向量存储层：Chroma 持久化 + 余弦距离（TDD §4.4.3）。

设计取舍：
- 单 collection，显式声明 hnsw:space=cosine。Chroma 默认是 L2，分数口径会与检索层
  score = 1 - distance 不一致，阈值 0.55 也就失去意义。
- 元数据只放定位字段，正文放在 documents 里，命中后直接还原 Chunk，不必回表读文件。
- Chroma 客户端是同步 API，所有调用统一经 asyncio.to_thread，避免阻塞事件循环。
- page 为 None 时不写该键，Chroma 元数据不接受 None 值。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from app.config import Settings
from app.core.errors import RAGError
from app.rag.chunking import Chunk

COLLECTION_NAME = "xiaosu_kb"
MANIFEST_FILENAME = "active_versions.json"
#: 标题层级的连接符。与 im.formatter.format_citations 和 tools.knowledge.format_references
#: 的展示口径保持一致，这样元数据里存的字符串可以直接给用户看，不必二次加工。
HEADING_SEP = " › "
SNIPPET_MAX_CHARS = 200


@dataclass(frozen=True, slots=True)
class Hit:
    """一次检索命中。score = 1 - 余弦距离，越大越相关。"""

    chunk: Chunk
    score: float
    snippet: str
    #: 命中所属文档的展示名。Chunk 本身只带 doc_id，引用卡片要的是原始文件名。
    filename: str = ""


class VectorStore(Protocol):
    """向量存储契约，检索层只依赖它，测试时可替换为内存实现。"""

    async def upsert(
        self,
        chunks: Sequence[Chunk],
        vectors: Sequence[Sequence[float]],
        *,
        filenames: Mapping[str, str],
    ) -> None: ...

    async def query(
        self,
        vector: Sequence[float],
        *,
        top_k: int,
        where: Mapping[str, Any] | None = None,
    ) -> list[Hit]: ...

    async def delete_by_doc(self, doc_id: str) -> int: ...

    async def count(self) -> int: ...


def make_snippet(text: str, max_chars: int = SNIPPET_MAX_CHARS) -> str:
    """把块正文压成单行摘要，用于 IM 引用卡片与后台日志展示。"""
    flat = " ".join(text.split())
    return flat if len(flat) <= max_chars else flat[: max_chars - 1] + "…"


def _metadata_of(chunk: Chunk, filename: str) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "doc_id": chunk.doc_id,
        "filename": filename,
        "index": chunk.index,
        "heading_path": HEADING_SEP.join(chunk.heading_path),
        "char_start": chunk.char_start,
        "char_end": chunk.char_end,
        "chunk_id": chunk.chunk_id,
        "content_hash": chunk.content_hash,
    }
    if chunk.page is not None:
        meta["page"] = chunk.page
    return meta


def _chunk_of(storage_id: str, meta: Mapping[str, Any], text: str) -> Chunk:
    heading = str(meta.get("heading_path", ""))
    page = meta.get("page")
    return Chunk(
        chunk_id=str(meta.get("chunk_id") or storage_id),
        doc_id=str(meta.get("doc_id", "")),
        index=int(meta.get("index", 0)),
        text=text,
        heading_path=tuple(part for part in heading.split(HEADING_SEP) if part),
        char_start=int(meta.get("char_start", 0)),
        char_end=int(meta.get("char_end", len(text))),
        page=None if page is None else int(page),
        content_hash=str(meta.get("content_hash", "")),
    )


def _score(distance: Any) -> float:
    value = float(distance)
    return 0.0 if math.isnan(value) else 1.0 - value


def _first_row(rows: Any) -> list[Any]:
    return [] if not rows else list(rows[0])


def _hits_of(raw: Mapping[str, Any]) -> list[Hit]:
    ids = _first_row(raw.get("ids"))
    metadatas = _first_row(raw.get("metadatas"))
    documents = _first_row(raw.get("documents"))
    distances = _first_row(raw.get("distances"))
    hits: list[Hit] = []
    for position, chunk_id in enumerate(ids):
        text = documents[position] if position < len(documents) else ""
        meta = metadatas[position] if position < len(metadatas) else {}
        distance = distances[position] if position < len(distances) else 0.0
        chunk = _chunk_of(str(chunk_id), meta, str(text))
        hits.append(
            Hit(
                chunk=chunk,
                score=_score(distance),
                snippet=make_snippet(chunk.text),
                filename=str(meta.get("filename", "")),
            )
        )
    return hits


class ChromaStore:
    """基于 Chroma PersistentClient 的向量存储实现。"""

    def __init__(
        self,
        *,
        path: Path | str,
        embed_model: str = "bge-m3",
        embed_dim: int = 1024,
        collection_name: str = COLLECTION_NAME,
    ) -> None:
        self._path = Path(path)
        self._embed_model = embed_model
        self._embed_dim = embed_dim
        self._collection_name = collection_name
        self._manifest_path = self._path / MANIFEST_FILENAME
        self._manifest_lock = asyncio.Lock()
        self._client: Any = None
        self._collection: Any = None

    @property
    def path(self) -> Path:
        return self._path

    @property
    def collection_name(self) -> str:
        return self._collection_name

    def _ensure_sync(self) -> Any:
        if self._collection is not None:
            return self._collection
        try:
            import chromadb
            from chromadb.config import Settings as ChromaSettings
        except ImportError as exc:
            raise RAGError("未安装 chromadb，向量存储不可用", cause=exc) from exc
        self._path.mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(
            path=str(self._path),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        self._client = client
        expected = {
            "hnsw:space": "cosine",
            "embed_model": self._embed_model,
            "embed_dim": self._embed_dim,
        }
        try:
            self._collection = client.get_collection(name=self._collection_name)
        except Exception as exc:
            # Chroma 各版本的 NotFoundError 类型位置不同；只在明确的未找到语义下创建。
            if "does not exist" not in str(exc).lower() and "not found" not in str(exc).lower():
                raise RAGError("打开向量 collection 失败", cause=exc) from exc
            self._collection = client.create_collection(
                name=self._collection_name,
                metadata=expected,
            )
        actual = dict(self._collection.metadata or {})
        for key, value in expected.items():
            if actual.get(key) != value:
                raise RAGError(
                    "向量索引模型或维度与配置不一致，请执行 reindex",
                    code="VECTOR_INDEX_INCOMPATIBLE",
                    retryable=False,
                    field=key,
                    expected=value,
                    actual=actual.get(key),
                )
        return self._collection

    @staticmethod
    def _storage_id(chunk: Chunk) -> str:
        """物理 ID 含内容版本，使新旧版本可在切换前安全共存。"""
        return f"{chunk.chunk_id}@{chunk.content_hash}"

    def _read_manifest_sync(self) -> dict[str, str] | None:
        if not self._manifest_path.exists():
            return None
        try:
            payload = json.loads(self._manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RAGError("读取向量版本清单失败", cause=exc) from exc
        if not isinstance(payload, dict):
            raise RAGError("向量版本清单格式错误", retryable=False)
        return {str(key): str(value) for key, value in payload.items()}

    def _write_manifest_sync(self, manifest: Mapping[str, str]) -> None:
        self._path.mkdir(parents=True, exist_ok=True)
        temp = self._path / f".{MANIFEST_FILENAME}.{uuid4().hex}.tmp"
        try:
            temp.write_text(
                json.dumps(dict(manifest), ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
            temp.replace(self._manifest_path)
        except OSError as exc:
            with contextlib.suppress(OSError):
                temp.unlink()
            raise RAGError("切换文档向量版本失败，旧版本保持可用", cause=exc) from exc

    async def upsert(
        self,
        chunks: Sequence[Chunk],
        vectors: Sequence[Sequence[float]],
        *,
        filenames: Mapping[str, str],
    ) -> None:
        if not chunks:
            return
        if len(chunks) != len(vectors):
            raise RAGError(
                "向量数量与分块数量不一致",
                code="VECTOR_COUNT_MISMATCH",
                retryable=False,
                chunk_count=len(chunks),
                vector_count=len(vectors),
            )
        ids = [self._storage_id(chunk) for chunk in chunks]
        documents = [chunk.text for chunk in chunks]
        metadatas = [
            _metadata_of(chunk, filenames.get(chunk.doc_id, chunk.doc_id)) for chunk in chunks
        ]
        payload = [[float(value) for value in item] for item in vectors]
        collection = await asyncio.to_thread(self._ensure_sync)
        try:
            await asyncio.to_thread(
                collection.upsert,
                ids=ids,
                embeddings=payload,
                documents=documents,
                metadatas=metadatas,
            )
        except Exception as exc:
            raise RAGError("写入向量库失败", cause=exc, chunk_count=len(ids)) from exc

    async def query(
        self,
        vector: Sequence[float],
        *,
        top_k: int,
        where: Mapping[str, Any] | None = None,
    ) -> list[Hit]:
        """按向量检索。where 用于把检索范围限制在单个文档内（后台「测试这篇文档」用）。"""
        collection = await asyncio.to_thread(self._ensure_sync)
        if await asyncio.to_thread(collection.count) == 0:
            return []
        options: dict[str, Any] = {
            "query_embeddings": [[float(value) for value in vector]],
            "n_results": max(1, top_k),
            "include": ["metadatas", "documents", "distances"],
        }
        if where:
            options["where"] = dict(where)
        manifest = await asyncio.to_thread(self._read_manifest_sync)
        if manifest is not None:
            if not manifest:
                return []
            active = {"content_hash": {"$in": sorted(set(manifest.values()))}}
            options["where"] = (
                {"$and": [active, options["where"]]} if "where" in options else active
            )
        try:
            raw = await asyncio.to_thread(collection.query, **options)
        except Exception as exc:
            raise RAGError("向量检索失败", cause=exc, top_k=top_k) from exc
        return _hits_of(raw)

    async def delete_by_doc(self, doc_id: str) -> int:
        """删除某文档的全部向量，返回实际删除条数（TDD §4.4.3 契约）。"""
        collection = await asyncio.to_thread(self._ensure_sync)
        where = {"doc_id": doc_id}
        try:
            existing = await asyncio.to_thread(collection.get, where=where, include=["metadatas"])
            removed = len(existing.get("ids") or [])
            await asyncio.to_thread(collection.delete, where=where)
        except Exception as exc:
            raise RAGError("删除向量失败", cause=exc, doc_id=doc_id) from exc
        async with self._manifest_lock:
            manifest = await asyncio.to_thread(self._read_manifest_sync)
            if manifest is not None and doc_id in manifest:
                manifest.pop(doc_id)
                await asyncio.to_thread(self._write_manifest_sync, manifest)
        return removed

    async def activate_version(self, doc_id: str, content_hash: str) -> str | None:
        """原子切换当前版本；调用前新版本必须已完整写入。返回旧版本哈希。"""
        collection = await asyncio.to_thread(self._ensure_sync)
        where = {"$and": [{"doc_id": doc_id}, {"content_hash": content_hash}]}
        staged = await asyncio.to_thread(collection.get, where=where, include=["metadatas"])
        if not (staged.get("ids") or []):
            raise RAGError(
                "待激活的文档版本没有向量，旧版本保持可用",
                retryable=False,
                doc_id=doc_id,
                content_hash=content_hash,
            )
        async with self._manifest_lock:
            manifest = await asyncio.to_thread(self._read_manifest_sync) or {}
            previous = manifest.get(doc_id)
            manifest[doc_id] = content_hash
            await asyncio.to_thread(self._write_manifest_sync, manifest)
        return previous

    async def delete_version(self, doc_id: str, content_hash: str) -> int:
        """清理已失效版本；清理失败不影响 manifest 指向的新版本。"""
        collection = await asyncio.to_thread(self._ensure_sync)
        where = {"$and": [{"doc_id": doc_id}, {"content_hash": content_hash}]}
        existing = await asyncio.to_thread(collection.get, where=where, include=["metadatas"])
        removed = len(existing.get("ids") or [])
        if removed:
            await asyncio.to_thread(collection.delete, where=where)
        return removed

    async def count(self) -> int:
        collection = await asyncio.to_thread(self._ensure_sync)
        return int(await asyncio.to_thread(collection.count))

    async def close(self) -> None:
        """释放 Chroma 持有的文件句柄，供优雅停机与测试收尾调用。

        只清 SharedSystemClient 缓存不够，Windows 上 data_level0.bin 仍被占用，
        必须先把底层 System 停掉再清缓存。
        """
        client = self._client
        self._client = None
        self._collection = None
        if client is None:
            return
        try:
            from chromadb.api.shared_system_client import SharedSystemClient
        except ImportError:
            return

        def _release() -> None:
            system = getattr(client, "_system", None)
            if system is not None:
                system.stop()
            SharedSystemClient.clear_system_cache()

        with contextlib.suppress(Exception):
            await asyncio.to_thread(_release)


def build_vector_store(settings: Settings) -> ChromaStore:
    """组合根用的工厂，路径直接取 settings.chroma_dir（已被 resolve_paths 锚定）。"""
    return ChromaStore(
        path=settings.chroma_dir,
        embed_model=settings.embed_model,
        embed_dim=settings.embed_dim,
    )
