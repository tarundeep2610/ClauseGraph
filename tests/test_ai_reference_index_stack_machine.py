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
    assert [s["key"] for s in stack] == ["article:2", "subdoc:scope-note"]
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
    assert candidates[0]["key"] == "subdoc:index"
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

    assert [s["key"] for s in new_scope["stack"]] == ["subdoc:2", "subdoc:c-third-ground"]  # only (C) survives
    closed_keys = [rec["key"] for rec in new_scope["closed_this_page"]]
    assert closed_keys == ["subdoc:a-first-ground", "subdoc:b-second-ground"]  # (A) then (B), in the order they were superseded
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

    assert not any(s["key"] == "subdoc:xvi-a-ground" for s in new_scope["stack"])
    assert len(new_scope["closed_this_page"]) == 1
    rec = new_scope["closed_this_page"][0]
    assert rec["key"] == "subdoc:xvi-a-ground"
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
    assert family == "subdoc" and bo == 1 and key == "subdoc:scope-of-work"


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


# ─── Leaf-family / low-confidence gate (fix for the real bug: a stray
# AI-reported roman-numeral ground item minted as its own top-level subdoc
# container, staying open for ~40 subsequent pages) ─────────────────────────


def test_ai_reported_roman_numeral_ground_item_becomes_a_leaf_not_a_top_level_container():
    seed = _seeded_scope([{"family": "subdoc", "key": "subdoc:2", "label": "Grounds Of Appeal"}])
    # Deliberately wrong depth=0 — if the leaf gate didn't kick in, this
    # would truncate the stack to root and evict "Grounds Of Appeal".
    events = [_event("(VII) That the Ld. Arbitral Tribunal erred.", depth=0)]
    candidates, new_scope = ri.apply_heading_events_to_scope(events, seed, page_num=21, has_next_page=True)

    assert candidates[0]["key"] == "ground:vii"
    assert new_scope["stack"] == [{"family": "subdoc", "key": "subdoc:2", "label": "Grounds Of Appeal"}]
    assert not any(s["key"] == "ground:vii" for s in new_scope["stack"])


def test_ai_reported_roman_ground_does_not_collide_with_a_same_valued_decimal_ground():
    """Direct repro of the confirmed real bug (job bb417471's "Grounds of
    Appeal"): a decimal ground ("10.") and a roman ground of the SAME value
    ("(X)") reported by the AI in the same scope must mint distinct keys —
    apply_heading_events_to_scope's forced-family branch had the identical
    normalize_key decimal-conversion bug as build_targets_for_page's own
    main loop, independently."""
    seed = _seeded_scope([{"family": "subdoc", "key": "subdoc:2", "label": "Grounds Of Appeal"}])
    events = [
        _event("10. That the tenth ground.", depth=0),
        _event("(X) That the tenth actual ground.", depth=0),
    ]
    candidates, _ = ri.apply_heading_events_to_scope(events, seed, page_num=18, has_next_page=True)
    keys = [c["key"] for c in candidates]
    assert keys == ["ground:10", "ground:x"], keys


