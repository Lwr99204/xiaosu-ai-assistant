"""IM 回复格式化（TDD §4.6.4 / FR-4-07）。

输出模板固定三段：答案正文 → 📎 来源（有引用时）→ ⏱ 耗时与 token 页脚。
企业微信单条消息 ≤4000 字符，超限时在**引用条目边界**分段发送，不硬截断。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from app.agent.events import AgentReply
from app.core.timing import format_duration

#: 来源摘要展示上限，防止一条引用吃掉整屏。
_SNIPPET_MAX = 80

#: 引用条目起始行：「1. 来源名」。分段时以它为边界。
_ENTRY_START = re.compile(r"^\d+\.\s")

#: 降级话术分级（TDD §4.6.3 表格）。键是 AgentReply.refused_by 的取值。
DEGRADED_TEXTS: dict[str, str] = {
    # 验收题 7.5-10 的标准答案：鉴权失败要直指 API Key 配置。
    "llm:LLMAuthError": "⚠️ 我的模型服务鉴权失败了，请联系管理员检查 API Key 配置。",
    "llm:LLMTimeoutError": "😵 模型响应超时，我已重试过。请稍后再试，或换个更具体的问法。",
    "llm:LLMUnavailableError": "😵 模型响应超时，我已重试过。请稍后再试，或换个更具体的问法。",
}


def degraded_answer_text(reply: AgentReply) -> str | None:
    """degraded 回复映射到分级话术；未命中返回 None（用编排器默认文本）。"""
    if reply.status != "degraded" or not reply.refused_by:
        return None
    return DEGRADED_TEXTS.get(reply.refused_by)


def format_citations(citations: Sequence[Mapping[str, Any]]) -> str:
    """把 pick_citations 的结果拼成 TDD §4.6.4 的来源块。"""
    lines = ["---", "📎 **来源**"]
    for index, cite in enumerate(citations, start=1):
        filename = str(cite.get("filename") or "未知来源")
        heading = cite.get("heading_path") or []
        if isinstance(heading, (list, tuple)):
            heading = " > ".join(str(part) for part in heading)
        source = f"{filename} › {heading}" if heading else filename
        lines.append(f"{index}. {source}")
        snippet = str(cite.get("snippet") or "")
        if len(snippet) > _SNIPPET_MAX:
            snippet = snippet[:_SNIPPET_MAX] + "…"
        if snippet:
            lines.append(f"   > {snippet}")
    return "\n".join(lines)


def format_final_reply(reply: AgentReply, *, answer_override: str | None = None) -> str:
    """终局文本。answer_override 用于降级分级话术替换编排器默认文本。"""
    parts = [answer_override or reply.answer]
    if reply.citations:
        parts.append(format_citations(reply.citations))
    parts.append(
        f"⏱ 耗时 {format_duration(reply.latency_ms)} · 🔢 {reply.usage.total_tokens:,} tokens"
    )
    return "\n\n".join(parts)


def split_reply(text: str, max_chars: int) -> list[str]:
    """超限时在引用条目边界分段；单块仍超长才退化为按字符切。"""
    if len(text) <= max_chars:
        return [text]
    blocks: list[str] = []
    for block in _split_blocks(text):
        if len(block) <= max_chars:
            blocks.append(block)
        else:
            blocks.extend(
                block[start : start + max_chars] for start in range(0, len(block), max_chars)
            )
    segments: list[str] = []
    current = ""
    for block in blocks:
        joined = f"{current}\n\n{block}" if current else block
        if len(joined) <= max_chars:
            current = joined
        else:
            segments.append(current)
            current = block
    if current:
        segments.append(current)
    return segments


def _split_blocks(text: str) -> list[str]:
    """按段落与引用条目边界拆块。条目行（数字开头）与其缩进摘要行保持同块。"""
    blocks: list[str] = []
    for part in text.split("\n\n"):
        current: list[str] = []
        for line in part.split("\n"):
            if _ENTRY_START.match(line) and current:
                blocks.append("\n".join(current))
                current = [line]
            else:
                current.append(line)
        if current:
            blocks.append("\n".join(current))
    return [block for block in blocks if block]
