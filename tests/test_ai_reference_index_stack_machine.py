"""Regression tests for reference_index.apply_heading_events_to_scope /
mint_key_for_heading / validate_scope_shape — the shared AI/rule stack
machine. These specifically cover bugs identified during design review:
(1) an AI-minted keyword-less heading must not evict an open Article/
Section ancestor (the rank-0/subdoc "pop everything" bug); (2) a heading
closed via an explicit AI end_page must not survive into the next page's
persisted scope; (3) a repeated running-header heading must not truncate
the stack.
"""

import reference_index as ri


def _event(text, depth=None, end_page=None, **kw):
    ev = {"heading_text": text, "depth": depth, "end_page": end_page}
    ev.update(kw)
    return ev


def test_ai_keyword_less_heading_does_not_evict_open_article_section():
    # Seed: Article 2 > Section 3 already open (two ordinary rank-bearing
    # containers), mimicking a real document mid-way through.
    seed_scope = {
        "stack": [
            {"family": "article", "key": "article:2", "label": "Article 2"},
            {"family": "section", "key": "section:3", "label": "Section 3"},
        ],
        "boundary_ordinal": 0, "label_occurrence_counts": {},
        "last_bare_integer": None, "last_letter_ordinal": None,
    }
    # AI reports a keyword-less/number-less heading ("SCOPE OF WORK") as a
    # child of whatever's currently open (depth=None -> append).
    events = [_event("SCOPE OF WORK", depth=None)]
    candidates, new_scope = ri.apply_heading_events_to_scope(events, seed_scope, page_num=5, has_next_page=True)

    stack = new_scope["stack"]
    assert [s["key"] for s in stack[:2]] == ["article:2", "section:3"], (
        "Article 2 and Section 3 must survive — this is the rank-0/subdoc "
        "pop-everything bug if they don't."
    )
    assert stack[2]["family"] == "subdoc"
    assert candidates[0]["scope_chain"][0]["key"] == "article:2"
    assert candidates[0]["scope_chain"][1]["key"] == "section:3"
    ri.validate_scope_shape(new_scope)  # must not raise


def test_ai_keyword_less_heading_can_still_truncate_to_an_explicit_parent():
    # Same seed, but this time the AI resolved an explicit parent at depth 0
    # (Article 2), meaning this heading is a SIBLING of Section 3, not its
    # child — Section 3's range should correctly end here.
    seed_scope = {
        "stack": [
            {"family": "article", "key": "article:2", "label": "Article 2"},
            {"family": "section", "key": "section:3", "label": "Section 3"},
        ],
        "boundary_ordinal": 0, "label_occurrence_counts": {},
        "last_bare_integer": None, "last_letter_ordinal": None,
    }
    events = [_event("SCOPE NOTE", depth=1)]  # depth=1 -> keep only Article 2
    candidates, new_scope = ri.apply_heading_events_to_scope(events, seed_scope, page_num=5, has_next_page=True)
    stack = new_scope["stack"]
    assert [s["key"] for s in stack] == ["article:2", "subdoc:1"]
    assert candidates[0]["scope_chain"] == [{"family": "article", "key": "article:2", "label": "Article 2"}]


def test_explicit_end_page_closes_container_before_persisting_next_page():
    # A heading whose own content opens and closes on the SAME page (the
    # common case for a short section — matches the plan's own worked
    # example: start_page == end_page == 21).
    events = [_event("3.4 QUALITY PLANS", depth=0, end_page=21)]
    candidates, new_scope = ri.apply_heading_events_to_scope(events, None, page_num=21, has_next_page=True)
    assert candidates[0]["key"] == "numheading:3.4"  # the heading itself is still detected/persisted

    # It must NOT survive into what page 22 would inherit as page 21's seed
    # — otherwise compute_scope_ranges' pure page-to-page diff would keep
    # extending it regardless of what end_page said.
    assert not any(s["key"] == "numheading:3.4" for s in new_scope["stack"])

    _, new_scope2 = ri.apply_heading_events_to_scope([], new_scope, page_num=22, has_next_page=True)
    assert not any(s["key"] == "numheading:3.4" for s in new_scope2["stack"])


