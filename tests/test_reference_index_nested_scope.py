"""Regression tests for the unified containment hierarchy in
reference_index.py — a single _HIERARCHY_RANK table (replacing the old
article/section-only _SCOPE_RANK plus a separate subdoc_counter/subdoc_label
mechanism) so scope_key/scope_label/scope_chain are always the full ancestor
chain, including the sub-document tier itself, with no parallel bookkeeping."""

from reference_index import build_targets_for_page


def _by_key(page_text, seed_scope=None):
    candidates, new_scope = build_targets_for_page(page_text, seed_scope=seed_scope, has_next_page=False)
    return {c["key"]: c for c in candidates}, new_scope


def test_full_chain_appears_in_scope_key_and_label():
    page_text = (
        "Article 2. Payment Terms\n\n"
        "Section 3. Timing\n\n"
        "Clause 4.2 The contractor shall invoice monthly.\n"
    )
    by_key, _ = _by_key(page_text)
    clause = by_key["clause:4.2"]
    assert clause["scope_key"] == "article:2/section:3"
    assert clause["scope_label"] == "Article 2 › Section 3"


def test_scope_chain_carries_ancestors_only_not_self():
    page_text = (
        "Article 2. Payment Terms\n\n"
        "Section 3. Timing\n\n"
        "Clause 4.2 The contractor shall invoice monthly.\n"
    )
    by_key, _ = _by_key(page_text)
    clause = by_key["clause:4.2"]
    assert clause["scope_chain"] == [
        {"family": "article", "key": "article:2", "label": "Article 2"},
        {"family": "section", "key": "section:3", "label": "Section 3"},
    ]
    # The container's own row only carries chain *above* itself.
    section = by_key["section:3"]
    assert section["scope_chain"] == [{"family": "article", "key": "article:2", "label": "Article 2"}]
    article = by_key["article:2"]
    assert article["scope_chain"] == []


def test_same_section_number_under_different_articles_gets_different_scope_key():
    page_text = (
        "Article 2. Payment Terms\n\n"
        "Section 3. Timing\n\n"
        "Clause 4.1 First clause.\n\n"
        "Article 5. Termination\n\n"
        "Section 3. Notice\n\n"
        "Clause 4.1 Second clause with same number.\n"
    )
    candidates, _ = build_targets_for_page(page_text, seed_scope=None, has_next_page=False)
    clause_rows = [c for c in candidates if c["key"] == "clause:4.1"]
    assert len(clause_rows) == 2
    assert clause_rows[0]["scope_key"] != clause_rows[1]["scope_key"]
    assert clause_rows[0]["scope_label"] == "Article 2 › Section 3"
    assert clause_rows[1]["scope_label"] == "Article 5 › Section 3"


def test_a_replacing_sibling_container_does_not_show_the_replaced_one_as_its_own_ancestor():
    """Regression test for an ordering bug found while writing this suite:
    Article 5's own candidate row must NOT show Article 2 (and its Section 3)
    as its ancestor just because they were still on the stack the instant
    before Article 5 popped them off — its own scope must reflect the stack
    *after* the pop, not before."""
    page_text = (
        "Article 2. Payment Terms\n\n"
        "Section 3. Timing\n\n"
        "Article 5. Termination\n\n"
        "Clause 9 Under the new article only.\n"
    )
    by_key, _ = _by_key(page_text)
    assert by_key["article:5"]["scope_chain"] == []
    assert by_key["article:5"]["scope_key"] == ""
    assert by_key["clause:9"]["scope_chain"] == [{"family": "article", "key": "article:5", "label": "Article 5"}]


def test_single_level_nesting_has_no_leading_counter_or_separator():
    page_text = (
        "Article 2. Payment Terms\n\n"
        "Clause 4.2 The contractor shall invoice monthly.\n"
    )
    by_key, _ = _by_key(page_text)
    clause = by_key["clause:4.2"]
    assert clause["scope_key"] == "article:2"
    assert clause["scope_label"] == "Article 2"


def test_bare_target_before_any_ancestor_has_empty_scope_key_and_label():
    page_text = "Clause 1 A bare clause with nothing above it.\n"
    by_key, _ = _by_key(page_text)
    clause = by_key["clause:1"]
    assert clause["scope_key"] == ""
    assert clause["scope_label"] == ""
    assert clause["scope_chain"] == []


