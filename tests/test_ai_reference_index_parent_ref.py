"""Regression tests for ai_reference_index's parent_ref resolution and
heading->block grounding — the fixes from design review: an opaque
carry-in ref_id matched exactly (no fuzzy needed there, removing the
prompt-echo-vs-fuzzy-match ambiguity), a strictly-backward-only same-chunk
fuzzy match (which also makes a reference cycle structurally impossible),
and markdown-artifact sanitization before block grounding.
"""

import pytest

import ai_indexing
import ai_reference_index as ari
from ai_indexing.prompt import CarryContext
from ai_indexing.schema import AIHeading


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


def test_parent_ref_fuzzy_matches_a_carried_in_label_from_an_earlier_chunk():
    """Direct repro of the confirmed real bug: a chunk where the AI
    correctly flagged 12 roman-numeral grounds as headings, all with
    parent_ref naming "Grounds of Appeal" (opened in an EARLIER chunk, so
    neither an exact carry_ref_map ref_id nor a same-chunk
    chunk_heading_depths entry) — every one fell back to
    ai_unresolved_parent_ref with no fuzzy attempt against the carry-in
    labels at all."""
    carry_label_map = {ari._norm_for_match("Grounds of Appeal"): 0}
    depth, warn = ari._resolve_depth_for_heading(
        "Grounds of Appeal", level=2, carry_ref_map={}, chunk_heading_depths={},
        current_stack_len=1, carry_label_map=carry_label_map,
    )
    assert (depth, warn) == (1, None)  # exact label match, fully resolved


def test_parent_ref_fuzzy_matches_a_paraphrased_carried_in_label():
    carry_label_map = {ari._norm_for_match("Grounds of Appeal"): 0}
    depth, warn = ari._resolve_depth_for_heading(
        "Grounds Of the Appeal", level=2, carry_ref_map={}, chunk_heading_depths={},
        current_stack_len=1, carry_label_map=carry_label_map,
    )
    assert depth == 1
    assert warn == "ai_fuzzy_carry_parent_ref"


def test_parent_ref_unresolvable_even_against_carry_labels_still_degrades_safely():
    carry_label_map = {ari._norm_for_match("Grounds of Appeal"): 0}
    depth, warn = ari._resolve_depth_for_heading(
        "Some Completely Unrelated Heading", level=2, carry_ref_map={}, chunk_heading_depths={},
        current_stack_len=1, carry_label_map=carry_label_map,
    )
    assert warn == "ai_unresolved_parent_ref"
    assert depth == 1  # clamped to current_stack_len via level - 1


def test_locate_heading_block_exact_and_fuzzy():
    blocks = ["ANNEXURE A-1", "some intro text", "3.0 CONDITIONS AND COVENANTS", "body text here"]
    result = ari.locate_heading_block(blocks, "ANNEXURE A-1", set())
    assert result[0] == 0 and result[1] == "exact"
    # Re-cased text with only punctuation/case differences normalizes to an
    # identical string (_norm_for_match folds case and collapses non-word
    # chars) -> still an "exact" match, not fuzzy.
    result = ari.locate_heading_block(blocks, "3.0 Conditions and Covenants", {0, 1})
    assert result[0] == 2 and result[1] == "exact"


def test_locate_heading_block_never_reuses_a_claimed_block():
    blocks = ["3.0 CONDITIONS", "body", "3.0 CONDITIONS"]  # duplicate text on one page
    used = set()
    first = ari.locate_heading_block(blocks, "3.0 CONDITIONS", used)
    assert first[0] == 0
    used.add(first[0])
    second = ari.locate_heading_block(blocks, "3.0 CONDITIONS", used)
    assert second[0] == 2  # not 0 again


def test_locate_heading_block_returns_none_below_cutoff():
    blocks = ["completely unrelated text about welding equipment"]
    assert ari.locate_heading_block(blocks, "3.4 QUALITY PLANS", set()) is None


