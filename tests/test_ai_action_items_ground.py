"""Tests for ai_action_items.locate_action_item_block / _ground_action_item —
the anti-hallucination gate action items must pass before persistence.
Deliberately NOT reusing ai_reference_index.locate_heading_block (confirmed
during planning to fail on short evidence_quotes embedded in longer blocks,
and to wrongly claim blocks exclusively) — these tests pin down exactly the
behavior that reuse would have gotten wrong."""

import ai_action_items
import ai_indexing
import config


def _item(**kwargs):
    defaults = dict(item_text="x", item_type="obligation", page=1, evidence_quote="")
    defaults.update(kwargs)
    return ai_indexing.AIActionItem(**defaults)


# ─── locate_action_item_block ──────────────────────────────────────────────

def test_exact_match():
    blocks = ["Introduction.", "EMD shall be submitted on or before 15.03.2025."]
    loc = ai_action_items.locate_action_item_block(blocks, "EMD shall be submitted on or before 15.03.2025.")
    assert loc == (1, "exact", 1.0)


def test_substring_match_short_quote_inside_long_paragraph():
    # This is exactly the case locate_heading_block gets wrong: a short
    # sentence embedded in the middle of a much longer block.
    long_block = (
        "Clause 4.2 Notwithstanding anything contained elsewhere in this tender, "
        "the Bidder shall submit the EMD on or before 15.03.2025 failing which "
        "the bid shall be summarily rejected without further notice to the bidder."
    )
    loc = ai_action_items.locate_action_item_block([long_block], "the Bidder shall submit the EMD on or before 15.03.2025")
    assert loc is not None
    idx, kind, ratio = loc
    assert idx == 0
    assert kind == "substring"
    assert ratio == 1.0


def test_no_used_block_idxs_exclusivity_two_items_same_block():
    # One paragraph routinely states both an obligation and a deadline —
    # both items must be able to ground to the SAME block.
    blocks = ["The Agency shall submit the report within 30 days of contract signing, failing which a penalty applies."]
    loc1 = ai_action_items.locate_action_item_block(blocks, "The Agency shall submit the report")
    loc2 = ai_action_items.locate_action_item_block(blocks, "within 30 days of contract signing")
    assert loc1[0] == 0
    assert loc2[0] == 0


def test_html_entity_normalization_matches_across_sides():
    # Confirmed during reconnaissance: real OCR text contains un-decoded
    # entities; without html.unescape() first, these normalize differently
    # on each side of the comparison and silently fail to ground.
    blocks = ['The term &quot;Effective Date&quot; shall mean the date of signing.']
    loc = ai_action_items.locate_action_item_block(blocks, 'The term "Effective Date" shall mean the date of signing.')
    assert loc is not None
    assert loc[1] in ("exact", "substring")


def test_no_match_returns_none():
    blocks = ["Completely unrelated content about something else entirely."]
    loc = ai_action_items.locate_action_item_block(blocks, "A clause about EMD submission deadlines")
    assert loc is None


def test_page_text_fallback_when_quote_splits_across_blocks():
    blocks = ["The Bidder shall submit the EMD", "on or before 15.03.2025 to the Purchaser."]
    page_text = " ".join(blocks)
    # No single block contains the whole target (it's split across two) —
    # confirm that without the page_text fallback, it genuinely finds nothing.
    assert ai_action_items.locate_action_item_block(blocks, "submit the EMD on or before 15.03.2025") is None
    loc = ai_action_items.locate_action_item_block(blocks, "submit the EMD on or before 15.03.2025", page_text=page_text)
    assert loc is not None
    assert loc[1] == "page_text"


# ─── _ground_action_item ────────────────────────────────────────────────────

def test_grounds_on_claimed_page():
    page_blocks = {5: ["EMD shall be submitted on or before 15.03.2025."]}
    page_texts = {5: page_blocks[5][0]}
    item = _item(page=5, evidence_quote="EMD shall be submitted on or before 15.03.2025.")
    grounded = ai_action_items._ground_action_item(item, page_blocks, page_texts)
    assert grounded is not None
    assert grounded["page"] == 5
    assert grounded["anchor_id"] == "page-block-5"


def test_page_correction_when_model_misreports_claimed_page():
    # The model can and does misreport which page a clause is on (same
    # confirmed failure mode as headings — see
    # ai_reference_index._locate_heading_in_chunk's own docstring). The item
    # should be re-pointed to the page that actually grounds, within the
    # configured window.
    page_blocks = {
        4: ["Unrelated content on page 4."],
        5: ["EMD shall be submitted on or before 15.03.2025."],
        6: ["Unrelated content on page 6."],
    }
    page_texts = {k: v[0] for k, v in page_blocks.items()}
    item = _item(page=4, evidence_quote="EMD shall be submitted on or before 15.03.2025.")
    grounded = ai_action_items._ground_action_item(item, page_blocks, page_texts)
    assert grounded is not None
    assert grounded["page"] == 5


def test_drops_item_that_grounds_nowhere_in_window():
    page_blocks = {p: [f"Unrelated content on page {p}."] for p in range(1, 10)}
    page_texts = {k: v[0] for k, v in page_blocks.items()}
    item = _item(page=5, evidence_quote="A clause that genuinely does not appear anywhere nearby.")
    grounded = ai_action_items._ground_action_item(item, page_blocks, page_texts)
    assert grounded is None


def test_grounding_window_respects_config_ground_page_window(monkeypatch):
    monkeypatch.setattr(config, "AI_INDEX_GROUND_PAGE_WINDOW", 1)
    page_blocks = {
        5: ["Unrelated content."],
        8: ["EMD shall be submitted on or before 15.03.2025."],  # far outside the window
    }
    page_texts = {k: v[0] for k, v in page_blocks.items()}
    item = _item(page=5, evidence_quote="EMD shall be submitted on or before 15.03.2025.")
    grounded = ai_action_items._ground_action_item(item, page_blocks, page_texts)
    assert grounded is None  # page 8 is outside a window of 1 from claimed page 5


def test_grounded_dict_carries_through_all_schema_fields():
    page_blocks = {1: ["Within 30 days of the Effective Date, the SI shall submit the Exit Management Plan."]}
    page_texts = {1: page_blocks[1][0]}
    item = _item(
        page=1, item_type="deadline", item_text="Exit Management Plan due",
        date_text="within 30 days", anchor_event="Effective Date", cadence=None,
        evidence_quote="Within 30 days of the Effective Date, the SI shall submit the Exit Management Plan.",
        responsible_party="SI", confidence="high",
    )
    grounded = ai_action_items._ground_action_item(item, page_blocks, page_texts)
    assert grounded["item_type"] == "deadline"
    assert grounded["anchor_event"] == "Effective Date"
    assert grounded["responsible_party"] == "SI"
    assert grounded["confidence"] == "high"
