"""文档管理 REST 契约；状态类型独立定义，禁止从模型字段反向取类型。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

DocumentStatus = Literal["pending", "indexed", "failed"]
UploadAction = Literal["created", "skipped", "replaced", "rejected"]


class DocumentMeta(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    doc_id: str
    filename: str
    ext: str
    byte_size: int
    status: DocumentStatus
    chunk_count: int
    version: int
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime
    indexed_at: datetime | None = None


class DocumentListResponse(BaseModel):
    items: list[DocumentMeta]
    total: int


class UploadResultItem(BaseModel):
    filename: str
    doc_id: str | None = None
    action: UploadAction
    status: DocumentStatus | None = None
    reason: str | None = None


class UploadResponse(BaseModel):
    results: list[UploadResultItem]


class DocumentContent(BaseModel):
    doc_id: str
    filename: str
    content: str


class DeleteDocumentResponse(BaseModel):
    deleted_chunks: int
