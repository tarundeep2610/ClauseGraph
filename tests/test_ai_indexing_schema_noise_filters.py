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


def test_contract_execution_signature_block_is_dropped():
    """Commercial-contract execution/signature boilerplate (as opposed to the
    court-pleading signature blocks already covered above) — surfaced by the
    100-page fixture's embedded Contract Agreement annexure (pages 66-68),
    where these lines were found escaping the filter and over-generating."""
    chunk = AIStructureChunk(
        headings=[
            AIHeading(heading_text="IN WITNESS WHEREOF, the parties through their duly authorised representatives have executed these presents", start_page=66),
            AIHeading(heading_text="Signed, sealed and Delivered by the.", start_page=66),
            AIHeading(heading_text="NAME : H. G. CHHABRA", start_page=66),
            AIHeading(heading_text="TITLE : CHIEF ENGINEER/MM,", start_page=66),
            AIHeading(heading_text="IN THE PRESENCE OF", start_page=66),
        ]
    )
    norm_chunk, report = normalize_chunk(chunk, expected_pages=[66])
    assert len(norm_chunk.headings) == 0
    assert report.dropped_invalid == 5


def test_note_colon_container_headings_are_preserved_bare_or_not():
    """Confirmed regression against the 135-page tender-doc gold fixture's
    own build script (scratch/build_gold_fixture.py): 'NOTE: -'/'Note: -'/
    'Note:' are the REAL, literal heading text of real containers with real
    numbered children (Tender Notice's 13-point Note, Key Dates' nested
    Note, Section-II's, Annexure-IV's 11-point Note) — confirmed directly,
    the child headings reference the container via parent_ref="NOTE: -"
    etc., so the container's own text is exactly this bare string, not a
    richer "Note: <content>" variant. The original unscoped
    'Note\\s*:\\s*.*' regex blanket-dropped every 'Note:'-headed heading
    regardless of content, which would drop the container object itself
    and orphan its children. No confirmed real-document noise case for a
    bare 'Note:'/'Note: -' heading was found anywhere in this repo's tests
    or fixtures, and no existing test relied on dropping one — the
    alternative was removed from the filter outright rather than narrowed,
    since narrowing to "bare-only" would have kept dropping exactly the
    real container shape gold wants kept (same literal string, both
    cases)."""
    chunk = AIStructureChunk(
        headings=[
            AIHeading(heading_text="NOTE: -", start_page=3, level=2, parent_ref="TENDER NOTICE"),
            AIHeading(
                heading_text="(1) Other details can be seen in the complete tender documents",
                start_page=3, level=3, parent_ref="NOTE: -",
            ),
            AIHeading(heading_text="Note: See clauses 1-13 below for full requirements", start_page=3, level=2),
        ]
    )
    norm_chunk, report = normalize_chunk(chunk, expected_pages=[3])
    assert len(norm_chunk.headings) == 3
    assert report.dropped_invalid == 0


def test_document_specific_project_title_strings_are_not_generically_matched():
    """KIRORI/CONSTRUCTION OF .../SUPPLY OF EQUIPMENT FOR were one
    fixture's own project-title fragments hardcoded into this supposedly
    'generic' filter, not real generic noise — after removal, a different
    document's equally boilerplate-shaped project title line must survive,
    proving the filter no longer overfits to the one document it was
    tuned against."""
    chunk = AIStructureChunk(
        headings=[
            AIHeading(heading_text="CONSTRUCTION OF 220 KV GRID SUB-STATION AT RAIPUR", start_page=1, level=1),
            AIHeading(heading_text="SUPPLY OF EQUIPMENT FOR RURAL ELECTRIFICATION SCHEME", start_page=1, level=1),
        ]
    )
    norm_chunk, report = normalize_chunk(chunk, expected_pages=[1])
    assert len(norm_chunk.headings) == 2
    assert report.dropped_invalid == 0


