"""Regression tests for the generalized, recall-biased fixes to Root Causes
A (a keyword match unconditionally resetting the whole stack even when
it's just incidental word usage) and B (open-vocabulary headings no fixed
keyword list could ever enumerate) — confirmed real bugs found by
comparing two indexing runs of the same 100-page document. Every fix here
is a general, document-agnostic structural/shape rule, not a patch tied to
this one document's specific vocabulary — tests below use synthetic text
alongside the real confirmed examples to prove that."""

from reference_index import build_targets_for_page, detect_subdoc_trigger


def _keys(page_text, seed_scope=None, has_next_page=False):
    candidates, scope = build_targets_for_page(page_text, seed_scope=seed_scope, has_next_page=has_next_page)
    return [c["key"] for c in candidates], scope


def _by_key(page_text, seed_scope=None):
    candidates, scope = build_targets_for_page(page_text, seed_scope=seed_scope, has_next_page=False)
    return {c["key"]: c for c in candidates}, scope


# ─── 1.1: whole-line vs. qualified-word gate (Root Cause A, general) ───────


def test_qualified_generic_trigger_does_not_reset_the_stack():
    """The confirmed real bug: "Price Schedule"/"Work Schedule:" (a minor
    sub-label inside a covering letter) opened a container that stayed
    open for 20 subsequent pages, swallowing everything real in between.
    Proven here with BOTH the real document's exact wording and an
    unrelated synthetic example, to show this is a general fix."""
    page_text = (
        "3.0 CONDITIONS AND COVENANTS\n\n"
        "Some real clause text under this section.\n\n"
        "Price Schedule\n\n"
        "The details of materials ordered are as follows.\n"
    )
    by_key, scope = _by_key(page_text)
    # The real container must still be open at the end of the page.
    assert [s["key"] for s in scope["stack"]] == ["numheading:3.0"]
    # "Price Schedule" is still visible, but as a non-mutating low-confidence leaf.
    weak = [c for c in by_key.values() if c.get("own_label") == "Schedule"]
    assert len(weak) == 1
    assert weak[0]["heading_confidence"] == "low"
    assert weak[0]["heading_confidence_reasons"] == ["qualified_generic_trigger"]

    # A synthetic, never-seen-in-this-document example of the same shape.
    synthetic = (
        "4.0 DELIVERY TERMS\n\n"
        "Some real clause text.\n\n"
        "Delivery Schedule for widgets\n\n"
        "More unrelated text.\n"
    )
    by_key2, scope2 = _by_key(synthetic)
    assert [s["key"] for s in scope2["stack"]] == ["numheading:4.0"]


def test_standalone_schedule_divider_still_resets_the_stack():
    """A GENUINE standalone schedule divider — nothing precedes the
    keyword — still opens a real new top-level container, as before."""
    page_text = "3.0 CONDITIONS AND COVENANTS\n\nSome clause text.\n\nSCHEDULE-1A\n\nNew section content.\n"
    _, scope = _by_key(page_text)
    assert [s["key"] for s in scope["stack"]] == ["subdoc:1"]
    assert scope["stack"][0]["label"] == "Schedule"


def test_detect_subdoc_trigger_qualifying_word_shape():
    assert detect_subdoc_trigger("Price Schedule", []) == ("Schedule", False)
    assert detect_subdoc_trigger("Work Schedule:", []) == ("Schedule", False)
    assert detect_subdoc_trigger("Delivery Schedule for widgets", []) == ("Schedule", False)
    assert detect_subdoc_trigger("SCHEDULE-1A", []) == ("Schedule", True)
    assert detect_subdoc_trigger("ANNEXURE A-9", []) is None  # numbered — handled elsewhere entirely


# ─── 1.2: "Versus" is never a heading ──────────────────────────────────────


def test_versus_never_opens_a_container_in_any_case_caption():
    page_text = (
        "MEMO OF PARTIES\n\n"
        "Some Corp Ltd.\n\n"
        "Versus\n\n"
        "1. Some Other Party.\n"
    )
    by_key, scope = _by_key(page_text)
    assert scope["stack"][0]["label"] == "Memo Of Parties"
    # "Versus" produced no container/label of its own anywhere.
    assert not any(c.get("own_label") == "Versus" for c in by_key.values())


# ─── 1.3: general text-shape detector for open-vocabulary headings ─────────


