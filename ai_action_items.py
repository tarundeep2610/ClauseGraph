"""AI-based actionable-item extraction — deadlines, obligations, conditions,
and litigation-shaped "other" items (relief sought, dispositions) — over a
job's already-OCR'd page text.

A SEPARATE pass from ai_reference_index.py's structure/cross-reference
indexing: own prompt (ai_indexing.ACTION_ITEM_SYSTEM_PROMPT), own schema
(ai_indexing.AIActionItem), own table (ocr_action_items), own on-demand
trigger (main.py POST .../reindex-action-items). Deliberately does NOT
touch reference_index.py's scope-stack machine at all — action items are a
flat per-page list, not a hierarchy, so there is no parent_ref/depth
resolution, no carry_ref_map, and no heading-style carried-forward merge
across chunk-overlap boundaries: an overlap page is simply re-extracted and
fully replaced by whichever chunk processes it last, the same "last chunk
wins" persistence semantics ai_reference_index.reindex_job_with_ai already
relies on for headings (its own per-page loop re-persists every page of
every chunk it appears in, overlap pages included — see
`_persist_page_index`'s call sites there).

Optional two-pass macro context (`ai_index_two_pass`, same config knob the
structure pass already uses) DOES reuse
ai_reference_index._extract_macro_skeleton / _get_active_macro_context
as-is — confirmed necessary, not a nice-to-have: a real tender's 40+-page
"already implemented" module catalogue has its only "this is existing
background, not a tender obligation" signal on its own root heading, far
outside any single 3-page action-item chunk's own context window. See
reindex_action_items_for_job's two_pass handling below.

Grounding is the one place this pass cannot simply reuse
ai_reference_index.py's existing `locate_heading_block`: that function
matches a candidate block's FULL text against the FULL target (exact /
prefix / whole-string difflib ratio) and claims a block exclusively via
`used_block_idxs` — both wrong for a short evidence_quote embedded in the
middle of a longer paragraph block, and wrong for the common case where one
block states both an obligation and a deadline. See
`locate_action_item_block` below.
"""

import asyncio
import collections
import difflib
import html
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import ai_indexing
import ai_reference_index
import config
import database
import reference_index
import task_queue

logger = logging.getLogger("ai_action_items")

_active_action_item_jobs: set = set()


def is_reindex_active(job_id: str) -> bool:
    return job_id in _active_action_item_jobs


# ─── Grounding ──────────────────────────────────────────────────────────────

def _norm_for_action_item_match(text: str) -> str:
    """Same normalization ai_reference_index._norm_for_match uses
    (`re.sub(r"[\\s\\W]+", " ", text.casefold()).strip()`), plus an
    html.unescape() pass FIRST. Confirmed necessary via manual reconnaissance
    against the real gold-fixture documents: un-decoded HTML entities
    (`&quot;`, `&amp;`, `&#x27;`) show up in roughly a third of table-
    embedded clauses, and without unescaping first, an entity on one side
    and the literal character it represents on the other normalize to
    different strings even though they're the same content (`&quot;`
    survives `\\W`-stripping as the letters "quot"; a real `"` character is
    itself stripped to nothing) — silently breaking grounding on exactly
    the table-heavy clauses (price schedules, key-dates tables) action
    items are most often pulled from."""
    if not text:
        return ""
    return re.sub(r"[\s\W]+", " ", html.unescape(text).casefold()).strip()


_ACTION_ITEM_FUZZY_CUTOFF = 0.85
_MATCH_KIND_RANK = {"exact": 3, "substring": 3, "fuzzy": 2, "page_text": 1}


