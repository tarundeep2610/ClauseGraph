"""Regression tests for ai_indexing.parsing / schema — the output-parsing
pipeline. Covers the real bug fixed over tender_ai's own repair function
(brackets inside a string value), the object-vs-bare-array envelope
inconsistency fixed during review, and normalize_chunk's reported cleanup.
"""

import json

import pytest

from ai_indexing.errors import AIInvalidOutputError
from ai_indexing.parsing import (
    extract_json_object,
    parse_structure_response,
    repair_truncated_json,
    strip_code_fences,
)
from ai_indexing.schema import AIStructureChunk, normalize_chunk


def test_strip_code_fences():
    assert strip_code_fences("```json\n{\"a\": 1}\n```") == '{"a": 1}'
    assert strip_code_fences('{"a": 1}') == '{"a": 1}'


def test_extract_json_object_from_leading_chatter():
    text = 'Here is the JSON:\n{"headings": []}'
    assert extract_json_object(text) == '{"headings": []}'


def test_extract_json_object_raises_when_no_bracket_at_all():
    with pytest.raises(AIInvalidOutputError):
        extract_json_object("no json here at all")


def test_repair_truncated_json_ignores_brackets_inside_strings():
    """The real bug fixed over tender_ai's own _repair_json: a brace/bracket
    INSIDE a string value (entirely plausible in verbatim heading text like
    "3.4(a) {as amended}") must not push a phantom stack frame."""
    truncated = '{"headings": [{"heading_text": "Clause 4 {as amended}", "start_page": 1'
    repaired = repair_truncated_json(truncated)
    import json
    data = json.loads(repaired)
    assert data["headings"][0]["heading_text"] == "Clause 4 {as amended}"
    assert data["headings"][0]["start_page"] == 1


def test_repair_truncated_json_closes_dangling_string_and_trailing_comma():
    truncated = '{"headings": [{"heading_text": "Annexure B", "start_page": 1,'
    repaired = repair_truncated_json(truncated)
    import json
    data = json.loads(repaired)
    assert data["headings"][0]["heading_text"] == "Annexure B"


def test_object_wrapped_response_parses():
    raw = '{"headings": [{"heading_text": "3.4 QUALITY PLANS", "start_page": 21, "level": 2}], "notes": ""}'
    chunk, report = parse_structure_response(raw, expected_pages=[20, 21, 22])
    assert len(chunk.headings) == 1
    assert chunk.headings[0].heading_text == "3.4 QUALITY PLANS"


def test_bare_array_response_still_parses():
    """Not every provider will follow the object-wrapper contract perfectly
    — a bare array must still work, not just the documented shape."""
    raw = '[{"heading_text": "ANNEXURE A-1", "start_page": 1, "level": 1}]'
    chunk, report = parse_structure_response(raw, expected_pages=[1])
    assert chunk.headings[0].heading_text == "ANNEXURE A-1"


def test_alternate_envelope_keys_are_recognized():
    raw = '{"items": [{"heading_text": "X", "start_page": 1, "level": 1}]}'
    chunk, report = parse_structure_response(raw, expected_pages=[1])
    assert len(chunk.headings) == 1


def test_markdown_wrapped_response_with_fences_and_chatter():
    raw = (
        "Sure, here's the structure:\n"
        "```json\n"
        '{"headings": [{"heading_text": "### 3.4 QUALITY PLANS", "start_page": 5, "level": 1}]}\n'
        "```"
    )
    chunk, report = parse_structure_response(raw, expected_pages=[5])
    # Sanitization happens in the Pydantic validator — markdown stripped.
    assert chunk.headings[0].heading_text == "3.4 QUALITY PLANS"


def test_normalize_chunk_drops_offpage_and_reports_it():
    chunk = AIStructureChunk(headings=[
        {"heading_text": "In range", "start_page": 5, "level": 1},
        {"heading_text": "Out of range", "start_page": 99, "level": 1},
    ])
    cleaned, report = normalize_chunk(chunk, expected_pages=[4, 5, 6])
    assert len(cleaned.headings) == 1
    assert report.dropped_offpage == 1


def test_normalize_chunk_drops_exact_duplicates():
    chunk = AIStructureChunk(headings=[
        {"heading_text": "3.0 CONDITIONS", "start_page": 4, "level": 1},
        {"heading_text": "3.0 conditions", "start_page": 4, "level": 1},  # casefold-equal dup
    ])
    cleaned, report = normalize_chunk(chunk, expected_pages=[4])
    assert len(cleaned.headings) == 1
    assert report.dropped_duplicate == 1


