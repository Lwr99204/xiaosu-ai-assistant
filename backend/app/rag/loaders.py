"""文档加载与解析（TDD §4.4.1）。

职责边界：本模块只负责「把字节变成带偏移信息的纯文本」，不做分块、不做向量化。
产出的 RawDocument.content 是后续所有偏移量（Chunk.char_start / char_end）的
唯一索引空间，因此解析期的改写必须最小且可复现：

- 换行统一为 LF，并剥掉 UTF-8 BOM（Windows 导出的语料常见 CRLF）；
- Markdown 保留井号标题标记，供分块器提取章节路径；
- PDF 逐页提取，并记录每页在 content 中占据的字符区间（page_spans），
  分块器据此回填 Chunk.page，引用可标注「第 N 页」；
- DOCX 按 Heading / 标题 样式重建井号层级，表格行用竖线连接。

🔴 刻意不对 content 做 strip：任何首尾裁剪都会让 page_spans 的偏移与 content
的索引空间错位，前端高亮（FR-2-03）就会整体漂移。空文档由 strip 判空后抛
ParseError，但判空不等于改写。

page_spans 是相对 TDD §4.4.1 字段表的追加字段（带默认值，向后兼容）：TDD 要求
Chunk.page 存在，但页码只在解析期可得，若不由 RawDocument 承载，分块器就无从回填。
"""

from __future__ import annotations

import hashlib
import io
import re
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from app.core.errors import ParseError

if TYPE_CHECKING:
    from docx.table import Table
    from docx.text.paragraph import Paragraph

DocExt = Literal["md", "pdf", "docx", "txt"]

#: 扩展名到规范名的映射。.markdown / .text 是常见别名，统一归到 md / txt。
_EXT_ALIASES: dict[str, DocExt] = {
    "md": "md",
    "markdown": "md",
    "txt": "txt",
    "text": "txt",
    "pdf": "pdf",
    "docx": "docx",
}

#: 对外公布的支持类型（M6 上传接口的 422 文案要用）
SUPPORTED_EXTS: tuple[str, ...] = ("md", "txt", "pdf", "docx")

#: python-docx 的 style.name 在英文模板下是 Heading 2，中文模板下是「标题 2」
_HEADING_STYLE = re.compile(r"^(?:Heading|标题)\s*(\d)$")

_BOM = "\ufeff"


@dataclass(frozen=True, slots=True)
class PageSpan:
    """PDF 单页在 RawDocument.content 中占据的字符区间（左闭右开）。"""

    page: int
    char_start: int
    char_end: int


@dataclass(frozen=True, slots=True)
class RawDocument:
    """解析后的文档全文。TDD §4.4.1 字段表加上追加的 page_spans。"""

    doc_id: str
    filename: str
    ext: DocExt
    content: str
    content_hash: str
    byte_size: int
    page_spans: tuple[PageSpan, ...] = ()

    def page_of(self, char_start: int) -> int | None:
        """字符偏移到页码（1 起）。非 PDF 文档恒为 None。"""
        if not self.page_spans:
            return None
        starts = [span.char_start for span in self.page_spans]
        index = bisect_right(starts, char_start) - 1
        if index < 0:
            return None
        return self.page_spans[index].page


def normalize_doc_id(filename: str) -> str:
    """文件名转 doc_id（知识库唯一键）。

    取 basename 后 casefold：Windows 与 macOS 的文件系统大小写不敏感，
    同一篇文档的大写扩展名写法在业务上就是一篇，FR-1-06 的同名替换必须能
    命中，否则会产生两份互相打架的向量。
    """
    return Path(filename).name.casefold()


