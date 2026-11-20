import pytest
from unittest.mock import AsyncMock, MagicMock
from ai_indexing.prompt import (
    PageInput,
    ChunkPlan,
    classify_page_density,
    plan_adaptive_chunks,
    plan_chunks,
    build_structure_prompt,
    _render_macro_context,
    MACRO_SYSTEM_PROMPT,
)
from ai_indexing.client import ChunkOutcome, ChunkStructureResult, StructureIndexer
from ai_indexing.schema import AIHeading
from ai_reference_index import (
    _is_table_header_or_form_field,
    _is_substantive_heading_companion,
    _is_bare_heading_marker,
    _get_active_macro_context,
    _merge_macro_and_micro_events,
    _extract_macro_skeleton,
    _is_index_or_toc_page,
)


def test_t1_adaptive_chunker_dense_table_split():
    """T1: Pages 13-19 with dense table rows evaluate to DENSE_TABLE and
    split into small slices (<= 2 pages per chunk)."""
    table_page_text = "<table>\n" + "\n".join(f"<tr><td>{i}.1</td><td>Clause text {i}</td></tr>" for i in range(8)) + "</table>"
    pages = [PageInput(page_num=p, text=table_page_text) for p in range(13, 20)]

    for p in pages:
        density = classify_page_density(p.text)
        assert density == "DENSE_TABLE", f"Page {p.page_num} should be classified as DENSE_TABLE, got {density}"

    chunks = plan_adaptive_chunks(pages, dense_page_limit=2, dense_row_limit=20)
    assert len(chunks) == 4
    assert chunks[0].page_nums == [13, 14]
    assert chunks[1].page_nums == [15, 16]
    assert chunks[2].page_nums == [17, 18]
    assert chunks[3].page_nums == [19]

    # When a single page exceeds dense_row_limit, it isolates into 1 page per chunk:
    heavy_page_text = "<table>\n" + "\n".join(f"<tr><td>{i}.1</td><td>Clause text {i}</td></tr>" for i in range(21)) + "</table>"
    heavy_pages = [PageInput(page_num=12, text=heavy_page_text), PageInput(page_num=13, text=heavy_page_text)]
    heavy_chunks = plan_adaptive_chunks(heavy_pages, dense_page_limit=2, dense_row_limit=20)
    assert len(heavy_chunks) == 2
    assert heavy_chunks[0].page_nums == [12]
    assert heavy_chunks[1].page_nums == [13]



def test_t2_adaptive_chunker_narrative_invariant():
    """T2: 17 narrative pages with 0 table rows evaluate to NARRATIVE and
    remain in 1 single chunk (zero regression for narrative legal docs)."""
    narrative_text = "This is ordinary legal narrative without any table cells.\nThe learned counsel for the petitioner argued at length."
    pages = [PageInput(page_num=p, text=narrative_text) for p in range(1, 18)]

    for p in pages:
        density = classify_page_density(p.text)
        assert density == "NARRATIVE", f"Page {p.page_num} should be NARRATIVE, got {density}"

    chunks = plan_adaptive_chunks(pages, max_pages=20)
    assert len(chunks) == 1
    assert chunks[0].page_nums == list(range(1, 18))


def test_ground_roman_numeral_marker_classifies_as_moderate_not_narrative():
    """Real regression repro: a 100-page arbitration appeal's "Grounds of
    Appeal" section has only 1-2 roman-numeral ground markers per page
    ("(VII) That the Ld. Arbitral Tribunal...") — too few to trip
    _CLAUSE_MARKER_RE's own count thresholds, but each one is a full
    multi-sentence legal paragraph. Left classified NARRATIVE, such pages
    pooled into one oversized chunk once AI_INDEX_CHUNK_PAGES was raised for
    narrative content, overwhelming a single AI call's output budget and
    losing ~20 ground:N targets. A single marker must be enough to bump the
    page to MODERATE (the tighter 5-page chunk cap), regardless of count."""
    ground_page_text = (
        "on the part of the appellant in supply of the final GELO to respondent no.1. "
        "While allowing such extension in favour of respondent no.1 the Ld. Arbitral "
        "Tribunal grossly overlooked the material facts on the record.\n\n"
        "(VII) That the Ld. Arbitral Tribunal, while holding appellant responsible for "
        "delay of 108 days in supplying GELO, did not take into account the nature and "
        "importance of revisions made to GELO."
    )
    assert classify_page_density(ground_page_text) == "MODERATE"

    narrative_text = "This is ordinary legal narrative without any table cells.\nThe learned counsel for the petitioner argued at length."
    assert classify_page_density(narrative_text) == "NARRATIVE"


