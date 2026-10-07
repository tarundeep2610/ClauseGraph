"""Locks in the specific prompt-guidance wording added after two real live
runs against job a00816d2-... exposed concrete failure modes:

Round 1 (prose rule): numbered body text mis-classified as headings; a
missed OCR-garbled root heading; a misread heading number. Verified live to
only partially work (deepest sub-clauses stopped, but 2.1/3.1/3.2/3.5-3.8
kept being misclassified despite an almost-verbatim negative example).

Round 2 (mechanical test): replaced the fuzzy "is this a sentence" prose
rule with a named, checkable NOUN-PHRASE TEST (finite-verb presence + word
count) plus multiple real worked examples and an explicit self-check pass;
replaced the soft OCR-garbled tolerance note with a mandatory, positional
rule for the document's opening heading, plus an explicit warning against
mistaking a repeated title block for a new root.

These are plain string-presence checks, not behavioral tests (those need a
live model call) — they protect this wording from being silently dropped or
reworded during a future edit, which is the failure mode that would be
easiest to introduce by accident."""

from ai_indexing.prompt import ChunkPlan, PageInput, STRUCTURE_SYSTEM_PROMPT, build_structure_prompt


def test_prompt_has_the_mechanical_noun_phrase_test():
    assert "NOUN-PHRASE TEST" in STRUCTURE_SYSTEM_PROMPT
    for verb in ("shall", "guarantees", "agrees", "constitutes", "responsible for"):
        assert verb in STRUCTURE_SYSTEM_PROMPT


def test_prompt_has_multiple_real_worked_examples_not_just_one_pair():
    assert "3.4 QUALITY PLANS" in STRUCTURE_SYSTEM_PROMPT
    assert "4.0 SETTLEMENT OF DISPUTES" in STRUCTURE_SYSTEM_PROMPT
    assert "3.6 The contractor guarantees" in STRUCTURE_SYSTEM_PROMPT
    assert "3.1 The scope of the contract" in STRUCTURE_SYSTEM_PROMPT
    assert "2.1 The contract shall be performed" in STRUCTURE_SYSTEM_PROMPT


def test_prompt_has_a_self_check_pass_naming_multi_segment_decimals():
    assert "SELF-CHECK" in STRUCTURE_SYSTEM_PROMPT
    assert "3.4.1" in STRUCTURE_SYSTEM_PROMPT


def test_prompt_makes_the_root_heading_mandatory_not_just_tolerated():
    assert "AmexureAl" in STRUCTURE_SYSTEM_PROMPT
    assert "MANDATORY" in STRUCTURE_SYSTEM_PROMPT
    assert "You MUST" in STRUCTURE_SYSTEM_PROMPT


def test_prompt_warns_against_repeated_title_blocks_masquerading_as_new_roots():
    assert "repeated title block" in STRUCTURE_SYSTEM_PROMPT
    assert "CONTRACT AGREEMENT" in STRUCTURE_SYSTEM_PROMPT


def test_prompt_teaches_family_hint_and_number_hint():
    """Round 3: instead of a regex-only fix for garbled headings, the AI
    itself reports its best-guess resolution via two additive fields —
    heading_text must stay verbatim (needed for page-anchoring), so the
    correction can only ever live in these separate fields."""
    assert "family_hint" in STRUCTURE_SYSTEM_PROMPT
    assert "number_hint" in STRUCTURE_SYSTEM_PROMPT
    assert "AmexureAl" in STRUCTURE_SYSTEM_PROMPT
    assert '"family_hint": "annexure"' in STRUCTURE_SYSTEM_PROMPT
    assert '"number_hint": "A-1"' in STRUCTURE_SYSTEM_PROMPT


def test_output_contract_includes_hint_fields():
    from ai_indexing.prompt import STRUCTURE_OUTPUT_CONTRACT
    assert '"family_hint": null' in STRUCTURE_OUTPUT_CONTRACT
    assert '"number_hint": null' in STRUCTURE_OUTPUT_CONTRACT


def test_prompt_excludes_administrative_stamps_and_form_codes():
    """Round 5, item 1: a real false positive from a 100-page job — a court
    filing's cover-page form-code stamp ("Stereo H.C.J.D./A-1") got
    misread as a heading and resolved via the family_hint mechanism to a
    spurious "annexure:a-1". None of these are garbled — the model just
    misjudged administrative marginalia as a real heading."""
    assert "Stereo H.C.J.D./A" in STRUCTURE_SYSTEM_PROMPT
    assert "Govt. Press" in STRUCTURE_SYSTEM_PROMPT
    assert "MARGINALIA" in STRUCTURE_SYSTEM_PROMPT


def test_prompt_has_enumeration_completeness_backward_scan():
    """Round 5, item 2: a real recall miss from the same job — grounds
    (I)-(VI) were skipped entirely in one chunk, while (VII)-(XV) in the
    very next chunk were all caught (that chunk started fresh, at (VII),
    with full attention). This self-check tells the model that finding a
    mid-run enumeration member is itself a signal to scan backward for the
    earlier members it likely skimmed past."""
    assert "DON'T STOP AT THE FIRST ONE" in STRUCTURE_SYSTEM_PROMPT
    assert "(VII)" in STRUCTURE_SYSTEM_PROMPT
    assert "re-scan EVERY page" in STRUCTURE_SYSTEM_PROMPT


