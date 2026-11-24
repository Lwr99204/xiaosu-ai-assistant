"""结构化分块单测（TDD §4.4.2）。

四阶段（语义单元 → 超长二次切分 → 过短合并 → 重叠对齐）逐阶段验证，外加贯穿全部
阶段的不变量 chunk.text == content[char_start:char_end]。这条不变量是前端原文高亮
（FR-2-03）能成立的前提，因此在合成语料与 4 份真实语料上都逐块断言。

隔离手法：验证阶段 A / B / C 时把 min_chars 压到 1、overlap_chars 压到 0，
避免合并与重叠把被验证阶段的效果改掉，导致断言说不清是哪一步出的问题。
"""

from __future__ import annotations

from collections.abc import Sequence
from itertools import pairwise

import pytest

from app.config import REPO_ROOT, Settings
from app.rag.chunking import (
    DEFAULT_MAX_CHARS,
    DEFAULT_MIN_CHARS,
    DEFAULT_OVERLAP_CHARS,
    Chunk,
    split_chunks,
)
from app.rag.loaders import DocExt, PageSpan, RawDocument, parse_file

pytestmark = pytest.mark.allow_hosts("127.0.0.1")

KNOWLEDGE_DIR = REPO_ROOT / "data" / "knowledge"

#: 已人工核对过的真实语料分块数（默认 600/80/50 参数下）。这些数字变化意味着分块
#: 行为被改动，需要重新确认 Q1-Q3 的考题落点，因此当作回归基线钉住。
CORPUS_CHUNK_COUNTS = {
    "员工手册.md": 26,
    "常见FAQ.md": 27,
    "新人入职指南.md": 14,
    "考勤管理办法.txt": 11,
}


def make_doc(
    content: str,
    *,
    ext: DocExt = "md",
    spans: tuple[PageSpan, ...] = (),
) -> RawDocument:
    """构造只关心 content / ext / page_spans 的 RawDocument。"""
    return RawDocument(
        doc_id="test.md",
        filename="test.md",
        ext=ext,
        content=content,
        content_hash="hash",
        byte_size=len(content.encode()),
        page_spans=spans,
    )


def assert_invariant(doc: RawDocument, chunks: Sequence[Chunk]) -> None:
    """逐块断言切片不变量、编号连续性与原文顺序。"""
    assert chunks, "分块结果不应为空"
    for index, chunk in enumerate(chunks):
        assert chunk.text == doc.content[chunk.char_start : chunk.char_end], (
            f"第 {index} 块偏移错位：text 与 content[char_start:char_end] 不一致"
        )
        assert chunk.char_end > chunk.char_start
        assert chunk.index == index
        assert chunk.chunk_id == f"{doc.doc_id}#{index:04d}"
        assert chunk.doc_id == doc.doc_id
        assert chunk.content_hash == doc.content_hash
    starts = [chunk.char_start for chunk in chunks]
    assert starts == sorted(starts), "块必须按原文顺序排列"


# ── 阶段 A：语义单元切分与标题栈 ────────────────────────────────

HEADING_STACK_MD = """# 一级甲
## 二级A
### 三级
## 二级B
# 一级乙
"""


def test_heading_stack_pops_on_same_or_higher_level() -> None:
    chunks = split_chunks(make_doc(HEADING_STACK_MD), min_chars=1, overlap_chars=0)
    assert [chunk.heading_path for chunk in chunks] == [
        ("一级甲",),
        ("一级甲", "二级A"),
        ("一级甲", "二级A", "三级"),
        ("一级甲", "二级B"),
        ("一级乙",),
    ]


def test_markdown_section_text_includes_its_own_heading() -> None:
    """标题词往往正是查询命中的关键，因此节文本必须含标题行本身。"""
    content = "# 第三章 休假制度\n本章规定年假、病假与事假的天数与申请流程。\n"
    doc = make_doc(content)
    chunks = split_chunks(doc, min_chars=1, overlap_chars=0)
    assert len(chunks) == 1
    assert chunks[0].text.startswith("# 第三章 休假制度")
    assert chunks[0].heading_path == ("第三章 休假制度",)
    assert_invariant(doc, chunks)


def test_content_before_first_heading_has_empty_heading_path() -> None:
    content = (
        "前言：本手册适用于全体员工，阅读前请先确认版本。\n"
        "\n"
        "# 第一章 总则\n"
        "本章正文足够长，能够独立成块，因此这里多写一些文字来凑够最小长度要求。\n"
    )
    doc = make_doc(content)
    chunks = split_chunks(doc, min_chars=1, overlap_chars=0)
    assert chunks[0].heading_path == ()
    assert chunks[0].text.startswith("前言")
    assert chunks[1].heading_path == ("第一章 总则",)
    assert_invariant(doc, chunks)


