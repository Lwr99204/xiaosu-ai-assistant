"""文档上传、异步索引、替换回滚与删除的一致性编排。"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import re
from dataclasses import dataclass, replace
from pathlib import Path

from app.config import Settings
from app.core.errors import NotFoundError, ParseError, RAGError
from app.db.engine import init_db, now_iso
from app.db.entities import DocumentRecord
from app.db.repositories import DocumentRepo
from app.rag.loaders import detect_ext, normalize_doc_id, parse_file
from app.rag.pipeline import RAGPipeline

logger = logging.getLogger(__name__)

MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
_UNSAFE_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}


@dataclass(frozen=True, slots=True)
class UploadDecision:
    filename: str
    doc_id: str | None
    action: str
    status: str | None
    reason: str | None = None


class DocumentService:
    """协调 SQLite、原文件与向量库；不让 API 层直接操作任一存储。"""

    def __init__(self, *, settings: Settings, pipeline: RAGPipeline) -> None:
        self._settings = settings
        self._pipeline = pipeline
        self._uploads_root = (settings.storage_dir / "uploads").resolve()
        init_db(settings.sqlite_path)
        self._repo = DocumentRepo(settings.sqlite_path)
        self._mutation_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[None]] = set()

    async def submit(self, filename: str, data: bytes) -> UploadDecision:
        """接收单文件并立即返回；耗时的解析、向量化在后台任务执行。"""
        safe_name = self._safe_filename(filename)
        ext = detect_ext(safe_name)
        if not data:
            raise ParseError("文档内容为空", filename=safe_name)
        if len(data) > MAX_DOCUMENT_BYTES:
            raise RAGError(
                f"文件超过 {MAX_DOCUMENT_BYTES // 1024 // 1024} MB 上限",
                code="DOCUMENT_TOO_LARGE",
                http_status=413,
                retryable=False,
                filename=safe_name,
            )

        doc_id = normalize_doc_id(safe_name)
        raw_hash = hashlib.sha256(data).hexdigest()
        async with self._mutation_lock:
            previous = self._repo.find_by_doc_id(doc_id)
            if (
                previous is not None
                and previous.content_hash == raw_hash
                and previous.status != "failed"
            ):
                return UploadDecision(safe_name, doc_id, "skipped", previous.status)
            if previous is not None and previous.status == "pending":
                return UploadDecision(
                    safe_name,
                    doc_id,
                    "rejected",
                    "pending",
                    "同名文档正在索引，请完成后再替换",
                )

            storage_path = self._version_path(doc_id, raw_hash, safe_name)
            await asyncio.to_thread(self._write_file, storage_path, data)
            timestamp = now_iso()
            record = DocumentRecord(
                doc_id=doc_id,
                filename=safe_name,
                ext=ext,
                byte_size=len(data),
                content_hash=raw_hash,
                status="pending",
                chunk_count=0,
                version=(previous.version + 1) if previous else 1,
                storage_path=str(storage_path),
                created_at=previous.created_at if previous else timestamp,
                updated_at=timestamp,
            )
            self._repo.upsert(record)
            task = asyncio.create_task(
                self._index_pending(record, previous),
                name=f"index-document:{doc_id}:v{record.version}",
            )
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

        action = "replaced" if previous else "created"
        return UploadDecision(safe_name, doc_id, action, "pending")

    def list_documents(self, *, offset: int = 0, limit: int = 50) -> tuple[list[DocumentRecord], int]:
        return self._repo.list_paged(offset=offset, limit=limit)

    def get_document(self, doc_id: str) -> DocumentRecord:
        record = self._repo.find_by_doc_id(normalize_doc_id(doc_id))
        if record is None:
            raise NotFoundError("文档不存在", doc_id=doc_id)
        return record

    async def get_content(self, doc_id: str) -> str:
        record = self.get_document(doc_id)
        if not record.storage_path:
            raise NotFoundError("文档原文件不存在", doc_id=doc_id)
        document = await asyncio.to_thread(parse_file, record.storage_path)
        return document.content

    async def delete(self, doc_id: str) -> int:
        normalized = normalize_doc_id(doc_id)
        async with self._mutation_lock:
            record = self._repo.find_by_doc_id(normalized)
            if record is None:
                raise NotFoundError("文档不存在", doc_id=doc_id)
            if record.status == "pending":
                raise RAGError(
                    "文档正在索引，暂不能删除",
                    code="DOCUMENT_BUSY",
                    http_status=409,
                    retryable=True,
                    doc_id=normalized,
                )
            deleted_chunks = await self._pipeline.delete_document(normalized)
            self._repo.delete(normalized)
            await self._unlink(record.storage_path)
            return deleted_chunks

    async def reindex(self, doc_id: str) -> DocumentRecord:
        """后台重建现有原文件；失败时恢复调用前的完整状态。"""
        normalized = normalize_doc_id(doc_id)
        async with self._mutation_lock:
            previous = self._repo.find_by_doc_id(normalized)
            if previous is None:
                raise NotFoundError("文档不存在", doc_id=doc_id)
            if previous.status == "pending":
                raise RAGError(
                    "文档正在索引",
                    code="DOCUMENT_BUSY",
                    http_status=409,
                    retryable=True,
                    doc_id=normalized,
                )
            if not previous.storage_path or not Path(previous.storage_path).is_file():
                raise NotFoundError("文档原文件不存在", doc_id=doc_id)
            pending = replace(
                previous,
                status="pending",
                chunk_count=0,
                error_message=None,
                updated_at=now_iso(),
                indexed_at=None,
            )
            self._repo.upsert(pending)
            task = asyncio.create_task(
                self._index_pending(pending, previous),
                name=f"reindex-document:{normalized}:v{pending.version}",
            )
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            return pending

    async def drain(self) -> None:
        """等待当前后台索引结束；测试与优雅停机共用。"""
        tasks = tuple(self._tasks)
        if tasks:
            await asyncio.gather(*tasks)

    async def _index_pending(
        self,
        record: DocumentRecord,
        previous: DocumentRecord | None,
    ) -> None:
        try:
            result = await self._pipeline.index_file(record.storage_path or "")
            if result.doc_id != record.doc_id:
                raise RAGError("索引结果文档 ID 不一致", retryable=False)
        except Exception as exc:
            logger.exception("文档索引失败 doc_id=%s version=%d", record.doc_id, record.version)
            if previous is not None:
                self._repo.upsert(previous)
                await self._unlink(record.storage_path)
            else:
                self._repo.mark_failed(record.doc_id, str(exc))
            return

        self._repo.mark_indexed(record.doc_id, chunk_count=result.chunk_count)
        if previous is not None and previous.storage_path != record.storage_path:
            await self._unlink(previous.storage_path)

    def _version_path(self, doc_id: str, raw_hash: str, filename: str) -> Path:
        doc_key = hashlib.sha256(doc_id.encode("utf-8")).hexdigest()[:16]
        path = (self._uploads_root / doc_key / raw_hash / filename).resolve()
        if not path.is_relative_to(self._uploads_root):
            raise ParseError("文件名包含非法路径", filename=filename)
        return path

    @staticmethod
    def _safe_filename(filename: str) -> str:
        if not filename or "\x00" in filename:
            raise ParseError("文件名不能为空或包含非法字符")
        basename = Path(filename.replace("\\", "/")).name
        name = _UNSAFE_FILENAME_CHARS.sub("_", basename).strip().rstrip(". ")
        if not name or name in {".", ".."}:
            raise ParseError("文件名无效", filename=filename)
        if Path(name).stem.casefold() in _WINDOWS_RESERVED_NAMES:
            name = f"_{name}"
        return name

    @staticmethod
    def _write_file(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{path.name}.uploading")
        temp.write_bytes(data)
        temp.replace(path)

    async def _unlink(self, storage_path: str | None) -> None:
        if not storage_path:
            return
        path = Path(storage_path).resolve()
        if not path.is_relative_to(self._uploads_root):
            logger.error("拒绝删除 uploads 目录外文件 path=%s", path)
            return
        with contextlib.suppress(FileNotFoundError, OSError):
            await asyncio.to_thread(path.unlink)
