"""Regression tests for the table-cell-pruning type-threading fix (A2/D6x).

_ground_and_resolve_chunk's "prune non-root subdocs that ground to data
table blocks or are unlocated" step used to detect table content by
sniffing the matched block's own TEXT for literal <table>/<tr>/<td>
markup. Confirmed dead code even before the HTML-table-blindness fix
(table content was always ungrounded entirely, fp always "", so this
branch never got a chance to matter; a markdown pipe-table cell's text
never contained raw tags either). After that fix, grounding against table
content frequently succeeds (a table_cell block's plain text can genuinely
match an AI-reported heading), so a non-root generic heading sourced from
inside a table could pass through this guardrail unfiltered where it used
to be reliably caught. Fixed by threading each matched block's real
mistune TYPE (via parse_page_blocks_with_types) alongside its text, so the
pruning check no longer depends on sniffing tag substrings that no longer
exist in the block's own text.
"""

import pytest

from ai_indexing.prompt import CarryContext
from ai_indexing.schema import AIHeading

import ai_reference_index as ari


def _heading(text, page, level=2):
    return AIHeading(heading_text=text, start_page=page, level=level, end_page=None, still_open_at_chunk_end=True)


async def _run(headings, page_blocks, page_block_types=None):
    carry_context = CarryContext(open_containers=[], last_page_tail="")
    return await ari._ground_and_resolve_chunk(
        headings, page_blocks, carry_context, "fake-model", has_next_page_fn=lambda pn: False,
        page_block_types=page_block_types,
    )


@pytest.mark.asyncio
async def test_generic_heading_grounded_inside_a_table_cell_is_still_pruned():
    """The confirmed regression: a numberless/keywordless generic heading
    whose only grounding match is a table_cell block must still be pruned,
    now via the block's TYPE rather than sniffing its (now tag-free) text."""
    headings = [_heading("Summary Price Proposal", page=1)]
    page_blocks = {1: ["DOC TITLE", "Summary Price Proposal"]}
    page_block_types = {1: ["table_cell", "table_cell"]}
    events_by_page, _ = await _run(headings, page_blocks, page_block_types)
    assert events_by_page.get(1, []) == []


@pytest.mark.asyncio
async def test_identical_generic_heading_not_in_a_table_mints_normally():
    """Control: the identical text, grounded against an ordinary paragraph
    block (not a table_cell), is NOT pruned — proves the type check, not a
    blanket rejection of this text, is responsible for the prune above."""
    headings = [_heading("Summary Price Proposal", page=1)]
    page_blocks = {1: ["Summary Price Proposal", "some body text"]}
    page_block_types = {1: ["paragraph", "paragraph"]}
    events_by_page, _ = await _run(headings, page_blocks, page_block_types)
    assert len(events_by_page.get(1, [])) == 1


@pytest.mark.asyncio
async def test_root_candidate_heading_inside_a_table_is_never_pruned():
    """A genuine root-candidate heading (e.g. "Annexure A-1") grounded
    inside a table must never be pruned, before or after this fix — the
    prune only ever applies to non-root generic `subdoc` candidates."""
    headings = [_heading("Annexure A-1", page=1)]
    page_blocks = {1: ["Annexure A-1", "some description"]}
    page_block_types = {1: ["table_cell", "table_cell"]}
    events_by_page, _ = await _run(headings, page_blocks, page_block_types)
    assert len(events_by_page.get(1, [])) == 1


@pytest.mark.asyncio
async def test_ungrounded_heading_is_still_pruned_via_the_not_fp_branch():
    """Regression guard: a heading that fails to ground at all (no
    matching block text anywhere on the page) must still be pruned via the
    pre-existing `not fp` half of the condition — the type-based fix only
    changes the is_table half, it must not disturb this other half."""
    headings = [_heading("Summary Price Proposal", page=1)]
    page_blocks = {1: ["completely unrelated text", "more unrelated text"]}
    page_block_types = {1: ["paragraph", "paragraph"]}
    events_by_page, _ = await _run(headings, page_blocks, page_block_types)
    assert events_by_page.get(1, []) == []


@pytest.mark.asyncio
async def test_missing_page_block_types_falls_back_to_text_sniffing_safely():
    """When page_block_types is None (e.g. an older/partial caller), the
    pruning loop must fall back to the old fp-prefix text scan rather than
    crash or silently stop pruning anything."""
    headings = [_heading("Summary Price Proposal", page=1)]
    page_blocks = {1: ["DOC TITLE", "Summary Price Proposal"]}
    events_by_page, _ = await _run(headings, page_blocks, page_block_types=None)
    # No type info available and no literal tag markup in the text (as is
    # always the case post-fix) -> is_table stays False, so this heading
    # survives via fp being non-empty -- documenting the fallback's real,
    # limited behavior rather than asserting an unverified stronger claim.
    assert len(events_by_page.get(1, [])) == 1