# ─── Short-prefix false-match fix: a "prefix" match must also clear a
# minimum length floor, not get an automatic 1.0-confidence free pass for
# any startswith relationship regardless of length ─────────────────────────
# Confirmed real bug: a bare table-cell row marker like "1." (normalizes to
# "1") trivially prefix-matched ANY heading starting with that same digit,
# at full confidence — found while rebuilding the tender-doc gold fixture
# (job 89cfa529): a heading "1. The Sole Bidder/Lead Bidder and the
# Consortium Partner" (real text only on page 8) wrongly grounded to page
# 5's Key Dates table, which has nothing but a bare "1." cell at that
# position. A ratio-based gate (candidate/target similarity) was tried
# first and rejected: most hand-authored gold headings are deliberately
# short labels that are a genuine prefix of a much longer real paragraph
# block, so a ratio cutoff wrongly broke ~226 genuine long-prefix matches
# along with the ~301 real bug cases (confirmed via a full document-wide
# scan). A pure length floor, independent of the ratio BETWEEN the two
# sides, correctly separates the two groups.


def test_locate_heading_block_rejects_a_bare_short_marker_prefix_match():
    blocks = ["1.", "unrelated body text"]
    result = ari.locate_heading_block(
        blocks, "1. The Sole Bidder/Lead Bidder and the Consortium Partner", set(),
    )
    assert result is None


def test_locate_heading_block_still_accepts_a_genuine_long_prefix_match_despite_a_large_length_mismatch():
    # Real confirmed case from job 89cfa529: a 60-char heading label is a
    # genuine prefix of a 105-char real paragraph block (ratio is only
    # 0.727 -- well below the fuzzy cutoff, exactly the shape a ratio-based
    # gate would wrongly reject, but both sides clear the length floor).
    heading = "(1) Other details can be seen in the complete tender documents"
    blocks = [heading + " available on e-portal https://mptenders.gov.in."]
    result = ari.locate_heading_block(blocks, heading, set())
    assert result == (0, "prefix", 1.0)


def test_locate_heading_block_prefix_floor_is_exactly_15_inclusive():
    # A long continuation keeps the fuzzy ratio well below the fuzzy
    # cutoff on its own (so a short-side match can only succeed via the
    # "prefix" path, never by accidentally also clearing the fuzzy bar) --
    # isolates exactly what the length floor itself gates.
    suffix = " ABOUT TIME SCHEDULE FOR EXECUTION OF WORKS AND COMMISSIONING"

    # Shorter side at 14 normalized chars -> rejected.
    short_heading = "CLAUSE NO 1456"
    assert len(ari._norm_for_match(short_heading)) == 14
    assert ari.locate_heading_block([short_heading + suffix], short_heading, set()) is None

    # Shorter side at 15 normalized chars -> accepted.
    boundary_heading = "CLAUSE NO 14567"
    assert len(ari._norm_for_match(boundary_heading)) == 15
    result = ari.locate_heading_block([boundary_heading + suffix], boundary_heading, set())
    assert result == (0, "prefix", 1.0)


# ─── Row-merge reconstruction fallback: a heading split across a table
# row's first two cells (a "number cell" + "content cell", e.g. "9.1." and
# "Bidder shall submit...") can be reconstructed as one logical unit for
# matching purposes. Confirmed pervasive real need: job 89cfa529's entire
# 135-page tender document renders almost all of its numbered clauses as
# HTML tables shaped exactly like this -- the length-floor fix above
# correctly stopped the "1."-style false match, but honestly revealed that
# 231 real headings could no longer be grounded at all (up from a baseline
# of 14 that itself was artificially low, masked by the very bug just
# fixed). This fallback recovers them. ─────────────────────────────────────


def test_locate_heading_block_row_merge_recovers_a_split_number_and_content_cell():
    blocks = ["9.1.", "Bidder shall submit only one (1) unconditional bid in response to this tender/bid document."]
    row_ids = [0, 0]
    heading = "9.1. Bidder shall submit only one (1) unconditional bid"
    result = ari.locate_heading_block(blocks, heading, set(), row_ids=row_ids)
    assert result == (0, "row_merged", 1.0)


