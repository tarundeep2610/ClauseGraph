"""Regression tests for two structure-detection gaps found on a real,
completed job (b6a06baf, a 6-page contract excerpt) whose "Rebuild
Cross-References" produced zero real headings:

1. Fuzzy keyword matching (`_fuzzy_family_match`) - recovers a real family
   match from an OCR-garbled keyword ("AmneXureA-1" -> annexure:a-1)
   before falling through to anything else.
2. Decimal-heading detection/nesting (`DECIMAL_HEADING_RE` +
   build_targets_for_page's numbered-heading branch) - a bare
   decimal-numbered heading ("1. Article", "3.3 TIME SCHEDULE") with no
   family keyword at all, nested by its own number's segments, not by
   position or an assumed "current depth + 1".

Both interact with the pre-existing `detect_subdoc_trigger` mechanism in
ways that would otherwise hijack the tree - see the two
`test_detect_subdoc_trigger_*` tests below, which are direct regression
tests for real hijacks found while tracing this exact job page by page.

The `_REAL_PAGES` literal below is this job's own real `text` +
`raw_ocr_text` for all 6 pages, pulled directly from the database - not
synthetic fixtures - so the end-to-end test replays the actual regression,
not a simplified stand-in for it."""

from reference_index import (
    DECIMAL_HEADING_RE,
    _fuzzy_family_match,
    build_targets_for_page,
    detect_subdoc_trigger,
    is_subdoc_root_candidate,
    mint_key_for_heading,
    normalize_key,
    reconcile_prefix_and_number,
)


# ─── Section 1: fuzzy keyword matching ─────────────────────────────────────

def test_fuzzy_match_recovers_ocr_garbled_annexure_with_correct_prefix():
    # The real regression: an earlier version of this logic discarded the
    # trailing "A" when the trimmed candidate won the ratio race, producing
    # "annexure:-1" instead of "annexure:a-1".
    family, prefix, number_raw = _fuzzy_family_match("AmneXureA-1")
    assert family == "annexure"
    assert prefix == "A-"  # trailing "-" kept, same shape as CORE_RE's own
    # prefix group ([A-Z]-?) - normalize_key()/reconcile_prefix_and_number()
    # already strip it when building the final key ("annexure:a-1").
    assert number_raw == "1"


def test_fuzzy_match_end_to_end_key_and_label_are_the_clean_spelling():
    page_text = "AmneXureA-1\n\nSome body text.\n"
    candidates, _ = build_targets_for_page(page_text, seed_scope=None, has_next_page=False)
    assert len(candidates) == 1
    row = candidates[0]
    assert row["key"] == "annexure:a-1"
    assert row["heading_confidence"] == "low"
    assert "fuzzy_keyword_match" in row["heading_confidence_reasons"]


def test_fuzzy_match_rejects_unrelated_word():
    assert _fuzzy_family_match("Signature-1") is None


def test_exact_match_still_takes_priority_over_fuzzy():
    page_text = "Annexure A-1\n\nSome body text.\n"
    candidates, _ = build_targets_for_page(page_text, seed_scope=None, has_next_page=False)
    row = candidates[0]
    assert row["key"] == "annexure:a-1"
    assert row["heading_confidence"] == "high"
    assert row["heading_confidence_reasons"] == []


def test_reconcile_prefix_and_number_recombines_a_wrongly_split_roman_numeral():
    # Confirmed real bug: CORE_RE's optional prefix group is greedy with no
    # hyphen requirement, so on "XI" it wrongly peels off "X" as a one-letter
    # exhibit prefix, leaving "I" as the number.
    assert reconcile_prefix_and_number("Annexure", "X", "I") == (None, "XI")
    # Control: a genuine hyphenated exhibit prefix ("Annexure A-1") must be
    # completely unaffected by the new recombination guard.
    assert reconcile_prefix_and_number("Annexure", "A-", "1") == ("A-", "1")


def test_annexure_space_and_hyphen_spellings_mint_the_identical_key():
    space_candidates, _ = build_targets_for_page("Annexure XI: Service Level Agreement\n", seed_scope=None, has_next_page=False)
    hyphen_candidates, _ = build_targets_for_page("Annexure-XI: Service Level Agreement\n", seed_scope=None, has_next_page=False)
    assert space_candidates[0]["key"] == "annexure:xi"
    assert hyphen_candidates[0]["key"] == "annexure:xi"


def test_mint_key_for_heading_annexure_space_and_hyphen_spellings_match():
    space = mint_key_for_heading("Annexure XI: Service Level Agreement", boundary_ordinal=0, label_occurrence_counts={})
    hyphen = mint_key_for_heading("Annexure-XI: Service Level Agreement", boundary_ordinal=0, label_occurrence_counts={})
    assert space[0] == "annexure"
    assert space[1] == "annexure:xi"
    assert hyphen[0] == "annexure"
    assert hyphen[1] == "annexure:xi"


def test_annexure_plain_multichar_roman_with_no_suffix():
    candidates, _ = build_targets_for_page("Annexure XVII: Final Clause\n", seed_scope=None, has_next_page=False)
    assert candidates[0]["key"] == "annexure:xvii"


def test_annexure_lettered_suffix_both_spellings_match():
    space_candidates, _ = build_targets_for_page("Annexure XIII-A: GIS Geoportal\n", seed_scope=None, has_next_page=False)
    hyphen_candidates, _ = build_targets_for_page("Annexure-XIII-A: GIS Geoportal\n", seed_scope=None, has_next_page=False)
    assert space_candidates[0]["key"] == "annexure:xiii-a"
    assert hyphen_candidates[0]["key"] == "annexure:xiii-a"


def test_annexure_with_and_without_suffix_do_not_collide():
    # Confirmed real collision risk: the tender-spec fixture genuinely has
    # BOTH a plain "Annexure XIII" and a distinct "Annexure XIII-A" heading
    # — fixing the roman-split bug alone would make both mint "annexure:xiii"
    # without the trailing-suffix capture.
    plain_candidates, _ = build_targets_for_page("Annexure XIII: ERP Modules\n", seed_scope=None, has_next_page=False)
    suffixed_candidates, _ = build_targets_for_page("Annexure XIII-A: GIS Geoportal\n", seed_scope=None, has_next_page=False)
    assert plain_candidates[0]["key"] == "annexure:xiii"
    assert suffixed_candidates[0]["key"] == "annexure:xiii-a"
    assert plain_candidates[0]["key"] != suffixed_candidates[0]["key"]


def test_detect_subdoc_trigger_defers_for_hyphenated_annexure_same_as_space():
    assert detect_subdoc_trigger("Annexure-XI:", []) is None
    assert detect_subdoc_trigger("Annexure XI:", []) is None


def test_section_space_and_hyphen_spellings_mint_the_identical_key():
    # Real regression: a 135-page tender fixture spells Section I-VI with a
    # hyphen ("Section-I:") but Section VII with a space ("Section VII:") —
    # only the space form minted as the numbered `section:N` family; the
    # hyphen form fell through to a generic subdoc fallback, breaking every
    # in-document citation that expected a `section:N` target to exist.
    space_candidates, _ = build_targets_for_page("Section VII: General Terms\n", seed_scope=None, has_next_page=False)
    hyphen_candidates, _ = build_targets_for_page("Section-I: Essential Tender Information\n", seed_scope=None, has_next_page=False)
    assert space_candidates[0]["key"] == "section:7"
    assert hyphen_candidates[0]["key"] == "section:1"


def test_mint_key_for_heading_section_space_and_hyphen_spellings_match():
    space = mint_key_for_heading("Section VII: General Terms", boundary_ordinal=0, label_occurrence_counts={})
    hyphen = mint_key_for_heading("Section-I: Essential Tender Information", boundary_ordinal=0, label_occurrence_counts={})
    assert space[0] == "section"
    assert space[1] == "section:7"
    assert hyphen[0] == "section"
    assert hyphen[1] == "section:1"