def locate_action_item_block(
    blocks: List[str], evidence_quote: str, page_text: Optional[str] = None,
) -> Optional[Tuple[int, str, float]]:
    """Locates an AI-reported action-item evidence_quote against ONE page's
    real parsed blocks. Returns (block_idx, match_kind, ratio) — match_kind
    one of "exact"/"substring"/"fuzzy"/"page_text" — or None if nothing
    clears the cutoff. No `used_block_idxs` exclusivity: multiple items are
    allowed to ground to the same block, since grounding here is "does this
    quote exist on this page," not "which single block does this heading
    own" (unlike locate_heading_block, which this deliberately does not
    call — see module docstring)."""
    target = _norm_for_action_item_match(evidence_quote)
    if not target:
        return None

    best_idx, best_ratio = None, 0.0
    for idx, raw_blk in enumerate(blocks):
        candidate = _norm_for_action_item_match(raw_blk)
        if not candidate:
            continue
        if candidate == target:
            return idx, "exact", 1.0
        if target in candidate:
            return idx, "substring", 1.0
        ratio = difflib.SequenceMatcher(None, candidate, target).ratio()
        if ratio > best_ratio:
            best_idx, best_ratio = idx, ratio

    if best_idx is not None and best_ratio >= _ACTION_ITEM_FUZZY_CUTOFF:
        return best_idx, "fuzzy", best_ratio

    if page_text:
        # Rare fallback: the quote spans two adjacent blocks (an OCR line
        # break mid-sentence split it into separately-parsed blocks), so no
        # single block contains it whole, but the page's full concatenated
        # text does. Anchor to whichever single block came closest (even
        # below the fuzzy cutoff) rather than an arbitrary block 0 — any
        # anchor on the right page beats none.
        page_norm = _norm_for_action_item_match(page_text)
        if target in page_norm:
            return (best_idx if best_idx is not None else 0), "page_text", best_ratio

    return None


def _ground_action_item(
    item: Any, page_blocks: Dict[int, List[str]], page_texts: Dict[int, str],
) -> Optional[Dict[str, Any]]:
    """Grounds one AI-reported action item's evidence_quote against real
    page text before it's ever allowed to be persisted — this codebase's
    single most-documented recurring failure mode (per
    AI_INDEXING_ACCURACY_INVESTIGATION.md) is the model reporting something
    not actually on the page. Tries the model's claimed page first, then a
    small window of neighboring pages within the SAME chunk (the model can
    misreport which page a clause is on, exactly as already proven for
    headings — see ai_reference_index._locate_heading_in_chunk) — only
    ever searches pages already available in `page_blocks` (i.e. pages in
    this chunk), never fetches further afield. Returns a dict with the
    item's `page` corrected to wherever it actually grounded, or None if it
    grounds nowhere in the window at all (dropped, never persisted on the
    model's word alone)."""
    claimed_page = item.page
    candidate_pages = [claimed_page]
    for delta in range(1, config.AI_INDEX_GROUND_PAGE_WINDOW + 1):
        candidate_pages.append(claimed_page - delta)
        candidate_pages.append(claimed_page + delta)

    best: Optional[Tuple[int, str, float, int]] = None  # (page, kind, ratio, rank)
    for page in candidate_pages:
        blocks = page_blocks.get(page)
        if blocks is None:
            continue
        loc = locate_action_item_block(blocks, item.evidence_quote or "", page_text=page_texts.get(page))
        if loc is None:
            continue
        _block_idx, kind, ratio = loc
        rank = _MATCH_KIND_RANK.get(kind, 0)
        if (
            best is None
            or rank > best[3]
            or (rank == best[3] and abs(page - claimed_page) < abs(best[0] - claimed_page))
        ):
            best = (page, kind, ratio, rank)

    if best is None:
        return None

    found_page, _kind, _ratio, _rank = best
    return {
        "page": found_page,
        "item_type": item.item_type,
        "item_text": item.item_text,
        "date_text": item.date_text,
        "date_iso": item.date_iso,
        "anchor_event": item.anchor_event,
        "cadence": item.cadence,
        "end_page": item.end_page,
        # Reuses the exact self-reference anchor convention already used for
        # a reference-target's own page-level fallback (see main.py's
        # get_job_references: `"anchor_id": f"page-block-{page_num}"`) —
        # the Studio editor's per-page wrapper div is the only anchor that
        # reliably exists in the DOM today (see ViewerPanel.jsx's own
        # comment: the WysiwygEditor redesign dropped fine-grained
        # per-block xref anchors from the editable body, so "jump to page"
        # is already the accepted precision level for every click-to-
        # navigate feature, not something unique to action items).
        "anchor_id": f"page-block-{found_page}",
        "evidence_quote": item.evidence_quote,
        "responsible_party": item.responsible_party,
        "confidence": item.confidence,
    }


# ─── Orchestration ──────────────────────────────────────────────────────────