def test_numbered_subdoc_family_preserves_its_letter_prefix_in_the_label():
    """Regression test: an earlier version derived a container's push label
    from `family` + `number_raw` alone, discarding `prefix` — dormant while
    only article/section (never prefix-eligible) were containers, but
    activated the moment annexure/schedule/appendix joined _HIERARCHY_RANK.
    "Annexure A-1" must not become "Annexure 1"."""
    page_text = (
        "ANNEXURE A-1: SERVICE AGREEMENT\n\n"
        "Clause 4.2 The contractor shall invoice monthly.\n"
    )
    by_key, _ = _by_key(page_text)
    assert by_key["clause:4.2"]["scope_chain"] == [
        {"family": "annexure", "key": "annexure:a-1", "label": "Annexure A-1"}
    ]


def test_schedule_after_annexure_is_a_sibling_not_nested_inside_it():
    page_text = (
        "ANNEXURE A-1: SERVICE AGREEMENT\n\n"
        "Clause 1 Under the annexure.\n\n"
        "SCHEDULE 2: PRICING\n\n"
        "Clause 2 Under the schedule.\n"
    )
    by_key, _ = _by_key(page_text)
    assert by_key["clause:1"]["scope_chain"] == [{"family": "annexure", "key": "annexure:a-1", "label": "Annexure A-1"}]
    # Schedule is rank 0, same as Annexure — it replaces it, not nests under it.
    assert by_key["schedule:2"]["scope_chain"] == []
    assert by_key["clause:2"]["scope_chain"] == [{"family": "schedule", "key": "schedule:2", "label": "Schedule 2"}]


def test_generic_trigger_closes_the_open_accumulator_instead_of_swallowing_it():
    """Regression test: an earlier version never finalized on a generic
    (unnumbered) trigger, so an AFFIDAVIT/JUDGMENT heading with no
    accompanying keyword match fell through to the open-accumulator append
    tail and got silently absorbed into the previous clause's snippet."""
    page_text = (
        "Clause 1 First clause before any boundary appears.\n\n"
        "AFFIDAVIT OF JOHN DOE\n\n"
        "Clause 2 After the affidavit.\n"
    )
    by_key, _ = _by_key(page_text)
    assert "AFFIDAVIT" not in by_key["clause:1"]["snippet"].upper()
    assert by_key["clause:2"]["scope_chain"] == [{"family": "subdoc", "key": "subdoc:affidavit", "label": "Affidavit"}]


def test_repeated_generic_trigger_gets_a_disambiguating_label():
    page_text = (
        "AFFIDAVIT OF JOHN DOE\n\n"
        "Clause 1 Under the first affidavit.\n\n"
        "AFFIDAVIT OF JANE DOE\n\n"
        "Clause 2 Under the second affidavit.\n"
    )
    by_key, _ = _by_key(page_text)
    assert by_key["clause:1"]["scope_chain"][0]["label"] == "Affidavit"
    assert by_key["clause:2"]["scope_chain"][0]["label"] == "Affidavit (2)"
    assert by_key["clause:1"]["scope_chain"][0]["key"] != by_key["clause:2"]["scope_chain"][0]["key"]


def test_case_caption_trigger_synthesizes_label_from_the_distinguishing_line_that_follows():
    """The case-caption trigger itself ("In the High Court of...") carries
    no distinguishing text of its own — the SAME caption legitimately
    repeats before several different pleadings in one paper-book (real
    example: the same court-caption block opens three different pleadings
    on consecutive pages of one paper-book). The label must come from
    whichever later block actually distinguishes this occurrence, not the
    generic caption phrase."""
    page_text = (
        "In the High Court of Punjab and Haryana at Chandigarh\n\n"
        "Civil Misc. No. 63-CARB/2019\n\n"
        "In F.A.O. (Comm.Arb.) 23 2019\n\n"
        "Clause 1 Under this application.\n"
    )
    by_key, _ = _by_key(page_text)
    assert by_key["clause:1"]["scope_chain"][0]["label"] == "Civil Misc. No. 63-carb/2019"


def test_facts_and_prayer_bare_headings_open_a_new_subdoc_boundary():
    by_key, _ = _by_key("FACTS\n\nClause 1 The petitioner invited tenders.\n")
    assert by_key["clause:1"]["scope_chain"][0]["label"] == "Facts"

    by_key, _ = _by_key("PRAYER\n\nClause 1 It is prayed that the appeal be allowed.\n")
    assert by_key["clause:1"]["scope_chain"][0]["label"] == "Prayer"


def test_grounds_of_appeal_still_activates_ground_scope_detection():
    page_text = (
        "GROUNDS OF APPEAL\n\n"
        "1. That the findings recorded below are perverse.\n\n"
        "2. That the learned court erred in fact.\n"
    )
    by_key, _ = _by_key(page_text)
    assert "ground:1" in by_key
    assert "ground:2" in by_key
