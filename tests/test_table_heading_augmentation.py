"""Tests for multi-column table clause heading augmentation and companion cell validation.

Verifies:
1. Bare decimal marker ("6.1.") in a table row is enriched with companion clause text.
2. Bare integer marker ("6") in a table row is enriched with companion title text.
3. Already-complete heading ("6.1. The bidder is advised...") is not double-augmented.
4. Non-table paragraph blocks (row_id=None) are never touched by table augmentation.
5. Non-substantive companion cells (financial figures like "50000.00") are rejected.
6. Boolean/placeholder status cells ("Yes", "N/A", "Complied") are rejected.
7. Multi-column forward search selects the description column, ignoring leading serial numbers
   and trailing status columns.
"""

import pytest

import ai_indexing
import ai_reference_index as ari
import reference_index
from ai_indexing.prompt import CarryContext
from ai_indexing.schema import AIHeading


@pytest.mark.asyncio
async def test_table_row_bare_decimal_heading_augmentation():
    """Bare decimal '6.1.' in table cell is enriched with companion text,
    mints numheading:6.1, and marks both cells used."""
    page_blocks = {
        1: [
            "Header",
            "6.1.",
            "The bidder is advised to visit and examine the site where the works are executed.",
            "Footer",
        ]
    }
    page_block_types = {1: ["paragraph", "table_cell", "table_cell", "paragraph"]}
    page_row_ids = {1: [None, 0, 0, None]}

    headings = [AIHeading(heading_text="6.1.", start_page=1, level=3, parent_ref="6")]
    carry_context = CarryContext(open_containers=[{"ref_id": "6", "label": "6 Project site visit", "level": 2}])

    events_by_page, warns = await ari._ground_and_resolve_chunk(
        headings,
        page_blocks,
        carry_context,
        "qwen3:8b",
        has_next_page_fn=lambda pn: False,
        page_block_types=page_block_types,
        page_row_ids=page_row_ids,
    )

    evs = events_by_page.get(1, [])
    assert len(evs) == 1
    ev = evs[0]
    assert ev["heading_text"].startswith("6.1. The bidder is advised to visit")
    assert ev["block_idx"] == 1
    assert "ai_row_merged_grounded" in ev["heading_confidence_reasons"]
    assert "table_row_sourced" in ev["heading_confidence_reasons"]

    # Verify minting produces numheading:6.1 and survives Line 1138 filtering
    targets, _ = reference_index.apply_heading_events_to_scope(
        evs, seed_scope=None, page_num=1, has_next_page=False,
    )
    assert len(targets) == 1
    assert targets[0]["key"] == "numheading:6.1"
    assert "6.1" in targets[0]["own_label"]


@pytest.mark.asyncio
async def test_table_row_bare_integer_heading_augmentation():
    """Bare integer '6' in table cell is enriched with companion title 'Project site visit',
    minting numheading:6."""
    page_blocks = {
        1: [
            "6",
            "Project site visit",
        ]
    }
    page_block_types = {1: ["table_cell", "table_cell"]}
    page_row_ids = {1: [1, 1]}

    headings = [AIHeading(heading_text="6", start_page=1, level=2, parent_ref=None)]
    carry_context = CarryContext(open_containers=[])

    events_by_page, _ = await ari._ground_and_resolve_chunk(
        headings,
        page_blocks,
        carry_context,
        "qwen3:8b",
        has_next_page_fn=lambda pn: False,
        page_block_types=page_block_types,
        page_row_ids=page_row_ids,
    )

    evs = events_by_page.get(1, [])
    assert len(evs) == 1
    assert evs[0]["heading_text"] == "6 Project site visit"

    targets, _ = reference_index.apply_heading_events_to_scope(
        evs, seed_scope=None, page_num=1, has_next_page=False,
    )
    assert len(targets) == 1
    assert targets[0]["key"] == "numheading:6"


@pytest.mark.asyncio
async def test_table_row_already_complete_heading_not_duplicated():
    """When the model emits the complete combined text, it grounds via _try_row_merge_match
    and does NOT duplicate or re-concatenate the text."""
    full_text = "6.1. The bidder is advised to visit and examine the site."
    page_blocks = {
        1: [
            "6.1.",
            "The bidder is advised to visit and examine the site.",
        ]
    }
    page_block_types = {1: ["table_cell", "table_cell"]}
    page_row_ids = {1: [0, 0]}

    headings = [AIHeading(heading_text=full_text, start_page=1, level=3, parent_ref="6")]
    carry_context = CarryContext(open_containers=[])

    events_by_page, _ = await ari._ground_and_resolve_chunk(
        headings,
        page_blocks,
        carry_context,
        "qwen3:8b",
        has_next_page_fn=lambda pn: False,
        page_block_types=page_block_types,
        page_row_ids=page_row_ids,
    )

    evs = events_by_page.get(1, [])
    assert len(evs) == 1
    assert evs[0]["heading_text"] == full_text