def test_generic_heading_shape_detected_for_never_before_seen_labels():
    """Synthetic labels this exact detector has never seen, proving the
    fix generalizes rather than hardcoding this document's vocabulary."""
    page_text = (
        "Warranty Obligations:\n\n"
        "The supplier warrants the goods for 12 months.\n\n"
        "SHIPPING TERMS\n\n"
        "FOB destination applies to all shipments.\n"
    )
    keys, _ = _keys(page_text)
    labels = set()
    candidates, _ = build_targets_for_page(page_text, seed_scope=None, has_next_page=False)
    for c in candidates:
        if c["heading_confidence_reasons"] == ["generic_heading_shape"]:
            labels.add(c["own_label"])
    assert "Warranty Obligations" in labels
    assert "SHIPPING TERMS" in labels


def test_generic_heading_shape_never_catches_a_long_body_sentence():
    page_text = "This is a perfectly ordinary sentence of body text describing something in detail.\n"
    candidates, _ = build_targets_for_page(page_text, seed_scope=None, has_next_page=False)
    assert candidates == []


def test_generic_heading_shape_is_a_leaf_never_an_ancestor():
    """Even a real generic-heading catch never becomes a stack-mutating
    ancestor — it's always additive, recall-biased, never destructive."""
    page_text = (
        "3.0 CONDITIONS AND COVENANTS\n\n"
        "Insurance:\n\n"
        "Some real clause text continues right after.\n"
    )
    _, scope = _by_key(page_text)
    assert [s["key"] for s in scope["stack"]] == ["numheading:3.0"]


# ─── 1.4: sequential letters vs. roman numerals ────────────────────────────


def test_sequential_letters_not_misparsed_as_roman_numerals():
    """The confirmed real bug: "(C)"/"(D)" (3rd/4th item in a plain
    lettered list) were misparsed as roman 100/500; "(A)"/"(B)" (not
    valid roman numerals at all) were silently dropped."""
    page_text = (
        "FACTS\n\n"
        "3. The award has been challenged on the following grounds:\n\n"
        "(A) First ground text.\n\n"
        "(B) Second ground text.\n\n"
        "(C) Third ground text.\n\n"
        "(D) Fourth ground text.\n\n"
        "(E) Fifth ground text.\n"
    )
    keys, _ = _keys(page_text)
    assert keys == ["point:3", "point:a", "point:b", "point:c", "point:d", "point:e"]


def test_genuine_roman_sequence_still_parses_as_roman():
    """Regression guard: a real roman-numeral ground sequence (always
    multi-character past "I" in practice — II, III, IV, ... — but tested
    here through the ambiguous single-character values too) must be
    completely unaffected by the sequential-letter fix."""
    page_text = (
        "GROUNDS OF APPEAL\n\n"
        "(I) First ground.\n\n"
        "(II) Second ground.\n\n"
        "(III) Third ground.\n\n"
        "(IV) Fourth ground.\n\n"
        "(V) Fifth ground.\n"
    )
    keys, _ = _keys(page_text)
    assert keys == ["ground:i", "ground:ii", "ground:iii", "ground:iv", "ground:v"]


# ─── 1.5: lowercase roman sub-list nested under a numbered clause ─────────


def test_lowercase_roman_sublist_nests_under_the_numbered_clause():
    """The confirmed real case: sub-items i) through iii) under clause
    2.1 were entirely invisible — not matched by any existing pattern
    (GROUND_ROMAN_RE is uppercase/either-paren-or-period only)."""
    page_text = (
        "2.0 CONTRACT DOCUMENTS\n\n"
        "2.1 The contract shall be performed as per the following documents.\n\n"
        "i) HVPNL bidding document No. REC-064.\n\n"
        "ii) HVPN Technical Specification No. REC-064.\n\n"
        "iii) M/s Areva proposal No. EI070151.\n"
    )
    by_key, _ = _by_key(page_text)
    for k, label_fragment in [
        ("subpoint:1", "i) HVPNL bidding document"),
        ("subpoint:2", "ii) HVPN Technical Specification"),
        ("subpoint:3", "iii) M/s Areva proposal"),
    ]:
        assert by_key[k]["scope_key"] == "numheading:2.0/numheading:2.1", k
        assert by_key[k]["own_label"].startswith(label_fragment), k


def test_lowercase_roman_sublist_is_a_leaf_never_an_ancestor():
    page_text = (
        "2.0 CONTRACT DOCUMENTS\n\n"
        "2.1 As per the following documents.\n\n"
        "i) First document.\n\n"
        "3.0 CONDITIONS AND COVENANTS\n\n"
        "Some real clause text.\n"
    )
    by_key, scope = _by_key(page_text)
    assert by_key["numheading:3.0"]["scope_key"] == ""  # sibling of 2.0, not nested under subpoint:1
    assert [s["key"] for s in scope["stack"]] == ["numheading:3.0"]


