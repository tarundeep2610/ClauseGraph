"""Shared Pass 1 Macro Skeleton extraction and active document division tracking.

Used by both Document Structure Indexing (ai_reference_index.py) and
Actionable Items Extraction (ai_action_items.py) to extract high-level
structural divisions (Sections, Chapters, Annexures, Schedules, Parts)
in coarse sweeps and steer micro chunk extraction.
"""

import collections
import logging
import re
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import ai_indexing
import reference_index
from ai_indexing.guards import (
    is_table_header_or_form_field,
    is_document_noise_or_running_header,
)

logger = logging.getLogger("ai_indexing.macro")


def classify_division_role(heading_text: str, parent_clause: Optional[str] = None) -> str:
    """Classifies a top-level container or section into a functional division role:
    'as_is', 'eligibility', 'sow', 'scc', or 'general'."""
    combined = f"{heading_text or ''} {parent_clause or ''}"
    norm = re.sub(r"[\s\W]+", " ", combined.casefold()).strip()
    if any(k in norm for k in ("implemented", "as is", "as-is", "current system", "existing", "integrations", "customizations", "inventory", "bill of quantities", "bill of materials")):
        return "as_is"
    if any(k in norm for k in ("eligibility", "qualification", "check list", "checklist")):
        return "eligibility"
    if any(k in norm for k in ("scope of work", "technical specification", "technical spec", "terms of reference", "sow")):
        return "sow"
    if any(k in norm for k in ("special conditions", "general conditions", "conditions of contract", "gcc", "scc")):
        return "scc"
    return "general"


def _norm_for_match(text: str) -> str:
    return re.sub(r"[\s\W]+", " ", (text or "").casefold()).strip()


def _is_index_or_toc_page(blocks: List[str]) -> bool:
    """True if any of the opening blocks on this page explicitly titles it as an Index or Table of Contents."""
    if not blocks:
        return False
    return any(
        re.sub(r"[^\w]+", " ", b).strip().lower() in ("index", "table of contents", "contents", "table of content")
        for b in blocks[:6]
    )