def test_explicit_end_page_on_a_later_page_survives_until_then():
    # Opened on page 4, end_page=5 -> must still be present while page 5's
    # own events are processed (its content genuinely includes page 5),
    # and only disappear from what page 6 inherits.
    events = [_event("3.0 CONDITIONS AND COVENANTS", depth=0, end_page=5)]
    _, scope_p4 = ri.apply_heading_events_to_scope(events, None, page_num=4, has_next_page=True)
    assert any(s["key"] == "numheading:3.0" for s in scope_p4["stack"])

    child_events = [_event("3.4 QUALITY PLANS", depth=1)]
    candidates_p5, scope_p5 = ri.apply_heading_events_to_scope(child_events, scope_p4, page_num=5, has_next_page=True)
    assert candidates_p5[0]["scope_chain"][0]["key"] == "numheading:3.0"
    assert not any(s["key"] == "numheading:3.0" for s in scope_p5["stack"])

    _, scope_p6 = ri.apply_heading_events_to_scope([], scope_p5, page_num=6, has_next_page=True)
    assert not any(s["key"] == "numheading:3.0" for s in scope_p6["stack"])


def test_still_open_at_chunk_end_heading_survives_across_pages():
    # end_page omitted entirely (still_open_at_chunk_end=True upstream) ->
    # must behave exactly like the existing rule-based carry-forward.
    events = [_event("3.0 CONDITIONS AND COVENANTS", depth=0, end_page=None)]
    _, scope_p4 = ri.apply_heading_events_to_scope(events, None, page_num=4, has_next_page=True)
    _, scope_p5 = ri.apply_heading_events_to_scope([], scope_p4, page_num=5, has_next_page=True)
    assert any(s["key"] == "numheading:3.0" for s in scope_p5["stack"])


def test_repeated_running_header_is_a_noop_not_a_truncate():
    seed_scope = {
        "stack": [
            {"family": "annexure", "key": "annexure:a-3", "label": "Annexure A-3"},
            {"family": "numheading", "key": "numheading:3.0", "label": "3.0 CONDITIONS",
             "num_segments": ["3"]},
        ],
        "boundary_ordinal": 0, "label_occurrence_counts": {},
        "last_bare_integer": None, "last_letter_ordinal": None,
    }
    # A running "ANNEXURE A-3" header re-detected on a later page, resolved
    # (incorrectly, by the model) to depth 0 (its own real depth already on
    # the stack) — must be a no-op, not a truncate-to-0-then-push, or it
    # would evict "3.0 CONDITIONS" mid-section.
    events = [_event("ANNEXURE A-3", depth=0)]
    _, new_scope = ri.apply_heading_events_to_scope(events, seed_scope, page_num=21, has_next_page=True)
    assert [s["key"] for s in new_scope["stack"]] == ["annexure:a-3", "numheading:3.0"]


def test_ai_minted_subdoc_heading_gets_a_real_own_label():
    """Regression, found via a real end-to-end run against a live Gemini
    call: an AI-minted keyword-less heading ("INDEX") persisted with
    own_label=None, so the frontend (which only special-cases numheading/
    letterheading/subdoc away from its generic key-formatting fallback)
    fell back to rendering the bare key fragment ("1" for "subdoc:1")
    instead of the real heading text."""
    events = [_event("INDEX")]
    candidates, _ = ri.apply_heading_events_to_scope(events, None, page_num=2, has_next_page=True)
    assert candidates[0]["key"] == "subdoc:1"
    assert candidates[0]["own_label"] == "INDEX"


