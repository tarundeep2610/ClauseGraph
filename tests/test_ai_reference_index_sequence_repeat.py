"""Tests for _drop_sequence_repeat_hallucinations (D11) -- the whole-block
prefix-repeat hallucination detector, sibling of the already-shipped
single-heading _drop_cross_page_hallucinated_repeats.

Ground-truth-based, not page-locality-based: a later occurrence of a
repeated contiguous 2+-heading sequence is dropped ONLY when NONE of its
headings can be located against the real parsed page blocks. Confirmed
against this job's own real recorded data that a naive page-count/gap
discriminator (copying the sibling fix's "3+ consecutive pages" shape)
would wrongly flag a LEGITIMATE repeated per-voltage-class equipment
line-item sequence (["CT","CVT","NCT","ISO (for 3 phase)","SA"] recurring
under "400 KV Lattice Structure (Standard)" and then "132 KV Lattice
Structure (Standard)") -- so this detector grounds each later occurrence
independently instead."""

import pytest

import ai_indexing
import ai_reference_index as ari
from ai_indexing.prompt import CarryContext
from tests.test_ai_reference_index_parent_ref import _heading, _fake_get_provider, _FakeProvider


def _drop(headings, page_blocks):
    return ari._drop_sequence_repeat_hallucinations(headings, page_blocks)


def test_a_single_occurrence_sequence_is_left_untouched():
    headings = [_heading("CT", page=1, level=1), _heading("CVT", page=1, level=1)]
    kept, dropped = _drop(headings, {1: ["CT", "CVT"]})
    assert dropped == 0
    assert kept == headings


def test_a_single_repeated_heading_not_part_of_a_2plus_sequence_is_untouched():
    # Length-1 repeats are the sibling fix's own job (decimal-numbered
    # headings only); this detector's minimum sequence length is 2.
    headings = [
        _heading("ANNEXURE A-1", page=1, level=1),
        _heading("ANNEXURE A-1", page=5, level=1),
    ]
    kept, dropped = _drop(headings, {1: ["ANNEXURE A-1"], 5: ["something else entirely"]})
    assert dropped == 0
    assert kept == headings


def test_a_later_occurrence_with_zero_grounding_evidence_is_dropped():
    headings = [
        _heading("4.0 SETTLEMENT OF DISPUTES", page=10, level=1),
        _heading("4.1 NOTICE OF DEFAULT", page=10, level=1),
        _heading("some unrelated heading", page=11, level=1),
        _heading("4.0 SETTLEMENT OF DISPUTES", page=12, level=1),
        _heading("4.1 NOTICE OF DEFAULT", page=12, level=1),
    ]
    page_blocks = {
        10: ["4.0 SETTLEMENT OF DISPUTES", "4.1 NOTICE OF DEFAULT", "real body text"],
        11: ["some unrelated heading", "body"],
        # Page 12's real content has nothing matching either repeated heading --
        # the model hallucinated re-emitting the same two headings instead of
        # reading this page's actual (unrelated) Bank Guarantee content.
        12: ["BANK GUARANTEE FORMAT", "whereas the bank undertakes..."],
    }
    kept, dropped = _drop(headings, page_blocks)
    assert dropped == 2
    kept_texts = [h.heading_text for h in kept]
    assert kept_texts == [
        "4.0 SETTLEMENT OF DISPUTES", "4.1 NOTICE OF DEFAULT",
        "some unrelated heading",
    ]


