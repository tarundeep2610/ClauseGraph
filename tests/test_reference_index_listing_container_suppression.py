"""Tests for _LISTING_CONTAINER_RE / _immediate_parent_is_listing_container —
suppressing point/ground/numheading/letterheading rows that are really
table/list rows (a price-schedule line item, a spares-list part, a document-
index entry) rather than real document headings. Confirmed via a live
diagnostic on the 100-page gold fixture: pages 62 (a 23-item document INDEX)
and 82-87 (a price-schedule/spares table) accounted for 58% of remaining
false positives after the ordinal-drift fix.

Unlike qualified_generic_trigger/generic_heading_shape, these candidates are
fully skipped (never appended, never pushed) — not just marked low-
confidence — since the scorer counts every persisted target regardless of
confidence level; marking-but-persisting would not have moved precision at
all. See reference_index._immediate_parent_is_listing_container's docstring.

Every case here is seeded directly via seed_scope (a price-schedule/index
container already open) rather than relying on page text to trigger that
container itself — `detect_subdoc_trigger`'s own strong/weak classification
of phrases like "Price Schedule" is a separate, already-tested concern (see
test_reference_index_generic_triggers.py), and conflating the two in these
tests made several early drafts here pass for the wrong reason (the row
text didn't match any family at all, with or without this gate — confirmed
by checking each one against a non-listing control parent first)."""

import reference_index as ri
from reference_index import build_targets_for_page


def _by_key(page_text, seed_scope):
    candidates, scope = build_targets_for_page(page_text, seed_scope=seed_scope, has_next_page=False)
    return {c["key"]: c for c in candidates}, scope


def _seed(label, key="subdoc:price-schedule", family="subdoc"):
    return {
        "stack": [{"family": family, "key": key, "label": label}],
        "boundary_ordinal": 1, "label_occurrence_counts": {},
        "last_bare_integer": None, "last_letter_ordinal": None,
    }


def test_ground_rows_under_a_price_schedule_are_fully_suppressed():
    # Control: the identical row DOES mint as ground:11 under a non-listing,
    # ground-scope-active parent (confirmed below) — proving the absence
    # under "Price Schedule" is this gate's doing, not a non-match.
    page_text = "11. Relays, power contactors, switch fuses units\n"
    by_key, scope = _by_key(page_text, _seed("Price Schedule"))
    assert "ground:11" not in by_key
    assert scope["stack"][0]["label"] == "Price Schedule"  # the container itself is untouched

    control_by_key, _ = _by_key(page_text, _seed("Grounds of Appeal", key="subdoc:grounds-of-appeal"))
    assert "ground:11" in control_by_key


def test_point_rows_under_an_index_listing_are_fully_suppressed():
    page_text = "10. Letter of M/s Areva T&D India Ltd. dated 27.12.2007\n"
    by_key, _ = _by_key(page_text, _seed("Respectfully Showeth", key="subdoc:respectfully-showeth"))
    # Sanity: this exact row DOES mint as point:10 under this pleading-scope
    # parent when it ISN'T also a listing container (this IS the control —
    # "Respectfully Showeth" is not a listing label).
    assert "point:10" in by_key

    index_by_key, _ = _by_key(page_text, _seed("INDEX", key="subdoc:index"))
    assert "point:10" not in index_by_key


def test_numheading_rows_under_a_price_schedule_are_fully_suppressed():
    page_text = "4.10 Isolation valves\n\n4.11 Deluge valves\n"
    by_key, _ = _by_key(page_text, _seed("Price Schedule"))
    assert "numheading:4.10" not in by_key
    assert "numheading:4.11" not in by_key

    control_by_key, _ = _by_key(page_text, _seed("Grounds of Appeal", key="subdoc:grounds-of-appeal"))
    assert "numheading:4.10" in control_by_key


