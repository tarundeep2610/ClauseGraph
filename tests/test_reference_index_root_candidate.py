"""Tests for is_subdoc_root_candidate's root_label parameter (B7 fix) —
the root-container-protection clamp (reference_index.py's "Rank-0 root
container protection" block, and its AI-path mirror in
ai_reference_index.py's per-heading loop) wrongly treated "Section-II"
through "Section-VI" as nested children of whichever "Section-N" heading
opened the root first, because is_subdoc_root_candidate had no concept of
"Section" (or any other numbered keyword) as a signal at all -- "Section-I"
and "Section VII" only escaped the clamp by COINCIDENCE (their own labels
also contained the unrelated trigger words "Tender"/"Annexures"), not
because of their keyword/number shape, which prior documentation
(incorrectly) blamed on a hyphen-vs-space spelling inconsistency.

The fix: when the caller has the currently-open root's own label
(`root_label`), a heading sharing the SAME explicit numbered-family
keyword as that root (e.g. both say "Section") is treated as a SIBLING of
the root, never its child -- regardless of whether its own label also
happens to contain some unrelated trigger word. A genuinely nested child
sharing the root's own number (e.g. "Section-I.1" under root "Section-I")
is excluded via a decimal-extension guard, so real nesting is unaffected."""

import reference_index as ri
from ai_reference_index import _ground_and_resolve_chunk
from ai_indexing.prompt import CarryContext
from tests.test_ai_reference_index_parent_ref import _heading
import pytest


# ─── Unit tests: is_subdoc_root_candidate(text, family, root_label) ────────


def test_section_ii_through_vi_are_siblings_of_an_open_section_i_root():
    for label in ["Section-II", "Section-III", "Section-IV", "Section-V", "Section-VI"]:
        assert ri.is_subdoc_root_candidate(label, None, "Section-I") is True, label


def test_section_vii_is_still_a_sibling_of_section_vi_root_without_relying_on_annexures_coincidence():
    assert ri.is_subdoc_root_candidate("Section VII", None, "Section-VI") is True


def test_decimal_extension_of_the_root_s_own_number_is_not_a_sibling():
    # "Section-I.1" genuinely nests under "Section-I" -- not a sibling root.
    assert ri.is_subdoc_root_candidate("Section-I.1", None, "Section-I") is False


def test_original_section_10_1_case_is_unaffected_by_the_new_keyword_signal():
    # "JUDGMENT" has no numbered-keyword shape at all, so the new signal
    # simply never fires here -- this must still resolve False exactly as
    # it always has (no coincidental trigger word, no keyword match).
    assert ri.is_subdoc_root_candidate("Challenge to the Arbitral Award.", None, "JUDGMENT") is False


def test_a_different_keyword_family_against_the_root_is_not_treated_as_a_sibling():
    # "Article 5" vs root "Section-I" -- different keyword families, must
    # not be conflated even though both match _IMPLICIT_KEYWORD_NUMBER_RE.
    assert ri.is_subdoc_root_candidate("Article 5", None, "Section-I") is False


def test_no_root_label_falls_back_to_the_existing_behavior():
    # Without a root_label, the new branch can't fire at all -- confirms
    # it's purely additive, never a replacement for the existing checks.
    assert ri.is_subdoc_root_candidate("Section-II", None, None) is False


# ─── apply_heading_events_to_scope integration: the stack-machine clamp ───


def test_apply_heading_events_lets_section_ii_become_a_new_root_sibling_not_a_child():
    scope = {
        "stack": [{"family": "subdoc", "key": "subdoc:1", "label": "Section-I"}],
        "label_occurrence_counts": {},
    }
    events = [{"heading_text": "Section-II", "depth": 0, "block_index": 1}]
    candidates, new_scope = ri.apply_heading_events_to_scope(
        events=events, seed_scope=scope, page_num=5, has_next_page=True,
    )
    stack = new_scope["stack"]
    assert len(stack) == 1
    # "Section-II" now correctly mints into the numbered `section` family
    # (the Tier-1 hyphen fix in _SUBDOC_HYPHEN_RE) rather than falling
    # through to a generic subdoc bucket that preserved the literal text —
    # it normalizes to "Section 2" exactly like the space-spelled "Section
    # VII" already does (both via _format_label_from_key), so the sibling
    # check this test actually cares about (len == 1, not nested) still
    # holds under the new, correct key-minting behavior.
    assert stack[0]["key"] == "section:2"
    assert stack[0]["label"] == "Section 2"