def test_t2_boundary_hysteresis_transition_flush():
    """Refinement 1: Transition between NARRATIVE and DENSE_TABLE flushes chunk boundary."""
    narrative_text = "Legal narrative background..."
    dense_text = "<table>\n" + "\n".join(f"<tr><td>{i}</td><td>Row</td></tr>" for i in range(10)) + "</table>"

    pages = [
        PageInput(page_num=1, text=narrative_text),
        PageInput(page_num=2, text=narrative_text),
        PageInput(page_num=3, text=dense_text),
        PageInput(page_num=4, text=dense_text),
        PageInput(page_num=5, text=narrative_text),
    ]

    chunks = plan_adaptive_chunks(pages, max_pages=10, dense_page_limit=2)
    # Chunk 0: narrative pages [1, 2]
    # Chunk 1: dense table pages [3, 4]
    # Chunk 2: narrative page [5]
    assert len(chunks) == 3
    assert chunks[0].page_nums == [1, 2]
    assert chunks[1].page_nums == [3, 4]
    assert chunks[2].page_nums == [5]


def test_t3_macro_skeleton_extraction_and_bounds():
    """T3: Macro skeleton extraction bounds container end_page by next container's start_page - 1."""
    mock_indexer = MagicMock()
    mock_indexer.extract_structure_for_chunk = AsyncMock()

    # Pass 1 mock response
    mock_indexer.extract_structure_for_chunk.return_value = ChunkStructureResult(
        outcome=ChunkOutcome.OK,
        page_nums=[1, 50],
        headings=[
            AIHeading(heading_text="SECTION I: INVITATION FOR BIDS", level=1, start_page=1),
            AIHeading(heading_text="SECTION II: INSTRUCTIONS TO BIDDERS", level=1, start_page=7),
            AIHeading(heading_text="SECTION III: BID DATA SHEET", level=1, start_page=52),
        ],
    )

    def _text_for_p(p):
        if p == 1:
            return "SECTION I: INVITATION FOR BIDS\n\nContent..."
        if p == 7:
            return "SECTION II: INSTRUCTIONS TO BIDDERS\n\nContent..."
        if p == 52:
            return "SECTION III: BID DATA SHEET\n\nContent..."
        return "Body paragraph..."

    pages = [PageInput(page_num=p, text=_text_for_p(p)) for p in range(1, 60)]

    import asyncio
    skeleton = asyncio.run(_extract_macro_skeleton(pages, mock_indexer, coarse_chunk_pages=60))

    assert len(skeleton) == 3
    assert skeleton[0]["heading_text"] == "SECTION I: INVITATION FOR BIDS"
    assert skeleton[0]["start_page"] == 1
    assert skeleton[0]["end_page"] == 6

    assert skeleton[1]["heading_text"] == "SECTION II: INSTRUCTIONS TO BIDDERS"
    assert skeleton[1]["start_page"] == 7
    assert skeleton[1]["end_page"] == 51

    assert skeleton[2]["heading_text"] == "SECTION III: BID DATA SHEET"
    assert skeleton[2]["start_page"] == 52
    assert skeleton[2]["end_page"] == 59


