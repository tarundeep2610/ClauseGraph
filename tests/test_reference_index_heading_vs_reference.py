"""Regression tests for heading-vs-reference classification — auditing
reference_targets_sample.md's 30 samples against a real 955-page document
found ~14/30 (~47%) were false positives: citations/running headers
misclassified as real headings. These fixtures use the *actual real text*
from that audit (confirmed real failure cases, not synthetic guesses)."""

from reference_index import (
    FAMILIES,
    _HIERARCHY_RANK,
    _FAMILY_ALIASES,
    _SUBDOC_NUMBERED_FAMILIES,
    build_targets_for_page,
    parse_layout_blocks,
)


def _keys(page_text, raw_text=None):
    candidates, _ = build_targets_for_page(page_text, seed_scope=None, has_next_page=False, raw_text=raw_text)
    return [c["key"] for c in candidates]


def _by_key(page_text, raw_text=None):
    candidates, _ = build_targets_for_page(page_text, seed_scope=None, has_next_page=False, raw_text=raw_text)
    return {c["key"]: c for c in candidates}


# ─── Signal #1: hard exclude — explicit backward-reference words ──────────

def test_backward_reference_word_excludes_the_match_entirely():
    page_text = (
        "Para 5.01 above that the claimant had raked up this issue during "
        "the arbitration proceedings and it was rejected by the Tribunal."
    )
    assert _keys(page_text) == []


def test_genuine_para_without_a_backward_reference_word_is_not_excluded():
    page_text = "Para 5.01 The contractor shall complete the work within 90 days."
    assert "para:5.01" in _keys(page_text)


# ─── Signals #2/#3: chained citation / range-shaped continuation (flag, not exclude) ──

def test_chained_multi_citation_is_flagged_low_confidence_not_excluded():
    page_text = (
        "Clause No. 34.0, Sec. GCC, Vol.I and Cl. No. 9 SCC Vol-I.\n\n"
        "(b) Bid Guarantee"
    )
    row = _by_key(page_text)["clause:34.0"]
    assert row["heading_confidence"] == "low"
    assert "chained_citation" in row["heading_confidence_reasons"]
    assert "other_doc_marker" in row["heading_confidence_reasons"]


def test_range_shaped_continuation_is_flagged_and_does_not_become_active_ancestor():
    """Regression test for the generalized stack-neutral fix: a running
    header shaped like "SECTION 8 TO 15..." must not pop the genuinely
    active Section 2 and orphan the real clause that follows it — the
    flagged match neither pops nor pushes, so Section 2 stays the ancestor
    for content that follows, matching what's actually still true of the
    document (the false match is noise *within* Section 2, not proof it
    ended)."""
    page_text = (
        "Section 2. Real Section\n\n"
        "SECTION 8 TO 15- SCHEDULES/ SCH-9A\n\n"
        "Clause 4.1 Some clause text.\n"
    )
    by_key = _by_key(page_text)
    section_8 = by_key["section:8"]
    assert section_8["heading_confidence"] == "low"
    assert "range_shaped_continuation" in section_8["heading_confidence_reasons"]
    # The false match itself is noise *inside* Section 2's still-active scope.
    assert section_8["scope_chain"] == [{"family": "section", "key": "section:2", "label": "Section 2"}]
    # And the real clause that follows still shows Section 2 as its ancestor —
    # not Section 8 (never pushed), and not orphaned to no ancestor either.
    clause = by_key["clause:4.1"]
    assert clause["scope_chain"] == [{"family": "section", "key": "section:2", "label": "Section 2"}]


# ─── Genuine headings are unaffected (regression check) ────────────────────

def test_genuine_article_heading_is_high_confidence_with_no_reasons():
    row = _by_key("Article 2. Payment Terms\n\nClause 1 Text.\n")["article:2"]
    assert row["heading_confidence"] == "high"
    assert row["heading_confidence_reasons"] == []