def test_a_genuine_repeated_sequence_that_grounds_on_its_own_page_is_kept():
    """Direct repro of the real confirmed case from job bb417471: a 5-heading
    equipment line-item sequence genuinely recurs per voltage class and
    grounds fine on its own real page -- must survive untouched, unlike the
    hallucinated-repeat case above."""
    seq = ["CT", "CVT", "NCT", "ISO (for 3 phase)", "SA"]
    headings = (
        [_heading("400 KV Lattice Structure (Standard)", page=76, level=1)]
        + [_heading(t, page=76, level=2) for t in seq]
        + [_heading("132 KV Lattice Structure (Standard)", page=77, level=1)]
        + [_heading(t, page=77, level=2) for t in seq]
    )
    page_blocks = {
        76: ["400 KV Lattice Structure (Standard)"] + seq,
        77: ["132 KV Lattice Structure (Standard)"] + seq,
    }
    kept, dropped = _drop(headings, page_blocks)
    assert dropped == 0
    assert len(kept) == len(headings)


def test_a_longer_repeated_sequence_is_resolved_whole_not_fragmented_into_shorter_subruns():
    """A 4-heading sequence that repeats as a whole must be judged once at
    its own full length -- not independently re-litigated (and potentially
    conflictingly judged) as two separate 2-heading sub-runs."""
    headings = [
        _heading("A", page=1, level=1), _heading("B", page=1, level=1),
        _heading("C", page=1, level=1), _heading("D", page=1, level=1),
        _heading("filler", page=2, level=1),
        # Later occurrence: grounds on page 3 for "A","B" but not "C","D" --
        # since ANY heading in the occurrence grounding is sufficient, the
        # whole 4-run is kept when judged as one unit.
        _heading("A", page=3, level=1), _heading("B", page=3, level=1),
        _heading("C", page=3, level=1), _heading("D", page=3, level=1),
    ]
    page_blocks = {
        1: ["A", "B", "C", "D"],
        2: ["filler"],
        3: ["A", "B", "unrelated real text", "more unrelated text"],
    }
    kept, dropped = _drop(headings, page_blocks)
    assert dropped == 0
    assert len(kept) == len(headings)


def test_grounding_probes_use_isolated_scratch_never_the_shared_used_block_idxs():
    """Two later occurrences on the SAME page, each independently located
    against the one real matching block there -- if the probe mutated a
    shared used_block_idxs across occurrences, the second occurrence's probe
    would wrongly fail to find the already-"consumed" block index."""
    headings = [
        _heading("X", page=1, level=1), _heading("Y", page=1, level=1),
        _heading("filler1", page=2, level=1),
        _heading("X", page=3, level=1), _heading("Y", page=3, level=1),
        _heading("filler2", page=4, level=1),
        _heading("X", page=5, level=1), _heading("Y", page=5, level=1),
    ]
    page_blocks = {
        1: ["X", "Y"],
        2: ["filler1"],
        3: ["X", "Y"],
        4: ["filler2"],
        5: ["X", "Y"],
    }
    kept, dropped = _drop(headings, page_blocks)
    assert dropped == 0
    assert len(kept) == len(headings)


@pytest.mark.asyncio
async def test_sequence_repeat_dropped_count_surfaces_in_warning_counts(monkeypatch):
    fake = _FakeProvider(answer="A")
    monkeypatch.setattr(ai_indexing, "get_provider", _fake_get_provider(fake))
    headings = [
        _heading("4.0 SETTLEMENT OF DISPUTES", page=10, level=1),
        _heading("4.1 NOTICE OF DEFAULT", page=10, level=1),
        _heading("4.0 SETTLEMENT OF DISPUTES", page=12, level=1),
        _heading("4.1 NOTICE OF DEFAULT", page=12, level=1),
    ]
    page_blocks = {
        10: ["4.0 SETTLEMENT OF DISPUTES", "4.1 NOTICE OF DEFAULT", "real body text"],
        12: ["BANK GUARANTEE FORMAT", "whereas the bank undertakes..."],
    }
    carry_context = CarryContext(open_containers=[], last_page_tail="")
    events_by_page, warning_counts = await ari._ground_and_resolve_chunk(
        headings, page_blocks, carry_context, "fake-model", has_next_page_fn=lambda pn: False,
    )
    assert warning_counts.get("ai_sequence_repeat_dropped") == 2
    assert 12 not in events_by_page or not events_by_page[12]
