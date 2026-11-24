"""答案引用编号、越界防御与原文定位单测。"""

from __future__ import annotations

import logging

from app.rag.citations import (
    Citation,
    build_citations,
    citation_to_mapping,
    citations_from_mappings,
    iter_ref_nos,
)

HITS = [
    {
        "doc_id": "员工手册.md",
        "filename": "员工手册.md",
        "heading_path": ["第三章 休假制度", "3.1 年假"],
        "snippet": "员工每年享受十天年假。",
        "char_start": 120,
        "char_end": 132,
        "page": None,
        "score": 0.81,
    },
    {
        "doc_id": "常见faq.md",
        "filename": "常见FAQ.md",
        "heading_path": "休假 > 申请流程",
        "snippet": "年假应提前提交申请。",
        "char_start": 40,
        "char_end": 51,
        "page": 2,
        "score": 0.72,
    },
]


def test_iter_ref_nos_keeps_first_seen_order_and_deduplicates() -> None:
    assert iter_ref_nos("年假十天[2]，需申请[1]，再次说明[2]。") == [2, 1]


def test_build_citations_keeps_valid_markers_and_exact_offsets() -> None:
    answer, citations = build_citations("年假为十天[1]，需提前申请[2]。", HITS)
    assert answer == "年假为十天[1]，需提前申请[2]。"
    assert [item.ref_no for item in citations] == [1, 2]
    assert citations[0].heading_path == "第三章 休假制度 > 3.1 年假"
    assert (citations[0].char_start, citations[0].char_end) == (120, 132)
    assert citations[1].page == 2


def test_build_citations_drops_out_of_range_marker(caplog) -> None:
    with caplog.at_level(logging.WARNING):
        answer, citations = build_citations("制度要求如此[1]，不存在的来源[9]。", HITS)
    assert answer == "制度要求如此[1]，不存在的来源。"
    assert [item.ref_no for item in citations] == [1]
    assert "citation_out_of_range" in caplog.text


def test_mapping_round_trip_preserves_all_location_fields() -> None:
    original = Citation(
        ref_no=3,
        doc_id="制度.pdf",
        filename="制度.pdf",
        heading_path="考勤",
        snippet="迟到规则",
        char_start=88,
        char_end=92,
        page=4,
        score=0.66,
    )
    restored = citations_from_mappings([citation_to_mapping(original)])
    assert restored == [original]