def test_locate_heading_block_row_merge_is_opt_in_only():
    # Same inputs, but row_ids omitted (the default for every existing
    # caller) -- must still return None, exactly as today.
    blocks = ["9.1.", "Bidder shall submit only one (1) unconditional bid in response to this tender/bid document."]
    heading = "9.1. Bidder shall submit only one (1) unconditional bid"
    assert ari.locate_heading_block(blocks, heading, set()) is None


def test_locate_heading_block_row_merge_still_rejects_a_degenerate_pair():
    # Both cells in the row are tiny markers -- the length floor applies to
    # the MERGED candidate too, not just a single block.
    blocks = ["1.", "2."]
    result = ari.locate_heading_block(
        blocks, "completely unrelated heading about welding equipment specs", set(), row_ids=[0, 0],
    )
    assert result is None


@pytest.mark.asyncio
async def test_ground_and_resolve_chunk_row_merge_consumes_both_cells_and_stays_leaf_only():
    """End-to-end repro of the real job-89cfa529 case. Also proves, in one
    shot: (1) both the number cell AND the content cell are marked used --
    a second heading independently targeting the content cell alone can no
    longer claim it, so it correctly comes out ai_unlocated instead of a
    double-claim; (2) ai_row_merged_grounded is surfaced, never silently
    swallowed; (3) the existing table_row_sourced leaf-forcing mechanism
    needs zero changes -- it already fires automatically off block_idx's
    real table_cell type."""
    headings = [
        _heading("9.1. Bidder shall submit only one (1) unconditional bid", page=13, level=1),
        _heading(
            "Bidder shall submit only one (1) unconditional bid in response to this tender/bid document.",
            page=13, level=1,
        ),
    ]
    page_blocks = {13: ["9.1.", "Bidder shall submit only one (1) unconditional bid in response to this tender/bid document."]}
    page_block_types = {13: ["table_cell", "table_cell"]}
    page_row_ids = {13: [0, 0]}
    carry_context = CarryContext(open_containers=[], last_page_tail="")
    events_by_page, warning_counts = await ari._ground_and_resolve_chunk(
        headings, page_blocks, carry_context, "fake-model", has_next_page_fn=lambda pn: False,
        page_block_types=page_block_types, page_row_ids=page_row_ids,
    )
    assert warning_counts.get("ai_row_merged_grounded") == 1
    assert warning_counts.get("ai_unlocated") == 1
    merged_event, unlocated_event = events_by_page[13]
    assert "table_row_sourced" in merged_event["heading_confidence_reasons"]
    assert "ai_row_merged_grounded" in merged_event["heading_confidence_reasons"]


def test_locate_heading_in_chunk_claimed_page_row_merge_beats_a_higher_ranked_neighbor_match():
    """Without the has_direct_claimed_match fix (ai_reference_index.py:448),
    a coincidental "exact" match on a neighboring page (rank 3) would beat
    a correct "row_merged" reconstruction on the actually-claimed page
    (rank 2) purely on kind-rank, even though the claimed page's
    reconstruction is the real content and the neighbor's "exact" hit is
    coincidental. The claimed page must win."""
    heading = "9.1. Bidder shall submit only one (1) unconditional bid"
    page_blocks = {
        13: ["9.1.", "Bidder shall submit only one (1) unconditional bid in response to this tender/bid document."],
        14: ["9.1. Bidder shall submit only one (1) unconditional bid"],
    }
    row_ids = {13: [0, 0], 14: [None]}
    result = ari._locate_heading_in_chunk(page_blocks, 13, heading, {}, row_ids=row_ids)
    assert result == (13, 0, "row_merged", 1.0)


def test_locate_heading_in_chunk_never_migrates_to_a_bare_marker_on_a_neighboring_page():
    """Real-shaped integration repro of the confirmed bug: the real content
    is split across two table cells on the claimed page (a bare "1." row
    marker plus a separate content cell, neither of which alone equals the
    combined "number + text" heading the AI reported) while a neighboring
    page has nothing but its own unrelated bare "1." marker. The honest
    outcome is unlocated -- never a confident but wrong page."""
    page_blocks = {
        5: ["1.", "other text"],
        8: ["1.", "The Sole Bidder/Lead Bidder and the Consortium Partner shall be a company registered in India"],
    }
    result = ari._locate_heading_in_chunk(
        page_blocks, 8, "1. The Sole Bidder/Lead Bidder and the Consortium Partner", {},
    )
    assert result is None