def test_letterheading_rows_under_a_price_schedule_are_fully_suppressed():
    # A `subdoc`-family root always activates pleading scope
    # (_pleading_scope_active: "nested_stack[0]['family'] == 'subdoc'"),
    # which resolves sequential letters to `point`, not `letterheading`,
    # before LETTER_HEADING_RE ever gets a chance — a rule-path-only quirk
    # unrelated to this gate (the real letterheading FPs this fix targets
    # came from the AI path, which resolves family differently). Seeded
    # under a non-subdoc family root here specifically to isolate and test
    # THIS gate's own logic (label-string match, family-agnostic) rather
    # than fighting that unrelated rule-path behavior.
    page_text = "A. Circuit Breaker Specifications\n\nB. Transformer Specifications\n"
    by_key, _ = _by_key(page_text, _seed("Price Schedule", key="annexure:a-1", family="annexure"))
    assert "letterheading:a" not in by_key
    assert "letterheading:b" not in by_key

    control_by_key, _ = _by_key(page_text, _seed("Annexure A-1", key="annexure:a-1", family="annexure"))
    assert "letterheading:a" in control_by_key


def test_sibling_numheading_is_not_wrongly_suppressed_by_a_stale_pre_pop_schedule_sibling():
    """Regression for the real bug found while implementing this: checking
    the parent BEFORE the ancestor pop-loop resolves the true parent wrongly
    suppressed "3.4" because the stale, about-to-be-popped sibling "3.3 TIME
    SCHEDULE" matched the gate — even though 3.4's true resolved parent,
    "3.0 CONDITIONS AND COVENANTS", does not. "3.3 TIME SCHEDULE" is also a
    realistic, ordinary clause title (not a real listing container) and
    must mint normally itself."""
    page_text = (
        "3.0 CONDITIONS AND COVENANTS\n\n"
        "3.3 TIME SCHEDULE\n\n"
        "3.4 QUALITY PLANS\n"
    )
    candidates, _ = build_targets_for_page(page_text, seed_scope=None, has_next_page=False)
    by_key = {c["key"]: c for c in candidates}
    assert "numheading:3.3" in by_key
    assert "numheading:3.4" in by_key
    assert by_key["numheading:3.4"]["scope_label"] == "3.0 CONDITIONS AND COVENANTS"


def test_ai_path_suppresses_a_point_row_under_a_price_schedule_parent():
    seed_scope = {
        "stack": [{"family": "subdoc", "key": "subdoc:price-schedule", "label": "Price Schedule"}],
        "boundary_ordinal": 1, "label_occurrence_counts": {},
        "last_bare_integer": None, "last_letter_ordinal": None,
    }
    events = [{
        "heading_text": "11. Relays, power contactors, switch fuses units",
        "depth": 1, "end_page": None, "family_hint": "point",
    }]
    candidates, new_scope = ri.apply_heading_events_to_scope(events, seed_scope, page_num=83, has_next_page=True)
    assert candidates == []
    assert new_scope["stack"] == seed_scope["stack"]  # the real container itself survives untouched


def test_ai_path_does_not_suppress_the_same_shaped_row_under_a_non_listing_parent():
    seed_scope = {
        "stack": [{"family": "subdoc", "key": "subdoc:grounds-of-appeal", "label": "Grounds of Appeal"}],
        "boundary_ordinal": 1, "label_occurrence_counts": {},
        "last_bare_integer": None, "last_letter_ordinal": None,
    }
    events = [{
        "heading_text": "11. That the tribunal erred in holding.",
        "depth": 1, "end_page": None, "family_hint": "ground",
    }]
    candidates, _ = ri.apply_heading_events_to_scope(events, seed_scope, page_num=25, has_next_page=True)
    assert len(candidates) == 1
    assert candidates[0]["key"] == "ground:11"


def test_ai_path_suppresses_a_bare_numbered_row_under_an_index_listing_with_no_family_hint():
    """The AI path's own generic fallback (mint_key_for_heading, no
    family_hint at all) resolves a bare "N. text" row to `numheading`, not
    `point`/`ground` — confirmed this is what a real qwen run actually
    produced for the page-62 INDEX listing. Covers that resolution path
    too, not just the family_hint-driven one."""
    seed_scope = {
        "stack": [{"family": "subdoc", "key": "subdoc:index", "label": "INDEX"}],
        "boundary_ordinal": 1, "label_occurrence_counts": {},
        "last_bare_integer": None, "last_letter_ordinal": None,
    }
    events = [{"heading_text": "10. Letter of M/s Areva T&D India Ltd. dated 27.12.2007", "depth": 1, "end_page": None}]
    candidates, _ = ri.apply_heading_events_to_scope(events, seed_scope, page_num=62, has_next_page=True)
    assert candidates == []