def test_lowercase_roman_only_fires_directly_under_a_numheading():
    """No false positives outside the intended context — a lowercase
    roman-shaped line with nothing numbered open at all stays unmatched."""
    page_text = "i) This should not be treated as a subpoint at all.\n"
    keys, _ = _keys(page_text)
    assert keys == []


# ─── 1.6: ground/point numbering-scheme transitions signal nesting ────────


def test_roman_ground_continuing_a_decimal_one_nests_as_its_child():
    """The confirmed real case: grounds 1-9 given as plain decimals, then
    ground 10 introduces roman-numeral sub-grounds (XI)-(XIII) — those
    must nest under ground 10 specifically, not continue as flat
    siblings of it."""
    page_text = (
        "GROUNDS OF APPEAL\n\n"
        "9. That the ninth ground.\n\n"
        "10. That the tenth ground introduces the following sub-grounds.\n\n"
        "(XI) First sub-ground under ground 10.\n\n"
        "(XII) Second sub-ground under ground 10.\n\n"
        "(XIII) Third sub-ground under ground 10.\n"
    )
    by_key, _ = _by_key(page_text)
    assert by_key["ground:9"]["scope_key"] == "subdoc:1"
    assert by_key["ground:10"]["scope_key"] == "subdoc:1"
    for k in ("ground:xi", "ground:xii", "ground:xiii"):
        assert by_key[k]["scope_key"] == "subdoc:1/ground:10", k
        assert by_key[k]["scope_label"] == "Grounds Of Appeal › Ground 10", k


def test_sequential_letter_ground_continuing_a_decimal_one_nests_as_its_child():
    """A sequential-letter list (not a genuine roman numeral) continuing a
    decimal ground/point is just as much a numbering-style transition as a
    roman-numeral continuation, and must nest the same way: grounds 9-10
    are plain decimals, ground 10 introduces the list, and (A)-(C) must
    nest under ground 10 specifically, not stay flat siblings of it."""
    page_text = (
        "GROUNDS OF APPEAL\n\n"
        "9. That the ninth ground.\n\n"
        "10. That the tenth ground introduces the following sub-grounds on following grounds:\n\n"
        "(A) First sub-ground under ground 10.\n\n"
        "(B) Second sub-ground under ground 10.\n\n"
        "(C) Third sub-ground under ground 10.\n"
    )
    by_key, _ = _by_key(page_text)
    assert by_key["ground:9"]["scope_key"] == "subdoc:1"
    assert by_key["ground:10"]["scope_key"] == "subdoc:1"
    for k in ("ground:a", "ground:b", "ground:c"):
        assert by_key[k]["scope_key"] == "subdoc:1/ground:10", k
        assert by_key[k]["scope_label"] == "Grounds Of Appeal › Ground 10", k


def test_roman_grounds_overlapping_decimal_values_do_not_collide_and_arabic_resumes_as_sibling():
    """Direct repro of the confirmed real bug (job bb417471's actual
    "Grounds of Appeal"): decimal grounds 1-10 are the background-fact
    recital, ground 10 introduces roman sub-grounds (I)-(X) — whose VALUES
    overlap the preceding decimals 1-10 exactly — and a final decimal "11."
    resumes as a sibling of "10.", not a child of the roman sub-list. Before
    the fix, roman (I)-(X) decimal-converted to the same keys as the real
    decimal grounds 1-10, silently overwriting/duplicating them."""
    page_text = (
        "GROUNDS OF APPEAL\n\n"
        "1. First background fact.\n\n"
        "2. Second background fact.\n\n"
        "3. Third background fact.\n\n"
        "4. Fourth background fact.\n\n"
        "5. Fifth background fact.\n\n"
        "6. Sixth background fact.\n\n"
        "7. Seventh background fact.\n\n"
        "8. Eighth background fact.\n\n"
        "9. Ninth background fact.\n\n"
        "10. Tenth background fact, introducing the following grounds:-\n\n"
        "(I) First actual ground.\n\n"
        "(X) Tenth actual ground.\n\n"
        "11. That the appellant has not filed a similar appeal before.\n"
    )
    by_key, _ = _by_key(page_text)
    # The decimal background facts and the roman grounds of the same value
    # are distinct rows, neither overwriting the other.
    assert "First background fact" in by_key["ground:1"]["snippet"]
    assert "First actual ground" in by_key["ground:i"]["snippet"]
    assert "Tenth actual ground" in by_key["ground:x"]["snippet"]
    # Roman sub-grounds nest under decimal ground 10 specifically.
    assert by_key["ground:i"]["scope_key"] == "subdoc:1/ground:10"
    assert by_key["ground:x"]["scope_key"] == "subdoc:1/ground:10"
    # The next decimal item resumes as a sibling of ground 10, not a child
    # of the roman sub-list.
    assert by_key["ground:11"]["scope_key"] == "subdoc:1"