def test_normalize_chunk_sorts_by_page_not_ai_response_order():
    chunk = AIStructureChunk(headings=[
        {"heading_text": "Later page heading", "start_page": 10, "level": 1},
        {"heading_text": "Earlier page heading", "start_page": 5, "level": 1},
    ])
    cleaned, report = normalize_chunk(chunk, expected_pages=[5, 10])
    assert [h.start_page for h in cleaned.headings] == [5, 10]


def test_level_is_clamped_not_rejected():
    chunk = AIStructureChunk(headings=[{"heading_text": "X", "start_page": 1, "level": 999}])
    assert chunk.headings[0].level == 16
    chunk2 = AIStructureChunk(headings=[{"heading_text": "X", "start_page": 1, "level": -5}])
    assert chunk2.headings[0].level == 1


def test_one_overlong_heading_does_not_discard_the_rest_of_the_chunk():
    """Regression, found via a real end-to-end run: the model
    mis-classified a body paragraph as a heading, producing a heading_text
    far past any sane length. Pydantic's max_length used to reject the
    WHOLE headings array atomically on that one item, discarding 9 other
    genuinely good headings in the same response and falling the entire
    chunk back to rules. Must now just truncate that one item instead.

    Uses a single-segment numbered paragraph (pleading-point shaped, not a
    multi-segment decimal contract clause) so normalize_chunk's narrative-
    prose filter (added later, see test_overlong_narrative_prose_paragraph_
    is_dropped_not_truncated below) doesn't also fire here — this test is
    specifically about the truncate-vs-discard-the-whole-array bug, not
    about which shapes count as narrative prose."""
    overlong = "16. " + ("It is further argued that the delay was attributable to the respondent. " * 8)
    raw = json.dumps({"headings": [
        {"heading_text": "ANNEXURE A-1", "start_page": 1, "level": 1},
        {"heading_text": overlong, "start_page": 5, "level": 2},
        {"heading_text": "4.0 SETTLEMENT OF DISPUTES", "start_page": 6, "level": 1},
    ]})
    chunk, report = parse_structure_response(raw, expected_pages=[1, 5, 6])
    assert len(chunk.headings) == 3
    assert chunk.headings[1].heading_text == overlong[:300]
    assert report.dropped_invalid == 0


def test_overlong_narrative_prose_paragraph_is_dropped_not_truncated():
    """Fix #4 ('Narrative Prose vs. Outline Paragraph Discrimination',
    AI_INDEXING_ACCURACY_INVESTIGATION.md §11.2): a multi-segment decimal
    clause (here "3.4.1 ...") that reads as a verb-bearing body paragraph
    (>15 words) is dropped entirely by normalize_chunk, not truncated and
    kept — unlike the single-segment pleading-point case above, which is
    legitimately a heading even when long. The other two genuine headings
    in the same response must still survive untouched."""
    overlong = "3.4.1 " + ("The contractor is responsible for ensuring compliance. " * 10)
    raw = json.dumps({"headings": [
        {"heading_text": "ANNEXURE A-1", "start_page": 1, "level": 1},
        {"heading_text": overlong, "start_page": 5, "level": 2},
        {"heading_text": "4.0 SETTLEMENT OF DISPUTES", "start_page": 6, "level": 1},
    ]})
    chunk, _report = parse_structure_response(raw, expected_pages=[1, 5, 6])
    assert [h.heading_text for h in chunk.headings] == ["ANNEXURE A-1", "4.0 SETTLEMENT OF DISPUTES"]


def test_a_genuinely_malformed_item_is_dropped_not_fatal():
    raw = json.dumps({"headings": [
        {"heading_text": "ANNEXURE A-1", "start_page": 1, "level": 1},
        "not a dict at all",
        {"heading_text": "4.0 SETTLEMENT OF DISPUTES", "start_page": 6, "level": 1},
    ]})
    chunk, report = parse_structure_response(raw, expected_pages=[1, 6])
    assert [h.heading_text for h in chunk.headings] == ["ANNEXURE A-1", "4.0 SETTLEMENT OF DISPUTES"]
    # A non-dict item still constructs a valid (blank, start_page=0)
    # AIHeading rather than raising — normalize_chunk then drops it for
    # being off-page (0 isn't in expected_pages), same as any other
    # nonsense page number would be.
    assert report.dropped_invalid == 0
    assert report.dropped_offpage == 1


def test_unresolvable_json_raises_invalid_output_not_a_crash():
    with pytest.raises(AIInvalidOutputError):
        parse_structure_response("This is not JSON at all, sorry.", expected_pages=[1])
