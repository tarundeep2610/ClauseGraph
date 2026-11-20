"""Regression tests for reference_index.build_layout_annotated_text — the
AI-assisted indexer's PROMPT INPUT ONLY annotation (grounding/persistence
always use the plain clean text; see ai_reference_index.reindex_job_with_ai).

Rebuilt to source entirely from layout_format.parse_blocks(raw_text) —
type, position and content all come from that one parse, so there is no
cross-referencing against clean_text's separately-parsed blocks and
therefore no 1:1 alignment to fail. The previous implementation required
parse_layout_blocks(raw_text)'s block count to match
parse_page_blocks(clean_text)'s mistune-parsed block count, falling back
silently to plain clean_text otherwise — measured elsewhere at ~38%
alignment success on real pages (empty-content `image` tags dropped on one
side, table-cell splitting mismatched on the other). That fallback case is
exactly what this file's tests now assert does NOT happen anymore."""

from reference_index import build_layout_annotated_text


def test_annotates_each_aligned_block_with_its_real_layout_type():
    raw_text = (
        "<|det|>header [612, 29, 905, 61]<|/det|>AmexureAl"
        "<|det|>text [383, 223, 792, 243]<|/det|>CONTRACT AGREEMENT BETWEEN"
        "<|det|>text [400, 300, 800, 320]<|/det|>some body text"
    )
    annotated = build_layout_annotated_text("ignored, unused source of truth now", raw_text)
    assert "[OCR-LAYOUT: HEADER]\nAmexureAl" in annotated
    assert "[OCR-LAYOUT: TEXT]\nCONTRACT AGREEMENT BETWEEN" in annotated
    assert "[OCR-LAYOUT: TEXT]\nsome body text" in annotated


def test_falls_back_to_clean_text_when_raw_text_is_absent():
    clean_text = "3.3 TIME SCHEDULE\n\nsome body text"
    assert build_layout_annotated_text(clean_text, None) == clean_text
    assert build_layout_annotated_text(clean_text, "") == clean_text


def test_falls_back_to_clean_text_when_raw_text_has_no_tags_at_all():
    clean_text = "plain GLM-engine text, no det tags ever present"
    assert build_layout_annotated_text(clean_text, "no det tags here either") == clean_text


def test_no_longer_falls_back_on_block_count_mismatch_with_clean_text():
    """The regression this fix targets: clean_text has 3 mistune-parsed
    blocks, raw_text has only 1 tag — the OLD implementation silently fell
    back to plain clean_text here. The new one doesn't need clean_text's
    block count at all, so it still annotates."""
    clean_text = "AmexureAl\n\nCONTRACT AGREEMENT BETWEEN\n\nsome body text"
    raw_text = "<|det|>header [612, 29, 905, 61]<|/det|>AmexureAl"
    annotated = build_layout_annotated_text(clean_text, raw_text)
    assert annotated == "[OCR-LAYOUT: HEADER]\nAmexureAl"
    assert annotated != clean_text


def test_whole_table_is_one_block_not_fragmented_per_cell():
    """The old mistune-based path split a table into one block per cell,
    each separately (mis-)tagged. The new path treats a whole table as one
    block, matching the model's own one-tag-per-table granularity."""
    raw_text = (
        "<|det|>table [220, 308, 891, 908]<|/det|>"
        "<table><tr><td>Sr No.</td><td>Particulars</td></tr>"
        "<tr><td>A.</td><td>Stamp Paper</td></tr></table>"
    )
    annotated = build_layout_annotated_text("x", raw_text)
    assert annotated.count("[OCR-LAYOUT:") == 1
    assert "[OCR-LAYOUT: TABLE]\n<table>" in annotated


def test_indented_block_gets_the_indented_marker():
    raw_text = (
        "<|det|>text [100, 100, 400, 120]<|/det|>Left margin text one\n"
        "<|det|>text [110, 140, 400, 160]<|/det|>Left margin text two\n"
        "<|det|>text [120, 180, 400, 200]<|/det|>Left margin text three\n"
        "<|det|>text [700, 220, 900, 240]<|/det|>Far right offset text"
    )
    annotated = build_layout_annotated_text("x", raw_text)
    assert "[OCR-LAYOUT: TEXT, INDENTED]\nFar right offset text" in annotated
    assert "[OCR-LAYOUT: TEXT]\nLeft margin text one" in annotated
    assert "INDENTED" not in annotated.split("Left margin text one")[0].split("[OCR-LAYOUT:")[-1]


def test_oversized_page_is_returned_whole_never_truncated():
    # Well over the old AI_INDEX_PAGE_CHARS_MAX cutoff (12000) — must still
    # come back in full. Truncating here would silently drop content the
    # model never gets to see; plan_chunks (ai_indexing/prompt.py) is
    # responsible for isolating a dense page into its own chunk instead.
    long_text = "word " * 3000  # ~15000 chars
    raw_text = f"<|det|>text [10,10,100,30]<|/det|>{long_text}"
    out = build_layout_annotated_text("x", raw_text)
    assert "truncated" not in out
    assert long_text.strip() in out
    assert len(out) >= len(long_text.strip())


def test_normal_sized_page_is_unaffected():
    raw_text = "<|det|>text [10,10,100,30]<|/det|>A perfectly normal, short page of text."
    out = build_layout_annotated_text("x", raw_text)
    assert "truncated" not in out


def test_never_leaks_the_raw_det_tag_syntax_itself_into_the_output():
    raw_text = "<|det|>header [612, 29, 905, 61]<|/det|>AmexureAl"
    annotated = build_layout_annotated_text("AmexureAl", raw_text)
    assert "<|det|>" not in annotated
    assert "<|/det|>" not in annotated