def test_t4_context_seeding_in_micro_prompt():
    """T4: Active macro container is seeded into micro prompt with Refinement 4 guideline."""
    skeleton = [
        {"heading_text": "SECTION II: INSTRUCTIONS TO BIDDERS", "level": 1, "start_page": 7, "end_page": 51},
    ]

    macro_ctx = _get_active_macro_context(skeleton, 14)
    assert macro_ctx is not None
    assert macro_ctx["root_container"] == "SECTION II: INSTRUCTIONS TO BIDDERS"

    chunk = ChunkPlan(pages=[PageInput(page_num=14, text="Sample table content")])
    sys_prompt, user_prompt = build_structure_prompt(chunk, macro_context=macro_ctx)

    assert "# ACTIVE DOCUMENT DIVISION (FROM MACRO SKELETON)" in user_prompt
    assert 'Root Division: "SECTION II: INSTRUCTIONS TO BIDDERS"' in user_prompt
    # Refinement 4 check
    assert "Active division context is a default guideline. If this page range explicitly begins a NEW major section" in user_prompt


def test_t5_table_column_header_blacklist():
    """T5: Standard table column headers are deterministically rejected."""
    headers = [
        "S.No.", "Sr. No.", "SL. NO.", "Clause", "Clause No.", "Cl. No.",
        "Description", "Particulars", "Specification", "Compliance (Yes/No)",
        "Remarks", "Page No."
    ]
    for h in headers:
        assert _is_table_header_or_form_field(h) is True, f"'{h}' should be recognized as a table header"
        assert _is_substantive_heading_companion(h) is False, f"'{h}' should NOT be substantive companion"


def test_t6_signature_block_and_form_field_suppression():
    """T6: Form fields, dotted fill-in lines, and signature placeholders are rejected."""
    form_lines = [
        "Authorized Signatory:",
        "Signature of Bidder: ............",
        "Name of Firm: ____________",
        "Seal of Company:",
        "Date: ........................",
        "Place: ________________________",
    ]
    for line in form_lines:
        assert _is_table_header_or_form_field(line) is True, f"'{line}' should be recognized as form field"
        assert _is_substantive_heading_companion(line) is False, f"'{line}' should NOT be substantive companion"


def test_t7_3_segment_decimal_and_table_row_merge():
    """T7: 3-segment decimal like '24.2.1.' is recognized as bare marker and substantive companion accepted."""
    is_bare, is_enum = _is_bare_heading_marker("24.2.1.")
    assert is_bare is True
    assert is_enum is False

    companion = "Prior to detailed evaluation, the Employer will determine..."
    assert _is_substantive_heading_companion(companion) is True


def test_refinement_3_macro_micro_event_merging():
    """Refinement 3: When Pass 2 omits the section title, Pass 1's container is
    placed FIRST in the page's event list."""
    macro_skeleton = [
        {"heading_text": "SECTION II: INSTRUCTIONS TO BIDDERS", "level": 1, "start_page": 7, "end_page": 51},
    ]

    # Pass 2 on page 7 only extracted clauses 6.1 and 6.2 (omitted SECTION II)
    events_by_page = {
        7: [
            {"heading_text": "6.1 Scope of Bid", "depth": 1, "block_idx": 5},
            {"heading_text": "6.2 Eligible Bidders", "depth": 1, "block_idx": 10},
        ]
    }
    page_blocks = {
        7: [
            "SECTION II: INSTRUCTIONS TO BIDDERS",  # block 0
            "Paragraph...",                         # block 1
            "More text...",                         # block 2
            "Table intro...",                       # block 3
            "Table...",                             # block 4
            "6.1 Scope of Bid",                     # block 5
            "...",
            "...",
            "...",
            "...",
            "6.2 Eligible Bidders",                 # block 10
        ]
    }

    merged = _merge_macro_and_micro_events(macro_skeleton, events_by_page, page_blocks, detection_model="test-model")

    p7_events = merged[7]
    assert len(p7_events) == 3
    # SECTION II placed first with depth 0 and block_idx 0!
    assert p7_events[0]["heading_text"] == "SECTION II: INSTRUCTIONS TO BIDDERS"
    assert p7_events[0]["depth"] == 0
    assert p7_events[0]["block_idx"] == 0

    assert p7_events[1]["heading_text"] == "6.1 Scope of Bid"
    assert p7_events[2]["heading_text"] == "6.2 Eligible Bidders"