def test_section_hyphen_acronym_is_not_mistaken_for_a_roman_numeral():
    # Real OCR regression: "SECTION-GCC" (General Conditions of Contract,
    # an acronym with no number) immediately precedes "A. INTRODUCTION" on
    # a real 7-page contract excerpt. Before requiring a literal hyphen
    # after any prefix letter, "G" silently absorbed as a bare prefix and
    # "CC" parsed as a roman numeral (100+100), wrongly minting a
    # `section:*` container that swallowed "A. INTRODUCTION" as its child.
    candidates, _ = build_targets_for_page("SECTION-GCC\n\nGENERAL CONDITIONS OF CONTRACT\n\nA. INTRODUCTION\n", seed_scope=None, has_next_page=False)
    assert not any(c["key"].startswith("section:") for c in candidates)


def test_section_hyphen_sub_clause_not_wrongly_clamped_as_new_root():
    # A genuinely nested child sharing the root's own number ("Section-I.1")
    # must not be mistaken for a new top-level Section root now that the
    # hyphen form is recognized.
    assert is_subdoc_root_candidate("Section-I.1: Sub-clause", family=None, root_label="Section-I: Essential Tender Information") is False


def test_schedule_variant_non_regression_after_hyphen_annexure_fix():
    # Non-regression guard: SCHEDULE-1A's number group can't cross the
    # digit->letter word boundary, so the new hyphen regex correctly
    # declines to match this shape — unaffected by this fix.
    result = detect_subdoc_trigger("SCHEDULE-1A", [])
    assert result is not None
    assert result[0] == "Schedule"
    assert result[1] is True


def test_normalize_key_article_roman_still_converts_to_arabic():
    # Confirms the roman-literal-preservation branch is correctly scoped to
    # the prefix-eligible families only (annexure/schedule/appendix) —
    # Article/Section's existing arabic-conversion behavior is untouched.
    assert normalize_key("Article", "I", "I") == "article:2"


def test_fuzzy_matched_container_still_anchors_later_headings():
    """The push must not be blocked just because 'fuzzy_keyword_match' was
    added to confidence_reasons - fuzzy uncertainty is about spelling, not
    about whether this is really a heading, unlike a citation-shaped false
    positive (which correctly still blocks the push)."""
    page_text = "AmneXureA-1\n\nArticle 2. Payment Terms\n"
    candidates, _ = build_targets_for_page(page_text, seed_scope=None, has_next_page=False)
    by_key = {c["key"]: c for c in candidates}
    assert by_key["article:2"]["scope_label"] == "Annexure A-1"


# ─── Section 2: decimal-heading detection & nesting ────────────────────────

def test_decimal_heading_regex_catches_title_case_and_all_caps():
    assert DECIMAL_HEADING_RE.match("1. Article")
    assert DECIMAL_HEADING_RE.match("1.1 Award of contract")
    assert DECIMAL_HEADING_RE.match("2.0 CONTRACT DOCUMENTS")


def test_decimal_heading_regex_rejects_long_body_paragraphs():
    assert DECIMAL_HEADING_RE.match(
        "2.1 The contract shall be performed strictly as per the terms and "
        "conditions stipulated herein and in the following documents"
    ) is None
    assert DECIMAL_HEADING_RE.match(
        "3.4.1 The contractor is responsible for the proper execution of "
        "the quality plans."
    ) is None


def test_decimal_heading_nests_child_under_parent_by_number_not_geometry():
    page_text = (
        "1. Article\n\n"
        "Some clause body.\n\n"
        "1.1 Award of contract\n\n"
        "More body text.\n"
    )
    candidates, _ = build_targets_for_page(page_text, seed_scope=None, has_next_page=False)
    by_key = {c["key"]: c for c in candidates}
    assert by_key["numheading:1"]["scope_label"] == ""
    assert by_key["numheading:1.1"]["scope_label"] == "1 Article"


def test_decimal_heading_dot_dot_zero_behaves_as_top_level():
    """"2.0" strips its trailing ".0" segment and behaves as top-level "2" -
    a sibling of "1", not a child of it."""
    page_text = "1. Article\n\n2.0 CONTRACT DOCUMENTS\n\nBody.\n"
    candidates, _ = build_targets_for_page(page_text, seed_scope=None, has_next_page=False)
    by_key = {c["key"]: c for c in candidates}
    assert by_key["numheading:1"]["scope_label"] == ""
    assert by_key["numheading:2.0"]["scope_label"] == ""  # sibling of "1", not its child


def test_decimal_heading_siblings_correctly_pop_deeper_chain():
    page_text = (
        "3.0 CONDITIONS AND COVENANTS\n\n"
        "3.3 TIME SCHEDULE\n\n"
        "3.4 QUALITY PLANS\n\n"
        "4.0 SETTLEMENT OF DISPUTES\n\n"
        "4.1 NOTICE OF DEFAULT\n"
    )
    candidates, _ = build_targets_for_page(page_text, seed_scope=None, has_next_page=False)
    by_key = {c["key"]: c for c in candidates}
    assert by_key["numheading:3.3"]["scope_label"] == "3.0 CONDITIONS AND COVENANTS"
    # 3.4 must be a SIBLING of 3.3 (both under 3.0), not nested under 3.3.
    assert by_key["numheading:3.4"]["scope_label"] == "3.0 CONDITIONS AND COVENANTS"
    # 4.0 pops the entire 3.x chain back to top-level.
    assert by_key["numheading:4.0"]["scope_label"] == ""
    assert by_key["numheading:4.1"]["scope_label"] == "4.0 SETTLEMENT OF DISPUTES"


def test_new_container_clears_numbered_heading_scope():
    """A brand new Annexure obviously starts fresh numbering - a
    numbered-heading entry pushed under the first container must not leak
    into the second, unrelated container's scope_chain (not exercised by
    the real 6-page job, which only ever has one container). Split across
    two page calls (threading seed_scope, like real consecutive pages)
    rather than one page - two same-family triggers on the SAME page would
    instead hit the existing, unrelated same_page_repeat guard."""
    page1 = "Annexure A-1\n\n1.1 Award of contract\n\nSome body text.\n"
    _, scope = build_targets_for_page(page1, seed_scope=None, has_next_page=True)

    page2 = "Annexure A-2\n\nClause 5 Some other clause.\n"
    candidates, _ = build_targets_for_page(page2, seed_scope=scope, has_next_page=False)
    by_key = {c["key"]: c for c in candidates}
    assert by_key["clause:5"]["scope_chain"] == [
        {"family": "annexure", "key": "annexure:a-2", "label": "Annexure A-2"},
    ]


def test_headerless_clause_ignores_incidental_other_doc_mention():
    """A headerless clause that merely mentions another volume/section deep
    in its own body text (real regression: "technical specifications
    (Vol-II)") must not be treated as citation-shaped noise - it still
    correctly anchors and pops its predecessor, unlike a titled match with
    the same mention (see the next test)."""
    page_text = (
        "2.0 CONTRACT DOCUMENTS\n\n"
        "2.1 This clause references the GCC and SCC of Vol-II for details.\n\n"
        "2.2 A further clause.\n"
    )
    candidates, _ = build_targets_for_page(page_text, seed_scope=None, has_next_page=False)
    by_key = {c["key"]: c for c in candidates}
    assert by_key["numheading:2.1"]["heading_confidence_reasons"] == ["headerless_numbered_clause"]
    # 2.1 correctly popped by 2.2 (not left stale as 2.2's ancestor).
    assert by_key["numheading:2.2"]["scope_label"] == "2.0 CONTRACT DOCUMENTS"