async def reindex_action_items_for_job(
    job_id: str,
    chunk_pages_override: Optional[int] = None,
    overlap_pages_override: Optional[int] = None,
    chunk_chars_max_override: Optional[int] = None,
    adaptive_chunking_override: Optional[bool] = None,
    two_pass_override: Optional[bool] = None,
    start_page: Optional[int] = None,
    end_page: Optional[int] = None,
) -> Dict[str, Any]:
    """Full-job actionable-item extraction — always a whole-document rebuild
    (unlike reindex_job_with_ai, there is no single-page targeted retry path
    for this pass; strictly on-demand means the common case is "run once
    against a complete, already-OCR'd job," not a per-page live hook).

    `start_page`/`end_page`, when given, restrict extraction to that page
    range only (via `database.get_pages`'s own `page_from`/`page_to` SQL
    filter) — used by the eval harness's slice mode for cheap iteration on
    a large document without reprocessing every page. Unlike
    `reindex_job_with_ai`'s equivalent, there is no seed_scope/resume
    concept to carry across the boundary: action items are flat, per-page,
    and independently extracted per chunk, so a slice is just a smaller
    page set, nothing more."""
    if job_id in _active_action_item_jobs:
        return {}
    _active_action_item_jobs.add(job_id)
    try:
        job = database.get_job(job_id, include_pages=False)
        if not job:
            return {}
        engine_params = job.get("engine_params") or {}
        chunk_pages_size = chunk_pages_override or engine_params.get("ai_index_chunk_pages") or config.AI_INDEX_CHUNK_PAGES
        overlap_pages = (
            overlap_pages_override if overlap_pages_override is not None
            else engine_params.get("ai_index_chunk_overlap_pages", config.AI_INDEX_CHUNK_OVERLAP_PAGES)
        )
        chunk_chars_max = (
            chunk_chars_max_override if chunk_chars_max_override is not None
            else engine_params.get("ai_index_chunk_chars_max", config.AI_INDEX_CHUNK_CHARS_MAX)
        )
        adaptive_chunking = (
            adaptive_chunking_override if adaptive_chunking_override is not None
            else bool(engine_params.get("ai_index_adaptive_chunking", config.AI_INDEX_ADAPTIVE_CHUNKING))
        )
        # Same config knob the structure pass already uses — conceptually the
        # same feature (macro skeleton for document-structure context) applied
        # to a sibling pipeline, not a new independent toggle.
        two_pass = (
            two_pass_override if two_pass_override is not None
            else bool(engine_params.get("ai_index_two_pass", config.AI_INDEX_TWO_PASS))
        )

        try:
            resolved = ai_indexing.resolve_active_config()
        except ai_indexing.AIConfigError as e:
            database.update_action_item_report(job_id, status="failed", error=str(e))
            return {"status": "failed", "error": str(e)}

        indexer = ai_indexing.ActionItemIndexer(resolved)

        pages = database.get_pages(job_id, page_from=start_page, page_to=end_page)
        if not pages:
            database.update_action_item_report(job_id, status="done", pages_done=0)
            return {"status": "done", "pages_done": 0}

        raw_text_by_page: Dict[int, str] = {p["page_num"]: (p.get("raw_ocr_text") or "") for p in pages}
        page_texts: Dict[int, str] = {p["page_num"]: (p.get("text") or "") for p in pages}
        page_inputs = [ai_indexing.PageInput(page_num=pn, text=text) for pn, text in page_texts.items()]

        def _cancel_check():
            return task_queue.is_job_paused(job_id)

        macro_skeleton: Optional[List[Dict[str, Any]]] = None
        if two_pass:
            try:
                # Structure-shaped pass, reused as-is — a plain StructureIndexer
                # over MACRO_SYSTEM_PROMPT, nothing action-item-specific about
                # it (see ai_reference_index._extract_macro_skeleton). Built
                # from the SAME resolved provider config as the action-item
                # indexer, but it is its own indexer instance since the macro
                # pass and the action-item pass have independent circuit
                # breakers / retry state.
                structure_indexer = ai_indexing.StructureIndexer(resolved)
                macro_skeleton = await ai_reference_index._extract_macro_skeleton(
                    pages=page_inputs, indexer=structure_indexer,
                    cancel_check=_cancel_check, raw_text_by_page=raw_text_by_page,
                )
                logger.info(
                    "Action-items two-pass: extracted %d macro containers for job %s",
                    len(macro_skeleton or []), job_id,
                )
            except Exception as e:
                # Non-fatal, same graceful-degradation stance
                # reindex_job_with_ai already takes for this same call — a
                # failed macro pass just means no document-structure context
                # for this run, not a reason to abort the whole extraction.
                logger.warning("Action-items two-pass macro extraction failed for job %s: %s", job_id, e)
                macro_skeleton = None

        if adaptive_chunking:
            chunks = ai_indexing.plan_adaptive_chunks(
                page_inputs, max_pages=chunk_pages_size, max_chars=chunk_chars_max, overlap_pages=overlap_pages,
            )
        else:
            chunks = ai_indexing.plan_chunks(
                page_inputs, max_pages=chunk_pages_size, max_chars=chunk_chars_max, overlap_pages=overlap_pages,
            )
        total_chunks = len(chunks)

        chunks_log: List[Dict[str, Any]] = []
        database.update_action_item_report(
            job_id, status="running", provider=resolved.provider_config.provider,
            model=resolved.provider_config.model, chunk_pages=chunk_pages_size,
            current_chunk=0, total_chunks=total_chunks, pages_done=0, chunks=chunks_log,
            items_found=0, items_dropped_ungrounded=0,
            two_pass=two_pass, macro_container_count=len(macro_skeleton or []),
        )

        final_status = "done"
        final_error = ""
        pages_done_total = 0
        last_persisted_page_num: Optional[int] = None
        items_found_total = 0
        items_dropped_ungrounded_total = 0

        # Deque-based so a truncated chunk can be split in half and requeued
        # at finer granularity — mirrors ai_reference_index.py's own micro
        # chunk loop exactly (see its `chunks_queue`/`result.truncated`
        # handling). `chunks_processed_count` (not a plain enumerate index)
        # tracks completed, persisted chunks for progress reporting, since a
        # split chunk is requeued rather than counted as done.
        chunks_queue = collections.deque(chunks)
        chunks_processed_count = 0
        run_first_page_num = pages[0]["page_num"] if pages else None
        last_chunk_page_nums: set = set()

        while chunks_queue:
            chunk = chunks_queue.popleft()
            if task_queue.is_job_paused(job_id):
                final_status = "paused"
                break

            is_run_first_page = (chunk.pages[0].page_num == run_first_page_num)
            if not is_run_first_page:
                await asyncio.sleep(config.AI_INDEX_MIN_CHUNK_INTERVAL_SECONDS)

            chunk_start_time = time.perf_counter()
            # Layout-annotated text is built fresh per chunk, for the model
            # prompt only — grounding/persistence below always use the
            # plain `page_texts`/`page_blocks`, never this annotated form,
            # same separation ai_reference_index.py's own chunk loop keeps.
            annotated_chunk = ai_indexing.ChunkPlan(
                pages=[
                    ai_indexing.PageInput(
                        page_num=p.page_num,
                        text=reference_index.build_layout_annotated_text(p.text, raw_text_by_page.get(p.page_num)),
                    )
                    for p in chunk.pages
                ],
                truncated_pages=chunk.truncated_pages,
                total_chars=chunk.total_chars,
            )

            macro_ctx = (
                ai_reference_index._get_active_macro_context(macro_skeleton, chunk.page_nums[0])
                if macro_skeleton else None
            )

            try:
                result = await indexer.extract_action_items_for_chunk(
                    annotated_chunk, cancel_check=_cancel_check, macro_context=macro_ctx,
                )
            except ai_indexing.AIConfigError as e:
                final_status, final_error = "failed", str(e)
                break

            if result.outcome == ai_indexing.ChunkOutcome.CANCELLED:
                final_status = "paused"
                break

            if result.truncated:
                if len(chunk.pages) > 1:
                    mid = len(chunk.pages) // 2
                    first_half = chunk.pages[:mid]
                    second_half = chunk.pages[mid:]
                    logger.warning(
                        "Action-item chunk %s truncated (finish_reason=length); splitting into %s and %s",
                        chunk.page_nums, [p.page_num for p in first_half], [p.page_num for p in second_half],
                    )
                    c_first = ai_indexing.ChunkPlan(
                        pages=first_half, truncated_pages=[],
                        total_chars=sum(len(p.text or "") for p in first_half),
                    )
                    c_second = ai_indexing.ChunkPlan(
                        pages=second_half, truncated_pages=[],
                        total_chars=sum(len(p.text or "") for p in second_half),
                    )
                    chunks_queue.appendleft(c_second)
                    chunks_queue.appendleft(c_first)
                    total_chunks += 1
                    continue
                else:
                    logger.warning(
                        "Single-page action-item chunk %s truncated; cannot split further, accepting as-is",
                        chunk.page_nums,
                    )

            warning_counts: Dict[str, int] = {}
            final_items_by_page: Dict[int, List[Dict[str, Any]]] = {}

            if result.outcome == ai_indexing.ChunkOutcome.FAILED:
                # No rule-based fallback exists for this pass — a failed
                # chunk contributes zero items for its pages, logged
                # explicitly so "extraction failed here" is never confused
                # with "this page genuinely has no action items."
                warning_counts["chunk_failed"] = 1
            else:
                page_blocks: Dict[int, List[str]] = {}
                chunk_page_texts: Dict[int, str] = {}
                for p in chunk.pages:
                    texts, _types, _row_ids = reference_index.parse_page_blocks_with_row_ids(p.text)
                    page_blocks[p.page_num] = texts
                    chunk_page_texts[p.page_num] = p.text

                ungrounded_this_chunk = 0
                for item in result.items:
                    grounded = _ground_action_item(item, page_blocks, chunk_page_texts)
                    if grounded is None:
                        ungrounded_this_chunk += 1
                        continue
                    grounded["detection_model"] = result.model
                    final_items_by_page.setdefault(grounded["page"], []).append(grounded)

                if ungrounded_this_chunk:
                    warning_counts["action_item_ungrounded_dropped"] = ungrounded_this_chunk
                    items_dropped_ungrounded_total += ungrounded_this_chunk

            for p in chunk.pages:
                pn = p.page_num
                items_for_page = final_items_by_page.get(pn, [])
                for occ_idx, row in enumerate(items_for_page):
                    row["occurrence_index"] = occ_idx
                version = database.bump_action_item_index_version(job_id)
                database.upsert_action_items_for_page(job_id, pn, items_for_page, version)
                last_persisted_page_num = pn

            items_found_total += sum(len(v) for v in final_items_by_page.values())

            # Overlap is detected by actual page-set intersection with the
            # immediately-preceding COMPLETED chunk, not a positional
            # chunk_idx>0 check — a split sub-chunk's pages never overlap
            # its sibling half (they're a clean partition of the original
            # chunk), so this naturally yields 0 overlap for split
            # fragments without special-casing them.
            this_chunk_page_nums = {p.page_num for p in chunk.pages}
            overlap_count = len(this_chunk_page_nums & last_chunk_page_nums) if not is_run_first_page else 0
            new_in_chunk = max(len(chunk.pages) - overlap_count, 0)
            pages_done_total += new_in_chunk
            last_chunk_page_nums = this_chunk_page_nums
            chunks_processed_count += 1
            duration_seconds = time.perf_counter() - chunk_start_time
            chunks_log.append({
                "first_page": chunk.page_nums[0], "last_page": chunk.page_nums[-1],
                "status": "ok" if result.outcome != ai_indexing.ChunkOutcome.FAILED else "failed",
                "error_kind": result.error_kind, "error": result.error_message,
                "item_count": len(result.items) if result.outcome != ai_indexing.ChunkOutcome.FAILED else None,
                "warnings": warning_counts,
                "duration_seconds": round(duration_seconds, 2),
                "provider_duration_ms": result.duration_ms,
            })
            database.update_action_item_report(
                job_id, current_chunk=chunks_processed_count, total_chunks=total_chunks,
                pages_done=pages_done_total, chunks=chunks_log,
                items_found=items_found_total, items_dropped_ungrounded=items_dropped_ungrounded_total,
                ai_last_persisted_page=last_persisted_page_num,
            )

        final_report = database.update_action_item_report(job_id, status=final_status, error=final_error)
        return final_report
    finally:
        _active_action_item_jobs.discard(job_id)