def test_ai_reported_sequential_letter_list_stays_flat_even_when_some_letters_are_roman_valid():
    """Confirmed real bug (jobs bb417471 and 9c3dc6e6, the Judgment's
    "Challenge to the Arbitral Award" (A)-(O) list): apply_heading_events_
    to_scope's forced-family branch used raw GROUND_DIGIT_RE/GROUND_ROMAN_RE
    matching instead of the sequential-letter-aware
    _resolve_ground_or_point_match. Letters that are also valid roman
    numerals (C, D, I, L, M) were misclassified as roman and forced into
    the ground/point LEAF family, while non-roman-valid letters (A, B, E,
    ...) fell through to mint_key_for_heading's generic "subdoc" CONTAINER
    bucket and got pushed as real ancestors — so (C) ended up nesting under
    whichever non-roman letter was most recently pushed, e.g. (B). All of
    (A) through (E) must instead resolve as one flat sequential-letter run,
    every one a leaf, none pushed as an ancestor of the others."""
    seed = _seeded_scope([{"family": "subdoc", "key": "subdoc:2", "label": "Challenge to the Arbitral Award"}])
    events = [
        _event("(A) That the award is based on no evidence.", depth=0),
        _event("(B) The Arbitral Tribunal has erred in ignoring the material.", depth=0),
        _event("(C) The Arbitral Tribunal has erroneously held respondent No.1 liable.", depth=0),
        _event("(D) Arbitral Tribunal has erroneously condoned the grave lapse.", depth=0),
        _event("(E) The Arbitral Tribunal did not take into account the nature.", depth=0),
    ]
    candidates, new_scope = ri.apply_heading_events_to_scope(events, seed, page_num=42, has_next_page=True)
    by_key = {c["key"]: c for c in candidates}

    assert set(by_key.keys()) == {"point:a", "point:b", "point:c", "point:d", "point:e"}
    for key in by_key:
        assert by_key[key]["scope_key"] == "subdoc:2", (key, by_key[key]["scope_key"])
    # None of them became a real ancestor — the stack is untouched.
    assert new_scope["stack"] == [{"family": "subdoc", "key": "subdoc:2", "label": "Challenge to the Arbitral Award"}]


def test_ai_reported_letter_marker_with_redundant_trailing_period_stays_flat():
    """Confirmed real bug (bb417471's "(XIV)." and 9c3dc6e6's "(N)."): a
    redundant trailing period after the closing parenthesis wasn't matched
    by any ground/point regex, so that item fell through to
    mint_key_for_heading's generic subdoc-container bucket and got pushed
    as a real ancestor — so (O) right after it nested under (N) instead of
    staying its flat sibling."""
    seed = _seeded_scope([{"family": "subdoc", "key": "subdoc:2", "label": "Challenge to the Arbitral Award"}])
    events = [
        _event("(N). Fourteenth sub-ground.", depth=0),
        _event("(O) Fifteenth sub-ground.", depth=0),
    ]
    candidates, new_scope = ri.apply_heading_events_to_scope(events, seed, page_num=42, has_next_page=True)
    by_key = {c["key"]: c for c in candidates}

    assert set(by_key.keys()) == {"point:n", "point:o"}
    for key in by_key:
        assert by_key[key]["scope_key"] == "subdoc:2", (key, by_key[key]["scope_key"])
    assert new_scope["stack"] == [{"family": "subdoc", "key": "subdoc:2", "label": "Challenge to the Arbitral Award"}]


def test_ai_reported_genuine_roman_run_with_no_preceding_letters_still_resolves_as_roman():
    """Regression guard: a real roman-numeral enumeration that starts fresh
    (no letter run already established) must still resolve as roman, not
    be mistaken for sequential letters."""
    seed = _seeded_scope([{"family": "subdoc", "key": "subdoc:2", "label": "Grounds Of Appeal"}])
    events = [
        _event("(I) First ground.", depth=0),
        _event("(II) Second ground.", depth=0),
        _event("(III) Third ground.", depth=0),
    ]
    candidates, _ = ri.apply_heading_events_to_scope(events, seed, page_num=19, has_next_page=True)
    keys = [c["key"] for c in candidates]
    assert keys == ["ground:i", "ground:ii", "ground:iii"], keys


