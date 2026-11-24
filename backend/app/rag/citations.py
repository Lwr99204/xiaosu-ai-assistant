"""引用定位与高亮：把答案里的 [n] 与检索命中绑定成可跳转的引用（TDD §4.4.5）。

两端消费同一份 Citation，只是渲染方式不同，所以字段一次给全，不在各端各算一遍：
Web 端拿 doc_id + char_start + char_end 在原文预览里精确高亮那一段（FR-2-03），
IM 端拿 filename + heading_path + snippet 拼「📎 来源」区块（FR-4-07）。

越界防御：模型可能编造 [9] 而命中里没有。这里把无效标记从答案文本里摘掉并记 warning，
不让脏数据冒泡到前端。注意 L3（agent.refusal.check_citations）在编号越界时会整篇撤回
答案，所以这条分支在编排器主链路上通常走不到，它保护的是直接调用本模块的场景，
比如后台重放历史答案、Evals 离线评分。

heading_path 统一收敛成字符串。检索器输出的已经是 HEADING_SEP 连接的字符串，但历史
数据与 conftest 桩里存在列表形式，这里做一次归一，保证落库 JSON 的形状稳定。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

logger = logging.getLogger(__name__)

#: 与 agent.refusal 同一套编号规则：上限三位数字，[2026] 这种年份写法不算引用。
_CITATION_PATTERN = re.compile(r"\[(\d{1,3})\]")

#: heading_path 为列表时的连接符，与 im.formatter.format_citations 的展示口径一致。
_HEADING_JOIN = " > "


@dataclass(frozen=True, slots=True)
class Citation:
    """一条可定位的引用。TDD §4.4.5 字段表原样实现。"""

    ref_no: int
    doc_id: str = ""
    filename: str = ""
    heading_path: str = ""
    snippet: str = ""
    char_start: int = 0
    char_end: int = 0
    page: int | None = None
    score: float = 0.0


def citation_of(hit: Mapping[str, Any], ref_no: int) -> Citation:
    """把一条命中 mapping 转成 Citation。ref_no 优先取命中自带的编号。"""
    heading = hit.get("heading_path") or ""
    if isinstance(heading, (list, tuple)):
        heading = _HEADING_JOIN.join(str(part) for part in heading)
    page = hit.get("page")
    return Citation(
        ref_no=int(hit.get("ref_no") or ref_no),
        doc_id=str(hit.get("doc_id") or ""),
        filename=str(hit.get("filename") or ""),
        heading_path=str(heading),
        snippet=str(hit.get("snippet") or ""),
        char_start=int(hit.get("char_start") or 0),
        char_end=int(hit.get("char_end") or 0),
        page=None if page is None else int(page),
        score=float(hit.get("score") or 0.0),
    )


def citation_to_mapping(citation: Citation) -> dict[str, Any]:
    """转回 mapping，供 JSON 落库与 API 响应使用。"""
    return asdict(citation)


def citations_from_mappings(mappings: Sequence[Mapping[str, Any]]) -> list[Citation]:
    """把已落库或已透出的引用 mapping 批量还原成 Citation。"""
    return [citation_of(item, position) for position, item in enumerate(mappings, start=1)]


def iter_ref_nos(answer: str) -> list[int]:
    """按出现顺序取出答案里的 [n] 编号并去重。"""
    seen: list[int] = []
    for match in _CITATION_PATTERN.finditer(answer):
        number = int(match.group(1))
        if number not in seen:
            seen.append(number)
    return seen


def strip_invalid_refs(answer: str, hit_count: int) -> str:
    """摘掉答案里超出命中范围的 [n] 标记，并记 warning。"""
    fabricated: list[int] = []

    def _replace(match: re.Match[str]) -> str:
        number = int(match.group(1))
        if 1 <= number <= hit_count:
            return match.group(0)
        fabricated.append(number)
        return ""

    cleaned = _CITATION_PATTERN.sub(_replace, answer)
    if fabricated:
        logger.warning("citation_out_of_range dropped=%s hit_count=%d", fabricated, hit_count)
    return cleaned


def build_citations(answer: str, hits: Sequence[Mapping[str, Any]]) -> tuple[str, list[Citation]]:
    """返回 (清洗后的答案文本, 引用列表)。

    合法 [n] 原样保留，前端渲染成可点击角标；引用列表只含答案真正引用到的条目，
    按出现顺序排列，与 agent.refusal.pick_citations 的语义保持一致。
    """
    cleaned = strip_invalid_refs(answer, len(hits))
    cited: list[Citation] = []
    for number in iter_ref_nos(answer):
        if 1 <= number <= len(hits):
            cited.append(citation_of(hits[number - 1], number))
    return cleaned, cited