def test_ai_hint_resolves_a_garbled_heading_the_regex_alone_cannot():
    """The real case that triggered this fix: OCR misread the digit "1"
    itself as a letter ("AmexureAl" for "Annexure A-1"), which
    _fuzzy_family_match's digit-anchored regex can't recover on its own —
    but the AI, reading it in context, can, and reports it via the hint
    fields. Must be trusted over the fallback subdoc bucket."""
    family, key, label, segs, _ = ri.mint_key_for_heading(
        "AmexureAl", 0, {}, family_hint="annexure", number_hint="A-1",
    )
    assert (family, key) == ("annexure", "annexure:a-1")
    assert label == "Annexure A-1"


def test_ai_hint_never_overrides_an_already_resolvable_heading():
    """Regression, found via a real run: the model volunteers a hint far
    more liberally than asked for, including on headings that already
    parse correctly on their own. The hint must be a LAST resort, never a
    pre-emptive override — confirmed here with a conflicting hint on a
    heading the regex fuzzy path (and separately, the decimal path) would
    already resolve correctly; the hint must be ignored in both cases."""
    # _fuzzy_family_match already resolves "AmneXureA-1" correctly on its
    # own (real digit present) — a conflicting hint must be ignored.
    family, key, label, segs, _ = ri.mint_key_for_heading(
        "AmneXureA-1", 0, {}, family_hint="schedule", number_hint="9",
    )
    assert (family, key) == ("annexure", "annexure:a-1")


# ─── closed_this_page: same-page supersession compute_scope_ranges can't
# otherwise see — found on a real 100-page job where entire headings were
# either vanishing outright or closing their container one page early. ───


def _seeded_scope(stack, boundary_ordinal=0):
    return {
        "stack": stack, "boundary_ordinal": boundary_ordinal, "label_occurrence_counts": {},
        "last_bare_integer": None, "last_letter_ordinal": None,
    }


def test_same_page_multiple_siblings_are_tracked_in_closed_this_page():
    # (A), (B), (C) all land on the same page at the same depth — only (C)
    # survives to be persisted in "stack", but (A) AND (B) must not simply
    # vanish (each is truncated away in turn — (A) when (B) is pushed, (B)
    # when (C) is pushed); both belong in closed_this_page so
    # compute_scope_ranges can still give them their own page-level range.
    seed = _seeded_scope([{"family": "subdoc", "key": "subdoc:2", "label": "GROUNDS OF APPEAL"}], boundary_ordinal=2)
    events = [_event("(A) first ground", depth=1), _event("(B) second ground", depth=1),
              _event("(C) third ground", depth=1)]
    _, new_scope = ri.apply_heading_events_to_scope(events, seed, page_num=42, has_next_page=True)

    assert [s["key"] for s in new_scope["stack"]] == ["subdoc:2", "subdoc:5"]  # only (C) survives
    closed_keys = [rec["key"] for rec in new_scope["closed_this_page"]]
    assert closed_keys == ["subdoc:3", "subdoc:4"]  # (A) then (B), in the order they were superseded
    for rec in new_scope["closed_this_page"]:
        assert rec["depth"] == 1
        assert rec["scope_key"] == "subdoc:2"
        assert rec["start_page"] == rec["end_page"] == 42


def test_same_page_self_closing_entry_appears_in_closed_this_page_not_stack():
    # The sole heading on the page, self-declared as closing on this same
    # page — must not vanish entirely (it never survives to any persisted
    # snapshot at all otherwise).
    seed = _seeded_scope([{"family": "subdoc", "key": "subdoc:2", "label": "GROUNDS OF APPEAL"}], boundary_ordinal=2)
    events = [_event("(XVI) a ground", depth=1, end_page=32)]
    _, new_scope = ri.apply_heading_events_to_scope(events, seed, page_num=32, has_next_page=True)

    assert not any(s["key"] == "subdoc:3" for s in new_scope["stack"])
    assert len(new_scope["closed_this_page"]) == 1
    rec = new_scope["closed_this_page"][0]
    assert rec["key"] == "subdoc:3"
    assert rec["depth"] == 1
    assert rec["scope_key"] == "subdoc:2"
    assert rec["start_page"] == rec["end_page"] == 32


