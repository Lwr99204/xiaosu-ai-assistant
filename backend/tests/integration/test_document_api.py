"""文档 REST API 最小闭环：上传、状态、原文、同名替换与删除。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest

from app.config import REPO_ROOT, Settings
from app.main import create_app
from app.rag.loaders import parse_file
from app.services.document_service import DocumentService

pytestmark = pytest.mark.allow_hosts("127.0.0.1")


@dataclass(frozen=True)
class _IndexResult:
    doc_id: str
    chunk_count: int


class _Pipeline:
    def __init__(self) -> None:
        self.indexed: list[Path] = []
        self.deleted: list[str] = []

    async def index_file(self, path: str) -> _IndexResult:
        file_path = Path(path)
        self.indexed.append(file_path)
        document = parse_file(file_path)
        return _IndexResult(document.doc_id, 2)

    async def delete_document(self, doc_id: str) -> int:
        self.deleted.append(doc_id)
        return 2


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        llm_provider="mock",
        llm_api_key="sk-test-1",
        storage_dir=tmp_path / "storage",
        sqlite_path=tmp_path / "storage" / "test.db",
        chroma_dir=tmp_path / "storage" / "chroma",
        log_dir=tmp_path / "logs",
        mock_data_dir=REPO_ROOT / "data" / "mock",
    )


@pytest.fixture()
async def document_client(tmp_path: Path):
    settings = _settings(tmp_path)
    pipeline = _Pipeline()
    service = DocumentService(settings=settings, pipeline=pipeline)  # type: ignore[arg-type]
    app = create_app(settings)
    app.state.document_service = service
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        yield client, service, pipeline
    await service.drain()


async def test_document_api_full_lifecycle(document_client) -> None:
    client, service, pipeline = document_client
    created = await client.post(
        "/api/documents",
        files={"files": ("员工手册.md", "# 年假\n每年十天。".encode(), "text/markdown")},
    )
    assert created.status_code == 202
    assert created.json()["results"][0] == {
        "filename": "员工手册.md",
        "doc_id": "员工手册.md",
        "action": "created",
        "status": "pending",
        "reason": None,
    }
    await service.drain()

    listing = await client.get("/api/documents")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["status"] == "indexed"
    assert listing.json()["items"][0]["chunk_count"] == 2

    content = await client.get("/api/documents/员工手册.md/content")
    assert content.status_code == 200
    assert content.json()["content"] == "# 年假\n每年十天。"

    replaced = await client.post(
        "/api/documents",
        files={"files": ("员工手册.md", "# 年假\n新版十二天。".encode(), "text/markdown")},
    )
    assert replaced.status_code == 202
    assert replaced.json()["results"][0]["action"] == "replaced"
    await service.drain()
    detail = await client.get("/api/documents/员工手册.md")
    assert detail.json()["version"] == 2

    reindex = await client.post("/api/documents/员工手册.md/reindex")
    assert reindex.status_code == 202
    assert (reindex.json()["status"], reindex.json()["version"]) == ("pending", 2)
    await service.drain()
    assert (await client.get("/api/documents/员工手册.md")).json()["status"] == "indexed"

    deleted = await client.delete("/api/documents/员工手册.md")
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted_chunks": 2}
    assert pipeline.deleted == ["员工手册.md"]
    assert (await client.get("/api/documents/员工手册.md")).status_code == 404


async def test_document_api_rejects_bad_extension(document_client) -> None:
    client, _, _ = document_client
    response = await client.post(
        "/api/documents",
        files={"files": ("病毒.exe", b"not really a document", "application/octet-stream")},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "DOC_PARSE_ERROR"
    assert response.headers["X-Request-ID"] == response.json()["request_id"]