def test_ai_reported_roman_grounds_nest_under_the_preceding_decimal_ground_and_decimal_resumes_as_sibling():
    """AI-path analogue of Round 9's rule-engine fix: a decimal ground
    ("10.") followed by roman grounds ("(I)", "(X)") followed by another
    decimal ("11.") — the roman items must nest under ground:10 specifically
    (not land flat as unrelated siblings), and the next decimal item must
    resume as ground:10's sibling, not a child of the roman sub-list.
    Confirmed real bug: apply_heading_events_to_scope had the roman/decimal
    key-collision fix but no nesting mechanism at all, unlike its
    build_targets_for_page counterpart."""
    seed = _seeded_scope([{"family": "subdoc", "key": "subdoc:2", "label": "Grounds Of Appeal"}])
    events = [
        _event("10. That the tenth ground.", depth=0),
        _event("(I) First actual ground.", depth=0),
        _event("(X) Tenth actual ground.", depth=0),
        _event("11. That the appellant has not filed a similar appeal.", depth=0),
    ]
    candidates, _ = ri.apply_heading_events_to_scope(events, seed, page_num=19, has_next_page=True)
    by_key = {c["key"]: c for c in candidates}

    assert by_key["ground:10"]["scope_key"] == "subdoc:2"
    assert by_key["ground:i"]["scope_key"] == "subdoc:2/ground:10"
    assert by_key["ground:x"]["scope_key"] == "subdoc:2/ground:10"
    assert by_key["ground:11"]["scope_key"] == "subdoc:2"


def test_ai_reported_sequential_letter_grounds_nest_under_the_preceding_decimal_ground():
    """User-clarified real hierarchy (job 9c3dc6e6): a decimal point/ground
    that introduces a sub-list ("3. ... on following grounds:") followed by
    a sequential-letter run ("(A)"-"(C)") must nest the letters under that
    decimal item specifically — not stay flat siblings of it — exactly like
    a genuine roman-numeral continuation already does. Before this fix,
    apply_heading_events_to_scope's effective_stack nesting only triggered
    for is_roman_ground_or_point, explicitly excluding is_seq_letter."""
    seed = _seeded_scope([{"family": "subdoc", "key": "subdoc:2", "label": "Challenge to the Arbitral Award"}])
    events = [
        _event("3. The Award is under challenge on following grounds:", depth=0),
        _event("(A) First sub-ground under point 3.", depth=0),
        _event("(B) Second sub-ground under point 3.", depth=0),
        _event("(C) Third sub-ground under point 3.", depth=0),
    ]
    candidates, _ = ri.apply_heading_events_to_scope(events, seed, page_num=19, has_next_page=True)
    by_key = {c["key"]: c for c in candidates}

    assert by_key["point:3"]["scope_key"] == "subdoc:2"
    for key in ("point:a", "point:b", "point:c"):
        assert by_key[key]["scope_key"] == "subdoc:2/point:3", key


def test_ai_reported_plain_numbered_pleading_paragraph_becomes_a_point_leaf():
    seed = _seeded_scope([{"family": "subdoc", "key": "subdoc:1", "label": "Respectfully Showeth"}])
    events = [_event("1. That the appellant is filing the accompanying appeal.", depth=0)]
    candidates, new_scope = ri.apply_heading_events_to_scope(events, seed, page_num=7, has_next_page=True)

    assert candidates[0]["key"] == "point:1"
    assert candidates[0]["own_label"] == "1. That the appellant is filing the accompanying appeal."
    assert new_scope["stack"] == [{"family": "subdoc", "key": "subdoc:1", "label": "Respectfully Showeth"}]


def test_leaf_family_ai_heading_never_evicts_a_real_ancestor():
    """General case (not ground/point): mint_key_for_heading resolves
    "Clause 5" to family "clause" (rank None, a leaf) — must never truncate
    a real Article/Section ancestor, even with depth=0."""
    seed = _seeded_scope([
        {"family": "article", "key": "article:2", "label": "Article 2"},
        {"family": "section", "key": "section:3", "label": "Section 3"},
    ])
    events = [_event("Clause 5", depth=0)]
    candidates, new_scope = ri.apply_heading_events_to_scope(events, seed, page_num=9, has_next_page=True)

    assert candidates[0]["key"] == "clause:5"
    assert [s["key"] for s in new_scope["stack"]] == ["article:2", "section:3"]