def test_titled_match_with_other_doc_mention_is_still_flagged():
    """Regression guard: a titled (non-headerless) match's own
    other_doc_marker/chained_citation flagging is completely untouched by
    the headerless-only exemption above."""
    page_text = (
        "Clause No. 34.0, Sec. GCC, Vol.I and Cl. No. 9 SCC Vol-I.\n\n"
        "(b) Bid Guarantee"
    )
    candidates, _ = build_targets_for_page(page_text, seed_scope=None, has_next_page=False)
    row = {c["key"]: c for c in candidates}["clause:34.0"]
    assert "chained_citation" in row["heading_confidence_reasons"]
    assert "other_doc_marker" in row["heading_confidence_reasons"]


def test_tag_type_gates_decimal_detection_when_layout_aligned():
    """When layout data IS available and aligned, a short digit-led block
    tagged "text" (not "title"/"header") must not become a numbered
    heading - confirms the tag check genuinely gates, not just decorates."""
    page_text = "3.3 TIME SCHEDULE\n"
    raw_text = "<|det|>text [1,1,1,1]<|/det|>3.3 TIME SCHEDULE"
    candidates, _ = build_targets_for_page(page_text, seed_scope=None, has_next_page=False, raw_text=raw_text)
    assert candidates == []


# ─── Section 3: detect_subdoc_trigger fixes ────────────────────────────────

def test_bare_index_fires_with_nothing_open():
    assert detect_subdoc_trigger("INDEX", []) == ("Index", True)


def test_bare_index_does_not_fire_with_annexure_already_open():
    stack = [{"family": "annexure", "key": "annexure:a-1", "label": "Annexure A-1"}]
    assert detect_subdoc_trigger("INDEX", stack) is None


def test_decimal_heading_containing_schedule_never_fires_subdoc_trigger():
    assert detect_subdoc_trigger("3.3 TIME SCHEDULE", []) is None
    stack = [{"family": "annexure", "key": "annexure:a-1", "label": "Annexure A-1"}]
    assert detect_subdoc_trigger("3.3 TIME SCHEDULE", stack) is None


def test_strong_triggers_unaffected_by_stack_state():
    # detect_subdoc_trigger's label is derived from the matched trigger
    # substring only ("AFFIDAVIT" -> "Affidavit"), not the whole block.
    # is_strong stays True here since nothing real qualifies "AFFIDAVIT"
    # itself (the extra text - "of John Doe" - comes AFTER the match, not
    # before it as a leading qualifying word).
    assert detect_subdoc_trigger("AFFIDAVIT of John Doe", []) == ("Affidavit", True)
    stack = [{"family": "annexure", "key": "annexure:a-1", "label": "Annexure A-1"}]
    assert detect_subdoc_trigger("AFFIDAVIT of John Doe", stack) == ("Affidavit", True)


def test_qualified_generic_trigger_is_not_strong():
    """The confirmed real bug: a leading real word before a bare SCHEDULE/
    ANNEXURE/APPENDIX trigger ("Price Schedule", "Work Schedule:") signals
    a minor sub-label, not a real top-level division — must come back
    is_strong=False so the caller never resets the whole stack for it."""
    assert detect_subdoc_trigger("Price Schedule", []) == ("Schedule", False)
    assert detect_subdoc_trigger("Work Schedule:", []) == ("Schedule", False)


def test_standalone_schedule_variants_are_still_strong():
    # The matched trigger text is just "SCHEDULE" either way (the regex
    # doesn't capture a trailing "-1A"/number) - what matters is nothing
    # REAL precedes it, so both stay is_strong=True.
    assert detect_subdoc_trigger("SCHEDULE-1A", []) == ("Schedule", True)
    assert detect_subdoc_trigger("SCHEDULE", []) == ("Schedule", True)


def test_bare_versus_never_fires_a_trigger():
    """"Versus" is party-separator furniture in every case caption, never
    a real structural heading — removed from SUBDOC_TRIGGER_RE entirely."""
    assert detect_subdoc_trigger("Versus", []) is None
    stack = [{"family": "subdoc", "key": "subdoc:1", "label": "Memo Of Parties"}]
    assert detect_subdoc_trigger("Versus", stack) is None


# ─── Section: residual layout-only fallback (no number, no keyword) ───────

def test_residual_layout_fallback_rejects_garbled_stamp_via_alpha_ratio_guard():
    """Real page-1 pair: "AmneXureA-1" resolves via fuzzy matching before
    this fallback is ever reached; the very next header-tagged block on the
    same page, "\\( \\xi a \\)" (garbled stamp notation), must never become
    a node - the direct regression test for the content-quality guard."""
    page_text = "AmneXureA-1\n\n\\( \\xi a \\)\n\nSome real body text.\n"
    raw_text = (
        "<|det|>header [612, 29, 905, 61]<|/det|>AmneXureA-1\n"
        "<|det|>header [882, 79, 955, 132]<|/det|>\\( \\xi a \\)\n"
        "<|det|>text [1,1,1,1]<|/det|>Some real body text."
    )
    candidates, _ = build_targets_for_page(page_text, seed_scope=None, has_next_page=False, raw_text=raw_text)
    keys = [c["key"] for c in candidates]
    assert keys == ["annexure:a-1"]  # never a second node for the garbled stamp


def test_residual_layout_fallback_nests_as_child_of_whatever_is_open():
    # Split across two page calls (threading seed_scope) so "Article 2"
    # pushes cleanly with no raw_text at all - keeps this test isolated to
    # the new residual-fallback logic, not the *pre-existing*,
    # already-tested layout_title/layout_header confidence-reason behavior
    # (see test_reference_index_heading_vs_reference.py) which is a
    # separate, unrelated concern this fix doesn't touch.
    page1 = "Article 2. Payment Terms\n\nSome body text.\n"
    _, scope = build_targets_for_page(page1, seed_scope=None, has_next_page=True)

    page2 = "Some unrelated heading\n"
    raw_text = "<|det|>title [1,1,1,1]<|/det|>Some unrelated heading"
    candidates, _ = build_targets_for_page(page2, seed_scope=scope, has_next_page=False, raw_text=raw_text)
    fallback_rows = [c for c in candidates if c["key"].startswith("subdoc:")]
    assert len(fallback_rows) == 1
    row = fallback_rows[0]
    assert row["scope_label"] == "Article 2"
    assert row["heading_confidence"] == "low"
    assert row["heading_confidence_reasons"] == ["layout_only_no_text_match"]


# ─── End-to-end: this job's own real 6 pages, in page order ───────────────

