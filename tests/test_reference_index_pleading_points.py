"""Regression tests for the `point` target family in reference_index.py —
numbered/roman pleading paragraphs ("1. That the appellant is filing...")
indexed as leaf `point` entries when they sit directly under a generic,
unnumbered sub-document boundary (a case-caption block, an affidavit, a
judgment, "Respectfully Showeth") — never inside a numbered contract
container (Annexure/Article/Section), and never overriding `ground`'s own
existing behavior under "Grounds of Appeal"."""

from reference_index import build_targets_for_page, detect_pleading_points_for_page


def _candidates(page_text):
    candidates, _ = build_targets_for_page(page_text, seed_scope=None, has_next_page=False)
    return candidates


def _keys(page_text):
    return [c["key"] for c in _candidates(page_text)]


def test_numbered_pleading_paragraph_detected_as_point_under_generic_subdoc():
    page_text = (
        "RESPECTFULLY SHOWETH:\n\n"
        "1. That the appellant is filing the accompanying appeal.\n\n"
        "2. That for proper and just decision of the appeal.\n"
    )
    assert _keys(page_text) == ["point:1", "point:2"]


def test_roman_numeral_pleading_point_still_detected_under_generic_subdoc():
    page_text = (
        "AFFIDAVIT OF JOHN DOE\n\n"
        "I. That the deponent is competent to swear this affidavit.\n\n"
        "II. That the contents are true to the best of his knowledge.\n"
    )
    assert _keys(page_text) == ["point:i", "point:ii"]


def test_point_not_detected_inside_annexure_or_schedule_scope():
    """Regression: a real numbered clause under an open Annexure/Schedule
    must stay a `numheading`/plain body text, never reinterpreted as a
    pleading `point` just because it's a bare numbered line."""
    page_text = (
        "ANNEXURE A-1\n\n"
        "3.3 TIME SCHEDULE\n\n"
        "1. That the contractor shall complete the work within the agreed schedule.\n"
    )
    assert "point:1" not in _keys(page_text)


def test_pleading_points_do_not_leak_into_an_unrelated_embedded_contract_annexure():
    """Confirmed real bug: _pleading_scope_active used to check only
    nested_stack[0] (the document's TRUE root), which is "subdoc" family
    for nearly any document (e.g. "JUDGMENT") — so it returned True
    basically everywhere, 90+ pages deep into an unrelated embedded
    Contract Agreement annexure included. Live confirmed: a 22-item
    numbered distribution/CC list on such an annexure page minted as
    point:1..point:22 even though none of them are pleading paragraphs.
    The fix walks to the NEAREST subdoc boundary instead of the root —
    here, "CONTRACT AGREEMENT BETWEEN..." (not "JUDGMENT") is nearest, so
    pleading-point supplementation must not fire at all."""
    from reference_index import detect_pleading_points_for_page

    scope = {
        "stack": [
            {"family": "subdoc", "key": "subdoc:judgment", "label": "JUDGMENT"},
            {"family": "subdoc", "key": "subdoc:contract-agreement", "label": "CONTRACT AGREEMENT BETWEEN HVPNL AND M/S AREVA"},
        ],
    }
    page_text = (
        "Copy of the above is forwarded to the following for information and necessary action please:\n\n"
        "1. Sr. PS to Chairman, HVPNL, Panchkula.\n\n"
        "2. Chief Engineer/MM, HVPNL, Panchkula.\n"
    )
    results, _ = detect_pleading_points_for_page(page_text, scope, [])
    assert results == []


def test_pleading_points_still_detected_when_nearest_boundary_is_a_real_pleading_document():
    """Control for the test above: pleading-point supplementation must
    still fire normally when the NEAREST subdoc boundary genuinely is a
    generic pleading-type document (not excluded), confirming the fix
    didn't just make this function stop working entirely."""
    from reference_index import detect_pleading_points_for_page

    scope = {
        "stack": [
            {"family": "subdoc", "key": "subdoc:judgment", "label": "JUDGMENT"},
            {"family": "subdoc", "key": "subdoc:respectfully-showeth", "label": "RESPECTFULLY SHOWETH"},
        ],
    }
    page_text = "1. That the appellant is filing the accompanying appeal.\n"
    results, _ = detect_pleading_points_for_page(page_text, scope, [])
    assert [r["key"] for r in results] == ["point:1"]