def test_grounding_low_confidence_event_does_not_anchor_as_ancestor():
    """A non-leaf (numheading) event whose grounding couldn't be trusted
    (ai_unlocated/ai_fuzzy_grounded) must not truncate/push — it gets a
    candidate row (attached under whatever's genuinely open) but is never
    itself an ancestor real descendants could be wrongly scoped under."""
    seed = _seeded_scope([{"family": "annexure", "key": "annexure:a-1", "label": "Annexure A-1"}])
    events = [_event(
        "3.4 QUALITY PLANS", depth=0, heading_confidence_reasons=["ai_fuzzy_grounded"],
    )]
    candidates, new_scope = ri.apply_heading_events_to_scope(events, seed, page_num=69, has_next_page=True)

    assert candidates[0]["key"] == "numheading:3.4"
    assert [s["key"] for s in new_scope["stack"]] == ["annexure:a-1"]
    assert candidates[0]["scope_chain"] == [{"family": "annexure", "key": "annexure:a-1", "label": "Annexure A-1"}]


def test_low_confidence_confirmed_root_candidate_still_anchors_as_ancestor():
    # Real bug this closes: unlike "3.4 QUALITY PLANS" above (an ambiguous
    # generic numheading with no independent evidence it's a genuine
    # boundary), "Annexure IV" is a CONFIRMED root-candidate by family
    # alone (is_subdoc_root_candidate) — a 135-page tender fixture had
    # exactly this heading hit `ai_unlocated` (pure grounding noise, the
    # text genuinely exists on the page) and never get pushed, so its own
    # rightful children (and the next several real Annexures) all stayed
    # wrongly nested under the stale PREVIOUS Annexure's own descendant.
    seed = _seeded_scope([{"family": "section", "key": "section:7", "label": "Section VII: Annexures"}])
    events = [
        _event("Annexure IV: Format for Price Schedule", depth=1, heading_confidence_reasons=["ai_unlocated"]),
        _event("Item-I : Cost for Oracle ERP Version Upgrade", depth=2),
    ]
    candidates, new_scope = ri.apply_heading_events_to_scope(events, seed, page_num=66, has_next_page=True)

    assert candidates[0]["key"] == "annexure:iv"
    assert [s["key"] for s in new_scope["stack"]][:2] == ["section:7", "annexure:iv"]
    # The child genuinely nests under the newly-pushed Annexure IV, not
    # under whatever was open before it.
    assert candidates[1]["scope_key"] == "section:7/annexure:iv"


def test_low_confidence_affidavit_still_anchors_as_ancestor():
    # Same mechanism, the 100-page fixture's own confirmed instance: an
    # Affidavit (a standalone legal document, independently confirmed via
    # is_subdoc_root_candidate's _STANDALONE_LEGAL_DOCS_RE) must still
    # displace an unrelated open root even when fuzzy-grounded.
    seed = _seeded_scope([{"family": "subdoc", "key": "subdoc:grounds", "label": "GROUNDS OF APPEAL"}])
    events = [_event("AFFIDAVIT", depth=0, heading_confidence_reasons=["ai_fuzzy_grounded"])]
    candidates, new_scope = ri.apply_heading_events_to_scope(events, seed, page_num=38, has_next_page=True)

    assert [s["key"] for s in new_scope["stack"]] == [candidates[0]["key"]]
    assert candidates[0]["key"] != "subdoc:grounds"


def test_unconditional_block_reasons_still_block_even_for_a_confirmed_root_candidate():
    # Regression guard: the exemption is specific to the two POSITIONAL
    # reasons — "possible_reproduced_text" (a clause quoted verbatim mid-
    # argument) and "table_row_sourced" (grounded to a table cell) must
    # still block unconditionally, even for a family that would otherwise
    # qualify as a confirmed root candidate by shape alone.
    seed = _seeded_scope([{"family": "section", "key": "section:7", "label": "Section VII: Annexures"}])
    events = [_event(
        "Annexure IV: Format for Price Schedule", depth=1,
        heading_confidence_reasons=["ai_unlocated", "possible_reproduced_text"],
    )]
    candidates, new_scope = ri.apply_heading_events_to_scope(events, seed, page_num=66, has_next_page=True)

    assert [s["key"] for s in new_scope["stack"]] == ["section:7"]


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