def test_prompt_requires_exact_number_copying():
    assert '"1." into "1.4"' in STRUCTURE_SYSTEM_PROMPT


def test_prompt_disambiguates_repeating_case_captions_from_running_headers():
    """A repeated court-caption block ("In the High Court of...") can
    legitimately introduce a DIFFERENT pleading each time (confirmed real
    miss: "RESPECTFULLY SHOWETH:" and a separate affidavit, both behind the
    same repeated caption, went entirely undetected) — distinct from the
    pure repeated-running-header case just above it, which must still be
    skipped."""
    assert "REPEATING CASE-CAPTION BLOCKS" in STRUCTURE_SYSTEM_PROMPT
    assert "RESPECTFULLY SHOWETH" in STRUCTURE_SYSTEM_PROMPT
    assert "Affidavit of Ishwar Chand Balan" in STRUCTURE_SYSTEM_PROMPT


def test_prompt_teaches_ocr_layout_annotations():
    """The OCR engine's own visual layout classification (see
    reference_index.build_layout_annotated_text /
    ai_reference_index._locate_heading_in_chunk) is surfaced to the model
    as a `[OCR-LAYOUT: ...]` tag — the model must be told what it means and
    to keep reporting heading_text verbatim regardless."""
    assert "OCR-LAYOUT" in STRUCTURE_SYSTEM_PROMPT
    assert "[OCR-LAYOUT: HEADER]" in STRUCTURE_SYSTEM_PROMPT
    assert "layout classifier" in STRUCTURE_SYSTEM_PROMPT


def test_prompt_weighs_the_layout_tag_rather_than_letting_it_override():
    """Regression guard for a real bug in the prompt itself, found and
    fixed separately from reference_index.build_layout_annotated_text's own
    fix: the original wording told the model to "trust this layout signal
    over your own text-shape judgment when the two conflict" — a purely
    visual tag overriding the model's own contextual reading, backwards for
    a centered-but-decorative block a human would only reject by reading
    what surrounds it. The tag must raise the model's prior, never settle
    the case outright."""
    assert "Trust this layout signal over your own text-shape judgment" not in STRUCTURE_SYSTEM_PROMPT
    assert "must never settle a case" in STRUCTURE_SYSTEM_PROMPT
    assert "read the blocks" in STRUCTURE_SYSTEM_PROMPT
    assert "immediately before and after" in STRUCTURE_SYSTEM_PROMPT
    # The specific recall case the original wording protected must still be
    # explained and still work the same way — garbled text with a HEADER/
    # TITLE tag is still "almost certainly a heading".
    assert "AmexureAl" in STRUCTURE_SYSTEM_PROMPT
    assert "almost certainly a heading" in STRUCTURE_SYSTEM_PROMPT


def test_prompt_documents_the_indented_marker_as_a_weighted_signal_too():
    assert "INDENTED" in STRUCTURE_SYSTEM_PROMPT
    assert "[OCR-LAYOUT: TEXT,\n  INDENTED]" in STRUCTURE_SYSTEM_PROMPT
    assert "never settles a" in STRUCTURE_SYSTEM_PROMPT
    assert "case by itself either" in STRUCTURE_SYSTEM_PROMPT


def test_prompt_states_the_recall_over_precision_principle():
    """2.0: the general design principle governing every subsequent rule —
    when uncertain, report and flag, never silently omit."""
    assert "REPORT, DON'T OMIT, WHEN UNCERTAIN" in STRUCTURE_SYSTEM_PROMPT
    assert "trivial for a human" in STRUCTURE_SYSTEM_PROMPT
    assert "uncertainty be a reason to leave something out" in STRUCTURE_SYSTEM_PROMPT


def test_prompt_generalizes_open_vocabulary_headings_beyond_fixed_examples():
    """2.3: the shape rule is stated in words, and worked examples are
    explicitly captioned as illustrations, not an exhaustive checklist."""
    assert "ILLUSTRATIONS of this shape" in STRUCTURE_SYSTEM_PROMPT
    assert "never seen in these examples" in STRUCTURE_SYSTEM_PROMPT
    assert "Insurance:" in STRUCTURE_SYSTEM_PROMPT
    assert "Bank Guarantee for Contract" in STRUCTURE_SYSTEM_PROMPT
    assert "minor supporting document" in STRUCTURE_SYSTEM_PROMPT


def test_prompt_teaches_quoted_reproduced_clause_text_is_not_a_heading():
    """2.1: report it (recall-biased), but never nest later headings under it."""
    assert "which reads as under:" in STRUCTURE_SYSTEM_PROMPT
    assert "QUOTED/REPRODUCED VERBATIM" in STRUCTURE_SYSTEM_PROMPT
    assert "not a child of \"9.0\"" in STRUCTURE_SYSTEM_PROMPT


