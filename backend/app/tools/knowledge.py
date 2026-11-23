"""search_knowledge_base 工具。

本模块刻意不 import app.rag（TDD §3.1：rag / llm / tools 之间不得互相 import）。检索器
以异步可调用对象的形式由 build_spec 注入，组合根（services 层）负责把真实的
rag.retriever 包装成这里约定的形状。这样 M2 阶段向量库还不存在时，Agent 的工具循环
也能完整跑通并被测试；M4 接上真检索器时本文件一行都不用改。

输出的【参考资料】块由本模块统一编号，system prompt 回答规则第 2 条承诺「编号对应
下方【参考资料】中的序号」，编号规则必须与这里一致，否则引用后校验会误判模型编造引用。
hits 与 references 用同一份编号结果生成，避免两处各编一次号导致错位。
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from pydantic import BaseModel, Field

from app.agent.tool_registry import ToolSpec

# 检索器契约：async (query, top_k) -> 命中列表。每个命中是含下列键的 mapping。
KnowledgeSearchFn = Callable[[str, int], Awaitable[Sequence[Mapping[str, Any]]]]

_SOURCE_KEYS = ("filename", "heading_path")


class KnowledgeArgs(BaseModel):
    """search_knowledge_base 入参。"""

    query: str = Field(
        min_length=1,
        max_length=200,
        description="检索用的关键词或自然语言问题，例如「年假天数」",
    )


DESCRIPTION = (
    "检索公司知识库。查询公司规章制度、员工手册、入职流程、报销标准等文档类问题时使用。"
    "返回带 [n] 编号的【参考资料】文本，回答中引用其中内容时必须按该编号标注来源。"
)


def number_hits(hits: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """给命中结果按 1 起编号，编号即答案里 [n] 的 n。"""
    return [{**dict(hit), "ref_no": index} for index, hit in enumerate(hits, start=1)]


def format_references(hits: Sequence[Mapping[str, Any]]) -> str:
    """把已编号的命中拼成 TDD 7.2 约定的【参考资料】块。"""
    if not hits:
        return "【参考资料】" + chr(10) + "没有检索到相关内容。"
    lines = ["【参考资料】"]
    for hit in hits:
        source = " › ".join(str(hit[key]) for key in _SOURCE_KEYS if hit.get(key))
        lines.append(f"[{hit.get('ref_no')}] 来源：{source or '未知来源'}")
        lines.append(f"    {hit.get('snippet', '')}")
    return chr(10).join(lines)


def _unavailable_payload(query: str) -> str:
    """检索器未注入时的返回。

    明确告诉模型「知识库当前不可用」而不是返回空命中：空命中会让模型以为「查过了但
    没有」，从而对验收题 Q1-Q3 给出「文档里没有找到」的错误结论，掩盖真实的部署问题。
    """
    return json.dumps(
        {
            "ok": False,
            "query": query,
            "references": "",
            "hits": [],
            "error": "知识库当前不可用：尚未建立文档索引。请勿据此断言文档里没有该内容。",
        },
        ensure_ascii=False,
    )


def build_spec(search: KnowledgeSearchFn | None = None, *, top_k: int = 8) -> ToolSpec:
    """构建知识库工具。search 为 None 表示本阶段还没接向量库。"""

    async def handler(query: str) -> str:
        if search is None:
            return _unavailable_payload(query)
        found = await search(query, top_k)
        hits = number_hits(found)
        return json.dumps(
            {
                "ok": True,
                "query": query,
                "references": format_references(hits),
                "hits": hits,
            },
            ensure_ascii=False,
        )

    return ToolSpec(
        name="search_knowledge_base",
        description=DESCRIPTION,
        parameters=KnowledgeArgs,
        handler=handler,
    )