def test_multi_level_truncate_records_a_distinct_scope_key_per_removed_entry():
    # Three-level stack (A > B > C); a new sibling of A (depth=0) truncates
    # all three at once — each removed entry must get ITS OWN scope_key
    # computed from its own ancestor chain, not one shared prefix for all.
    seed = _seeded_scope([
        {"family": "annexure", "key": "annexure:a-1", "label": "Annexure A-1"},
        {"family": "numheading", "key": "numheading:3.0", "label": "3.0 CONDITIONS", "num_segments": ["3"]},
        {"family": "numheading", "key": "numheading:3.4", "label": "3.4 QUALITY PLANS", "num_segments": ["3", "4"]},
    ])
    events = [_event("Annexure A-2", depth=0)]
    _, new_scope = ri.apply_heading_events_to_scope(events, seed, page_num=10, has_next_page=True)

    closed = {rec["key"]: rec for rec in new_scope["closed_this_page"]}
    assert set(closed) == {"annexure:a-1", "numheading:3.0", "numheading:3.4"}
    assert closed["annexure:a-1"]["depth"] == 0
    assert closed["annexure:a-1"]["scope_key"] == ""
    assert closed["numheading:3.0"]["depth"] == 1
    assert closed["numheading:3.0"]["scope_key"] == "annexure:a-1"
    assert closed["numheading:3.4"]["depth"] == 2
    assert closed["numheading:3.4"]["scope_key"] == "annexure:a-1/numheading:3.0"


def test_tail_end_close_cascades_to_a_child_with_no_end_page_of_its_own():
    # X (depth0) is declared closing on THIS page; Y (depth1, its child) has
    # NO end_page of its own — Y must close too, cascading, rather than
    # surviving at a silently shifted depth (the latent index-shift risk in
    # a blanket value-filter).
    seed = _seeded_scope([
        {"family": "annexure", "key": "annexure:a-1", "label": "Annexure A-1", "end_page": 9},
        {"family": "numheading", "key": "numheading:3.0", "label": "3.0 CONDITIONS", "num_segments": ["3"]},
    ])
    _, new_scope = ri.apply_heading_events_to_scope([], seed, page_num=9, has_next_page=True)

    assert new_scope["stack"] == []
    closed = {rec["key"]: rec for rec in new_scope["closed_this_page"]}
    assert set(closed) == {"annexure:a-1", "numheading:3.0"}
    assert closed["annexure:a-1"]["depth"] == 0
    assert closed["numheading:3.0"]["depth"] == 1
    assert closed["numheading:3.0"]["scope_key"] == "annexure:a-1"

    # The decimal-number parser already resolves this correctly too.
    family, key, label, segs, _ = ri.mint_key_for_heading(
        "3.4 QUALITY PLANS", 0, {}, family_hint="section", number_hint="3.4",
    )
    assert (family, key) == ("numheading", "numheading:3.4")


def test_hallucinated_family_hint_falls_through_safely_not_crashes():
    family, key, label, segs, _ = ri.mint_key_for_heading(
        "SCOPE OF WORK", 0, {}, family_hint="memorandum", number_hint="1",
    )
    assert family == "subdoc"  # unrecognized hint ignored, falls to the residual bucket


def test_unparseable_number_hint_falls_through_safely():
    family, key, label, segs, _ = ri.mint_key_for_heading(
        "SCOPE OF WORK", 0, {}, family_hint="annexure", number_hint="not a number",
    )
    assert family == "subdoc"


def test_no_hints_behaves_identically_to_before_this_change():
    """Backward compatibility: rule-fallback-synthesized events (and any
    event predating this feature) never set family_hint/number_hint —
    confirm the default (None, None) reaches the exact same cascade as
    before, unaffected by the new resolution step."""
    family, key, label, segs, _ = ri.mint_key_for_heading("3.4 QUALITY PLANS", 0, {})
    assert (family, key) == ("numheading", "numheading:3.4")