def test_markdown_without_headings_falls_back_to_whole_content() -> None:
    content = "这份文档没有任何井号标题，因此整体作为一个语义单元处理。"
    doc = make_doc(content)
    chunks = split_chunks(doc, min_chars=1, overlap_chars=0)
    assert len(chunks) == 1
    assert chunks[0].heading_path == ()
    assert chunks[0].text == content


def test_txt_splits_on_blank_lines_with_empty_heading_path() -> None:
    content = (
        "第一条 员工应当按时出勤，迟到超过三十分钟按旷工半天处理。\n"
        "\n"
        "第二条 请假需提前一天在系统提交申请，并由直属上级审批。\n"
        "\n"
        "第三条 加班需事先申请，未经审批的自行留岗不计入加班时长。\n"
    )
    doc = make_doc(content, ext="txt")
    chunks = split_chunks(doc, min_chars=1, overlap_chars=0)
    assert len(chunks) == 3
    assert all(chunk.heading_path == () for chunk in chunks)
    assert chunks[0].text.startswith("第一条")
    assert chunks[2].text.startswith("第三条")
    assert_invariant(doc, chunks)


def test_txt_does_not_guess_headings_from_hash_lines() -> None:
    """txt 走空行分段，正文里出现井号也不当标题（TDD §4.4.2 不做启发式猜测）。"""
    content = "# 这不是标题\n第一条 正文内容。\n\n第二条 另一段正文内容。"
    doc = make_doc(content, ext="txt")
    chunks = split_chunks(doc, min_chars=1, overlap_chars=0)
    assert all(chunk.heading_path == () for chunk in chunks)
    assert_invariant(doc, chunks)


# ── 阶段 B：超长二次切分的三级降级 ──────────────────────────────


def test_long_section_prefers_newline_break() -> None:
    lines = [f"第{i:03d}行内容，用于验证超长节优先在换行处断开而不是句中硬切。" for i in range(40)]
    content = "# 超长章节\n" + "\n".join(lines)
    doc = make_doc(content)
    chunks = split_chunks(doc, max_chars=200, overlap_chars=0, min_chars=1)
    assert len(chunks) > 1
    assert all(len(chunk.text) <= 200 for chunk in chunks)
    for chunk in chunks[1:]:
        assert content[chunk.char_start - 1] == "\n", "断点应落在换行之后"
    assert_invariant(doc, chunks)


def test_long_section_falls_back_to_sentence_terminator() -> None:
    unit = "这一句用来验证没有换行时会退到句子终止符断句。"
    content = unit * 40
    doc = make_doc(content, ext="txt")
    chunks = split_chunks(doc, max_chars=200, overlap_chars=0, min_chars=1)
    assert len(chunks) > 1
    assert "\n" not in content
    for chunk in chunks[:-1]:
        assert chunk.text.endswith("。"), "断点应落在句子终止符之后"
        assert len(chunk.text) <= 200
    assert_invariant(doc, chunks)


def test_long_section_hard_cuts_when_no_break_exists() -> None:
    content = "字" * 1500
    doc = make_doc(content, ext="txt")
    chunks = split_chunks(doc, max_chars=600, overlap_chars=0, min_chars=1)
    assert [len(chunk.text) for chunk in chunks] == [600, 600, 300]
    assert [(chunk.char_start, chunk.char_end) for chunk in chunks] == [
        (0, 600),
        (600, 1200),
        (1200, 1500),
    ]
    assert_invariant(doc, chunks)


def test_decimal_numbering_is_not_split_as_sentence() -> None:
    """🔴 句子终止符刻意不含英文句点，否则「3.1 年假」这类编号会被当成句尾切开。"""
    content = (
        "3.1 年假：入职满 1 年不满 5 年享受 10 天，满 5 年不满 10 年享受 15 天。"
        + "补充说明文字。" * 30
    )
    doc = make_doc(content, ext="txt")
    chunks = split_chunks(doc, max_chars=100, overlap_chars=0, min_chars=1)
    assert chunks[0].text.startswith("3.1 年假")
    assert "3.1" in chunks[0].text
    assert not any(chunk.text.startswith("1 年假") for chunk in chunks)
    assert_invariant(doc, chunks)


# ── 阶段 C：过短块合并 ──────────────────────────────────────────


def test_short_section_merges_into_previous() -> None:
    content = (
        "# 第一章 总则\n"
        "本章正文足够长，能够独立成块，因此不会被过短合并规则吞掉，这里再多写一些文字。\n"
        "# 第二章\n"
        "短\n"
    )
    doc = make_doc(content)
    chunks = split_chunks(
        doc,
        max_chars=DEFAULT_MAX_CHARS,
        overlap_chars=0,
        min_chars=DEFAULT_MIN_CHARS,
    )
    assert len(chunks) == 1
    assert chunks[0].heading_path == ("第一章 总则",)
    assert "第二章" in chunks[0].text
    assert_invariant(doc, chunks)