def test_toc_page_suppression():
    """TOC / Index page blocks suppress table row headings."""
    toc_blocks = [
        "Tender Specification...",
        "OPERATIONS, MAINTENANCE & MODERNIZATION...",
        "INDEX",
        "S. No.",
        "PARTICULARS",
        "Section VII: Annexures",
        "Annexure-I: Performa for Bid Form",
    ]
    assert _is_index_or_toc_page(toc_blocks) is True

    normal_blocks = [
        "Section VII: Annexures",
        "Annexure-I: Performa for Bid Form",
        "Content...",
    ]
    assert _is_index_or_toc_page(normal_blocks) is False


def test_macro_skeleton_rejects_table_cells():
    """Verify that Pass 1 macro skeleton extractor rejects candidates grounding to table_cells."""
    mock_indexer = MagicMock()
    mock_indexer.extract_structure_for_chunk = AsyncMock()

    mock_indexer.extract_structure_for_chunk.return_value = ChunkStructureResult(
        outcome=ChunkOutcome.OK,
        page_nums=[78],
        headings=[
            AIHeading(heading_text="SECTION VII: ANNEXURES", level=1, start_page=78),
            AIHeading(heading_text="Wave Traps (Pedestal type)", level=1, start_page=78),
        ],
    )

    page_text = "SECTION VII: ANNEXURES\n\n<table><tr><td>1</td><td>Wave Traps (Pedestal type)</td></tr></table>"
    pages = [PageInput(page_num=78, text=page_text)]

    import asyncio
    skeleton = asyncio.run(_extract_macro_skeleton(pages, mock_indexer, coarse_chunk_pages=1))

    # Only the genuine non-table heading survives; table cell is rejected!
    assert len(skeleton) == 1
    assert skeleton[0]["heading_text"] == "SECTION VII: ANNEXURES"


def test_macro_context_price_schedule_suppression():
    """Verify _render_macro_context includes BOQ/price-schedule suppression and substantive term carve-out."""
    # Case 1: Dense listing division
    boq_ctx = {
        "root_container": "ANNEXURE C-I: PRICE SCHEDULE FOR SUPPLY OF SUB-STATION EQUIPMENT",
        "parent_clause": None,
    }
    rendered_boq = _render_macro_context(boq_ctx)
    assert "PRICE SCHEDULE / BOQ SUPPRESSION" in rendered_boq
    assert "4.10 Isolation valves" in rendered_boq
    assert "420 KV SF-6 Circuit Breaker" in rendered_boq
    # Positive shape carve-out for substantive contractual terms:
    assert "CARVE-OUT (SUBSTANTIVE TERMS)" in rendered_boq
    assert "Payment Terms" in rendered_boq
    assert "Stage Penalty" in rendered_boq
    assert "Taxes & Duties" in rendered_boq
    assert "Active division context is a default guideline" in rendered_boq

    # Case 2: Standard narrative division retains standard guidance
    standard_ctx = {
        "root_container": "SECTION II: INSTRUCTIONS TO BIDDERS",
        "parent_clause": "6. Scope of Bid",
    }
    rendered_standard = _render_macro_context(standard_ctx)
    assert 'Root Division: "SECTION II: INSTRUCTIONS TO BIDDERS"' in rendered_standard
    assert 'Active Section: "6. Scope of Bid"' in rendered_standard
    assert "Report all clauses and sub-clauses belonging to this division." in rendered_standard
    assert "Active division context is a default guideline" in rendered_standard

    # Case 3: Empty / None macro context
    assert _render_macro_context(None) == ""
    assert _render_macro_context({}) == ""


def test_action_item_macro_context_untouched():
    """Verify _render_action_item_macro_context remains untouched and functions correctly."""
    from ai_indexing.prompt import _render_action_item_macro_context
    ctx = {"root_container": "Annexure-XIII – ERP Modules implemented in MPPKVVCL"}
    rendered = _render_action_item_macro_context(ctx)
    assert "catalogue of already-implemented/existing functionality" in rendered
    assert "AS-IS exclusion rule" in rendered