# ─── Sibling-chaining safety net (_enumeration_marker_style) ───────────────
# Confirmed real bug (job 9c3dc6e6's Conclusion "(i)-(vii)" award list): the
# AI itself is probabilistic about reporting parent_ref for flat enumerated
# lists — the prompt fix (ENUMERATED LIST ITEMS ARE SIBLINGS OF EACH OTHER)
# improves but does not guarantee correct depth/parent_ref, so this is a
# deterministic, generic-path-only safety net: consecutive same-style
# markers reported as "one level deeper than the previous one" are flattened
# back to that previous item's own depth.


def test_ai_runaway_lower_roman_chain_is_flattened_to_flat_siblings():
    """Direct repro of the confirmed real bug shape: (i) is reported as a
    child of "Conclusion" (depth=1), but each subsequent item is reported
    one level DEEPER than the one before it (depth=2, 3, 4, ...) as if each
    nests under the previous — none of them go through forced_family (no
    ground/pleading scope is active), so without the safety net they'd
    chain into a runaway-deepening stack. All seven must resolve to the
    SAME scope_key, as flat siblings of "Conclusion"."""
    seed = _seeded_scope([{"family": "subdoc", "key": "subdoc:1", "label": "Conclusion"}])
    events = [
        _event("(i) The Tribunal shall pay Rs. 10,00,000 as damages.", depth=1),
        _event("(ii) The Tribunal shall further pay costs of the arbitration.", depth=2),
        _event("(iii) Interest shall accrue at 9% per annum.", depth=3),
        _event("(iv) The counter-claim is dismissed.", depth=4),
        _event("(v) Both parties shall bear their own costs.", depth=5),
        _event("(vi) The award is final and binding.", depth=6),
        _event("(vii) The registry shall communicate this order.", depth=7),
    ]
    candidates, new_scope = ri.apply_heading_events_to_scope(events, seed, page_num=17, has_next_page=True)

    scope_keys = {c["scope_key"] for c in candidates}
    assert scope_keys == {"subdoc:1"}, scope_keys
    # Only the LAST same-style item survives onto the persisted stack — each
    # earlier one was truncated away as the next sibling was pushed at the
    # same depth, exactly like any other flat run of siblings.
    assert [s["key"] for s in new_scope["stack"]][:1] == ["subdoc:1"]


def test_ai_enum_chain_break_by_an_unrelated_heading_is_not_flattened_across():
    """Regression guard: a genuine unrelated heading between two same-style
    runs resets the tracking — a NEW "(i)"-style list starting fresh under
    a real deeper heading must nest under THAT heading, not get flattened
    all the way back to the outer "Conclusion" scope."""
    seed = _seeded_scope([{"family": "subdoc", "key": "subdoc:1", "label": "Conclusion"}])
    events = [
        _event("(i) The Tribunal shall pay damages.", depth=1),
        _event("FURTHER DIRECTIONS", depth=1),
        _event("(i) First direction issued by the registry.", depth=2),
    ]
    candidates, _ = ri.apply_heading_events_to_scope(events, seed, page_num=17, has_next_page=True)
    by_fingerprint_order = candidates

    first_i, further_directions, second_i = by_fingerprint_order
    assert first_i["scope_key"] == "subdoc:1"
    assert further_directions["scope_key"] == "subdoc:1"
    # The second "(i)" nests under the fresh "FURTHER DIRECTIONS" container,
    # not flattened back to "Conclusion" — the intervening unrelated heading
    # must have reset the same-style tracking.
    assert second_i["scope_key"] == f"subdoc:1/{further_directions['key']}"