def test_leading_short_section_absorbs_the_next_one() -> None:
    """首块过短时没有前一块可并，条件会让它顺势吸收下一块（等价于向后合并）。"""
    content = (
        "# 短\n"
        "# 第一章 总则\n"
        "本章正文足够长，能够独立成块，因此这里多写一些文字来凑够最小长度要求。\n"
    )
    doc = make_doc(content)
    chunks = split_chunks(doc, overlap_chars=0, min_chars=DEFAULT_MIN_CHARS)
    assert len(chunks) == 1
    assert chunks[0].heading_path == ("短",)
    assert "第一章 总则" in chunks[0].text
    assert_invariant(doc, chunks)


def test_trimmed_sections_are_dropped() -> None:
    """纯空白区间直接消失，不会产出空块污染向量库。"""
    assert split_chunks(make_doc("   \n\n  ")) == []


# ── 阶段 D：重叠并对齐整行起点 ──────────────────────────────────


def test_overlap_extends_start_to_a_line_boundary() -> None:
    lines = [
        f"第{i:02d}行：这一行的内容足够长，可以用来验证重叠区是否从整行起点开始。"
        for i in range(60)
    ]
    content = "\n".join(lines)
    doc = make_doc(content, ext="txt")
    chunks = split_chunks(doc, max_chars=300, overlap_chars=80, min_chars=1)
    assert len(chunks) > 2
    assert chunks[0].char_start == 0
    for previous, current in pairwise(chunks):
        assert current.char_start < previous.char_end, "相邻块必须保留重叠"
        assert current.char_start > previous.char_start, "起点必须单调推进"
        assert content[current.char_start - 1] == "\n", "重叠区必须从整行起点开始"
    assert_invariant(doc, chunks)


def test_overlap_is_skipped_when_disabled() -> None:
    content = "\n".join(f"第{i:02d}行内容，用来验证关闭重叠时相邻块首尾相接。" for i in range(60))
    doc = make_doc(content, ext="txt")
    chunks = split_chunks(doc, max_chars=200, overlap_chars=0, min_chars=1)
    assert len(chunks) > 1
    for previous, current in pairwise(chunks):
        assert current.char_start == previous.char_end
    assert_invariant(doc, chunks)


# ── 贯穿阶段：默认参数、页码回填、真实语料 ──────────────────────


def test_default_parameters_match_settings() -> None:
    """分块默认值与 Settings 字段必须同源，否则脚本与在线服务会切出不同的块。"""
    settings = Settings(_env_file=None, llm_provider="mock", llm_api_key="sk-test-1")
    assert settings.chunk_max_chars == DEFAULT_MAX_CHARS
    assert settings.chunk_overlap_chars == DEFAULT_OVERLAP_CHARS
    assert DEFAULT_MIN_CHARS == 50


def test_page_is_backfilled_from_page_spans() -> None:
    content = "甲" * 100 + "\n\n" + "乙" * 100
    spans = (
        PageSpan(page=1, char_start=0, char_end=100),
        PageSpan(page=2, char_start=102, char_end=202),
    )
    doc = make_doc(content, ext="pdf", spans=spans)
    chunks = split_chunks(doc, min_chars=1, overlap_chars=0)
    assert [chunk.page for chunk in chunks] == [1, 2]
    assert_invariant(doc, chunks)


def test_page_is_none_for_non_pdf_documents() -> None:
    doc = make_doc("# 标题\n正文内容足够长，可以独立成块，所以这里多写一些文字来凑够长度。")
    chunks = split_chunks(doc)
    assert chunks
    assert all(chunk.page is None for chunk in chunks)


@pytest.mark.parametrize(("filename", "expected"), sorted(CORPUS_CHUNK_COUNTS.items()))
def test_real_corpus_chunk_count_is_stable(filename: str, expected: int) -> None:
    doc = parse_file(KNOWLEDGE_DIR / filename)
    chunks = split_chunks(doc)
    assert len(chunks) == expected
    assert_invariant(doc, chunks)
    assert all(chunk.text.strip() for chunk in chunks), "真实语料不应切出纯空白块"


def test_real_corpus_hits_the_exam_questions() -> None:
    """考题对齐：Q1 年假与 Q2 报销发票都必须能在员工手册的标题路径里找到落点。"""
    handbook = parse_file(KNOWLEDGE_DIR / "员工手册.md")
    paths = [" › ".join(chunk.heading_path) for chunk in split_chunks(handbook)]
    assert any("年假" in path for path in paths)
    assert any("报销" in path for path in paths)


def test_real_corpus_guide_covers_first_day() -> None:
    """Q3 新人第一天的落点在新人入职指南，该文档必须能切出带标题路径的块。"""
    guide = parse_file(KNOWLEDGE_DIR / "新人入职指南.md")
    chunks = split_chunks(guide)
    assert chunks
    assert any(chunk.heading_path for chunk in chunks)
