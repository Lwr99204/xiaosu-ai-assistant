"""文档服务一致性测试：异步状态、去重、替换回滚与删除。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.config import Settings
from app.rag.loaders import parse_file
from app.services.document_service import MAX_DOCUMENT_BYTES, DocumentService

pytestmark = pytest.mark.allow_hosts("127.0.0.1")


@dataclass(frozen=True)
class _IndexResult:
    doc_id: str
    chunk_count: int


class _Pipeline:
    def __init__(self) -> None:
        self.indexed: list[Path] = []
        self.deleted: list[str] = []
        self.fail = False
        self.active = 0
        self.max_active = 0

    async def index_file(self, path: str) -> _IndexResult:
        file_path = Path(path)
        self.indexed.append(file_path)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0)
            if self.fail:
                raise RuntimeError("embedding down")
            document = parse_file(file_path)
            return _IndexResult(document.doc_id, 3)
        finally:
            self.active -= 1

    async def delete_document(self, doc_id: str) -> int:
        self.deleted.append(doc_id)
        return 3


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        llm_provider="mock",
        llm_api_key="sk-test-1",
        storage_dir=tmp_path / "storage",
        sqlite_path=tmp_path / "storage" / "test.db",
        chroma_dir=tmp_path / "storage" / "chroma",
        log_dir=tmp_path / "logs",
    )


@pytest.mark.asyncio
async def test_submit_indexes_in_background_and_duplicate_skips(tmp_path: Path) -> None:
    pipeline = _Pipeline()
    service = DocumentService(settings=_settings(tmp_path), pipeline=pipeline)  # type: ignore[arg-type]
    data = "# 年假\n每年十天。".encode()

    created = await service.submit("员工手册.md", data)
    assert (created.action, created.status) == ("created", "pending")
    await service.drain()
    record = service.get_document("员工手册.md")
    assert (record.status, record.chunk_count, record.version) == ("indexed", 3, 1)
    assert Path(record.storage_path or "").is_file()

    skipped = await service.submit("员工手册.md", data)
    assert (skipped.action, skipped.status) == ("skipped", "indexed")
    assert len(pipeline.indexed) == 1


@pytest.mark.asyncio
async def test_failed_replacement_restores_old_record_and_file(tmp_path: Path) -> None:
    pipeline = _Pipeline()
    service = DocumentService(settings=_settings(tmp_path), pipeline=pipeline)  # type: ignore[arg-type]
    await service.submit("制度.md", "# 旧制度\n旧内容".encode())
    await service.drain()
    old = service.get_document("制度.md")

    pipeline.fail = True
    replaced = await service.submit("制度.md", "# 新制度\n新内容".encode())
    assert replaced.action == "replaced"
    await service.drain()

    restored = service.get_document("制度.md")
    assert restored == old
    assert Path(old.storage_path or "").is_file()
    assert await service.get_content("制度.md") == "# 旧制度\n旧内容"


@pytest.mark.asyncio
async def test_delete_removes_vectors_record_and_original(tmp_path: Path) -> None:
    pipeline = _Pipeline()
    service = DocumentService(settings=_settings(tmp_path), pipeline=pipeline)  # type: ignore[arg-type]
    await service.submit("FAQ.txt", "报销需要发票。".encode())
    await service.drain()
    path = Path(service.get_document("faq.txt").storage_path or "")

    assert await service.delete("FAQ.txt") == 3
    assert pipeline.deleted == ["faq.txt"]
    assert not path.exists()
    with pytest.raises(Exception, match="文档不存在"):
        service.get_document("FAQ.txt")


@pytest.mark.asyncio
async def test_rejects_unsupported_empty_and_oversized_files(tmp_path: Path) -> None:
    service = DocumentService(settings=_settings(tmp_path), pipeline=_Pipeline())  # type: ignore[arg-type]
    with pytest.raises(Exception, match="不支持的文件类型"):
        await service.submit("恶意.exe", b"x")
    with pytest.raises(Exception, match="文档内容为空"):
        await service.submit("空文档.md", b"")
    with pytest.raises(Exception, match="文件超过"):
        await service.submit("太大.txt", b"x" * (MAX_DOCUMENT_BYTES + 1))


@pytest.mark.asyncio
async def test_failed_document_can_retry_same_content_and_filename_is_sanitized(tmp_path: Path) -> None:
    pipeline = _Pipeline()
    pipeline.fail = True
    service = DocumentService(settings=_settings(tmp_path), pipeline=pipeline)  # type: ignore[arg-type]
    data = "# 制度\n正文".encode()

    first = await service.submit("../薪酬:制度.md", data)
    assert first.filename == "薪酬_制度.md"
    await service.drain()
    assert service.get_document("薪酬_制度.md").status == "failed"

    pipeline.fail = False
    retried = await service.submit("薪酬:制度.md", data)
    assert retried.action == "replaced"
    await service.drain()
    record = service.get_document("薪酬_制度.md")
    assert (record.status, record.version) == ("indexed", 2)
    assert len(pipeline.indexed) == 2


@pytest.mark.asyncio
async def test_reindex_keeps_version_and_restores_state_on_failure(tmp_path: Path) -> None:
    pipeline = _Pipeline()
    service = DocumentService(settings=_settings(tmp_path), pipeline=pipeline)  # type: ignore[arg-type]
    await service.submit("制度.md", "# 制度\n正文".encode())
    await service.drain()

    pending = await service.reindex("制度.md")
    assert (pending.status, pending.version) == ("pending", 1)
    await service.drain()
    assert service.get_document("制度.md").version == 1

    before_failure = service.get_document("制度.md")
    pipeline.fail = True
    await service.reindex("制度.md")
    await service.drain()
    assert service.get_document("制度.md") == before_failure


@pytest.mark.asyncio
async def test_multiple_documents_are_indexed_by_single_worker(tmp_path: Path) -> None:
    pipeline = _Pipeline()
    service = DocumentService(settings=_settings(tmp_path), pipeline=pipeline)  # type: ignore[arg-type]
    await service.submit("甲.md", "甲文档内容".encode())
    await service.submit("乙.md", "乙文档内容".encode())
    await service.drain()

    assert pipeline.max_active == 1
    assert [path.name for path in pipeline.indexed] == ["甲.md", "乙.md"]
    assert service.get_document("甲.md").status == "indexed"
    assert service.get_document("乙.md").status == "indexed"
