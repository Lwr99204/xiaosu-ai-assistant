"""结构化分块（TDD §4.4.2）。本项目 RAG 的引用定位能力就落在这一层。

不用「固定长度滑窗」，而是按文档结构切分并在原文索引空间里记录精确偏移，
这样前端拿到 doc_id + char_start + char_end 就能在原文预览里高亮那一段
（FR-2-03）。滑窗方案丢了偏移就再也补不回来，这是自研分块器相对现成库的
核心增量价值。

四个阶段，顺序不可调换：

A. 切语义单元。md 与 docx 按井号标题切节并维护标题栈得到 heading_path；
   txt 与 pdf 按空行分段（TDD 明确不为纯文本做启发式标题猜测）。
B. 超长二次切分。单节超过 max_chars 时按段落、再按句子降级切，最后才硬切，
   保证不在句中截断。
C. 过短合并。跨度小于 min_chars 的块并入前一块，避免「3.1 年假」这种孤立
   标题变成无意义向量。
D. 重叠。把 char_start 向前扩展到前一块尾部，并把扩展点推进到最近的换行后，
   使重叠区从整行开始。

不变量：任何阶段都只调整 (start, end) 区间，Chunk.text 恒等于
content[char_start:char_end]。这条不变量是高亮定位与向量语义一致的前提，
单测里逐块断言。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.rag.loaders import RawDocument

#: TDD §4.4.2 的默认参数，与 Settings 中的同名字段一致
DEFAULT_MAX_CHARS = 600
DEFAULT_OVERLAP_CHARS = 80
DEFAULT_MIN_CHARS = 50

_HEADING_LINE = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)
_BLANK_LINE = re.compile(r"\n\s*\n")

#: 二次切分的句子边界。只收中文终止符加英文叹号问号，刻意不含英文句点，
#: 否则「3.1 年假」这类小数编号会被当成句子结尾切开。
_SENTENCE_TERMINATORS = ("。", "！", "？", "；", "!", "?")


@dataclass(frozen=True, slots=True)
class Chunk:
    """一个可向量化的文本块。TDD §4.4.2 字段表原样实现。"""

    chunk_id: str
    doc_id: str
    index: int
    text: str
    heading_path: tuple[str, ...]
    char_start: int
    char_end: int
    page: int | None = None
    content_hash: str = ""


@dataclass(frozen=True, slots=True)
class _Piece:
    """分块过程中的中间区间。heading_path 在阶段 A 定型后不再变化。"""

    start: int
    end: int
    heading_path: tuple[str, ...]

    @property
    def length(self) -> int:
        return self.end - self.start


def _trim(content: str, start: int, end: int) -> tuple[int, int]:
    """收缩区间到非空白字符，避免向量里塞进纯缩进与空行。"""
    while start < end and content[start].isspace():
        start += 1
    while end > start and content[end - 1].isspace():
        end -= 1
    return start, end


def _markdown_pieces(content: str) -> list[_Piece]:
    """按井号标题切节，节文本包含标题行本身（标题词往往正是查询命中的关键）。

    已知取舍：代码块里以井号开头的行会被误判为标题。本项目的语料是中文制度
    文档，不含代码块，故不为它引入 Markdown 词法分析。
    """
    matches = list(_HEADING_LINE.finditer(content))
    if not matches:
        return _whole_content_pieces(content)

    pieces: list[_Piece] = []
    if matches[0].start() > 0:
        pieces.extend(_make_piece(content, 0, matches[0].start(), ()))

    stack: list[tuple[int, str]] = []
    for position, match in enumerate(matches):
        level = len(match.group(1))
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, match.group(2).strip()))
        start = match.start()
        end = matches[position + 1].start() if position + 1 < len(matches) else len(content)
        pieces.extend(_make_piece(content, start, end, tuple(title for _, title in stack)))
    return pieces


def _paragraph_pieces(content: str) -> list[_Piece]:
    """txt 与 pdf 走这条：按空行分段，heading_path 恒为空。"""
    pieces: list[_Piece] = []
    cursor = 0
    for match in _BLANK_LINE.finditer(content):
        pieces.extend(_make_piece(content, cursor, match.start(), ()))
        cursor = match.end()
    pieces.extend(_make_piece(content, cursor, len(content), ()))
    return pieces


def _whole_content_pieces(content: str) -> list[_Piece]:
    return _make_piece(content, 0, len(content), ())


def _make_piece(content: str, start: int, end: int, heading_path: tuple[str, ...]) -> list[_Piece]:
    """构造单个 piece，返回 list 是为了让「空白区间直接消失」这件事在调用点
    表现为 extend 一个空列表，从而避免每处都写一遍判空分支。"""
    trimmed_start, trimmed_end = _trim(content, start, end)
    if trimmed_start >= trimmed_end:
        return []
    return [_Piece(trimmed_start, trimmed_end, heading_path)]


def _find_break(content: str, start: int, max_chars: int) -> int:
    """在 (start, start + max_chars] 内找最佳断点，返回切分位置。

    优先换行，其次句子终止符，都没有就硬切到 max_chars。搜索范围刻意跳过
    窗口前四分之一，否则一个紧跟在窗口开头的换行会切出几十字符的碎块。
    """
    limit = start + max_chars
    window = content[start:limit]
    floor = max_chars // 4

    newline = window.rfind("\n", floor)
    if newline >= 0:
        return start + newline + 1

    best = -1
    for terminator in _SENTENCE_TERMINATORS:
        best = max(best, window.rfind(terminator, floor))
    if best >= 0:
        return start + best + 1
    return limit


def _split_long(content: str, piece: _Piece, max_chars: int) -> list[_Piece]:
    if piece.length <= max_chars:
        return [piece]
    parts: list[_Piece] = []
    cursor = piece.start
    while piece.end - cursor > max_chars:
        cut = _find_break(content, cursor, max_chars)
        parts.append(_Piece(cursor, cut, piece.heading_path))
        cursor = cut
    parts.append(_Piece(cursor, piece.end, piece.heading_path))
    return parts


def _merge_short(pieces: list[_Piece], min_chars: int) -> list[_Piece]:
    """把过短的块并入前一块（TDD §4.4.2 第 6 条）。

    首块过短时没有前一块可并，条件里的 out[-1].length < min_chars 会让它
    顺势吸收下一块，等价于向后合并。合并后保留前一块的 heading_path：被吞掉
    的都是不足 50 字符的标题残片，其文字仍在块内参与向量化，只是引用展示时
    归到上一节的标题路径下。
    """
    out: list[_Piece] = []
    for piece in pieces:
        if out and (piece.length < min_chars or out[-1].length < min_chars):
            previous = out[-1]
            out[-1] = _Piece(previous.start, piece.end, previous.heading_path)
            continue
        out.append(piece)
    return out


def _apply_overlap(content: str, pieces: list[_Piece], overlap_chars: int) -> list[_Piece]:
    """把每块的起点向前扩展，与前一块尾部保留 overlap_chars 的重叠。

    扩展点会推进到 [候选起点, 原起点) 内第一个换行之后，让重叠区从整行开始，
    避免把半句话塞进向量。代价是实际重叠量小于配置值，这是可接受的：宁可
    重叠少一点，也不要碎句。首块没有前一块，不做扩展。
    """
    if overlap_chars <= 0:
        return pieces
    out: list[_Piece] = []
    for position, piece in enumerate(pieces):
        if position == 0:
            out.append(piece)
            continue
        start = max(pieces[position - 1].start, piece.start - overlap_chars)
        newline = content.find("\n", start, piece.start)
        out.append(_Piece(newline + 1 if newline >= 0 else start, piece.end, piece.heading_path))
    return out


def split_chunks(
    doc: RawDocument,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
    min_chars: int = DEFAULT_MIN_CHARS,
) -> list[Chunk]:
    """把 RawDocument 切成带精确偏移的 Chunk 列表。

    返回空列表表示文档没有可索引内容，调用方应把该文档置 failed。
    """
    content = doc.content
    split_sections = _markdown_pieces if doc.ext in ("md", "docx") else _paragraph_pieces
    pieces = split_sections(content)

    expanded: list[_Piece] = []
    for piece in pieces:
        expanded.extend(_split_long(content, piece, max_chars))
    overlapped = _apply_overlap(content, _merge_short(expanded, min_chars), overlap_chars)

    chunks: list[Chunk] = []
    for piece in overlapped:
        index = len(chunks)
        chunks.append(
            Chunk(
                chunk_id=f"{doc.doc_id}#{index:04d}",
                doc_id=doc.doc_id,
                index=index,
                text=content[piece.start : piece.end],
                heading_path=piece.heading_path,
                char_start=piece.start,
                char_end=piece.end,
                page=doc.page_of(piece.start),
                content_hash=doc.content_hash,
            )
        )
    return chunks
