"""Unit tests for ai_indexing.schema's noise filters and outline token protection."""

import pytest
from ai_indexing.schema import (
    AIHeading,
    AIStructureChunk,
    _GENERIC_LEGAL_NOISE_RE,
    normalize_chunk,
)


def test_bare_number_with_outline_context_is_retained():
    chunk = AIStructureChunk(
        headings=[
            AIHeading(heading_text="1.", start_page=7, level=2, parent_ref="RESPECTFULLY SHOWETH:"),
            AIHeading(heading_text="2.", start_page=7, level=2, parent_ref="RESPECTFULLY SHOWETH:"),
            AIHeading(heading_text="3", start_page=7, level=2, family_hint="point"),
        ]
    )
    norm_chunk, report = normalize_chunk(chunk, expected_pages=[7])
    assert len(norm_chunk.headings) == 3
    assert norm_chunk.headings[0].heading_text == "1."
    assert norm_chunk.headings[1].heading_text == "2."
    assert norm_chunk.headings[2].heading_text == "3"
    assert report.dropped_empty == 0


def test_paren_enum_token_with_outline_context_bypasses_noise_regex():
    chunk = AIStructureChunk(
        headings=[
            AIHeading(heading_text="(1)", start_page=5, level=3, parent_ref="Clause 4"),
            AIHeading(heading_text="1)", start_page=5, level=3, parent_ref="Clause 4"),
            AIHeading(heading_text="(a)", start_page=5, level=3, parent_ref="Clause 4"),
            AIHeading(heading_text="(i)", start_page=5, level=3, parent_ref="Clause 4"),
        ]
    )
    norm_chunk, report = normalize_chunk(chunk, expected_pages=[5])
    assert len(norm_chunk.headings) == 4
    assert report.dropped_invalid == 0


def test_bare_page_stamp_without_context_is_dropped():
    chunk = AIStructureChunk(
        headings=[
            AIHeading(heading_text="12", start_page=7, level=1, parent_ref=None),
            AIHeading(heading_text="-:12:-", start_page=7, level=1, parent_ref=None),
            AIHeading(heading_text="Page 5", start_page=7, level=1, parent_ref=None),
            AIHeading(heading_text="079789", start_page=7, level=1, parent_ref=None),
        ]
    )
    norm_chunk, report = normalize_chunk(chunk, expected_pages=[7])
    assert len(norm_chunk.headings) == 0
    assert report.dropped_empty == 4


def test_judicial_signature_at_footer_is_dropped():
    chunk = AIStructureChunk(
        headings=[
            AIHeading(heading_text="Alka Malik, Presiding Judge, Special Commercial Court, at Gurugram", start_page=15),
            AIHeading(heading_text="(R.K. Gauba), Additional District Judge, Delhi", start_page=15),
            AIHeading(heading_text="AIJ-cum-Presiding Judge, Special Commercial Court, at Gurugram", start_page=15),
        ]
    )
    norm_chunk, report = normalize_chunk(chunk, expected_pages=[15])
    assert len(norm_chunk.headings) == 0
    assert report.dropped_invalid == 3


def test_ecourts_docket_numbers_are_dropped():
    chunk = AIStructureChunk(
        headings=[
            AIHeading(heading_text="CNR No. HRGR01-2663-2018", start_page=2),
            AIHeading(heading_text="CIS No. ARB/160/2017", start_page=2),
            AIHeading(heading_text="Case No. Commercial Appeal 23 of 2019", start_page=2),
            AIHeading(heading_text="Diary No. 12345/2021", start_page=2),
        ]
    )
    norm_chunk, report = normalize_chunk(chunk, expected_pages=[2])
    assert len(norm_chunk.headings) == 0
    assert report.dropped_invalid == 4


def test_latex_formula_artifacts_are_dropped():
    chunk = AIStructureChunk(
        headings=[
            AIHeading(heading_text="\\$ \\therefore {S}{\\Delta APQ} = {S}{\\Delta AQP} + {S}{\\Delta", start_page=46),
            AIHeading(heading_text="\\therefore \\Delta ABC", start_page=46),
        ]
    )
    norm_chunk, report = normalize_chunk(chunk, expected_pages=[46])
    assert len(norm_chunk.headings) == 0
    assert report.dropped_invalid == 2


def test_preservation_of_legitimate_headings():
    """Negative test: none of these legitimate headings may be dropped."""
    legitimate = [
        "RESPECTFULLY SHOWETH:",
        "MEMO OF PARTIES",
        "JUDGMENT",
        "FACTS",
        "3.4 QUALITY PLANS",
        "ANNEXURE A-1",
        "SECTION 4: TECHNICAL SPECIFICATIONS FOR 400 KV SUB-STATION",
        "SCHEDULE 2: 220 KV SUB-STATION CIVIL WORKS",
        "Clause 11.1 Payment Terms",
        "10.0 TERMS OF PAYMENT",
    ]
    chunk = AIStructureChunk(
        headings=[AIHeading(heading_text=text, start_page=1, level=1) for text in legitimate]
    )
    norm_chunk, report = normalize_chunk(chunk, expected_pages=[1])
    assert len(norm_chunk.headings) == len(legitimate)
    assert report.dropped_invalid == 0
    assert report.dropped_empty == 0