_REAL_PAGES = [
    {
        "page_num": 1,
        "text": 'AmneXureA-1\n\n\\( \\xi a \\)\n\nCONTRACT AGREEMENT BETWEEN\n\nHARYANA VIDYUT PRASARAN NIGAM LIMITED\n\nAND\n\nM/S AREVA T&D INDIA LIMITED, NOIDA\n\nCONTRACT No. HDP-1484/REC-064/ XEN(PROJECTS)\n\nDATED 20.05.2008\n\nFOR\n\nSUPPLY OF EQUIPMENT FOR TURN-KEY\n\nCONSTRUCTION OF 400 KV SUB-STATION\n\nKIRORI (HISAR)\n\n![](images/0.jpg)\n\n![](images/1.jpg)\n\n---\n*⏱️ **Unlimited-OCR Execution Time:** 3m 34.7s (214.7s)*',
        "raw_ocr_text": '<|det|>header [612, 29, 905, 61]<|/det|>AmneXureA-1\n<|det|>header [882, 79, 955, 132]<|/det|>\\( \\xi a \\)\n<|det|>text [383, 223, 792, 243]<|/det|>CONTRACT AGREEMENT BETWEEN\n<|det|>text [324, 276, 851, 297]<|/det|>HARYANA VIDYUT PRASARAN NIGAM LIMITED\n<|det|>text [559, 332, 614, 349]<|/det|>AND\n<|det|>text [367, 384, 806, 404]<|/det|>M/S AREVA T&D INDIA LIMITED, NOIDA\n<|det|>text [293, 437, 881, 460]<|/det|>CONTRACT No. HDP-1484/REC-064/ XEN(PROJECTS)\n<|det|>text [483, 492, 688, 509]<|/det|>DATED 20.05.2008\n<|det|>text [558, 546, 612, 563]<|/det|>FOR\n<|det|>text [356, 599, 813, 619]<|/det|>SUPPLY OF EQUIPMENT FOR TURN-KEY\n<|det|>text [350, 652, 821, 671]<|/det|>CONSTRUCTION OF 400 KV SUB-STATION\n<|det|>text [495, 706, 672, 725]<|/det|>KIRORI (HISAR)\n<|det|>image [458, 834, 575, 907]<|/det|>\n<|det|>image [782, 791, 938, 895]<|/det|>',
    },
    {
        "page_num": 2,
        "text": '60\n\nINDEX\n\nContract No. HDP-1484/REC-064/Xen(Projects)\n\nDated: 20.05.2008\n\n<table><tr><td>Sr. No.</td><td>Description</td><td>Page No.</td></tr><tr><td>1.</td><td>Contract Agreement</td><td>1-4</td></tr><tr><td>2.</td><td>Performance BG</td><td>5-8</td></tr><tr><td>3.</td><td>Detailed Purchase Order</td><td>9-29</td></tr><tr><td>4.</td><td>Letter of Acceptance</td><td>30</td></tr><tr><td>5.</td><td>Letter of M/s Areva T&amp;D India Ltd. dated 29.04.2008</td><td>31-54</td></tr><tr><td>6.</td><td>Letter of M/s Areva T&amp;D India Ltd. dated 07.04.2007</td><td>55</td></tr><tr><td>7.</td><td>Letter of M/s Areva T&amp;D India Ltd. dated 28.03.2008</td><td>56</td></tr><tr><td>8.</td><td>Letter of M/s Areva T&amp;D India Ltd. dated 02.01.2008</td><td>57</td></tr><tr><td>9.</td><td>Minutes of Meeting dated 27.12.2007</td><td>58-59</td></tr><tr><td>10.</td><td>Letter of M/s Areva T&amp;D India Ltd. dated 27.12.2007</td><td>60-72</td></tr><tr><td>11.</td><td>HVPN letter dated 11.12.2007</td><td>73-77</td></tr><tr><td>12.</td><td>Special Power of Attorney</td><td>78-79</td></tr><tr><td>13.</td><td>Certificate regarding important conditions</td><td>80</td></tr><tr><td>14.</td><td>Letter of undertaking</td><td>81-82</td></tr><tr><td>15.</td><td>Offer of M/s Areva T&amp;D India Ltd. dated 26.11.2007</td><td>83-121</td></tr><tr><td>16.</td><td>Technical Data Sheets</td><td>122-265</td></tr><tr><td>17.</td><td>Notice Inviting Tender</td><td>266-268</td></tr><tr><td>18.</td><td>Instruction to Bidders</td><td>269-288</td></tr><tr><td>19.</td><td>General Conditions of Contract</td><td>289-315</td></tr><tr><td>20.</td><td>Erectional Conduit of Contract</td><td>316-337</td></tr><tr><td>21.</td><td>Special Conditions of Contract</td><td>338-363</td></tr><tr><td>22.</td><td>Technical Specification</td><td>364-1228</td></tr><tr><td>23.</td><td>List of Drawings.</td><td>1229-1232</td></tr></table>\n\n![](images/0.jpg)\n\n![](images/1.jpg)\n\nE:\\Projects\\REC-064\\Index Contract.doc\n\nThe quick brown fox jumps over the lazy dog.\n\n---\n*⏱️ **Unlimited-OCR Execution Time:** 3m 32.2s (212.2s)*',
        "raw_ocr_text": '<|det|>page_number [869, 28, 912, 47]<|/det|>60\n<|det|>text [515, 146, 596, 165]<|/det|>INDEX\n<|det|>text [200, 182, 611, 200]<|/det|>Contract No. HDP-1484/REC-064/Xen(Projects)\n<|det|>text [746, 186, 910, 200]<|/det|>Dated: 20.05.2008\n<|det|>table [209, 204, 946, 737]<|/det|><table><tr><td>Sr. No.</td><td>Description</td><td>Page No.</td></tr><tr><td>1.</td><td>Contract Agreement</td><td>1-4</td></tr><tr><td>2.</td><td>Performance BG</td><td>5-8</td></tr><tr><td>3.</td><td>Detailed Purchase Order</td><td>9-29</td></tr><tr><td>4.</td><td>Letter of Acceptance</td><td>30</td></tr><tr><td>5.</td><td>Letter of M/s Areva T&amp;D India Ltd. dated 29.04.2008</td><td>31-54</td></tr><tr><td>6.</td><td>Letter of M/s Areva T&amp;D India Ltd. dated 07.04.2007</td><td>55</td></tr><tr><td>7.</td><td>Letter of M/s Areva T&amp;D India Ltd. dated 28.03.2008</td><td>56</td></tr><tr><td>8.</td><td>Letter of M/s Areva T&amp;D India Ltd. dated 02.01.2008</td><td>57</td></tr><tr><td>9.</td><td>Minutes of Meeting dated 27.12.2007</td><td>58-59</td></tr><tr><td>10.</td><td>Letter of M/s Areva T&amp;D India Ltd. dated 27.12.2007</td><td>60-72</td></tr><tr><td>11.</td><td>HVPN letter dated 11.12.2007</td><td>73-77</td></tr><tr><td>12.</td><td>Special Power of Attorney</td><td>78-79</td></tr><tr><td>13.</td><td>Certificate regarding important conditions</td><td>80</td></tr><tr><td>14.</td><td>Letter of undertaking</td><td>81-82</td></tr><tr><td>15.</td><td>Offer of M/s Areva T&amp;D India Ltd. dated 26.11.2007</td><td>83-121</td></tr><tr><td>16.</td><td>Technical Data Sheets</td><td>122-265</td></tr><tr><td>17.</td><td>Notice Inviting Tender</td><td>266-268</td></tr><tr><td>18.</td><td>Instruction to Bidders</td><td>269-288</td></tr><tr><td>19.</td><td>General Conditions of Contract</td><td>289-315</td></tr><tr><td>20.</td><td>Erectional Conduit of Contract</td><td>316-337</td></tr><tr><td>21.</td><td>Special Conditions of Contract</td><td>338-363</td></tr><tr><td>22.</td><td>Technical Specification</td><td>364-1228</td></tr><tr><td>23.</td><td>List of Drawings.</td><td>1229-1232</td></tr></table>\n<|det|>image [481, 745, 593, 822]<|/det|>\n<|det|>image [715, 753, 880, 862]<|/det|>\n<|det|>footer [193, 837, 371, 849]<|/det|>E:\\Projects\\REC-064\\Index Contract.doc\n<|det|>footer [117, 848, 195, 937]<|/det|>The quick brown fox jumps over the lazy dog.',
    },
    {
        "page_num": 3,
        "text": '61\n\n![](images/0.jpg)\n\nहरियाणी HARYANA\n\nC 684307\n\nCONTRACT AGREEMENT\n\nThis agreement made this 29\\( ^{th} \\) day of July, Two Thousand Eight between Haryana Vidyut Prasaran Nigam Limited, Power Colony Industrial Area, Phase-II, Panchkula. (here in after referred to as "owner" or "HVPNL" which expression shall include its administrators, Company incorporated under the Companies Act, 1956) on the one part and M/s Areva T&D India Ltd., Noida having its registered office at D-2, Gillander House, Netaji Subhas Road, Calcutta - 700001 (here in after referred to as the "contractor" which expression shall include its administrators, successors, executors and permitted assigns) of the other part.\n\nWHEREAS HVPNL desirous for construction of 400 kV sub-station Kirori (Hisar) invited bids for design, manufacturing, transportation to site and supply of equipment for construction of 400 kV sub-station Kirori (Hisar) on Turn-key basis as per specification No. R&C-064 and whereas M/s Areva T&D India Ltd. Noida participated in the above referred bidding vide their proposal No. EI070151 dated 26.11.2007 and HVPNL awarded the contract bearing No. HDP-i484/REC-064/Xen(Projects) dated 20.05.2008 to M/s Areva T&D India Ltd. on terms and conditions, documents referred to there in, which have been accepted by the M/s Areva T&D India Ltd. resulting into a "Contract".\n\nNGW THEREFORE THIS DEED WITNESSETH AS UNDER:\n\n1. Article\n\nSupply of equipments for construction of 400 kV sub-station Kiroi (Hisar) on turnkey basis against tender enquiry No. REC-064.\n\n1.1 Award of contract\n\nHVPN has awarded the contract to M/s Areva T&D India Ltd., Noida for the supply of equipment for construction of 400 kV sub-station Kiroi (Hisar) on turn-key basis against tender enquiry No. REC-064 on the terms and conditions contained in\n\n![](images/1.jpg)\n\n![](images/2.jpg)\n\n![](images/3.jpg)\n\n---\n*⏱️ **Unlimited-OCR Execution Time:** 4m 5.9s (245.9s)*',
        "raw_ocr_text": '<|det|>page_number [875, 31, 921, 55]<|/det|>61\n<|det|>image [148, 98, 975, 386]<|/det|>\n<|det|>title [160, 390, 361, 409]<|/det|>हरियाणी HARYANA\n<|det|>text [849, 403, 960, 419]<|/det|>C 684307\n<|det|>title [424, 411, 673, 426]<|/det|>CONTRACT AGREEMENT\n<|det|>text [202, 441, 895, 553]<|/det|>This agreement made this 29\\( ^{th} \\) day of July, Two Thousand Eight between Haryana Vidyut Prasaran Nigam Limited, Power Colony Industrial Area, Phase-II, Panchkula. (here in after referred to as "owner" or "HVPNL" which expression shall include its administrators, Company incorporated under the Companies Act, 1956) on the one part and M/s Areva T&D India Ltd., Noida having its registered office at D-2, Gillander House, Netaji Subhas Road, Calcutta - 700001 (here in after referred to as the "contractor" which expression shall include its administrators, successors, executors and permitted assigns) of the other part.\n<|det|>text [202, 562, 894, 673]<|/det|>WHEREAS HVPNL desirous for construction of 400 kV sub-station Kirori (Hisar) invited bids for design, manufacturing, transportation to site and supply of equipment for construction of 400 kV sub-station Kirori (Hisar) on Turn-key basis as per specification No. R&C-064 and whereas M/s Areva T&D India Ltd. Noida participated in the above referred bidding vide their proposal No. EI070151 dated 26.11.2007 and HVPNL awarded the contract bearing No. HDP-i484/REC-064/Xen(Projects) dated 20.05.2008 to M/s Areva T&D India Ltd. on terms and conditions, documents referred to there in, which have been accepted by the M/s Areva T&D India Ltd. resulting into a "Contract".\n<|det|>title [202, 682, 671, 698]<|/det|>NGW THEREFORE THIS DEED WITNESSETH AS UNDER:\n<|det|>title [204, 708, 287, 721]<|/det|>1. Article\n<|det|>text [253, 722, 892, 751]<|/det|>Supply of equipments for construction of 400 kV sub-station Kiroi (Hisar) on turnkey basis against tender enquiry No. REC-064.\n<|det|>title [204, 761, 404, 774]<|/det|>1.1 Award of contract\n<|det|>text [253, 775, 892, 819]<|/det|>HVPN has awarded the contract to M/s Areva T&D India Ltd., Noida for the supply of equipment for construction of 400 kV sub-station Kiroi (Hisar) on turn-key basis against tender enquiry No. REC-064 on the terms and conditions contained in\n<|det|>image [258, 820, 454, 900]<|/det|>\n<|det|>image [523, 842, 571, 858]<|/det|>\n<|det|>image [782, 800, 943, 909]<|/det|>',
    },
    {
        "page_num": 4,
        "text": '62\n\nletter of award No. Ch-91/REC-064/Xen(Projects) dated 08.04.2008 and detailed award No. HDF-1484/REC-064/Xen(Projects) dated 20.05.2008 and the documents referred to there in. The award has taken effect from aforesaid letter of award. The terms and expressions used in this agreement shall have the same meaning as are assigned to them in the "Contract Documents" referred to in the succeeding article.\n\n2.0 CONTRACT DOCUMENTS\n\n2.1 The contract shall be performed strictly as per the terms and conditions stipulated herein and in the following documents attached herewith (herein after referred to as "Contract Documents".\n\ni) HVPNL bidding document No. REC-064 consisting of Notice Inviting Tender (NIT), Instruction to Bidders (ITB) General Conditions of contract (GCC), Erection Conditions of Contract (ECC), Special Conditions of Contract (SCC)\n\nii) HVPN Technical Specification No. REC-064.\n\niii) M/s Areva T&D India Ltd. proposal No. E1070151 dated 27.11.2007 along with bid proposal sheets, data requirements, payment terms, work schedules and the Power of Attorney forming part of the bid and HVPN letter No. Ch-22/ REC-064/Xen(Projects) dated 22.11.2007.\n\niv) HVPN letter No. Ch-35/REC-064/Xen(Projects) dated 11.12.2007, M/s Areva T&D India Ltd. letter No. E1070151/01 dated 27.12.2007, M/s Areva T&D India Ltd. letter No. E1070151/01 dated 02.01.2008, M/s Areva T&D India Ltd. letter dated 28.03.2008, M/s Areva T&D India Ltd. letter No. E1070151 dated 07.04.2008 and M/s Areva T&D India Ltd. letter No. E1070151 dated 29.04.2008.\n\nv) Agreed Minutes of the meeting held on 27.12.2007 between HVPN and M/s Areva T&D India. Ltd.\n\nvi) HVPN\'s letter of award No. Ch-91/REC-064/Xen(Projects) dated 08.04.2008 and detailed award No. HDP-1484/REC-064/Xen(Projects) dated 20.05.2008.\n\nAll the aforesaid contract documents shall form an integral part of this agreement, in so as the same or any part conform to the bidding documents (Vol-I&II) and what has been specifically agreed to by the owner in its letter of award. Any matter inconsistent therewith, contrary or: repugnant there to or any deviations taken by the contractor in its "Bid Proposal", but not agreed to specifically by the owner in its letter of award shall not form part of this agreement. For the sake of brevity this agreement alongwith its aforesaid contract documents shall be referred to as the "Agreement".\n\n3.0 CONDITIONS AND COVENANTS\n\n3.1 The scope of the contract, consideration, terms of payment, taxes and duties wherever applicable, insurance, liquidated damages, performance guarantee and all other terms and conditions are contained in HVPN\'s detailed award No. HDP-1484/REC-064/Xen(Projects) dated 20.05.2008 read in conjunction with other aforesaid contract documents. The contract shall be duly performed by the contractor strictly and faithfully in accordance with the terms of the agreement.\n\n3.2 The scope of work shall also include supply of all such items which are not specifically mentioned in the contract documents, but which are needed for successful, efficient, safe and reliable operation of the equipment, unless otherwise specifically excluded in the specifications under "exclusions" or "letter of award"\n\n![](images/0.jpg)\n\n![](images/1.jpg)\n\n---\n*⏱️ **Unlimited-OCR Execution Time:** 6m 19.7s (379.7s)*',
        "raw_ocr_text": '<|det|>page_number [875, 29, 926, 55]<|/det|>62\n<|det|>text [258, 134, 902, 215]<|/det|>letter of award No. Ch-91/REC-064/Xen(Projects) dated 08.04.2008 and detailed award No. HDF-1484/REC-064/Xen(Projects) dated 20.05.2008 and the documents referred to there in. The award has taken effect from aforesaid letter of award. The terms and expressions used in this agreement shall have the same meaning as are assigned to them in the "Contract Documents" referred to in the succeeding article.\n<|det|>title [208, 226, 476, 240]<|/det|>2.0 CONTRACT DOCUMENTS\n<|det|>text [208, 241, 900, 281]<|/det|>2.1 The contract shall be performed strictly as per the terms and conditions stipulated herein and in the following documents attached herewith (herein after referred to as "Contract Documents".\n<|det|>text [258, 282, 898, 323]<|/det|>i) HVPNL bidding document No. REC-064 consisting of Notice Inviting Tender (NIT), Instruction to Bidders (ITB) General Conditions of contract (GCC), Erection Conditions of Contract (ECC), Special Conditions of Contract (SCC)\n<|det|>text [258, 322, 653, 336]<|/det|>ii) HVPN Technical Specification No. REC-064.\n<|det|>text [258, 336, 897, 390]<|/det|>iii) M/s Areva T&D India Ltd. proposal No. E1070151 dated 27.11.2007 along with bid proposal sheets, data requirements, payment terms, work schedules and the Power of Attorney forming part of the bid and HVPN letter No. Ch-22/ REC-064/Xen(Projects) dated 22.11.2007.\n<|det|>text [258, 390, 897, 470]<|/det|>iv) HVPN letter No. Ch-35/REC-064/Xen(Projects) dated 11.12.2007, M/s Areva T&D India Ltd. letter No. E1070151/01 dated 27.12.2007, M/s Areva T&D India Ltd. letter No. E1070151/01 dated 02.01.2008, M/s Areva T&D India Ltd. letter dated 28.03.2008, M/s Areva T&D India Ltd. letter No. E1070151 dated 07.04.2008 and M/s Areva T&D India Ltd. letter No. E1070151 dated 29.04.2008.\n<|det|>text [258, 470, 895, 497]<|/det|>v) Agreed Minutes of the meeting held on 27.12.2007 between HVPN and M/s Areva T&D India. Ltd.\n<|det|>text [258, 497, 895, 537]<|/det|>vi) HVPN\'s letter of award No. Ch-91/REC-064/Xen(Projects) dated 08.04.2008 and detailed award No. HDP-1484/REC-064/Xen(Projects) dated 20.05.2008.\n<|det|>text [252, 551, 895, 658]<|/det|>All the aforesaid contract documents shall form an integral part of this agreement, in so as the same or any part conform to the bidding documents (Vol-I&II) and what has been specifically agreed to by the owner in its letter of award. Any matter inconsistent therewith, contrary or: repugnant there to or any deviations taken by the contractor in its "Bid Proposal", but not agreed to specifically by the owner in its letter of award shall not form part of this agreement. For the sake of brevity this agreement alongwith its aforesaid contract documents shall be referred to as the "Agreement".\n<|det|>title [202, 668, 521, 683]<|/det|>3.0 CONDITIONS AND COVENANTS\n<|det|>text [201, 684, 892, 768]<|/det|>3.1 The scope of the contract, consideration, terms of payment, taxes and duties wherever applicable, insurance, liquidated damages, performance guarantee and all other terms and conditions are contained in HVPN\'s detailed award No. HDP-1484/REC-064/Xen(Projects) dated 20.05.2008 read in conjunction with other aforesaid contract documents. The contract shall be duly performed by the contractor strictly and faithfully in accordance with the terms of the agreement.\n<|det|>text [200, 775, 892, 832]<|/det|>3.2 The scope of work shall also include supply of all such items which are not specifically mentioned in the contract documents, but which are needed for successful, efficient, safe and reliable operation of the equipment, unless otherwise specifically excluded in the specifications under "exclusions" or "letter of award"\n<|det|>image [272, 828, 432, 907]<|/det|>\n<|det|>image [834, 828, 955, 927]<|/det|>',
    },
    {
        "page_num": 5,
        "text": "63\n\n3.3 TIME SCHEDULE\n\nThe total work of design, procurement, manufacture & supply of equipment covered under the scope of the contract shall be planned and executed in a way that the testing and commissioning of all the sub-stations is completed within 18 months from the date of signing of contract as per clause 11 SCC of bidding documents. The contract agreement will be signed within 30 days of notification of award / issue of letter of acceptance. In case, there is delay in signing of contract, the contractual completion period will be counted from the \\(30^{\\text{th}}\\) day of notification of award / issue of LOA, irrespective of the reason of delay in signing of the contract.\n\nTime is the essence of the contract and schedules shall be strictly adhered to and the contractor shall perform the work in accordance with the agreed schedules.\n\n3.4 QUALITY PLANS\n\n3.4.1 The contractor is responsible for the proper execution of the quality plans. The work beyond the customer's hold points will progress only with the owner's consent. The owner also undertake quality surveillance and quality audit of the contractor's/sub-contractor's works, systems and procedures and quality control activities. The contractor further agrees that any change in the quality plan will be made only with the owner's approval. The contractor shall also perform all quality control activities, inspection and tests agreed with the owner to demonstrate full compliance with the contract requirements.\n\n3.4.2 The contractor also agrees to provide the owner with the necessary facilities for carrying out inspection, quality audit and quality surveillance of contractor's and its sub-contractor's quality assurance systems and manufacturing activities.\n\n3.5 Another contract for erection portion of sub-stations and Civil works has been placed upon the contractor simultaneously. Any breach in the contract for erection and Civil works will automatically mean breach of this contract also.\n\n3.6 The contractor guarantees that all the equipment under the contracts shall meet the ratings and performance parameters as stipulated in the technical specifications (Vol-II) and in the event of any deficiencies found in the requisite performance figures, the owner may at its option reject the equipment or alternatively accept it on the terms and conditions and subject to levy of the liquidated damages in terms of contract documents. The amount of liquidated damages so leviable shall be in accordance with the contract documents and without any limitation.\n\n3.7 It is further agreed by the contractor that the contract performance guarantee shall in no way be construed to limit or restrict the owner's right to recover the damages/compensations due to shortfall in the equipment performance figure as state above or under any other clause of the agreement. The amount of damages/compensations shall be recoverable either by way of deduction from the contract price, contract performance guarantee and/or otherwise.\n\nThe contract performance guarantee furnished by the contractor is irrevocable and unconditional and the owner shall have the powers to invoke it notwithstanding any dispute or difference between the owner and the contractor pending before any court, tribunal, arbitrator or any other authority.\n\n3.8 This agreement constitutes full and complete understanding between the parities and terms of the presents. It shall supersede any prior correspondence, terms and conditions contained in the agreement. Any modification of the agreement shall be effected only by a written instrument signed by the authorised representative of both the parties.\n\n![](images/0.jpg)\n\n-3-\n\n![](images/1.jpg)\n\n---\n*⏱️ **Unlimited-OCR Execution Time:** 5m 4.2s (304.2s)*",
        "raw_ocr_text": "<|det|>page_number [861, 35, 912, 62]<|/det|>63\n<|det|>title [221, 129, 416, 142]<|/det|>3.3 TIME SCHEDULE\n<|det|>text [269, 144, 910, 252]<|/det|>The total work of design, procurement, manufacture & supply of equipment covered under the scope of the contract shall be planned and executed in a way that the testing and commissioning of all the sub-stations is completed within 18 months from the date of signing of contract as per clause 11 SCC of bidding documents. The contract agreement will be signed within 30 days of notification of award / issue of letter of acceptance. In case, there is delay in signing of contract, the contractual completion period will be counted from the \\(30^{\\text{th}}\\) day of notification of award / issue of LOA, irrespective of the reason of delay in signing of the contract.\n<|det|>text [269, 257, 908, 285]<|/det|>Time is the essence of the contract and schedules shall be strictly adhered to and the contractor shall perform the work in accordance with the agreed schedules.\n<|det|>title [220, 288, 414, 302]<|/det|>3.4 QUALITY PLANS\n<|det|>text [220, 303, 910, 413]<|/det|>3.4.1 The contractor is responsible for the proper execution of the quality plans. The work beyond the customer's hold points will progress only with the owner's consent. The owner also undertake quality surveillance and quality audit of the contractor's/sub-contractor's works, systems and procedures and quality control activities. The contractor further agrees that any change in the quality plan will be made only with the owner's approval. The contractor shall also perform all quality control activities, inspection and tests agreed with the owner to demonstrate full compliance with the contract requirements.\n<|det|>text [220, 418, 909, 461]<|/det|>3.4.2 The contractor also agrees to provide the owner with the necessary facilities for carrying out inspection, quality audit and quality surveillance of contractor's and its sub-contractor's quality assurance systems and manufacturing activities.\n<|det|>text [220, 470, 909, 513]<|/det|>3.5 Another contract for erection portion of sub-stations and Civil works has been placed upon the contractor simultaneously. Any breach in the contract for erection and Civil works will automatically mean breach of this contract also.\n<|det|>text [220, 517, 909, 613]<|/det|>3.6 The contractor guarantees that all the equipment under the contracts shall meet the ratings and performance parameters as stipulated in the technical specifications (Vol-II) and in the event of any deficiencies found in the requisite performance figures, the owner may at its option reject the equipment or alternatively accept it on the terms and conditions and subject to levy of the liquidated damages in terms of contract documents. The amount of liquidated damages so leviable shall be in accordance with the contract documents and without any limitation.\n<|det|>text [218, 618, 909, 701]<|/det|>3.7 It is further agreed by the contractor that the contract performance guarantee shall in no way be construed to limit or restrict the owner's right to recover the damages/compensations due to shortfall in the equipment performance figure as state above or under any other clause of the agreement. The amount of damages/compensations shall be recoverable either by way of deduction from the contract price, contract performance guarantee and/or otherwise.\n<|det|>text [267, 705, 909, 760]<|/det|>The contract performance guarantee furnished by the contractor is irrevocable and unconditional and the owner shall have the powers to invoke it notwithstanding any dispute or difference between the owner and the contractor pending before any court, tribunal, arbitrator or any other authority.\n<|det|>text [212, 763, 907, 830]<|/det|>3.8 This agreement constitutes full and complete understanding between the parities and terms of the presents. It shall supersede any prior correspondence, terms and conditions contained in the agreement. Any modification of the agreement shall be effected only by a written instrument signed by the authorised representative of both the parties.\n<|det|>image [268, 836, 457, 911]<|/det|>\n<|det|>page_number [520, 844, 560, 859]<|/det|>-3-\n<|det|>image [795, 811, 955, 915]<|/det|>",
    },
    {
        "page_num": 6,
        "text": '64\n\n4.0 SETTLEMENT OF DISPUTES\n\nIt is specifically agreed by and between the parties that all the difference or disputes arising out of the Agreement or touching the subject matter of the Agreement shall be decided by the process of settlement & Arbitration as specified in clause 48 and 49 of the General Conditions of Contract & cf the provisions of the Indian, Arbitration and Conciliation Act 1996 shall apply and PANCHKULA Court alone shall have exclusive jurisdiction over the same.\n\n4.1 NOTICE OF DEFAULT\n\nNotice of default given by either party to the other party under Agreement shall be in writing and shall be deemed to have been duly and properly served upon the parties hereto if delivered against acknowledgement or by telex or by registered mail with acknowledgement due addressed to the signatories at the addresses mentioned herein above.\n\nIN WITNESS WHEREOF, the parties through their duly authorised representatives have executed these presents (Execution whereof has been approved by the competent authorities of both the parties), on the day, month and year first above mentioned at Panchkula.\n\nSigned, sealed and Delivered by the.\n\nFOR AND ON BEHALF OF HARYANA VIDYUT PRASARAN NIGAM LTD.\n\n![](images/0.jpg)\n\nNAME : H. G. CHHABRA\n\nTITLE : CHIEF ENGINEER/MM,\n\nHVPNL, PANCHKULA\n\nIN THE PRESENCE OF\n\n![](images/1.jpg)\n\n![](images/2.jpg)\n\nS.D.CHAWLA.\n\nSEIMON\n\nFOR AND ON BEHALF OF M/S AREVA T&D INDIA LTD.\n\nNAME\n\nTITLE\n\n![](images/3.jpg)\n\nDINESH DIWAN\n\nDY. GENERAL MANAGER-TENDERING\n\nIN THE PRESENCE OF\n\n![](images/4.jpg)\n\n![](images/5.jpg)\n\n-4-\n\n---\n*⏱️ **Unlimited-OCR Execution Time:** 3m 36.0s (216.0s)*',
        "raw_ocr_text": '<|det|>page_number [866, 26, 919, 53]<|/det|>64\n<|det|>title [212, 134, 502, 149]<|/det|>4.0 SETTLEMENT OF DISPUTES\n<|det|>text [260, 150, 905, 233]<|/det|>It is specifically agreed by and between the parties that all the difference or disputes arising out of the Agreement or touching the subject matter of the Agreement shall be decided by the process of settlement & Arbitration as specified in clause 48 and 49 of the General Conditions of Contract & cf the provisions of the Indian, Arbitration and Conciliation Act 1996 shall apply and PANCHKULA Court alone shall have exclusive jurisdiction over the same.\n<|det|>title [211, 242, 445, 256]<|/det|>4.1 NOTICE OF DEFAULT\n<|det|>text [259, 257, 903, 325]<|/det|>Notice of default given by either party to the other party under Agreement shall be in writing and shall be deemed to have been duly and properly served upon the parties hereto if delivered against acknowledgement or by telex or by registered mail with acknowledgement due addressed to the signatories at the addresses mentioned herein above.\n<|det|>text [259, 337, 902, 393]<|/det|>IN WITNESS WHEREOF, the parties through their duly authorised representatives have executed these presents (Execution whereof has been approved by the competent authorities of both the parties), on the day, month and year first above mentioned at Panchkula.\n<|det|>text [259, 404, 543, 420]<|/det|>Signed, sealed and Delivered by the.\n<|det|>text [259, 430, 843, 447]<|/det|>FOR AND ON BEHALF OF HARYANA VIDYUT PRASARAN NIGAM LTD.\n<|det|>image [465, 445, 541, 490]<|/det|>\n<|det|>text [259, 485, 548, 528]<|/det|>NAME : H. G. CHHABRA\nTITLE : CHIEF ENGINEER/MM,\nHVPNL, PANCHKULA\n<|det|>text [259, 538, 447, 552]<|/det|>IN THE PRESENCE OF\n<|det|>image [580, 511, 707, 582]<|/det|>\n<|det|>image [727, 479, 907, 594]<|/det|>\n<|det|>text [493, 556, 691, 584]<|/det|>S.D.CHAWLA.\nSEIMON\n<|det|>text [257, 591, 708, 609]<|/det|>FOR AND ON BEHALF OF M/S AREVA T&D INDIA LTD.\n<|det|>text [257, 645, 310, 671]<|/det|>NAME\nTITLE\n<|det|>image [396, 620, 560, 648]<|/det|>\n<|det|>text [411, 648, 728, 678]<|/det|>DINESH DIWAN\nDY. GENERAL MANAGER-TENDERING\n<|det|>text [257, 685, 446, 700]<|/det|>IN THE PRESENCE OF\n<|det|>image [366, 685, 645, 786]<|/det|>\n<|det|>image [731, 618, 848, 698]<|/det|>\n<|det|>page_number [478, 799, 545, 818]<|/det|>-4-',
    },
]


