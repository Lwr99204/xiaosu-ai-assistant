"""用真实语料与 BGE-M3 标定检索绝对阈值。"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from app.config import get_settings
from app.rag.embedder import build_embedder
from app.rag.vector_store import build_vector_store

RELATED = (
    "员工每年有几天年假？",
    "报销发票需要什么材料？",
    "新人入职第一天要做哪些事？",
    "迟到超过三十分钟怎么处理？",
    "年假需要提前多久申请？",
)

UNRELATED = (
    "2030 年的销售目标是多少？",
    "火星上如何申请年假？",
    "Python 怎么实现快速排序？",
    "公司有没有宠物医疗保险？",
    "明天上海的天气怎么样？",
)


def histogram(values: Sequence[float], *, width: float = 0.05) -> list[str]:
    lines: list[str] = []
    bucket = 0.0
    while bucket < 1.0:
        upper = bucket + width
        count = sum(bucket <= value < upper for value in values)
        lines.append(f"{bucket:.2f}-{upper:.2f} {'#' * count}")
        bucket = upper
    return lines


async def top_scores(queries: Sequence[str]) -> list[float]:
    settings = get_settings()
    embedder = build_embedder(settings)
    store = build_vector_store(settings)
    try:
        vectors = await embedder.embed(queries)
        scores: list[float] = []
        for query, vector in zip(queries, vectors, strict=True):
            hits = await store.query(vector, top_k=1)
            score = hits[0].score if hits else 0.0
            scores.append(score)
            filename = hits[0].filename if hits else "-"
            print(f"score={score:.4f} file={filename} query={query}")
        return scores
    finally:
        await embedder.close()
        await store.close()


async def main() -> None:
    print("[related]")
    related = await top_scores(RELATED)
    print("[unrelated]")
    unrelated = await top_scores(UNRELATED)
    print("[histogram related]")
    print("\n".join(histogram(related)))
    print("[histogram unrelated]")
    print("\n".join(histogram(unrelated)))
    lower = max(unrelated)
    upper = min(related)
    suggested = (lower + upper) / 2
    print(
        f"related_min={upper:.4f} unrelated_max={lower:.4f} "
        f"suggested_threshold={suggested:.4f}"
    )
    if lower >= upper:
        print("warning: 正负样本分布重叠，需要扩充样本或调整分块")


if __name__ == "__main__":
    asyncio.run(main())