def test_short_definition_clause_with_multiseg_decimal_is_preserved():
    """Boundary-pinning companion to the overlong-narrative-prose-paragraph
    drop test (tests/test_ai_indexing_parsing.py): a SHORT, correctly
    short-labeled definitions-clause heading with a multi-segment decimal
    number — e.g. '1.1 "Addendum"' from the tender-doc gold fixture's
    Definitions section — must survive. Confirms the 15-word+multi-segment-
    decimal filter only disqualifies long verb-bearing prose, not
    genuinely short numbered labels, matching the system prompt's own
    "noun-phrase test" instruction."""
    chunk = AIStructureChunk(
        headings=[
            AIHeading(heading_text='1.1. "Addendum"', start_page=11, level=3),
            AIHeading(heading_text="4.2 Verbal agreement or conversation", start_page=12, level=3),
        ]
    )
    norm_chunk, report = normalize_chunk(chunk, expected_pages=[11, 12])
    assert len(norm_chunk.headings) == 2
    assert report.dropped_invalid == 0


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
        "BANK GUARANTEE FOR CONTRACT PERFORMANCE",
    ]
    chunk = AIStructureChunk(
        headings=[AIHeading(heading_text=text, start_page=1, level=1) for text in legitimate]
    )
    norm_chunk, report = normalize_chunk(chunk, expected_pages=[1])
    assert len(norm_chunk.headings) == len(legitimate)
    assert report.dropped_invalid == 0
    assert report.dropped_empty == 0


def test_distribution_list_colon_recovery_suppressed():
    """Verify _recover_colon_sublists_in_chunk ignores correspondence distribution intros and recipient items."""
    import ai_reference_index
    from ai_indexing.schema import AIHeading

    h = AIHeading(heading_text="DATED 20.05.2008", start_page=91, level=1)
    page_blocks = {
        91: [
            "DATED 20.05.2008",
            "Copy of the above is forwarded to the following for information and necessary action please:",
            "1. Sr. PS to Chairman, HVPNL, Panchkula.",
            "2. Sr. PS to Director/Projects, HVPNL, Panchkula.",
            "10. The Superintending Engineer/MM-II, HVPNL, Panchkula.",
        ]
    }
    recovered = ai_reference_index._recover_colon_sublists_in_chunk([h], page_blocks)
    # Must NOT synthesize the distribution list as headings
    assert len(recovered) == 1
    assert recovered[0].heading_text == "DATED 20.05.2008"


def test_page_1_cover_letterhead_contact_noise_suppressed():
    """Verify _COVER_LETTERHEAD_NOISE_RE matches cover-page contact metadata."""
    from ai_reference_index import _COVER_LETTERHEAD_NOISE_RE

    assert _COVER_LETTERHEAD_NOISE_RE.search("Email: - cepurez@yahoo.in")
    assert _COVER_LETTERHEAD_NOISE_RE.search("Phone No.: 0761-2666040, 2702473, 2702442")
    assert _COVER_LETTERHEAD_NOISE_RE.search("Block No.8th 3rd Floor Shakti Bhawan")
    assert _COVER_LETTERHEAD_NOISE_RE.search("THE CHIEF GENERAL MANAGER (Store & Purchase)")
    assert _COVER_LETTERHEAD_NOISE_RE.search("M. P. Poorv Kshetra Vidyut Vitaran Co. Ltd,")

    # Legitimate headings must not match
    assert not _COVER_LETTERHEAD_NOISE_RE.search("INDEX")
    assert not _COVER_LETTERHEAD_NOISE_RE.search("Bid Document")
    assert not _COVER_LETTERHEAD_NOISE_RE.search("SECTION 1: GENERAL CONDITIONS")


def test_structure_system_prompt_includes_checklist_table_exception():
    """Verify prompt explicitly instructs extraction of bidder checklist and qualification criteria tables."""
    from ai_indexing.prompt import STRUCTURE_SYSTEM_PROMPT

    assert "bidder eligibility / checklist criteria tables" in STRUCTURE_SYSTEM_PROMPT
    assert "CORRESPONDENCE ENDORSEMENT / DISTRIBUTION RECIPIENT LISTS" in STRUCTURE_SYSTEM_PROMPT
    assert "TENDER COVER-PAGE LETTERHEAD AND CONTACT BLOCKS" in STRUCTURE_SYSTEM_PROMPT