def test_prompt_teaches_sequential_letters_vs_roman_numerals():
    assert "SEQUENTIAL LETTERS ARE NOT ROMAN NUMERALS" in STRUCTURE_SYSTEM_PROMPT
    assert "not roman numeral 100" in STRUCTURE_SYSTEM_PROMPT
    assert "CHILDREN of the" in STRUCTURE_SYSTEM_PROMPT


def test_prompt_teaches_enumerated_list_items_are_siblings_not_nested_under_the_previous_one():
    assert "ENUMERATED LIST ITEMS ARE SIBLINGS OF EACH OTHER" in STRUCTURE_SYSTEM_PROMPT
    assert "NOT a child of (F)" in STRUCTURE_SYSTEM_PROMPT


def test_prompt_has_a_lowercase_roman_worked_example_for_sibling_lists():
    """Finding 2a: the sibling-list section previously had only one worked
    example (uppercase letters) — the Conclusion's "(i)-(vii)" award list
    kept mis-chaining in practice, so a second, lowercase-roman-specific
    worked example was added right alongside it."""
    assert "(iii) is NOT a child of (ii)" in STRUCTURE_SYSTEM_PROMPT
    assert "lowercase\n  roman numerals in parens" in STRUCTURE_SYSTEM_PROMPT


def test_prompt_has_the_numbered_paragraph_gap_self_check():
    """Finding 1b: the enumeration-completeness self-check now explicitly
    covers the document's own running paragraph numbers, not just
    lettered/roman sub-lists."""
    assert 'paragraph "3."' in STRUCTURE_SYSTEM_PROMPT
    assert 'paragraph "16."' in STRUCTURE_SYSTEM_PROMPT


def test_prompt_describes_continuous_cross_heading_paragraph_numbering():
    """Finding 1b: a document's numbered paragraphs can run continuously
    across several section headings without ever restarting — confirmed
    real recall gap where paragraphs 4-15 (spanning several headings) were
    entirely skipped."""
    assert "CONTINUOUSLY ACROSS several" in STRUCTURE_SYSTEM_PROMPT
    assert "never resets just because a new heading appeared" in STRUCTURE_SYSTEM_PROMPT


def test_prompt_teaches_colon_dash_arrow_list_introducing_lines():
    """Finding 6: a general, shape-based heuristic — a short line ending in
    a colon/dash/arrow introduces the list that follows it — generalized
    with examples deliberately unrelated to the arbitration-judgment
    vocabulary already in the prompt, so it doesn't overfit to one PDF."""
    assert 'A SHORT LINE ENDING IN ":", "-", OR "->" OFTEN INTRODUCES THE LIST THAT FOLLOWS' in STRUCTURE_SYSTEM_PROMPT
    assert "The inspection revealed the following defects:" in STRUCTURE_SYSTEM_PROMPT
    assert "Delivery shall proceed in the" in STRUCTURE_SYSTEM_PROMPT
    assert "following stages -" in STRUCTURE_SYSTEM_PROMPT
    assert "The vendor's" in STRUCTURE_SYSTEM_PROMPT
    assert "obligations are as follows ->" in STRUCTURE_SYSTEM_PROMPT


def test_prompt_states_versus_is_never_a_heading():
    assert "NEVER itself a heading" in STRUCTURE_SYSTEM_PROMPT


def test_prompt_distinguishes_incidental_keyword_usage_from_a_real_divider():
    assert "MINOR sub-label of that supporting document" in STRUCTURE_SYSTEM_PROMPT
    assert "Price Schedule" in STRUCTURE_SYSTEM_PROMPT


def test_prompt_survives_assembly_into_a_real_chunk_prompt():
    chunk = ChunkPlan(pages=[PageInput(page_num=1, text="ANNEXURE A-1\n\nsome text")])
    system_prompt, user_prompt = build_structure_prompt(chunk)
    assert system_prompt == STRUCTURE_SYSTEM_PROMPT
    assert "NOUN-PHRASE TEST" in system_prompt


def test_include_pleading_points_appends_the_new_section_only_when_opted_in():
    """Round 10's opt-in test flag — the live default prompt (no kwarg, or
    explicitly False) must be byte-for-byte unchanged; only passing
    include_pleading_points=True appends the new section."""
    chunk = ChunkPlan(pages=[PageInput(page_num=1, text="GROUNDS OF APPEAL\n\n1. First ground.")])

    default_system_prompt, _ = build_structure_prompt(chunk)
    assert default_system_prompt == STRUCTURE_SYSTEM_PROMPT

    explicit_off_system_prompt, _ = build_structure_prompt(chunk, include_pleading_points=False)
    assert explicit_off_system_prompt == STRUCTURE_SYSTEM_PROMPT

    on_system_prompt, _ = build_structure_prompt(chunk, include_pleading_points=True)
    assert on_system_prompt != STRUCTURE_SYSTEM_PROMPT
    assert "NUMBERED PLEADING POINTS AND GROUNDS ARE HEADINGS TOO" in on_system_prompt
    assert "OVERRIDES THE EXCLUSION ABOVE" in on_system_prompt