def test_genuine_annexure_cover_page_is_high_confidence_with_no_reasons():
    page_text = (
        "Annexure A-2\n\n"
        "CONTRACT AGREEMENT BETWEEN HARYANA VIDYUT PRASARAN NIGAM LIMITED "
        "AND M/S AREVA T&D INDIA LIMITED, NOIDA\n\n"
        "Clause 1 Definitions.\n"
    )
    row = _by_key(page_text)["annexure:a-2"]
    assert row["heading_confidence"] == "high"
    assert row["heading_confidence_reasons"] == []


# ─── Signal #4: same-page-repeat sub-document trigger (numbered, same family only) ──

def test_same_family_same_page_repeat_is_flagged_and_does_not_replace_the_first():
    """Regression test for the confirmed annexure:p-4-after-annexure:a-5 bug
    (§7): an index/TOC-shaped repeat of the same numbered sub-document
    family on one page must not fragment the first one's descendants —
    content after the flagged repeat still shows the *first* Annexure as
    its ancestor."""
    page_text = (
        "ANNEXURE A-5: ARBITRATION CASE FILE\n\n"
        "Clause 1 First real clause under the arbitration file.\n\n"
        "ANNEXURE P-4 Copy of Additional Award made by the Ld. Arbitral Tribunal\n\n"
        "Filed through counsel.\n\n"
        "Clause 2 Should still be under Annexure A-5, not P-4.\n"
    )
    by_key = _by_key(page_text)
    p4 = by_key["annexure:p-4"]
    assert p4["heading_confidence"] == "low"
    assert "same_page_repeat" in p4["heading_confidence_reasons"]
    assert p4["scope_chain"] == [{"family": "annexure", "key": "annexure:a-5", "label": "Annexure A-5"}]
    clause_2 = by_key["clause:2"]
    assert clause_2["scope_chain"] == [{"family": "annexure", "key": "annexure:a-5", "label": "Annexure A-5"}]


def test_different_numbered_families_on_one_page_are_siblings_not_a_repeat():
    """A real document can legitimately bind in an Annexure *and* a Schedule
    on one page — different families must never trigger same_page_repeat
    (this is the existing, already-tested legitimate sibling-swap case)."""
    page_text = (
        "ANNEXURE A-1: SERVICE AGREEMENT\n\n"
        "Clause 1 Under the annexure.\n\n"
        "SCHEDULE 2: PRICING\n\n"
        "Clause 2 Under the schedule.\n"
    )
    by_key = _by_key(page_text)
    assert "same_page_repeat" not in by_key["schedule:2"]["heading_confidence_reasons"]
    assert by_key["schedule:2"]["heading_confidence"] == "high"
    assert by_key["clause:2"]["scope_chain"] == [{"family": "schedule", "key": "schedule:2", "label": "Schedule 2"}]


def test_repeated_generic_trigger_is_unaffected_by_same_page_repeat_logic():
    """Two different real people's affidavits on one page are legitimate and
    distinguishable by their own label text — same_page_repeat must only
    apply to numbered sub-document families, never to generic (unnumbered)
    triggers like AFFIDAVIT/JUDGMENT (an existing, already-tested case)."""
    page_text = (
        "AFFIDAVIT OF JOHN DOE\n\n"
        "Clause 1 Under the first affidavit.\n\n"
        "AFFIDAVIT OF JANE DOE\n\n"
        "Clause 2 Under the second affidavit.\n"
    )
    by_key = _by_key(page_text)
    assert by_key["clause:1"]["scope_chain"][0]["label"] == "Affidavit"
    assert by_key["clause:2"]["scope_chain"][0]["label"] == "Affidavit (2)"


# ─── FAMILIES consolidation — pure-refactor equivalence check ──────────────

def test_families_table_derives_the_same_rank_and_alias_values_every_existing_test_relies_on():
    assert _HIERARCHY_RANK == {"annexure": 0, "schedule": 0, "appendix": 0, "subdoc": 0, "article": 1, "section": 2}
    assert _FAMILY_ALIASES == {
        "sec": "section", "section": "section",
        "art": "article", "article": "article",
        "para": "para", "paragraph": "para",
        "clause": "clause", "annexure": "annexure", "schedule": "schedule",
        "appendix": "appendix", "page": "page", "ground": "ground",
    }
    assert _SUBDOC_NUMBERED_FAMILIES == {"annexure", "schedule", "appendix"}
    assert set(FAMILIES.keys()) == {
        "annexure", "schedule", "appendix", "subdoc", "article", "section",
        "para", "clause", "ground", "page",
    }