def test_flat_decimal_grounds_with_no_roman_transition_are_unaffected():
    """Regression guard: a document with only decimal grounds (no roman
    transition at all) must stay completely flat, exactly as before."""
    page_text = (
        "GROUNDS OF APPEAL\n\n"
        "1. First ground.\n\n"
        "2. Second ground.\n\n"
        "3. Third ground.\n"
    )
    by_key, _ = _by_key(page_text)
    for k in ("ground:1", "ground:2", "ground:3"):
        assert by_key[k]["scope_key"] == "subdoc:1", k


def test_a_new_real_boundary_resets_the_ground_roman_nesting_state():
    """A genuine new sub-document boundary between the decimal ground and
    a later roman numeral must NOT nest the roman item under the earlier,
    now-unrelated decimal ground from a different section entirely."""
    page_text = (
        "GROUNDS OF APPEAL\n\n"
        "10. That the tenth ground.\n\n"
        "AFFIDAVIT OF JOHN DOE\n\n"
        "(I) An unrelated roman-numbered item in a totally different context.\n"
    )
    by_key, _ = _by_key(page_text)
    assert by_key["ground:10"]["scope_key"] == "subdoc:1"
    assert by_key["point:i"]["scope_key"] == "subdoc:2"  # NOT nested under ground:10


# ─── 1.7: quotation signal — soft flag, never a hard exclude ──────────────


def test_reproduced_clause_text_flagged_low_confidence_not_a_hard_exclude():
    """The confirmed real bug: clause "9.0 Local/Site conditions" reproduced
    inside Ground XI's own argument was treated as a real new heading, and
    the NEXT real ground wrongly nested under it. Must still be persisted
    (recall-biased — a human can judge it) but never become an ancestor."""
    page_text = (
        "GROUNDS OF APPEAL\n\n"
        "(XI) That the tribunal erred in holding that respondent no.1 was "
        "entitled to an extension, as per clause 9.0 ITB, Vol-II, Contract, "
        "which reads as under:\n\n"
        "9.0 Local/ Site conditions\n\n"
        "9.1 It will be imperative on each Bidder to inform himself.\n\n"
        "(XII) That furthermore, this finding is incorrect.\n"
    )
    by_key, scope = _by_key(page_text)
    quoted = by_key["numheading:9.0"]
    assert quoted["heading_confidence"] == "low"
    assert "possible_reproduced_text" in quoted["heading_confidence_reasons"]
    assert quoted["scope_label"] == "Grounds Of Appeal"  # never anchored
    # The next real ground is a sibling of Ground XI, NOT a child of the quote.
    assert by_key["ground:xii"]["scope_label"] == "Grounds Of Appeal"


def test_reproduced_text_continuation_line_also_suppressed_when_detected_as_its_own_heading():
    """AI-path-mirroring regression: when a quote's OWN continuation line
    (not just its first line) does get detected as its own heading
    candidate — here forced via a short, title-shaped continuation rather
    than a full sentence, so the rule engine's decimal-heading matcher
    picks it up the way the AI's own judgment did in the real bug — it must
    inherit possible_reproduced_text too (sharing the same leading segment
    as the already-flagged line before it), not become a real ancestor that
    resets last_decimal_ground for every roman ground after it."""
    page_text = (
        "GROUNDS OF APPEAL\n\n"
        "10. That the tenth ground introduces the following sub-grounds.\n\n"
        "(XI) That the tribunal erred, as per clause 9.0 ITB, Vol-II, "
        "Contract, which reads as under:\n\n"
        "9.0 Local/ Site conditions\n\n"
        "9.1 Definitions\n\n"
        "(XII) That furthermore, this finding is incorrect.\n"
    )
    by_key, _ = _by_key(page_text)
    assert "possible_reproduced_text" in by_key["numheading:9.0"]["heading_confidence_reasons"]
    assert "possible_reproduced_text" in by_key["numheading:9.1"]["heading_confidence_reasons"]
    assert by_key["numheading:9.1"]["scope_label"] == "Grounds Of Appeal"  # never anchored either
    # ground:10 must still be the nesting parent all the way through XII.
    assert by_key["ground:xi"]["scope_key"] == "subdoc:1/ground:10"
    assert by_key["ground:xii"]["scope_key"] == "subdoc:1/ground:10"