def test_locate_heading_in_chunk_finds_a_match_on_a_neighboring_page_when_claimed_page_is_wrong():
    """Direct repro of the confirmed bug: the AI claims a heading is on one
    page, but the real matching text is a couple of pages away in the same
    chunk — the original single-page-only search had nothing to
    cross-check the claimed page against."""
    page_blocks = {5: ["unrelated court-fee stamp text"], 7: ["3.3 TIME SCHEDULE"]}
    result = ari._locate_heading_in_chunk(page_blocks, 5, "3.3 TIME SCHEDULE", {})
    assert result == (7, 0, "exact", 1.0)


def test_locate_heading_in_chunk_rejects_a_coincidental_fuzzy_match_with_a_different_leading_number():
    """The direct repro of the "3.3 TIME SCHEDULE"/"3.4 QUALITY PLANS"
    false-positive: a same-shaped decimal heading text with a DIFFERENT
    leading number must never be accepted, even though its fuzzy ratio
    clears the cutoff."""
    page_blocks = {9: ["3.4 TIME SCHEDULE"]}
    assert ari._locate_heading_in_chunk(page_blocks, 9, "3.3 TIME SCHEDULE", {}) is None


def test_locate_heading_in_chunk_tries_alt_text_for_a_hinted_garbled_heading():
    """alt_text (the AI's own family_hint/number_hint canonical form) is
    tried alongside the verbatim heading_text — rescues a case where the
    verbatim text alone can't be grounded but the canonical form matches
    the real page content."""
    page_blocks = {61: ["Annexure A-1"]}
    without_alt = ari._locate_heading_in_chunk(page_blocks, 61, "Zzxq Garble Nomatch", {})
    assert without_alt is None

    with_alt = ari._locate_heading_in_chunk(
        page_blocks, 61, "Zzxq Garble Nomatch", {}, alt_text="Annexure A-1",
    )
    assert with_alt == (61, 0, "exact", 1.0)


def test_markdown_artifacts_are_sanitized_before_and_after_grounding():
    from ai_indexing.schema import sanitize_heading_text
    assert sanitize_heading_text("### 3.4 QUALITY PLANS") == "3.4 QUALITY PLANS"
    assert sanitize_heading_text("**3.4 QUALITY PLANS**") == "3.4 QUALITY PLANS"
    assert sanitize_heading_text("3.4 QUALITY PLANS:") == "3.4 QUALITY PLANS"


# ─── Per-heading AI parent-resolution micro-decision (replaces the fuzzy/
# positional fallback specifically — never the exact-match path) ──────────


def test_build_parent_choice_prompt_lists_innermost_first_and_top_level_last():
    """Innermost-first, top-level-last ordering (see _build_parent_choice_
    prompt's docstring) — the safe, usually-correct default should be the
    first-listed option, not the riskiest one."""
    system_prompt, user_prompt = ari._build_parent_choice_prompt(
        "Respondent's case", ["JUDGMENT", "Challenge to the Arbitral Award"],
    )
    assert "single letter" in system_prompt
    assert "Respondent's case" in user_prompt
    assert "A) Directly inside 'Challenge to the Arbitral Award'" in user_prompt
    assert "B) Directly inside 'JUDGMENT'" in user_prompt
    assert "C) Top-level" in user_prompt


def test_parse_parent_choice_answer_maps_letters_to_depth_innermost_first():
    # num_candidates=2: A -> depth 2 (innermost), B -> depth 1, C -> depth 0 (top-level, last letter)
    assert ari._parse_parent_choice_answer("A", num_candidates=2) == 2
    assert ari._parse_parent_choice_answer("B", num_candidates=2) == 1
    assert ari._parse_parent_choice_answer("C.", num_candidates=2) == 0
    assert ari._parse_parent_choice_answer("  b  ", num_candidates=2) == 1  # lowercase, whitespace


