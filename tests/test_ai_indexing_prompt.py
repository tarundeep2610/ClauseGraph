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


def test_prompt_survives_assembly_into_a_real_chunk_prompt():
    chunk = ChunkPlan(pages=[PageInput(page_num=1, text="ANNEXURE A-1\n\nsome text")])
    system_prompt, user_prompt = build_structure_prompt(chunk)
    assert system_prompt == STRUCTURE_SYSTEM_PROMPT
    assert "NOUN-PHRASE TEST" in system_prompt