def test_reproduced_text_nesting_survives_even_without_the_continuation_matching_a_heading_shape():
    """The real document's actual "9.1 It will be imperative..." continuation
    (a full sentence, not title-shaped) is never even detected as its own
    heading candidate by the rule engine's decimal-heading matcher — so
    ground:10 stays the nesting parent regardless, without needing the
    continuation signal at all in this specific shape."""
    page_text = (
        "GROUNDS OF APPEAL\n\n"
        "10. That the tenth ground introduces the following sub-grounds.\n\n"
        "(XI) That the tribunal erred, as per clause 9.0 ITB, Vol-II, "
        "Contract, which reads as under:\n\n"
        "9.0 Local/ Site conditions\n\n"
        "9.1 It will be imperative on each Bidder to inform himself.\n\n"
        "(XII) That furthermore, this finding is incorrect.\n"
    )
    by_key, _ = _by_key(page_text)
    assert "numheading:9.1" not in by_key
    assert by_key["ground:xi"]["scope_key"] == "subdoc:1/ground:10"
    assert by_key["ground:xii"]["scope_key"] == "subdoc:1/ground:10"


def test_reproduced_text_signal_uses_general_phrasing_not_this_clause_number():
    """A synthetic example with a different clause number and wording,
    proving the signal is a general phrasing pattern, not specific to
    "clause 9.0" or this document."""
    page_text = (
        "GROUNDS OF APPEAL\n\n"
        "(I) That the agreement reproduced the definition, as per clause "
        "5.2 of the Master Agreement, which reads as under:\n\n"
        "5.2 Definitions\n\n"
        "(II) That the second ground is unrelated.\n"
    )
    by_key, _ = _by_key(page_text)
    quoted = by_key["numheading:5.2"]
    assert "possible_reproduced_text" in quoted["heading_confidence_reasons"]
    assert by_key["ground:ii"]["scope_label"] == "Grounds Of Appeal"


def test_a_real_non_quoted_decimal_heading_is_unaffected():
    page_text = (
        "GROUNDS OF APPEAL\n\n"
        "(I) That the tribunal erred in fact.\n\n"
        "3.0 CONDITIONS AND COVENANTS\n\n"
        "Some real clause text.\n"
    )
    by_key, _ = _by_key(page_text)
    assert by_key["numheading:3.0"]["heading_confidence"] == "high"
    assert by_key["numheading:3.0"]["heading_confidence_reasons"] == []


def test_letter_marker_with_redundant_trailing_period_still_matches_and_stays_flat():
    """Confirmed real bug (bb417471's "(XIV)." and 9c3dc6e6's "(N)."): a
    redundant trailing period after the closing parenthesis was not
    accepted by any of the ground/point regexes, so that one item fell
    through to the generic subdoc-container bucket and the next letter
    nested under it instead of staying a flat sibling. "(N)." must resolve
    exactly like "(N)" or "N." alone, and "(O)" right after it must stay
    flat too."""
    page_text = (
        "GROUNDS OF APPEAL\n\n"
        "9. That the ninth ground.\n\n"
        "10. That the tenth ground introduces the following sub-grounds:\n\n"
        "(N). Fourteenth sub-ground under ground 10.\n\n"
        "(O) Fifteenth sub-ground under ground 10.\n"
    )
    by_key, _ = _by_key(page_text)
    for k in ("ground:n", "ground:o"):
        assert by_key[k]["scope_key"] == "subdoc:1/ground:10", k


def test_roman_sequence_starting_mid_alphabet_stays_roman():
    """A roman sequence that starts at a multi-character numeral (as the
    real document does — grounds 1-10 as decimal, then (XI)-(XXII) as
    roman) is never ambiguous with a lettered list at all."""
    page_text = (
        "GROUNDS OF APPEAL\n\n"
        "10. Tenth ground, decimal style.\n\n"
        "(XI) Eleventh ground, roman style.\n\n"
        "(XII) Twelfth ground.\n"
    )
    keys, _ = _keys(page_text)
    assert keys == ["ground:10", "ground:xi", "ground:xii"]
