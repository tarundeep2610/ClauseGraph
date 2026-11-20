"""Tests for normalize_chunk's opt-in start_line_hint secondary ordering
(ai_index_order_by_line_hint, default off). Same-page heading order is
normally the model's own stable array order — the only signal available
when start_line_hint is absent/unreliable. When the flag is on AND every
heading on a page reports a non-null hint, that hint becomes the secondary
sort key instead. Deliberately all-or-nothing per page: an unhinted
heading is never interleaved against hinted siblings via a sentinel value,
which could actively make ordering worse."""

from ai_indexing.schema import AIHeading, AIStructureChunk, normalize_chunk


def _heading(text, page, line_hint=None):
    return AIHeading(heading_text=text, start_page=page, level=1, start_line_hint=line_hint)


def test_default_off_keeps_array_order_even_with_hints_present():
    chunk = AIStructureChunk(headings=[
        _heading("B", 1, line_hint=5),
        _heading("A", 1, line_hint=1),
    ])
    norm_chunk, _ = normalize_chunk(chunk, expected_pages=[1])
    assert [h.heading_text for h in norm_chunk.headings] == ["B", "A"]


def test_on_with_correct_increasing_hints_matches_array_order_already():
    chunk = AIStructureChunk(headings=[
        _heading("A", 1, line_hint=1),
        _heading("B", 1, line_hint=5),
    ])
    norm_chunk, _ = normalize_chunk(chunk, expected_pages=[1], order_by_line_hint=True)
    assert [h.heading_text for h in norm_chunk.headings] == ["A", "B"]


def test_on_with_out_of_order_hints_corrects_the_order():
    chunk = AIStructureChunk(headings=[
        _heading("B", 1, line_hint=5),
        _heading("A", 1, line_hint=1),
    ])
    norm_chunk, _ = normalize_chunk(chunk, expected_pages=[1], order_by_line_hint=True)
    assert [h.heading_text for h in norm_chunk.headings] == ["A", "B"]


def test_on_with_one_missing_hint_falls_back_to_array_order_for_that_page():
    chunk = AIStructureChunk(headings=[
        _heading("B", 1, line_hint=5),
        _heading("A", 1, line_hint=None),
    ])
    norm_chunk, _ = normalize_chunk(chunk, expected_pages=[1], order_by_line_hint=True)
    assert [h.heading_text for h in norm_chunk.headings] == ["B", "A"]


def test_different_pages_never_reordered_relative_to_each_other():
    chunk = AIStructureChunk(headings=[
        _heading("P2-first", 2, line_hint=1),
        _heading("P1-first", 1, line_hint=10),
    ])
    norm_chunk, _ = normalize_chunk(chunk, expected_pages=[1, 2], order_by_line_hint=True)
    assert [h.heading_text for h in norm_chunk.headings] == ["P1-first", "P2-first"]