def test_detect_pleading_points_roman_grounds_nest_under_the_scopes_last_decimal_ground():
    """Direct test of detect_pleading_points_for_page (the AI-path's own
    additive augmentation) — confirmed real bug: even after the roman/
    decimal key-collision fix, this function had no nesting mechanism at
    all (unlike build_targets_for_page's effective_stack), so its roman
    ground items landed flat instead of nesting under the preceding decimal
    ground. `last_decimal_ground` here simulates what
    apply_heading_events_to_scope now tracks and hands off via `scope`."""
    scope = {
        "stack": [{"family": "subdoc", "key": "subdoc:2", "label": "Grounds Of Appeal"}],
        "last_decimal_ground": {"key": "ground:10", "label": "Ground 10"},
    }
    page_text = "(I) First actual ground.\n\n(X) Tenth actual ground.\n"
    results, last_decimal_ground = detect_pleading_points_for_page(page_text, scope, existing_candidates=[])
    by_key = {r["key"]: r for r in results}

    assert set(by_key.keys()) == {"ground:i", "ground:x"}
    assert by_key["ground:i"]["scope_key"] == "subdoc:2/ground:10"
    assert by_key["ground:x"]["scope_key"] == "subdoc:2/ground:10"
    # A roman continuation never itself becomes the new "last decimal" —
    # unchanged from what was seeded in.
    assert last_decimal_ground == {"key": "ground:10", "label": "Ground 10"}


def test_detect_pleading_points_decimal_ground_does_not_collide_with_a_same_valued_roman_one():
    """No `last_decimal_ground` seeded here — a fresh decimal ground found
    by this function must mint its own plain key (no collision with a
    roman item of the same value, which would use the roman string, not
    the decimal one) and stay flat (no ancestor to nest under yet)."""
    scope = {"stack": [{"family": "subdoc", "key": "subdoc:2", "label": "Grounds Of Appeal"}]}
    page_text = "10. That the tenth ground.\n"
    results, last_decimal_ground = detect_pleading_points_for_page(page_text, scope, existing_candidates=[])
    assert results[0]["key"] == "ground:10"
    assert results[0]["scope_key"] == "subdoc:2"
    # This function's OWN finding — must propagate forward for a later
    # page's roman sub-grounds to nest under, since the AI is never asked
    # to report this decimal item itself.
    assert last_decimal_ground == {"key": "ground:10", "label": "Ground 10"}


def test_detect_pleading_points_never_misdetects_a_bare_date_as_a_ground():
    """Confirmed real bug (job bb417471): a bare date at a page-continuation
    boundary ("22.10.2008. Moreover, ...") matches the same digit-group
    shape as a real ground/point number — this function must reject it,
    not mint a bogus ground:22.10.2008 that then hijacks nesting for every
    real roman ground that follows."""
    scope = {"stack": [{"family": "subdoc", "key": "subdoc:2", "label": "Grounds Of Appeal"}]}
    page_text = "22.10.2008. Moreover, respondent did not place any document on record.\n"
    results, last_decimal_ground = detect_pleading_points_for_page(page_text, scope, existing_candidates=[])
    assert results == []
    assert last_decimal_ground is None


def test_ground_scope_still_wins_over_pleading_scope_for_grounds_of_appeal():
    """_ground_scope_active is checked before _pleading_scope_active — a
    numbered item under "GROUNDS OF APPEAL" (itself a generic subdoc
    trigger, so _pleading_scope_active's own condition would also be true)
    must still mint as `ground`, not `point`, preserving all of
    test_reference_index_ground.py's existing behavior."""
    page_text = (
        "GROUNDS OF APPEAL\n\n"
        "1. That the appellant is aggrieved by the impugned order.\n\n"
        "2. That the tribunal erred in fact and law.\n"
    )
    assert _keys(page_text) == ["ground:1", "ground:2"]


def test_point_gets_a_real_text_own_label_not_generic_point_n():
    page_text = (
        "RESPECTFULLY SHOWETH:\n\n"
        "1. That the appellant is filing the accompanying appeal.\n"
    )
    candidates = _candidates(page_text)
    assert len(candidates) == 1
    assert candidates[0]["own_label"] == "1. That the appellant is filing the accompanying appeal."


def test_bare_numbered_paragraph_ignored_outside_any_pleading_or_ground_scope():
    page_text = "1. That the appellant is filing the accompanying appeal.\n"
    assert _keys(page_text) == []