@pytest.mark.asyncio
async def test_paragraph_bare_number_not_augmented():
    """Non-table paragraph blocks (row_id=None) are never augmented by table logic."""
    page_blocks = {
        1: [
            "1.",
            "This is a paragraph in body text without table structure.",
        ]
    }
    page_block_types = {1: ["paragraph", "paragraph"]}
    page_row_ids = {1: [None, None]}

    headings = [AIHeading(heading_text="1.", start_page=1, level=2, parent_ref=None)]
    carry_context = CarryContext(open_containers=[])

    events_by_page, _ = await ari._ground_and_resolve_chunk(
        headings,
        page_blocks,
        carry_context,
        "qwen3:8b",
        has_next_page_fn=lambda pn: False,
        page_block_types=page_block_types,
        page_row_ids=page_row_ids,
    )

    evs = events_by_page.get(1, [])
    assert len(evs) == 1
    # Remains bare "1." because paragraphs have no row_id
    assert evs[0]["heading_text"] == "1."


@pytest.mark.asyncio
async def test_data_table_numeric_cell_not_promoted():
    """Pure numeric/financial amounts ('50000.00') in companion cells are rejected
    by _is_substantive_heading_companion, leaving data cells unpromoted and pruned by line 1138."""
    page_blocks = {
        1: [
            "1",
            "50000.00",
        ]
    }
    page_block_types = {1: ["table_cell", "table_cell"]}
    page_row_ids = {1: [5, 5]}

    headings = [AIHeading(heading_text="1", start_page=1, level=2, parent_ref=None)]
    carry_context = CarryContext(open_containers=[])

    events_by_page, _ = await ari._ground_and_resolve_chunk(
        headings,
        page_blocks,
        carry_context,
        "qwen3:8b",
        has_next_page_fn=lambda pn: False,
        page_block_types=page_block_types,
        page_row_ids=page_row_ids,
    )

    # Bare integer '1' fails augmentation because '50000.00' is not substantive text.
    # It mints as subdoc:1, and line 1138 prunes it because is_table is True.
    evs = events_by_page.get(1, [])
    assert len(evs) == 0


@pytest.mark.asyncio
async def test_boolean_status_cell_not_promoted():
    """Placeholder status cells ('Yes', 'N/A', 'Complied') are rejected as companion text."""
    page_blocks = {
        1: [
            "1.1.",
            "Yes",
        ]
    }
    page_block_types = {1: ["table_cell", "table_cell"]}
    page_row_ids = {1: [2, 2]}

    headings = [AIHeading(heading_text="1.1.", start_page=1, level=3, parent_ref="1")]
    carry_context = CarryContext(open_containers=[])

    events_by_page, _ = await ari._ground_and_resolve_chunk(
        headings,
        page_blocks,
        carry_context,
        "qwen3:8b",
        has_next_page_fn=lambda pn: False,
        page_block_types=page_block_types,
        page_row_ids=page_row_ids,
    )

    evs = events_by_page.get(1, [])
    # 'Yes' is rejected, '1.1.' remains bare subdoc, pruned by line 1138
    assert len(evs) == 0


@pytest.mark.asyncio
async def test_multi_column_forward_selection():
    """In a 4-column row [Serial, Marker, Description, Status], matching on Marker (Col 1)
    selects Col 2 (Description) by forward search, ignoring Col 0 (Serial) and Col 3 (Status)."""
    page_blocks = {
        1: [
            "1",                                                                      # Col 0: Serial
            "6.1.",                                                                  # Col 1: Marker
            "The bidder is advised to visit and examine the site where work occurs.", # Col 2: Description
            "Complied",                                                              # Col 3: Status
        ]
    }
    page_block_types = {1: ["table_cell", "table_cell", "table_cell", "table_cell"]}
    page_row_ids = {1: [3, 3, 3, 3]}

    headings = [AIHeading(heading_text="6.1.", start_page=1, level=3, parent_ref="6")]
    carry_context = CarryContext(open_containers=[{"ref_id": "6", "label": "6 Project site visit", "level": 2}])

    events_by_page, _ = await ari._ground_and_resolve_chunk(
        headings,
        page_blocks,
        carry_context,
        "qwen3:8b",
        has_next_page_fn=lambda pn: False,
        page_block_types=page_block_types,
        page_row_ids=page_row_ids,
    )

    evs = events_by_page.get(1, [])
    assert len(evs) == 1
    # Must pick Col 2, NOT Col 0 ("6.1. 1") and NOT Col 3 ("6.1. Complied")
    assert evs[0]["heading_text"].startswith("6.1. The bidder is advised to visit")
    assert "Complied" not in evs[0]["heading_text"]
    assert not evs[0]["heading_text"].endswith(" 1")
