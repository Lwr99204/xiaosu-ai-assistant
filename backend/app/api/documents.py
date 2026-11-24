"""文档管理 REST API：协议转换留在路由，一致性逻辑全部交给 DocumentService。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Query, Request, UploadFile, status

from app.core.errors import RAGError
from app.schemas.document import (
    DeleteDocumentResponse,
    DocumentContent,
    DocumentListResponse,
    DocumentMeta,
    UploadResponse,
    UploadResultItem,
)
from app.services.document_service import MAX_DOCUMENT_BYTES, DocumentService

router = APIRouter(prefix="/api/documents", tags=["documents"])


def _service(request: Request) -> DocumentService:
    service = getattr(request.app.state, "document_service", None)
    if service is None:
        raise RAGError(
            "文档服务当前不可用",
            http_status=503,
            retryable=True,
        )
    return service


@router.post("", response_model=UploadResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_documents(
    request: Request,
    files: Annotated[list[UploadFile], File(description="最多 10 个 md/txt/pdf/docx 文件")],
) -> UploadResponse:
    if len(files) > 10:
        raise RAGError(
            "单次最多上传 10 个文件",
            code="TOO_MANY_DOCUMENTS",
            http_status=422,
            retryable=False,
        )
    service = _service(request)
    results: list[UploadResultItem] = []
    for upload in files:
        try:
            data = await upload.read(MAX_DOCUMENT_BYTES + 1)
            decision = await service.submit(upload.filename or "", data)
            results.append(
                UploadResultItem(
                    filename=decision.filename,
                    doc_id=decision.doc_id,
                    action=decision.action,
                    status=decision.status,
                    reason=decision.reason,
                )
            )
        finally:
            await upload.close()
    return UploadResponse(results=results)


@router.get("", response_model=DocumentListResponse)
def list_documents(
    request: Request,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> DocumentListResponse:
    items, total = _service(request).list_documents(offset=(page - 1) * size, limit=size)
    return DocumentListResponse(
        items=[DocumentMeta.model_validate(item) for item in items],
        total=total,
    )


@router.get("/{doc_id}", response_model=DocumentMeta)
def get_document(request: Request, doc_id: str) -> DocumentMeta:
    return DocumentMeta.model_validate(_service(request).get_document(doc_id))


@router.get("/{doc_id}/content", response_model=DocumentContent)
async def get_document_content(request: Request, doc_id: str) -> DocumentContent:
    service = _service(request)
    record = service.get_document(doc_id)
    content = await service.get_content(doc_id)
    return DocumentContent(doc_id=record.doc_id, filename=record.filename, content=content)


@router.delete("/{doc_id}", response_model=DeleteDocumentResponse)
async def delete_document(request: Request, doc_id: str) -> DeleteDocumentResponse:
    deleted = await _service(request).delete(doc_id)
    return DeleteDocumentResponse(deleted_chunks=deleted)
