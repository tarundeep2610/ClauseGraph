"""Regression tests for ai_reference_index's parent_ref resolution and
heading->block grounding — the fixes from design review: an opaque
carry-in ref_id matched exactly (no fuzzy needed there, removing the
prompt-echo-vs-fuzzy-match ambiguity), a strictly-backward-only same-chunk
fuzzy match (which also makes a reference cycle structurally impossible),
and markdown-artifact sanitization before block grounding.
"""

import ai_reference_index as ari


def test_parent_ref_matches_carry_in_ref_id_exactly():
    carry_ref_map = {"c1": 0, "c2": 1}
    depth, warn = ari._resolve_depth_for_heading("c2", level=1, carry_ref_map=carry_ref_map,
                                                  chunk_heading_depths={}, current_stack_len=2)
    assert (depth, warn) == (2, None)


def test_parent_ref_prose_echo_of_carry_in_does_not_fuzzy_match():
    """The exact bug an earlier draft had: if the model echoes the
    formatted carry-in line back verbatim instead of the bare ref_id, it
    must NOT accidentally fuzzy-match — ref_id matching is exact-only."""
    carry_ref_map = {"c1": 0}
    depth, warn = ari._resolve_depth_for_heading(
        "Level 1: Annexure A-3 (open)", level=2, carry_ref_map=carry_ref_map,
        chunk_heading_depths={}, current_stack_len=1,
    )
    assert warn == "ai_unresolved_parent_ref"  # falls back to level, correctly flagged
    assert depth == 1


def test_parent_ref_fuzzy_matches_a_slightly_paraphrased_earlier_heading():
    chunk_heading_depths = {ari._norm_for_match("3.0 CONDITIONS AND COVENANTS"): 0}
    depth, warn = ari._resolve_depth_for_heading(
        "3.0 Conditions & Covenants",  # paraphrase drift, same heading
        level=2, carry_ref_map={}, chunk_heading_depths=chunk_heading_depths, current_stack_len=1,
    )
    assert depth == 1
    assert warn == "ai_fuzzy_parent_ref"


def test_parent_ref_forward_reference_is_rejected_not_resolved():
    """A parent_ref naming a heading that hasn't been processed yet (later
    in the same chunk) must not resolve — chunk_heading_depths only ever
    contains STRICTLY EARLIER headings by construction, so this is really a
    lookup-miss test, but it's the property that also makes a reference
    cycle structurally impossible."""
    chunk_heading_depths = {}  # the "later" heading was never added yet
    depth, warn = ari._resolve_depth_for_heading(
        "4.0 SETTLEMENT OF DISPUTES", level=3, carry_ref_map={},
        chunk_heading_depths=chunk_heading_depths, current_stack_len=0,
    )
    assert warn == "ai_unresolved_parent_ref"
    assert depth == 0  # clamped to current_stack_len


def test_no_parent_ref_falls_back_to_level():
    depth, warn = ari._resolve_depth_for_heading(None, level=2, carry_ref_map={},
                                                  chunk_heading_depths={}, current_stack_len=3)
    assert (depth, warn) == (1, None)


def test_locate_heading_block_exact_and_fuzzy():
    blocks = ["ANNEXURE A-1", "some intro text", "3.0 CONDITIONS AND COVENANTS", "body text here"]
    assert ari.locate_heading_block(blocks, "ANNEXURE A-1", set()) == 0
    # Slightly OCR-drifted / re-cased text should still fuzzy-match.
    assert ari.locate_heading_block(blocks, "3.0 Conditions and Covenants", {0, 1}) == 2


def test_locate_heading_block_never_reuses_a_claimed_block():
    blocks = ["3.0 CONDITIONS", "body", "3.0 CONDITIONS"]  # duplicate text on one page
    used = set()
    first = ari.locate_heading_block(blocks, "3.0 CONDITIONS", used)
    assert first == 0
    used.add(first)
    second = ari.locate_heading_block(blocks, "3.0 CONDITIONS", used)
    assert second == 2  # not 0 again


def test_locate_heading_block_returns_none_below_cutoff():
    blocks = ["completely unrelated text about welding equipment"]
    assert ari.locate_heading_block(blocks, "3.4 QUALITY PLANS", set()) is None


def test_markdown_artifacts_are_sanitized_before_and_after_grounding():
    from ai_indexing.schema import sanitize_heading_text
    assert sanitize_heading_text("### 3.4 QUALITY PLANS") == "3.4 QUALITY PLANS"
    assert sanitize_heading_text("**3.4 QUALITY PLANS**") == "3.4 QUALITY PLANS"
    assert sanitize_heading_text("3.4 QUALITY PLANS:") == "3.4 QUALITY PLANS"