def test_parse_parent_choice_answer_rejects_out_of_range_or_unparseable():
    assert ari._parse_parent_choice_answer("D", num_candidates=2) is None  # only A-C offered
    assert ari._parse_parent_choice_answer("", num_candidates=2) is None
    assert ari._parse_parent_choice_answer("I think it's the second one", num_candidates=2) is None


class _FakeProvider:
    def __init__(self, answer=None, raises=None):
        self.answer = answer
        self.raises = raises
        self.calls = []

    async def complete(self, *, system_prompt, user_prompt, cancel_check=None):
        self.calls.append((system_prompt, user_prompt))
        if self.raises:
            raise self.raises
        return ai_indexing.CompletionResult(text=self.answer, provider="fake", model="fake", duration_ms=0)


def _fake_get_provider(fake):
    async def _get(cfg):
        return fake
    return _get


@pytest.mark.asyncio
async def test_resolve_parent_via_ai_returns_depth_from_a_valid_answer(monkeypatch):
    fake = _FakeProvider(answer="A")  # innermost-first: "A" = directly inside the last (innermost) candidate
    monkeypatch.setattr(ai_indexing, "get_provider", _fake_get_provider(fake))
    depth = await ari._resolve_parent_via_ai(
        "Respondent's case", ["JUDGMENT", "Challenge to the Arbitral Award"], provider_config=object(),
    )
    assert depth == 2
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_resolve_parent_via_ai_returns_none_on_unparseable_answer(monkeypatch):
    fake = _FakeProvider(answer="I'm not sure, maybe the second one?")
    monkeypatch.setattr(ai_indexing, "get_provider", _fake_get_provider(fake))
    depth = await ari._resolve_parent_via_ai("X", ["A", "B"], provider_config=object())
    assert depth is None


@pytest.mark.asyncio
async def test_resolve_parent_via_ai_returns_none_on_provider_failure(monkeypatch):
    fake = _FakeProvider(raises=ai_indexing.AIRemoteError("simulated outage", provider="fake"))
    monkeypatch.setattr(ai_indexing, "get_provider", _fake_get_provider(fake))
    depth = await ari._resolve_parent_via_ai("X", ["A", "B"], provider_config=object())
    assert depth is None


@pytest.mark.asyncio
async def test_resolve_parent_via_ai_skips_the_call_with_no_candidates():
    # No monkeypatch of ai_indexing.get_provider here on purpose — an empty
    # candidate list must return before ever reaching the provider layer.
    depth = await ari._resolve_parent_via_ai("X", [], provider_config=object())
    assert depth is None


# ─── Wiring inside _ground_and_resolve_chunk: gated by resolve_parent_via_ai,
# and only ever consulted on the fuzzy/unresolved fallback path ────────────


def _heading(text, page, level, parent_ref=None):
    return AIHeading(
        heading_text=text, start_page=page, level=level, parent_ref=parent_ref,
        end_page=None, still_open_at_chunk_end=True,
    )


@pytest.mark.asyncio
async def test_ground_and_resolve_chunk_never_calls_ai_resolver_on_an_exact_match(monkeypatch):
    fake = _FakeProvider(answer="A")
    monkeypatch.setattr(ai_indexing, "get_provider", _fake_get_provider(fake))
    headings = [_heading("ANNEXURE A-1", page=1, level=1)]
    page_blocks = {1: ["ANNEXURE A-1", "body text"]}
    carry_context = CarryContext(open_containers=[], last_page_tail="")
    await ari._ground_and_resolve_chunk(
        headings, page_blocks, carry_context, "fake-model", has_next_page_fn=lambda pn: False,
        resolve_parent_via_ai=True, provider_config=object(),
    )
    assert fake.calls == []


