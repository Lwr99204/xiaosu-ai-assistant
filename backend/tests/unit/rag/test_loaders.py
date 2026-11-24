"""加载解析层单测（TDD §4.4.1）。

覆盖：编码三级回退、BOM/CRLF 归一但正文不改写、doc_id casefold、不支持扩展名的
422 ParseError、空内容、DOCX 标题与表格保序、page_of 边界、PDF 逐页 span，
以及真实语料的前导空格污染回归护栏。
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from docx import Document

from app.config import REPO_ROOT
from app.core.errors import ParseError
from app.rag.loaders import (
    SUPPORTED_EXTS,
    DocExt,
    PageSpan,
    RawDocument,
    decode_bytes,
    detect_ext,
    normalize_doc_id,
    parse_bytes,
    parse_file,
    sha256_hex,
)
from dev_tools.pdf_min import build_pdf

#: 与 tests/unit/test_calculator.py 同一处理：挂 allow_hosts 后 pytest-socket 改守
#: connect()，不再替换 socket 构造，Chroma 与 httpx 的本地初始化才不会被误伤。
pytestmark = pytest.mark.allow_hosts("127.0.0.1")

KNOWLEDGE_DIR = REPO_ROOT / "data" / "knowledge"
CORPUS_FILES = ("员工手册.md", "常见FAQ.md", "新人入职指南.md", "考勤管理办法.txt")


def make_doc(
    content: str,
    *,
    ext: DocExt = "md",
    spans: tuple[PageSpan, ...] = (),
) -> RawDocument:
    """构造只关心 content / ext / page_spans 的 RawDocument，供页码与分块测试复用。"""
    return RawDocument(
        doc_id="test.md",
        filename="test.md",
        ext=ext,
        content=content,
        content_hash="hash",
        byte_size=len(content.encode("utf-8")),
        page_spans=spans,
    )


# ── 编码三级回退 ────────────────────────────────────────────────


def test_decode_bytes_prefers_utf8_and_strips_bom() -> None:
    assert decode_bytes("\ufeff小苏科技".encode("utf-8")) == "小苏科技"


def test_decode_bytes_falls_back_to_gbk() -> None:
    text = "考勤管理办法：迟到超过 30 分钟按旷工半天处理。"
    raw = text.encode("gbk")
    # 先确认这份字节确实过不了 UTF-8，否则这条测试证明不了回退分支被走到
    with pytest.raises(UnicodeDecodeError):
        raw.decode("utf-8")
    assert decode_bytes(raw) == text


def test_decode_bytes_last_resort_replaces_undecodable_bytes() -> None:
    """0xFF 既不是合法 UTF-8 起始字节，也超出 GBK 引导字节范围（0x81-0xFE）。"""
    decoded = decode_bytes(b"\xff" + "小苏".encode())
    assert "\ufffd" in decoded
    assert "小苏" in decoded


# ── BOM 与换行归一，正文一个字都不改 ────────────────────────────


def test_parse_bytes_strips_bom_and_normalizes_newlines() -> None:
    data = "\ufeff# 考勤制度\r\n第一条 工时\r\n第二条 请假\r".encode("utf-8")
    doc = parse_bytes(data, filename="考勤.md")
    assert doc.content == "# 考勤制度\n第一条 工时\n第二条 请假\n"
    assert "\ufeff" not in doc.content
    assert "\r" not in doc.content


def test_parse_bytes_keeps_body_whitespace_intact() -> None:
    """🔴 刻意不 strip：首尾裁剪会让 page_spans 偏移与 content 索引空间整体错位。"""
    doc = parse_bytes("  缩进正文\n\n".encode(), filename="a.txt")
    assert doc.content == "  缩进正文\n\n"


# ── doc_id 与扩展名判定 ─────────────────────────────────────────


def test_normalize_doc_id_casefolds_basename() -> None:
    assert normalize_doc_id("data/knowledge/员工手册.MD") == "员工手册.md"
    assert normalize_doc_id("C:\\临时\\REPORT.PDF") == "report.pdf"
    assert normalize_doc_id("员工手册.md") == "员工手册.md"


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("a.md", "md"),
        ("a.markdown", "md"),
        ("a.txt", "txt"),
        ("a.text", "txt"),
        ("a.pdf", "pdf"),
        ("a.docx", "docx"),
        ("a.DOCX", "docx"),
        ("a.PdF", "pdf"),
    ],
)
def test_detect_ext_resolves_aliases(filename: str, expected: str) -> None:
    assert detect_ext(filename) == expected


@pytest.mark.parametrize("filename", ["报表.xls", "档案.zip", "README", "legacy.doc"])
def test_detect_ext_rejects_unsupported(filename: str) -> None:
    with pytest.raises(ParseError) as info:
        detect_ext(filename)
    assert info.value.code == "DOC_PARSE_ERROR"
    assert info.value.http_status == 422
    assert info.value.retryable is False
    assert filename in info.value.message
    assert "/".join(SUPPORTED_EXTS) in info.value.message


def test_supported_exts_are_all_parseable() -> None:
    """批量导入按 SUPPORTED_EXTS 过滤，因此这张表里的每个扩展名都必须能过判定。"""
    assert SUPPORTED_EXTS == ("md", "txt", "pdf", "docx")
    for ext in SUPPORTED_EXTS:
        assert detect_ext(f"sample.{ext}") == ext


# ── 空内容与元数据 ──────────────────────────────────────────────


@pytest.mark.parametrize("data", [b"", b"   ", b"\n\n\t \n", "\ufeff".encode("utf-8")])
def test_parse_bytes_rejects_empty_content(data: bytes) -> None:
    with pytest.raises(ParseError) as info:
        parse_bytes(data, filename="空.md")
    assert "内容为空" in info.value.message
    assert info.value.http_status == 422


def test_content_hash_is_encoding_independent() -> None:
    """FR-1-07 去重按解析后正文哈希：同一篇文档的 UTF-8 与 GBK 两份字节应判为重复。"""
    text = "小苏科技考勤管理办法"
    utf8 = parse_bytes(text.encode("utf-8"), filename="考勤.txt")
    gbk = parse_bytes(text.encode("gbk"), filename="考勤.txt")
    assert utf8.content == gbk.content == text
    assert utf8.content_hash == gbk.content_hash
    assert utf8.byte_size != gbk.byte_size


def test_parse_bytes_records_basename_and_doc_id() -> None:
    doc = parse_bytes("# 标题\n正文".encode(), filename="docs/员工手册.MD")
    assert doc.filename == "员工手册.MD"
    assert doc.doc_id == "员工手册.md"
    assert doc.ext == "md"
    assert doc.page_spans == ()


# ── DOCX：标题层级重建与段落表格保序 ────────────────────────────


def build_docx_bytes() -> bytes:
    document = Document()
    document.add_heading("第一章 总则", level=1)
    document.add_paragraph("本章说明制度的适用范围。")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "假期类型"
    table.cell(0, 1).text = "天数"
    table.cell(1, 0).text = "年假"
    table.cell(1, 1).text = "10"
    document.add_heading("1.1 年假", level=2)
    document.add_paragraph("入职满一年可享受年假。")
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def test_parse_docx_rebuilds_headings_and_keeps_table_order() -> None:
    doc = parse_bytes(build_docx_bytes(), filename="制度.docx")
    lines = [line for line in doc.content.splitlines() if line.strip()]
    assert lines == [
        "# 第一章 总则",
        "本章说明制度的适用范围。",
        "假期类型 | 天数",
        "年假 | 10",
        "## 1.1 年假",
        "入职满一年可享受年假。",
    ]
    assert doc.ext == "docx"
    assert doc.page_spans == ()
    assert doc.page_of(0) is None


def test_parse_docx_rejects_corrupt_bytes() -> None:
    with pytest.raises(ParseError) as info:
        parse_bytes(b"PK\x03\x04 not a real docx", filename="坏.docx")
    assert "DOCX 解析失败" in info.value.message
    assert info.value.code == "DOC_PARSE_ERROR"


# ── PDF：逐页 span 与往返提取 ───────────────────────────────────


def build_pdf_bytes() -> bytes:
    return build_pdf(
        [
            ["第一页 考勤制度", "员工应当按时出勤，迟到按本办法处理。"],
            ["第二页 请假流程", "请假需提前一天在系统提交申请。"],
        ]
    )


def test_parse_pdf_records_page_spans_and_round_trips() -> None:
    doc = parse_bytes(build_pdf_bytes(), filename="考勤.pdf")
    assert doc.ext == "pdf"
    assert len(doc.page_spans) == 2
    assert [span.page for span in doc.page_spans] == [1, 2]
    assert doc.page_spans[0].char_start == 0
    assert doc.page_spans[-1].char_end == len(doc.content)
    # 页间用一个 LF 连接，因此后一页起点 = 前一页终点 + 1
    assert doc.page_spans[1].char_start == doc.page_spans[0].char_end + 1
    first, second = doc.page_spans
    assert "第一页" in doc.content[first.char_start : first.char_end]
    assert "第二页" in doc.content[second.char_start : second.char_end]
    assert doc.page_of(doc.content.index("第一页")) == 1
    assert doc.page_of(doc.content.index("第二页")) == 2


def test_parse_pdf_rejects_corrupt_bytes() -> None:
    with pytest.raises(ParseError) as info:
        parse_bytes(b"%PDF-1.4 broken", filename="坏.pdf")
    assert info.value.code == "DOC_PARSE_ERROR"
    assert info.value.http_status == 422


# ── page_of 边界 ────────────────────────────────────────────────


def test_page_of_boundaries() -> None:
    spans = (
        PageSpan(page=1, char_start=0, char_end=10),
        PageSpan(page=2, char_start=11, char_end=25),
    )
    doc = make_doc("甲" * 10 + "\n" + "乙" * 14, ext="pdf", spans=spans)
    assert len(doc.content) == 25
    assert doc.page_of(0) == 1
    assert doc.page_of(9) == 1
    assert doc.page_of(10) == 1  # 页间分隔符 LF 仍归属前一页
    assert doc.page_of(11) == 2
    assert doc.page_of(24) == 2
    assert doc.page_of(999) == 2  # 越过末页仍归末页，重叠区不会拿到 None
    assert doc.page_of(-1) is None


def test_page_of_is_always_none_without_spans() -> None:
    doc = make_doc("正文内容")
    assert doc.page_spans == ()
    assert doc.page_of(0) is None


# ── parse_file ──────────────────────────────────────────────────


def test_parse_file_reads_from_disk(tmp_path: Path) -> None:
    target = tmp_path / "手册.md"
    target.write_text("# 标题\n正文内容。\n", encoding="utf-8")
    doc = parse_file(target)
    assert doc.content == "# 标题\n正文内容。\n"
    assert doc.doc_id == "手册.md"
    assert doc.byte_size == target.stat().st_size


def test_parse_file_wraps_os_error(tmp_path: Path) -> None:
    with pytest.raises(ParseError) as info:
        parse_file(tmp_path / "不存在.md")
    assert "文件读取失败" in info.value.message
    assert isinstance(info.value.cause, OSError)


# ── 真实语料回归护栏 ────────────────────────────────────────────


@pytest.mark.parametrize("filename", CORPUS_FILES)
def test_real_corpus_has_no_leading_space_pollution(filename: str) -> None:
    """🔴 语料每行多写一个前导空格曾让标题正则全瞎、分块退化成按 600 字硬切。"""
    path = KNOWLEDGE_DIR / filename
    assert path.exists(), f"缺少语料文件 {path}"
    doc = parse_file(path)
    assert doc.content.strip()
    assert not doc.content.startswith(" ")
    polluted = [line for line in doc.content.splitlines() if line.startswith(" #")]
    assert not polluted, f"{filename} 有 {len(polluted)} 行标题被前导空格污染"


def test_real_corpus_markdown_headings_are_detected() -> None:
    doc = parse_file(KNOWLEDGE_DIR / "员工手册.md")
    headings = [line for line in doc.content.splitlines() if line.startswith("#")]
    assert len(headings) >= 10
    assert any("年假" in heading for heading in headings)


@pytest.mark.parametrize("filename", CORPUS_FILES)
def test_real_corpus_content_hash_matches_content(filename: str) -> None:
    doc = parse_file(KNOWLEDGE_DIR / filename)
    assert doc.content_hash == sha256_hex(doc.content.encode("utf-8"))