def test_ai_enum_chain_single_lone_item_is_untouched():
    """No preceding same-style item at all — nothing to flatten against."""
    seed = _seeded_scope([{"family": "subdoc", "key": "subdoc:1", "label": "Conclusion"}])
    events = [_event("(i) The Tribunal shall pay damages.", depth=1)]
    candidates, _ = ri.apply_heading_events_to_scope(events, seed, page_num=17, has_next_page=True)
    assert candidates[0]["scope_key"] == "subdoc:1"


def test_ai_enum_chain_a_genuinely_different_style_nests_one_level_deeper_undisturbed():
    """A roman-numeral pair directly under a bare "article" family with no
    numbering context of its own — neither item qualifies for ground/point
    treatment from pure keyword/number matching alone, so (i) degrades to a
    generic subdoc leaf. This exact scenario doesn't occur in any of this
    project's real gold fixtures (a real "article" container always carries
    its own numbering convention); it's a synthetic edge case exercising
    _pleading_scope_active's nearest-subdoc-boundary walk (the same fix
    applied to the confirmed real bug of pleading-point detection leaking
    across 90+ pages into an unrelated embedded annexure — see
    reference_index._NON_PLEADING_SUBDOC_LABELS_RE). A side effect here:
    once (i) becomes a subdoc, (ii) now sees it as a legitimate (not
    excluded) nearby boundary and resolves as point:ii nested one level
    under it, rather than staying a flat subdoc sibling — understood and
    accepted, not a regression in any real document."""
    seed = _seeded_scope([{"family": "article", "key": "article:1", "label": "Article 1"}])
    events = [
        _event("(i) The Tribunal shall pay damages.", depth=1),
        _event("(ii) The Tribunal shall further pay costs.", depth=2),
        _event("PENALTY CLAUSE", depth=2),
    ]
    candidates, _ = ri.apply_heading_events_to_scope(events, seed, page_num=17, has_next_page=True)
    by_key_last = candidates[-1]
    second_item = candidates[1]
    # (ii) is now recognized as a pleading-point leaf nested under (i)'s subdoc ...
    assert second_item["key"] == "point:ii"
    assert second_item["scope_key"] == "article:1/subdoc:i-the-tribunal-shall-pay-damages"
    # ... and "PENALTY CLAUSE" (no marker at all, so the enumeration safety
    # net doesn't apply to it) lands as its sibling under that same subdoc,
    # not nested a further level under point:ii.
    assert by_key_last["scope_key"] == second_item["scope_key"]


def test_decimal_section_container_nests_hierarchically_not_as_pleading_points():
    """Confirmed real bug: a tender document's own "Section-III: Instructions
    to Bidders" container had every decimal-numbered heading under it
    (1, 1.1, 1.2, ...) force-classified as pleading-point leaves by
    _pleading_scope_active's default-active assumption, since the label
    didn't match _NON_PLEADING_SUBDOC_LABELS_RE's (at the time) FAO-CARB-only
    exclusion list. Net effect: "1 Definition(s)" minted as point:1, and its
    decimal children "1.1"/"1.2" landed at point:1's OWN scope_key (flat
    siblings) instead of nesting one level under it. Fixed by extending the
    exclusion list to recognize generic "Section N" container labels."""
    seed = _seeded_scope([{"family": "subdoc", "key": "subdoc:3", "label": "Section-III: Instructions to Bidders"}])
    events = [
        _event("1 Definition(s)", depth=1),
        _event("1.1. \u201cAddendum\u201d", depth=2),
        _event("1.2. \u201cAgency\u201d", depth=2),
    ]
    candidates, _ = ri.apply_heading_events_to_scope(events, seed, page_num=11, has_next_page=True)

    definitions, addendum, agency = candidates
    assert definitions["key"] == "numheading:1", "must mint as a real decimal container, not a pleading-point leaf"
    assert addendum["key"] == "numheading:1.1"
    assert agency["key"] == "numheading:1.2"
    assert addendum["scope_key"] == f"subdoc:3/{definitions['key']}", "1.1 must nest one level under 1, not beside it"
    assert agency["scope_key"] == addendum["scope_key"], "1.2 must be 1.1's sibling (both under 1), not 1's sibling"