async def extract_macro_skeleton(
    pages: List[ai_indexing.PageInput],
    indexer: ai_indexing.StructureIndexer,
    cancel_check=None,
    raw_text_by_page: Optional[Dict[int, str]] = None,
    coarse_chunk_pages: int = 15,
    locate_heading_fn: Optional[Callable] = None,
) -> List[Dict[str, Any]]:
    """Pass 1: Fast macro skeleton extraction using coarse chunks (~15 pages)
    and MACRO_SYSTEM_PROMPT.
    Returns list of macro containers bounded by sibling start_page - 1.
    Graceful degradation / non-fatal.
    """
    if not pages:
        return []

    if locate_heading_fn is None:
        from ai_reference_index import locate_heading_block, _is_bare_heading_marker
    else:
        from ai_reference_index import _is_bare_heading_marker
        locate_heading_block = locate_heading_fn

    total_pages = pages[-1].page_num if pages else 0
    macro_chunks_queue = collections.deque(ai_indexing.plan_chunks(
        pages,
        max_pages=coarse_chunk_pages,
        max_chars=60000,
        overlap_pages=0,
    ))
    raw_macro_headings: List[Any] = []
    while macro_chunks_queue:
        if cancel_check and cancel_check():
            break
        m_chunk = macro_chunks_queue.popleft()
        annotated_m_chunk = ai_indexing.ChunkPlan(
            pages=[
                ai_indexing.PageInput(
                    page_num=p.page_num,
                    text=reference_index.build_layout_annotated_text(p.text, (raw_text_by_page or {}).get(p.page_num)),
                )
                for p in m_chunk.pages
            ],
            truncated_pages=m_chunk.truncated_pages,
            total_chars=m_chunk.total_chars,
        )
        # Parse plain page blocks and block types for grounding validation
        m_page_blocks = {}
        m_page_types = {}
        for p in m_chunk.pages:
            b, t, _ = reference_index.parse_page_blocks_with_row_ids(p.text)
            m_page_blocks[p.page_num] = b
            m_page_types[p.page_num] = t
        try:
            m_res = await indexer.extract_structure_for_chunk(
                annotated_m_chunk,
                is_macro_pass=True,
                cancel_check=cancel_check,
            )
            if m_res.truncated:
                if len(m_chunk.pages) > 1:
                    mid = len(m_chunk.pages) // 2
                    first_half = m_chunk.pages[:mid]
                    second_half = m_chunk.pages[mid:]
                    logger.warning(
                        "Pass 1 macro chunk %s truncated (finish_reason=length); splitting into %s and %s",
                        m_chunk.page_nums,
                        [p.page_num for p in first_half],
                        [p.page_num for p in second_half],
                    )
                    c_first = ai_indexing.ChunkPlan(
                        pages=first_half,
                        truncated_pages=[],
                        total_chars=sum(len(p.text or "") for p in first_half),
                    )
                    c_second = ai_indexing.ChunkPlan(
                        pages=second_half,
                        truncated_pages=[],
                        total_chars=sum(len(p.text or "") for p in second_half),
                    )
                    macro_chunks_queue.appendleft(c_second)
                    macro_chunks_queue.appendleft(c_first)
                    continue
                else:
                    logger.warning(
                        "Pass 1 single-page macro chunk %s truncated; cannot split further, accepting result as-is",
                        m_chunk.page_nums,
                    )

            if m_res.outcome == ai_indexing.ChunkOutcome.OK and m_res.headings:
                for h in m_res.headings:
                    txt = (h.heading_text or "").strip()
                    if not txt:
                        continue
                    if is_table_header_or_form_field(txt) or is_document_noise_or_running_header(txt, h.start_page):
                        continue
                    is_bare, _ = _is_bare_heading_marker(txt)
                    if is_bare:
                        continue

                    # Grounding check: verify that this macro container actually exists
                    # on its claimed start_page (or an immediate neighbor within the chunk).
                    sp = h.start_page
                    blocks = m_page_blocks.get(sp, [])
                    loc = locate_heading_block(blocks, txt, used_block_idxs=set())
                    actual_page = sp
                    if loc is None and (sp - 1) in m_page_blocks:
                        loc = locate_heading_block(m_page_blocks[sp - 1], txt, used_block_idxs=set())
                        if loc is not None:
                            h.start_page = sp - 1
                            actual_page = sp - 1
                    elif loc is None and (sp + 1) in m_page_blocks:
                        loc = locate_heading_block(m_page_blocks[sp + 1], txt, used_block_idxs=set())
                        if loc is not None:
                            h.start_page = sp + 1
                            actual_page = sp + 1

                    # Exclude unlocated macro hallucinations (e.g. LLM guessing section pages from TOC)
                    if loc is None:
                        continue

                    # A macro container is a major top-level document division
                    # (Section, Chapter, Annexure, Schedule, Part), never an internal table cell.
                    act_types = m_page_types.get(actual_page, [])
                    if loc[0] < len(act_types) and act_types[loc[0]] == "table_cell":
                        continue

                    # Grounding check for TOC pages: An INDEX / Table of Contents page is merely
                    # an outline pointing elsewhere, not a substantive container starting on this page.
                    actual_blocks = m_page_blocks.get(h.start_page, [])
                    if _is_index_or_toc_page(actual_blocks):
                        continue

                    raw_macro_headings.append(h)
        except Exception as e:
            logger.warning("Pass 1 macro chunk %s encountered error: %s", m_chunk.page_nums, e)
            continue

    if not raw_macro_headings:
        return []

    raw_macro_headings.sort(key=lambda h: (h.start_page, h.level or 1))

    # Deduplicate headings on the same page with identical normalized text
    seen_on_page: Set[Tuple[int, str]] = set()
    unique_macro: List[Any] = []
    for h in raw_macro_headings:
        norm = _norm_for_match(h.heading_text)
        key = (h.start_page, norm)
        if key in seen_on_page:
            continue
        seen_on_page.add(key)
        unique_macro.append(h)

    # Compute container boundaries (end_page)
    containers: List[Dict[str, Any]] = []
    for idx, h in enumerate(unique_macro):
        lvl = h.level or 1
        end_p = None
        for next_h in unique_macro[idx + 1:]:
            next_lvl = next_h.level or 1
            if next_lvl <= lvl:
                if next_h.start_page > h.start_page:
                    end_p = next_h.start_page - 1
                else:
                    end_p = h.start_page
                break
        if end_p is None:
            end_p = total_pages

        containers.append({
            "heading_text": h.heading_text.strip(),
            "level": lvl,
            "start_page": h.start_page,
            "end_page": end_p,
            "parent_ref": h.parent_ref,
            "raw_heading": h,
            "division_role": classify_division_role(h.heading_text),
        })

    return containers


def get_active_macro_context(
    macro_skeleton: Optional[List[Dict[str, Any]]],
    page_num: int,
) -> Optional[Dict[str, Any]]:
    """Finds the open level-1 and level-2 macro container active on page_num."""
    if not macro_skeleton:
        return None
    active_root = None
    active_parent = None
    active_role = "general"
    for c in macro_skeleton:
        start_p = c["start_page"]
        end_p = c.get("end_page") or 999999
        if start_p <= page_num <= end_p:
            if c.get("level", 1) == 1:
                active_root = c["heading_text"]
                active_role = c.get("division_role") or classify_division_role(active_root)
            elif c.get("level") == 2:
                active_parent = c["heading_text"]

    if not active_root and not active_parent:
        return None
    return {
        "root_container": active_root,
        "parent_clause": active_parent,
        "division_role": active_role,
    }
