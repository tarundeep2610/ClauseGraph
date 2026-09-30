"""Regression tests for reference_index.build_layout_annotated_text — the
AI-assisted indexer's PROMPT INPUT ONLY annotation (grounding/persistence
always use the plain clean text; see ai_reference_index.reindex_job_with_ai).
Falls back to the plain clean text, silently, whenever the raw grounded
capture is absent or its parsed layout-block count doesn't line up 1:1 with
the clean text's own parsed blocks — the same layout_aligned gate
build_targets_for_page's own confidence scoring already relies on."""

from reference_index import build_layout_annotated_text


def test_annotates_each_aligned_block_with_its_real_layout_type():
    clean_text = "AmexureAl\n\nCONTRACT AGREEMENT BETWEEN\n\nsome body text"
    raw_text = (
        "<|det|>header [612, 29, 905, 61]<|/det|>AmexureAl"
        "<|det|>text [383, 223, 792, 243]<|/det|>CONTRACT AGREEMENT BETWEEN"
        "<|det|>text [400, 300, 800, 320]<|/det|>some body text"
    )
    annotated = build_layout_annotated_text(clean_text, raw_text)
    assert "[OCR-LAYOUT: HEADER]\nAmexureAl" in annotated
    assert "[OCR-LAYOUT: TEXT]\nCONTRACT AGREEMENT BETWEEN" in annotated
    assert "[OCR-LAYOUT: TEXT]\nsome body text" in annotated


def test_falls_back_to_clean_text_when_raw_text_is_absent():
    clean_text = "3.3 TIME SCHEDULE\n\nsome body text"
    assert build_layout_annotated_text(clean_text, None) == clean_text
    assert build_layout_annotated_text(clean_text, "") == clean_text


def test_falls_back_to_clean_text_when_block_counts_do_not_line_up():
    """Never guess which block a layout type belongs to — a page OCR'd
    with a different block-splitting than what raw_text's tags describe
    (or any other misalignment) must be a silent no-op, not a
    best-effort/partial annotation."""
    clean_text = "AmexureAl\n\nCONTRACT AGREEMENT BETWEEN\n\nsome body text"
    raw_text = "<|det|>header [612, 29, 905, 61]<|/det|>AmexureAl"  # only 1 tag, clean_text has 3 blocks
    assert build_layout_annotated_text(clean_text, raw_text) == clean_text


def test_never_leaks_the_raw_det_tag_syntax_itself_into_the_output():
    clean_text = "AmexureAl"
    raw_text = "<|det|>header [612, 29, 905, 61]<|/det|>AmexureAl"
    annotated = build_layout_annotated_text(clean_text, raw_text)
    assert "<|det|>" not in annotated
    assert "<|/det|>" not in annotated