# ─── Layout-tag signal (baidu/Unlimited-OCR raw <|det|> output) ────────────
# raw_text fixtures constructed to line up 1:1 with page_text's parsed
# blocks (one non-empty <|det|> tag per paragraph, in the same order) —
# matching the real captured sample from job 7f442300, page 427.

def test_header_tagged_block_adds_a_positive_layout_reason():
    page_text = "Annexure A-2\n\nClause 1 Definitions.\n"
    raw_text = (
        "<|det|>header [1,1,1,1]<|/det|>Annexure A-2\n"
        "<|det|>text [1,1,1,1]<|/det|>Clause 1 Definitions.\n"
    )
    row = _by_key(page_text, raw_text=raw_text)["annexure:a-2"]
    assert "layout_header" in row["heading_confidence_reasons"]


def test_text_tagged_block_adds_non_heading_layout_type_reason():
    page_text = "Section 8 TO 15- SCHEDULES\n\nClause 1 Real clause.\n"
    raw_text = (
        "<|det|>text [1,1,1,1]<|/det|>Section 8 TO 15- SCHEDULES\n"
        "<|det|>text [1,1,1,1]<|/det|>Clause 1 Real clause.\n"
    )
    row = _by_key(page_text, raw_text=raw_text)["section:8"]
    assert "non_heading_layout_type" in row["heading_confidence_reasons"]


def test_layout_reason_is_additive_never_erasing_a_text_heuristic_reason():
    """The critical regression test from the external-review discussion: a
    title-tagged block that ALSO trips a text heuristic must stay flagged —
    an unconditional layout override would silently defeat exactly the
    detection this plan exists to add (e.g. a repeating false-positive
    "SECTION 8 TO 15" that a vision model could plausibly tag as a title)."""
    page_text = "Clause No. 34.0, Sec. GCC, Vol.I and Cl. No. 9 SCC Vol-I.\n\n(b) Bid Guarantee\n"
    raw_text = (
        "<|det|>title [1,1,1,1]<|/det|>Clause No. 34.0, Sec. GCC, Vol.I and Cl. No. 9 SCC Vol-I.\n"
        "<|det|>text [1,1,1,1]<|/det|>(b) Bid Guarantee\n"
    )
    row = _by_key(page_text, raw_text=raw_text)["clause:34.0"]
    assert row["heading_confidence"] == "low"
    assert "chained_citation" in row["heading_confidence_reasons"]
    assert "layout_title" in row["heading_confidence_reasons"]


def test_block_count_mismatch_skips_the_layout_signal_entirely():
    """An embedded blank line (or any other cause of a raw/cleaned block
    count mismatch) must never mis-attribute a layout type to the wrong
    block — the signal is skipped gracefully, existing text-only heuristics
    still apply, no exception raised."""
    page_text = "Article 2. Payment Terms\n\nClause 1 Text.\n"
    raw_text = "<|det|>header [1,1,1,1]<|/det|>Only one tag for two page blocks\n"
    row = _by_key(page_text, raw_text=raw_text)["article:2"]
    assert row["heading_confidence_reasons"] == []
    assert row["heading_confidence"] == "high"


def test_raw_text_none_is_unaffected_by_the_layout_signal():
    row = _by_key("Article 2. Payment Terms\n\nClause 1 Text.\n", raw_text=None)["article:2"]
    assert row["heading_confidence_reasons"] == []


def test_parse_layout_blocks_drops_empty_content_and_returns_empty_list_for_no_input():
    raw = (
        "<|det|>header [1,1,1,1]<|/det|>Real heading\n"
        "<|det|>image [2,2,2,2]<|/det|>\n"
        "<|det|>text [3,3,3,3]<|/det|>Body text.\n"
    )
    blocks = parse_layout_blocks(raw)
    assert blocks == [
        {"type": "header", "text": "Real heading"},
        {"type": "text", "text": "Body text."},
    ]
    assert parse_layout_blocks(None) == []
    assert parse_layout_blocks("") == []
