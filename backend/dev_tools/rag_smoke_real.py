"""用真实 BGE-M3 索引验证考题 Q1～Q3 的分数与引用偏移。"""

from __future__ import annotations

import asyncio

from app.config import REPO_ROOT
from app.rag.loaders import parse_file
from app.rag.pipeline import build_pipeline

QUESTIONS = (
    "员工每年有几天年假？",
    "报销发票需要什么材料？",
    "新人入职第一天要做哪些事？",
)


async def main() -> None:
    knowledge_dir = REPO_ROOT / "data" / "knowledge"
    pipeline = build_pipeline()
    try:
        for question in QUESTIONS:
            result = await pipeline.retrieve(question, top_k=3)
            print(
                f"query={question} reliable={result.reliable} "
                f"top={result.top_score:.4f} spread={result.spread:.4f} reason={result.reason or '-'}"
            )
            if not result.hits:
                raise RuntimeError(f"没有检索命中: {question}")
            for rank, hit in enumerate(result.hits, start=1):
                chunk = hit.chunk
                document = parse_file(knowledge_dir / hit.filename)
                exact = document.content[chunk.char_start : chunk.char_end] == chunk.text
                heading = " > ".join(chunk.heading_path) or "-"
                print(
                    f"  [{rank}] score={hit.score:.4f} file={hit.filename} "
                    f"offset={chunk.char_start}:{chunk.char_end} exact={exact} heading={heading}"
                )
                if not exact:
                    raise RuntimeError(
                        f"引用偏移不匹配: {hit.filename}:{chunk.char_start}:{chunk.char_end}"
                    )
    finally:
        await pipeline.close()


if __name__ == "__main__":
    asyncio.run(main())