def test_resolve_ai_hint_rejects_missing_fields():
    assert ri._resolve_ai_hint(None, "1") is None
    assert ri._resolve_ai_hint("annexure", None) is None
    assert ri._resolve_ai_hint("annexure", "") is None


def test_fuzzy_family_match_no_digit_fallback_resolves_amexureal():
    """Direct unit test for the regex safety-net path (used by the
    rule-based detector, and as a fallback when the AI doesn't supply a
    hint) — hand-verified against the real algorithm this session."""
    result = ri._fuzzy_family_match("AmexureAl")
    assert result == ("annexure", "A", "1")


def test_fuzzy_family_match_no_digit_fallback_does_not_break_roman_numerals():
    """Guards the specific collision risk that made this fix non-trivial:
    _FUZZY_TAIL_RE's roman-numeral branch treats lowercase "l" as the
    numeral L (=50) — denoising must never corrupt a genuine bare
    roman-numeral tail into nonsense on some OTHER, unrelated input.
    (This input doesn't fuzzy-match any real family, so it should simply
    fail to resolve — not resolve to something wrong.)
    """
    assert ri._fuzzy_family_match("XyzzyVI") is None


def test_mint_key_for_heading_reuses_existing_key_shapes():
    family, key, label, segs, _ = ri.mint_key_for_heading("3.4 QUALITY PLANS", 0, {})
    assert (family, key, segs) == ("numheading", "numheading:3.4", ["3", "4"])

    family, key, label, segs, _ = ri.mint_key_for_heading("Annexure A-1", 0, {})
    assert (family, key) == ("annexure", "annexure:a-1")

    family, key, label, segs, bo = ri.mint_key_for_heading("A. INTRODUCTION", 0, {})
    assert (family, key) == ("letterheading", "letterheading:A")

    family, key, label, segs, bo = ri.mint_key_for_heading("SCOPE OF WORK", 0, {})
    assert family == "subdoc" and bo == 1 and key == "subdoc:1"


def test_validate_scope_shape_rejects_missing_fields():
    import pytest
    with pytest.raises(ValueError):
        ri.validate_scope_shape({"stack": []})  # missing boundary_ordinal etc.
    with pytest.raises(ValueError):
        ri.validate_scope_shape({
            "stack": [{"family": "numheading", "key": "numheading:3"}],  # missing label, num_segments
            "boundary_ordinal": 0, "label_occurrence_counts": {},
            "last_bare_integer": None, "last_letter_ordinal": None,
        })


def test_ai_stack_shape_matches_rule_based_shape_for_compute_scope_ranges():
    """The key cross-chunk-boundary property: a container opened via the AI
    path and one opened via the rule path must diff identically under
    compute_scope_ranges — verified by round-tripping through
    database.get_end_of_page_scope_bulk-shaped input directly."""
    ai_events = [_event("2.0 CONTRACT DOCUMENTS", depth=0)]
    _, ai_scope = ri.apply_heading_events_to_scope(ai_events, None, page_num=4, has_next_page=True)

    rule_cands, rule_scope = ri.build_targets_for_page("2.0 CONTRACT DOCUMENTS", None, has_next_page=True)

    assert ai_scope["stack"][0]["key"] == rule_scope["stack"][0]["key"] == "numheading:2.0"
    assert ai_scope["stack"][0]["family"] == rule_scope["stack"][0]["family"] == "numheading"
    assert ai_scope["stack"][0]["num_segments"] == rule_scope["stack"][0]["num_segments"]
    for field_name in ("boundary_ordinal", "label_occurrence_counts", "last_bare_integer", "last_letter_ordinal"):
        assert field_name in ai_scope and field_name in rule_scope