@pytest.mark.asyncio
async def test_ground_and_resolve_chunk_uses_ai_resolver_on_the_fallback_path_when_enabled(monkeypatch):
    """Direct repro of the confirmed real bug: a heading whose parent_ref
    doesn't exactly match anything known (today: fuzzy-string-match or give
    up positionally) instead gets a bounded multiple-choice question — here
    the fake provider picks "A" (the only candidate, JUDGMENT), overriding
    what the mechanical fallback would otherwise have resolved to."""
    fake = _FakeProvider(answer="A")
    monkeypatch.setattr(ai_indexing, "get_provider", _fake_get_provider(fake))
    headings = [
        _heading("JUDGMENT", page=1, level=1),
        _heading("Challenge to the Arbitral Award", page=2, level=1, parent_ref="something unresolvable"),
    ]
    page_blocks = {1: ["JUDGMENT", "body"], 2: ["Challenge to the Arbitral Award", "body"]}
    carry_context = CarryContext(open_containers=[], last_page_tail="")
    events_by_page, warning_counts = await ari._ground_and_resolve_chunk(
        headings, page_blocks, carry_context, "fake-model", has_next_page_fn=lambda pn: False,
        resolve_parent_via_ai=True, provider_config=object(),
    )
    assert len(fake.calls) == 1
    challenge_event = events_by_page[2][0]
    assert challenge_event["depth"] == 1  # "B" = directly inside candidate 1 (JUDGMENT)
    assert "ai_micro_resolved_parent_ref" in warning_counts


@pytest.mark.asyncio
async def test_ground_and_resolve_chunk_falls_back_when_ai_resolver_is_unparseable(monkeypatch):
    """Safety net: an unparseable/failed micro-decision must never regress
    below today's existing fuzzy/positional behavior."""
    fake = _FakeProvider(answer="not sure")
    monkeypatch.setattr(ai_indexing, "get_provider", _fake_get_provider(fake))
    headings = [
        _heading("JUDGMENT", page=1, level=1),
        _heading("Challenge to the Arbitral Award", page=2, level=1, parent_ref="something unresolvable"),
    ]
    page_blocks = {1: ["JUDGMENT", "body"], 2: ["Challenge to the Arbitral Award", "body"]}
    carry_context = CarryContext(open_containers=[], last_page_tail="")
    events_by_page, warning_counts = await ari._ground_and_resolve_chunk(
        headings, page_blocks, carry_context, "fake-model", has_next_page_fn=lambda pn: False,
        resolve_parent_via_ai=True, provider_config=object(),
    )
    assert len(fake.calls) == 1
    assert "ai_unresolved_parent_ref" in warning_counts
    assert "ai_micro_resolved_parent_ref" not in warning_counts


@pytest.mark.asyncio
async def test_ground_and_resolve_chunk_never_calls_ai_resolver_when_flag_is_off(monkeypatch):
    fake = _FakeProvider(answer="B")
    monkeypatch.setattr(ai_indexing, "get_provider", _fake_get_provider(fake))
    headings = [
        _heading("JUDGMENT", page=1, level=1),
        _heading("Challenge to the Arbitral Award", page=2, level=1, parent_ref="something unresolvable"),
    ]
    page_blocks = {1: ["JUDGMENT", "body"], 2: ["Challenge to the Arbitral Award", "body"]}
    carry_context = CarryContext(open_containers=[], last_page_tail="")
    await ari._ground_and_resolve_chunk(
        headings, page_blocks, carry_context, "fake-model", has_next_page_fn=lambda pn: False,
        resolve_parent_via_ai=False, provider_config=object(),
    )
    assert fake.calls == []


