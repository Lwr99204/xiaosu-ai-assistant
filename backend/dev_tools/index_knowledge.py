"""批量索引 data/knowledge 下的支持格式文档。"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from app.config import REPO_ROOT
from app.rag.loaders import SUPPORTED_EXTS
from app.rag.pipeline import build_pipeline


def _documents(directory: Path) -> list[Path]:
    supported = {f".{ext}" for ext in SUPPORTED_EXTS}
    return sorted(
        (path for path in directory.iterdir() if path.is_file() and path.suffix.lower() in supported),
        key=lambda path: path.name,
    )


async def index_directory(directory: Path) -> int:
    files = _documents(directory)
    if not files:
        raise SystemExit(f"没有找到可索引文档: {directory}")
    pipeline = build_pipeline()
    try:
        await pipeline.warmup()
        total_chunks = 0
        for path in files:
            result = await pipeline.index_file(path)
            total_chunks += result.chunk_count
            print(
                f"indexed {result.filename}: chunks={result.chunk_count} "
                f"hash={result.content_hash[:12]}"
            )
        print(
            f"done: documents={len(files)} chunks={total_chunks} directory={directory}"
        )
        return len(files)
    finally:
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