def test_end_to_end_real_job_reproduces_the_actual_document_hierarchy():
    """This job's own real 6 pages, fed through in order - the actual
    regression this fix exists for. Confirmed against the real PDF: root
    is "Annexure A-1" (surviving page 2's "INDEX"), with "1"/"1.1",
    "2.0"/"3.0"/"4.0" as top-level siblings, "3.3"/"3.4" under
    "3.0 CONDITIONS AND COVENANTS", and "4.1" under
    "4.0 SETTLEMENT OF DISPUTES".

    Every numbered sub-paragraph with no title of its own (2.1, 3.1, 3.2,
    3.4.1, 3.4.2, 3.5-3.8) is now ALSO a node (the headerless-numbered-clause
    fix) - each continues an already-open parent number, own_label is just
    the bare number, and heading_confidence is "low" with
    "headerless_numbered_clause" as the reason (display-only doubt; it still
    correctly anchors further nesting, e.g. 3.4.1/3.4.2 under 3.4)."""
    seed_scope = None
    all_candidates = []
    for i, p in enumerate(_REAL_PAGES):
        has_next = i < len(_REAL_PAGES) - 1
        candidates, seed_scope = build_targets_for_page(
            p["text"], seed_scope, has_next, raw_text=p["raw_ocr_text"]
        )
        all_candidates.extend(candidates)

    by_key = {c["key"]: c for c in all_candidates}
    # The full numbered hierarchy must be present and unchanged — checked
    # as a subset, not exact equality, since the general generic-heading-
    # shape fallback (recall-biased, added for open-vocabulary headings
    # like "CONTRACT AGREEMENT"/"INDEX" elsewhere in this same document)
    # now also flags plenty of surrounding title-page/signature-block text
    # ("NAME : H. G. CHHABRA", "IN THE PRESENCE OF", ...) as its own
    # low-confidence `subdoc:N` leaves in this fixture. That's the accepted
    # tradeoff (false positives are fine, a missed heading is not) — and
    # since every one is a pure leaf, never pushed onto the stack, it
    # cannot corrupt the real hierarchy asserted below.
    assert {
        "annexure:a-1", "numheading:1", "numheading:1.1", "numheading:2.0",
        "numheading:2.1", "numheading:3.0", "numheading:3.1", "numheading:3.2",
        "numheading:3.3", "numheading:3.4", "numheading:3.4.1", "numheading:3.4.2",
        "numheading:3.5", "numheading:3.6", "numheading:3.7", "numheading:3.8",
        "numheading:4.0", "numheading:4.1",
    }.issubset(by_key.keys())

    # Root survives the "INDEX" page (the subdoc-trigger hijack regression).
    assert by_key["annexure:a-1"]["scope_label"] == ""

    # "1. Article" (title-case, non-all-caps) is still detected and anchors
    # "1.1" - ancestor breadcrumbs now include the number too.
    assert by_key["numheading:1"]["scope_label"] == "Annexure A-1"
    assert by_key["numheading:1"]["own_label"] == "1 Article"
    assert by_key["numheading:1.1"]["scope_label"] == "Annexure A-1 › 1 Article"
    assert by_key["numheading:1.1"]["own_label"] == "1.1 Award of contract"

    # "2.0"/"3.0"/"4.0" are top-level siblings, all directly under the Annexure.
    assert by_key["numheading:2.0"]["scope_label"] == "Annexure A-1"
    assert by_key["numheading:2.0"]["own_label"] == "2.0 CONTRACT DOCUMENTS"
    assert by_key["numheading:3.0"]["scope_label"] == "Annexure A-1"
    assert by_key["numheading:3.0"]["own_label"] == "3.0 CONDITIONS AND COVENANTS"
    assert by_key["numheading:4.0"]["scope_label"] == "Annexure A-1"
    assert by_key["numheading:4.0"]["own_label"] == "4.0 SETTLEMENT OF DISPUTES"

    # "2.1" - no title of its own - nests under "2.0", bare-number own_label.
    assert by_key["numheading:2.1"]["scope_label"] == "Annexure A-1 › 2.0 CONTRACT DOCUMENTS"
    assert by_key["numheading:2.1"]["own_label"] == "2.1"
    assert by_key["numheading:2.1"]["heading_confidence"] == "low"
    assert "headerless_numbered_clause" in by_key["numheading:2.1"]["heading_confidence_reasons"]

    # "3.3"/"3.4" survive under "3.0 CONDITIONS AND COVENANTS" (the
    # "SCHEDULE"-inside-a-decimal-heading subdoc-trigger hijack regression).
    assert by_key["numheading:3.3"]["scope_label"] == "Annexure A-1 › 3.0 CONDITIONS AND COVENANTS"
    assert by_key["numheading:3.3"]["own_label"] == "3.3 TIME SCHEDULE"
    assert by_key["numheading:3.4"]["scope_label"] == "Annexure A-1 › 3.0 CONDITIONS AND COVENANTS"
    assert by_key["numheading:3.4"]["own_label"] == "3.4 QUALITY PLANS"

    # "3.1"/"3.2"/"3.5"-"3.8" - headerless siblings, all directly under "3.0".
    # "3.6" mentions "technical specifications (Vol-II)" - the unbounded
    # other_doc_marker citation heuristic is deliberately skipped for
    # headerless matches (it's tuned for a short, wholly-citation-shaped
    # title, not an incidental mention deep in a real clause's body text -
    # see _citation_confidence_reasons), so "3.6" correctly pops "3.5" and
    # anchors itself as a sibling, exactly like 3.1/3.2/3.5/3.7/3.8.
    for key, number in [
        ("numheading:3.1", "3.1"), ("numheading:3.2", "3.2"), ("numheading:3.5", "3.5"),
        ("numheading:3.6", "3.6"), ("numheading:3.7", "3.7"), ("numheading:3.8", "3.8"),
    ]:
        assert by_key[key]["scope_label"] == "Annexure A-1 › 3.0 CONDITIONS AND COVENANTS", key
        assert by_key[key]["own_label"] == number, key
        assert by_key[key]["heading_confidence"] == "low", key
        assert by_key[key]["heading_confidence_reasons"] == ["headerless_numbered_clause"], key

    # "3.4.1"/"3.4.2" - headerless, nested under "3.4 QUALITY PLANS" specifically.
    for key, number in [("numheading:3.4.1", "3.4.1"), ("numheading:3.4.2", "3.4.2")]:
        assert by_key[key]["scope_label"] == "Annexure A-1 › 3.0 CONDITIONS AND COVENANTS › 3.4 QUALITY PLANS", key
        assert by_key[key]["own_label"] == number, key

    # "4.1" nests under "4.0 SETTLEMENT OF DISPUTES", not under 3.x.
    assert by_key["numheading:4.1"]["scope_label"] == "Annexure A-1 › 4.0 SETTLEMENT OF DISPUTES"
    assert by_key["numheading:4.1"]["own_label"] == "4.1 NOTICE OF DEFAULT"