@pytest.mark.asyncio
async def test_ground_and_resolve_chunk_catches_a_bare_level_only_eviction_with_no_parent_ref(monkeypatch):
    """Direct repro of the confirmed real bug found on live verification: the
    model reported "Challenge to the Arbitral Award" with NO parent_ref at
    all (just level=1), which _resolve_depth_for_heading trusts outright
    (warn=None — "no parent_ref" was never a flagged case) even though it
    evicts both JUDGMENT and FACTS from the open stack. The widened trigger
    (any resolution that would evict an open ancestor, not just ones the
    mechanical resolver already flagged) must catch this too."""
    fake = _FakeProvider(answer="B")  # "child of JUDGMENT" — correct, sibling of FACTS
    monkeypatch.setattr(ai_indexing, "get_provider", _fake_get_provider(fake))
    headings = [
        _heading("JUDGMENT", page=1, level=1),
        _heading("FACTS", page=1, level=2, parent_ref="JUDGMENT"),
        _heading("Challenge to the Arbitral Award", page=2, level=1),  # no parent_ref at all
    ]
    page_blocks = {1: ["JUDGMENT", "FACTS", "body"], 2: ["Challenge to the Arbitral Award", "body"]}
    carry_context = CarryContext(open_containers=[], last_page_tail="")
    events_by_page, warning_counts = await ari._ground_and_resolve_chunk(
        headings, page_blocks, carry_context, "fake-model", has_next_page_fn=lambda pn: False,
        resolve_parent_via_ai=True, provider_config=object(),
    )
    assert len(fake.calls) == 1
    challenge_event = events_by_page[2][0]
    assert challenge_event["depth"] == 1  # child of JUDGMENT, not evicted to root
    assert "ai_micro_resolved_parent_ref" in warning_counts


@pytest.mark.asyncio
async def test_ground_and_resolve_chunk_also_double_checks_an_exact_label_match_that_evicts_ancestors(monkeypatch):
    """An exact match against a same-chunk heading's own text (not an opaque
    carry_ref_map id) still gets double-checked when it would close out
    several open ancestors — this is deliberately wider than "only what the
    mechanical resolver already flagged," per the approved widening."""
    fake = _FakeProvider(answer="B")
    monkeypatch.setattr(ai_indexing, "get_provider", _fake_get_provider(fake))
    headings = [
        _heading("JUDGMENT", page=1, level=1),
        _heading("Conclusion", page=1, level=2, parent_ref="JUDGMENT"),
        _heading("19.", page=1, level=3, parent_ref="Conclusion"),
        _heading("5.", page=1, level=4, parent_ref="19."),
        # Exact match against "Conclusion"'s own text — resolves cleanly via
        # chunk_heading_depths, not carry_ref_map — but closes both "19."
        # and "5." on the way back up.
        _heading("20.", page=2, level=2, parent_ref="Conclusion"),
    ]
    page_blocks = {1: ["JUDGMENT", "Conclusion", "19.", "5.", "body"], 2: ["20.", "body"]}
    carry_context = CarryContext(open_containers=[], last_page_tail="")
    await ari._ground_and_resolve_chunk(
        headings, page_blocks, carry_context, "fake-model", has_next_page_fn=lambda pn: False,
        resolve_parent_via_ai=True, provider_config=object(),
    )
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_ground_and_resolve_chunk_still_trusts_a_carry_ref_id_match_even_when_it_evicts(monkeypatch):
    """The one exemption from the widened trigger: an exact match against an
    opaque carry_ref_map id (the resolver's own minted identifier, copied
    back verbatim by the model) is never second-guessed, no matter how many
    ancestors it closes — it's the most reliable signal available."""
    fake = _FakeProvider(answer="A")
    monkeypatch.setattr(ai_indexing, "get_provider", _fake_get_provider(fake))
    headings = [
        _heading("FACTS", page=1, level=2, parent_ref="Conclusion inner"),
        _heading("Resumes at root", page=2, level=1, parent_ref="c1"),  # opaque carried-in ref_id
    ]
    page_blocks = {1: ["FACTS", "body"], 2: ["Resumes at root", "body"]}
    carry_context = CarryContext(
        open_containers=[{"ref_id": "c1", "level": 1, "label": "JUDGMENT", "source": "ai"}],
        last_page_tail="",
    )
    await ari._ground_and_resolve_chunk(
        headings, page_blocks, carry_context, "fake-model", has_next_page_fn=lambda pn: False,
        resolve_parent_via_ai=True, provider_config=object(),
    )
    assert fake.calls == []