def test_pleading_scope_under_non_section_subdoc_is_unaffected():
    """Negative/no-regression case: a genuine arbitration-document pleading
    paragraph under a plain "JUDGMENT" container (no "Section N" or any
    other excluded label) must still force-classify as a point leaf, exactly
    as before this fix -- the new exclusion is scoped to "Section N"-style
    labels only, never a blanket change to pleading-point detection."""
    seed = _seeded_scope([{"family": "subdoc", "key": "subdoc:1", "label": "JUDGMENT"}])
    events = [
        _event("3. The Arbitral Award has been challenged by the petitioner.", depth=1),
        _event("4. The Respondent opposes the challenge.", depth=1),
    ]
    candidates, _ = ri.apply_heading_events_to_scope(events, seed, page_num=4, has_next_page=True)

    first, second = candidates
    assert first["key"] == "point:3"
    assert second["key"] == "point:4"
    assert first["scope_key"] == second["scope_key"] == "subdoc:1", "pleading points stay flat leaves under JUDGMENT"


def test_enum_chain_safety_net_does_not_flatten_genuine_decimal_children():
    """Confirmed real bug: the sibling-chaining safety net (meant to catch an
    AI mis-nesting "(ii)" one level under "(i)" when they're really flat
    siblings) also fired on a genuine decimal parent/child pair whenever both
    happened to end with a trailing period before the label -- "4.1."
    followed immediately by "4.1.1." both bucket as the coarse "decimal"
    enum style and are exactly one level apart, the same shape the safety
    net exists to flatten, so a real sub-clause got flattened back to its
    parent's own depth. Fixed by additionally checking num_segments: a
    genuine decimal child's segments extend the parent's by exactly one
    (["4","1","1"] extends ["4","1"]), which a mis-nested same-level sibling
    never does."""
    seed = _seeded_scope([{"family": "subdoc", "key": "subdoc:4", "label": "Section-IV: Scope of Work"}])
    events = [
        _event("4.1. Introduction", depth=1),
        _event("4.1.1. Existing Oracle License.", depth=2),
        _event("4.1.2. Something else.", depth=2),
    ]
    candidates, _ = ri.apply_heading_events_to_scope(events, seed, page_num=23, has_next_page=True)

    intro, child1, child2 = candidates
    assert intro["key"] == "numheading:4.1"
    assert child1["key"] == "numheading:4.1.1"
    assert child2["key"] == "numheading:4.1.2"
    assert child1["scope_key"] == f"subdoc:4/{intro['key']}", "4.1.1 must nest under 4.1, not flatten beside it"
    assert child2["scope_key"] == child1["scope_key"], "4.1.2 must be 4.1.1's sibling (both under 4.1)"


def test_enum_chain_safety_net_still_flattens_genuine_mis_nested_siblings():
    """No-regression case: two roman-numeral items that are NOT a decimal
    extension of each other (the original bug this safety net was built for)
    must still be flattened back to the same depth when the AI over-nests
    the second one under the first."""
    seed = _seeded_scope([{"family": "subdoc", "key": "subdoc:5", "label": "Conclusion"}])
    events = [
        _event("(i) The Tribunal shall pay damages.", depth=1),
        _event("(ii) The Tribunal shall further pay costs.", depth=2),
    ]
    candidates, _ = ri.apply_heading_events_to_scope(events, seed, page_num=30, has_next_page=True)
    first, second = candidates
    assert second["scope_key"] == first["scope_key"], "(ii) must stay a flat sibling of (i), not nest under it"
