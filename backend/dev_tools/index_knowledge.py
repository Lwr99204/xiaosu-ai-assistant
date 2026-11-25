"""批量索引 data/knowledge 下的支持格式文档。"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from app.config import REPO_ROOT, get_settings
from app.rag.loaders import SUPPORTED_EXTS, normalize_doc_id
from app.rag.pipeline import build_pipeline
from app.services.document_service import DocumentService


def _documents(directory: Path) -> list[Path]:
    supported = {f".{ext}" for ext in SUPPORTED_EXTS}
    return sorted(
        (
            path
            for path in directory.iterdir()
            if path.is_file() and path.suffix.lower() in supported
        ),
        key=lambda path: path.name,
    )


async def index_directory(directory: Path) -> int:
    files = _documents(directory)
    if not files:
        raise SystemExit(f"没有找到可索引文档: {directory}")
    settings = get_settings()
    pipeline = build_pipeline(settings)
    service = DocumentService(settings=settings, pipeline=pipeline)
    try:
        await pipeline.warmup()
        for path in files:
            decision = await service.submit(path.name, path.read_bytes())
            print(f"queued {decision.filename}: action={decision.action} status={decision.status}")
        await service.drain()

        records = [service.get_document(normalize_doc_id(path.name)) for path in files]
        failed = [record for record in records if record.status != "indexed"]
        if failed:
            detail = "; ".join(
                f"{record.filename}: {record.error_message or record.status}" for record in failed
            )
            raise RuntimeError(f"种子文档索引失败: {detail}")
        total_chunks = sum(record.chunk_count for record in records)
        print(f"done: documents={len(records)} chunks={total_chunks} directory={directory}")
        return len(files)
    finally:
        await service.drain()
        await pipeline.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="索引小苏知识库文档")
    parser.add_argument(
        "--knowledge-dir",
        type=Path,
        default=REPO_ROOT / "data" / "knowledge",
        help="知识库目录（默认 data/knowledge）",
    )
    args = parser.parse_args()
    asyncio.run(index_directory(args.knowledge_dir.resolve()))


if __name__ == "__main__":
    main()
