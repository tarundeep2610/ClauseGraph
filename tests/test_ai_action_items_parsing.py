"""Tests for ai_indexing.parse_action_item_response and the AIActionItem/
normalize_action_item_chunk schema — mirroring test_ai_indexing_parsing.py's
coverage shape for the structure pass, adapted to action items' own fields
(item_type/date_iso/anchor_event/cadence) and failure modes."""

import json

import ai_indexing


def test_valid_response_parses_into_items():
    raw = json.dumps({
        "items": [
            {
                "item_text": "EMD submission deadline", "item_type": "deadline",
                "date_text": "15.03.2025", "date_iso": "2025-03-15", "page": 12,
                "evidence_quote": "EMD shall be submitted on or before 15.03.2025",
                "responsible_party": "Bidder", "confidence": "high",
            },
        ],
        "notes": "",
    })
    chunk, report = ai_indexing.parse_action_item_response(raw, expected_pages=[12])
    assert len(chunk.items) == 1
    item = chunk.items[0]
    assert item.item_type == "deadline"
    assert item.date_iso == "2025-03-15"
    assert report.dropped_invalid == 0


def test_one_malformed_item_does_not_discard_the_whole_chunk():
    # Same all-or-nothing-failure protection as
    # _validate_headings_individually — a single non-dict item must not
    # take down every other good item in the same response.
    raw = json.dumps({
        "items": [
            {"item_text": "Good item", "item_type": "obligation", "page": 1, "evidence_quote": "A real clause here"},
            "not a dict at all",
            123,
        ],
    })
    chunk, report = ai_indexing.parse_action_item_response(raw, expected_pages=[1])
    assert len(chunk.items) == 1
    assert chunk.items[0].item_text == "Good item"


def test_unrecognized_item_type_coerces_to_other_not_obligation():
    # Finding #6 from the gold-fixture reconnaissance: a bad/unknown
    # item_type must land on "other" (a deliberate, visible classification),
    # never silently relabeled as "obligation".
    raw = json.dumps({"items": [
        {"item_text": "x", "item_type": "relief_sought", "page": 1, "evidence_quote": "it is prayed that"},
    ]})
    chunk, _report = ai_indexing.parse_action_item_response(raw, expected_pages=[1])
    assert chunk.items[0].item_type == "other"


def test_malformed_date_iso_is_dropped_but_item_survives():
    raw = json.dumps({"items": [
        {"item_text": "x", "item_type": "deadline", "date_text": "within 30 days", "date_iso": "not-a-date",
         "page": 1, "evidence_quote": "within 30 days of signing"},
    ]})
    chunk, _report = ai_indexing.parse_action_item_response(raw, expected_pages=[1])
    item = chunk.items[0]
    assert item.date_iso is None
    assert item.date_text == "within 30 days"


def test_offpage_item_is_dropped_and_counted():
    raw = json.dumps({"items": [
        {"item_text": "x", "item_type": "obligation", "page": 999, "evidence_quote": "not shown here"},
    ]})
    chunk, report = ai_indexing.parse_action_item_response(raw, expected_pages=[1, 2])
    assert chunk.items == []
    assert report.dropped_offpage == 1


def test_item_with_no_evidence_quote_is_dropped():
    # No verbatim quote means grounding has nothing to work with later —
    # dropped at normalization time rather than wasting a grounding attempt.
    raw = json.dumps({"items": [
        {"item_text": "x", "item_type": "obligation", "page": 1, "evidence_quote": ""},
    ]})
    chunk, report = ai_indexing.parse_action_item_response(raw, expected_pages=[1])
    assert chunk.items == []
    assert report.dropped_empty == 1


def test_exact_duplicate_within_one_response_is_deduped():
    raw = json.dumps({"items": [
        {"item_text": "Same obligation", "item_type": "obligation", "page": 1, "evidence_quote": "Agency shall provide X"},
        {"item_text": "Same obligation", "item_type": "obligation", "page": 1, "evidence_quote": "Agency shall provide X"},
    ]})
    chunk, report = ai_indexing.parse_action_item_response(raw, expected_pages=[1])
    assert len(chunk.items) == 1
    assert report.dropped_duplicate == 1


def test_envelope_wrapped_in_items_key_and_bare_list_both_parse():
    raw_wrapped = json.dumps({"items": [{"item_text": "x", "item_type": "deadline", "page": 1, "evidence_quote": "abcdefgh"}]})
    chunk, _ = ai_indexing.parse_action_item_response(raw_wrapped, expected_pages=[1])
    assert len(chunk.items) == 1

    raw_bare = json.dumps([{"item_text": "x", "item_type": "deadline", "page": 1, "evidence_quote": "abcdefgh"}])
    chunk2, _ = ai_indexing.parse_action_item_response(raw_bare, expected_pages=[1])
    assert len(chunk2.items) == 1


def test_overflow_is_capped_and_counted(monkeypatch):
    import config
    monkeypatch.setattr(config, "AI_ACTION_ITEM_MAX_PER_CHUNK", 2)
    raw = json.dumps({"items": [
        {"item_text": f"item {i}", "item_type": "obligation", "page": 1, "evidence_quote": f"evidence number {i}"}
        for i in range(5)
    ]})
    chunk, report = ai_indexing.parse_action_item_response(raw, expected_pages=[1])
    assert len(chunk.items) == 2
    assert report.dropped_overflow == 3