def test_apply_heading_events_still_clamps_the_original_section_10_1_case():
    scope = {
        "stack": [{"family": "subdoc", "key": "subdoc:1", "label": "JUDGMENT"}],
        "label_occurrence_counts": {},
    }
    events = [{"heading_text": "Challenge to the Arbitral Award.", "depth": 0, "block_index": 1}]
    candidates, new_scope = ri.apply_heading_events_to_scope(
        events=events, seed_scope=scope, page_num=5, has_next_page=True,
    )
    stack = new_scope["stack"]
    assert len(stack) == 2
    assert stack[0]["label"] == "JUDGMENT"
    assert "Challenge" in stack[1]["label"]


def test_apply_heading_events_still_nests_a_genuine_decimal_child_of_the_root():
    scope = {
        "stack": [{"family": "subdoc", "key": "subdoc:1", "label": "Section-I"}],
        "label_occurrence_counts": {},
    }
    events = [{"heading_text": "Section-I.1", "depth": 0, "block_index": 1}]
    candidates, new_scope = ri.apply_heading_events_to_scope(
        events=events, seed_scope=scope, page_num=5, has_next_page=True,
    )
    stack = new_scope["stack"]
    assert len(stack) == 2
    assert stack[0]["label"] == "Section-I"
    # Real collision this fix had to close: _SUBDOC_HYPHEN_RE's number group
    # originally couldn't represent "roman numeral + decimal extension"
    # ("I.1"), so widening it to recognize "section" alone would have
    # truncated "Section-I.1" down to bare "I" and minted the identical
    # `section:1` key as a plain "Section-I" root — silently colliding two
    # different headings onto one key. Fixed by extending the roman branch
    # of _SUBDOC_HYPHEN_RE's number group to `(?:\.\d+)*` like the decimal
    # branch already had.
    assert stack[1]["key"] != "section:1"
    assert "I.1" in stack[1]["label"]


# ─── Chunk-level end-to-end: _ground_and_resolve_chunk with real tender-doc shapes ───


@pytest.mark.asyncio
async def test_ground_and_resolve_chunk_does_not_collapse_section_ii_through_vi_under_section_i():
    headings = [
        _heading("Section-I: Tender Particulars", page=1, level=1),
        _heading("Section-II: Instructions to Bidders", page=3, level=1),
        _heading("Section-III: Conditions of Contract", page=10, level=1),
        _heading("Section-IV: Scope of Work", page=20, level=1),
        _heading("Section-V: Technical Specifications", page=30, level=1),
        _heading("Section-VI: Price Schedule", page=40, level=1),
        _heading("Section VII: Annexures", page=50, level=1),
    ]
    page_blocks = {
        1: ["Section-I: Tender Particulars", "body"],
        3: ["Section-II: Instructions to Bidders", "body"],
        10: ["Section-III: Conditions of Contract", "body"],
        20: ["Section-IV: Scope of Work", "body"],
        30: ["Section-V: Technical Specifications", "body"],
        40: ["Section-VI: Price Schedule", "body"],
        50: ["Section VII: Annexures", "body"],
    }
    carry_context = CarryContext(open_containers=[], last_page_tail="")
    events_by_page, _ = await _ground_and_resolve_chunk(
        headings, page_blocks, carry_context, "fake-model", has_next_page_fn=lambda pn: False,
    )
    depths = {
        p: events_by_page[p][0]["depth"]
        for p in (1, 3, 10, 20, 30, 40, 50)
    }
    # Every Section-N heading must resolve as a sibling root (depth 0), not
    # get wrongly nested one level under whichever section opened first.
    assert all(d == 0 for d in depths.values()), depths