def detect_ext(filename: str) -> DocExt:
    """按扩展名判定解析器。不支持的类型直接抛 ParseError（422）。"""
    ext = Path(filename).suffix.lstrip(".").lower()
    alias = _EXT_ALIASES.get(ext)
    if alias is None:
        raise ParseError(
            f"不支持的文件类型: {filename}（支持 {'/'.join(SUPPORTED_EXTS)}）",
            filename=filename,
        )
    return alias


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def decode_bytes(data: bytes) -> str:
    """三级编码回退：UTF-8 含 BOM，再 GBK，最后 UTF-8 加 replace（TDD §4.4.1）。

    中文 Windows 导出的 txt 常是 GBK。任何情况都不抛异常导致整篇失败，
    最坏只是出现替换字符，仍比整篇文档被判 failed 好。
    """
    for encoding in ("utf-8-sig", "gbk"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _normalize_newlines(text: str) -> str:
    """只做换行统一与首部 BOM 剥离，不改写任何正文字符。"""
    return text.removeprefix(_BOM).replace("\r\n", "\n").replace("\r", "\n")


def _parse_pdf(data: bytes, *, filename: str) -> tuple[str, tuple[PageSpan, ...]]:
    from pypdf import PdfReader  # 延迟 import：md 与 txt 语料无需拉起该依赖

    try:
        reader = PdfReader(io.BytesIO(data))
        page_texts = [_normalize_newlines(page.extract_text() or "") for page in reader.pages]
    except Exception as exc:  # pypdf 对损坏或加密文件抛多种异常，统一收敛
        raise ParseError(f"PDF 解析失败: {exc}", cause=exc, filename=filename) from exc

    spans: list[PageSpan] = []
    offset = 0
    for number, page_text in enumerate(page_texts, start=1):
        spans.append(PageSpan(page=number, char_start=offset, char_end=offset + len(page_text)))
        offset += len(page_text) + 1  # 加 1 是下面 join 用的页间分隔符 LF
    return "\n".join(page_texts), tuple(spans)


def _paragraph_text(paragraph: Paragraph) -> str:
    style_name = getattr(paragraph.style, "name", "") or ""
    text = paragraph.text.strip()
    if not text:
        return ""
    match = _HEADING_STYLE.match(style_name)
    if match is None:
        return text
    return "#" * int(match.group(1)) + " " + text


def _table_lines(table: Table) -> list[str]:
    lines: list[str] = []
    for row in table.rows:
        cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
        lines.append(" | ".join(cells))
    return lines


def _parse_docx(data: bytes, *, filename: str) -> str:
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    try:
        document = Document(io.BytesIO(data))
        # iter_inner_content 按文档顺序产出 Paragraph 与 Table，
        # 分开取 paragraphs 和 tables 会丢掉两者的相对次序。
        blocks = list(document.iter_inner_content())
    except Exception as exc:
        raise ParseError(f"DOCX 解析失败: {exc}", cause=exc, filename=filename) from exc

    lines: list[str] = []
    for block in blocks:
        if isinstance(block, Paragraph):
            lines.append(_paragraph_text(block))
        elif isinstance(block, Table):
            lines.extend(_table_lines(block))
    return _normalize_newlines("\n".join(lines))


def parse_bytes(data: bytes, *, filename: str) -> RawDocument:
    """字节转 RawDocument。不支持的格式或空内容一律抛 ParseError（FR-1-08）。

    content_hash 取解析后正文的 SHA-256 而非原始字节的：FR-1-07 的去重语义是
    「同一份内容不要入库两遍」，同一篇文档以 UTF-8 和 GBK 各存一份时字节不同
    但内容相同，按正文哈希才能识别为重复。该值同时写入 Chunk.content_hash，
    供文档更新后判断向量是否失效。
    """
    ext = detect_ext(filename)
    if ext == "pdf":
        content, spans = _parse_pdf(data, filename=filename)
    elif ext == "docx":
        content, spans = _parse_docx(data, filename=filename), ()
    else:
        content, spans = _normalize_newlines(decode_bytes(data)), ()

    if not content.strip():
        raise ParseError("文档内容为空", filename=filename)

    return RawDocument(
        doc_id=normalize_doc_id(filename),
        filename=Path(filename).name,
        ext=ext,
        content=content,
        content_hash=sha256_hex(content.encode("utf-8")),
        byte_size=len(data),
        page_spans=spans,
    )


def parse_file(path: Path | str) -> RawDocument:
    """从磁盘读取并解析。批量导入 data/knowledge/ 时走这条入口。"""
    file_path = Path(path)
    try:
        data = file_path.read_bytes()
    except OSError as exc:
        raise ParseError(f"文件读取失败: {exc}", cause=exc, filename=file_path.name) from exc
    return parse_bytes(data, filename=file_path.name)
