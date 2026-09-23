"""AI-based document-structure indexing, stitched into the existing
deterministic reference_index.py pipeline. Owns everything that reaches
into reference_index.py/database.py for the AI path: heading->block
grounding, parent_ref resolution, and the chunked dispatch loop with
per-chunk fallback to the rule-based detector.

Core design decisions (see the approved plan for the full reasoning):
- The AI replaces detection only, never the scope-stack machine — both
  paths share reference_index.apply_heading_events_to_scope /
  build_targets_for_page's identical (candidates, new_scope) contract, so
  compute_scope_ranges/scopeRanges.js/StructureTree.jsx need zero changes.
- Cross-chunk carry-forward is one variable (`seed_scope`), reassigned at
  the end of every PAGE (not chunk) iteration, regardless of which
  mechanism produced it — this is the entire cross-chunk-boundary
  correctness story; see reference_index.py's own module docstring context.
- Fallback is per-chunk, persistence is per-page: one failed chunk only
  affects that chunk's pages.
- A single retried page re-runs from that page forward with an
  early-stop-on-reconvergence check, never an unconditional walk to the end
  of the document.
"""

import asyncio
import difflib
import re
from typing import Any, Dict, List, Optional, Tuple

import ai_indexing
import config
import database
import reference_index
import task_queue

_FUZZY_PARENT_CUTOFF = 0.85


def _norm_for_match(text: str) -> str:
    return re.sub(r"[\s\W]+", " ", (text or "").casefold()).strip()


def locate_heading_block(blocks: List[str], heading_text: str, used_block_idxs: set) -> Optional[int]:
    """Locates an AI-reported (already-sanitized) heading against a page's
    real parsed blocks — exact -> prefix -> difflib ratio >= cutoff —
    scanning forward from the last consumed block so document order is
    preserved and one block is never claimed twice."""
    target = _norm_for_match(heading_text)
    if not target:
        return None
    start = (max(used_block_idxs) + 1) if used_block_idxs else 0
    best_idx, best_ratio = None, 0.0
    for idx in range(start, len(blocks)):
        if idx in used_block_idxs:
            continue
        candidate = _norm_for_match(blocks[idx])
        if not candidate:
            continue
        if candidate == target or candidate.startswith(target) or target.startswith(candidate):
            return idx
        ratio = difflib.SequenceMatcher(None, candidate, target).ratio()
        if ratio > best_ratio:
            best_idx, best_ratio = idx, ratio
    return best_idx if best_ratio >= _FUZZY_PARENT_CUTOFF else None


def _carry_in_summary(seed_scope: Optional[Dict[str, Any]], last_page_text: str = "") -> "ai_indexing.CarryContext":
    stack = (seed_scope or {}).get("stack") or []
    containers = [
        {"ref_id": f"c{depth + 1}", "level": depth + 1, "label": entry.get("label", ""),
         "source": entry.get("source", "rule")}
        for depth, entry in enumerate(stack)
    ]
    return ai_indexing.CarryContext(open_containers=containers, last_page_tail=last_page_text)


def _resolve_depth_for_heading(
    parent_ref: Optional[str], level: int,
    carry_ref_map: Dict[str, int],           # ref_id -> depth of that container
    chunk_heading_depths: Dict[str, int],    # normalized heading_text (earlier in THIS chunk) -> its own depth
    current_stack_len: int,
) -> Tuple[int, Optional[str]]:
    """Returns (depth_to_push_at, warning_reason_or_None). `depth` is this
    heading's own resulting depth once pushed (== number of ancestors kept
    on the stack before it). Resolution never looks at family/rank — see
    reference_index.mint_key_for_heading's docstring for why that matters."""
    if parent_ref:
        if parent_ref in carry_ref_map:
            return carry_ref_map[parent_ref] + 1, None

        norm_ref = _norm_for_match(parent_ref)
        best_key, best_ratio = None, 0.0
        for heading_key, depth in chunk_heading_depths.items():
            if heading_key == norm_ref:
                return depth + 1, None
            ratio = difflib.SequenceMatcher(None, heading_key, norm_ref).ratio()
            if ratio > best_ratio:
                best_key, best_ratio = heading_key, ratio
        if best_key is not None and best_ratio >= _FUZZY_PARENT_CUTOFF:
            return chunk_heading_depths[best_key] + 1, "ai_fuzzy_parent_ref"

        depth = max(0, min(level - 1, current_stack_len))
        return depth, "ai_unresolved_parent_ref"

    depth = max(0, min(level - 1, current_stack_len))
    return depth, None


_SENTENCE_END_CHARS = reference_index._SENTENCE_END_CHARS


def _ground_and_resolve_chunk(
    headings: List[Any],  # List[ai_indexing.AIHeading], already normalized + sorted by start_page
    page_blocks: Dict[int, List[str]],
    carry_context: "ai_indexing.CarryContext",
    detection_model: str,
    has_next_page_fn,
) -> "Tuple[Dict[int, List[Dict[str, Any]]], Dict[str, int]]":
    """Resolves parent_ref -> depth and grounds each heading against its
    page's real blocks, walking headings in (page, order) sequence while
    tracking a simulated stack depth so within-chunk parent references
    resolve against the same state apply_heading_events_to_scope will later
    reconstruct for real. Returns ({page_num: [event, ...]}, warning_counts)."""
    carry_ref_map = {c["ref_id"]: c["level"] - 1 for c in carry_context.open_containers}
    sim_stack_len = len(carry_context.open_containers)
    chunk_heading_depths: Dict[str, int] = {}
    used_block_idxs: Dict[int, set] = {}
    # First pass: resolve depth + locate a block index per heading.
    located: List[Dict[str, Any]] = []
    warning_counts: Dict[str, int] = {}

    for h in headings:
        depth, warn = _resolve_depth_for_heading(
            h.parent_ref, h.level, carry_ref_map, chunk_heading_depths, sim_stack_len,
        )
        sim_stack_len = depth + 1
        chunk_heading_depths[_norm_for_match(h.heading_text)] = depth
        if warn:
            warning_counts[warn] = warning_counts.get(warn, 0) + 1

        blocks = page_blocks.get(h.start_page, [])
        used = used_block_idxs.setdefault(h.start_page, set())
        block_idx = locate_heading_block(blocks, h.heading_text, used)
        if block_idx is not None:
            used.add(block_idx)
        else:
            warning_counts["ai_unlocated"] = warning_counts.get("ai_unlocated", 0) + 1

        located.append({
            "heading": h, "depth": depth, "warn": warn, "block_idx": block_idx,
        })

    # Second pass, per page: sort by block_idx (unlocated last) so snippet
    # boundaries are computed against the next LOCATED heading on the page.
    events_by_page: Dict[int, List[Dict[str, Any]]] = {}
    by_page: Dict[int, List[Dict[str, Any]]] = {}
    for item in located:
        by_page.setdefault(item["heading"].start_page, []).append(item)

    for page_num, items in by_page.items():
        blocks = page_blocks.get(page_num, [])
        located_items = sorted((i for i in items if i["block_idx"] is not None), key=lambda i: i["block_idx"])
        for pos, item in enumerate(located_items):
            idx = item["block_idx"]
            next_idx = located_items[pos + 1]["block_idx"] if pos + 1 < len(located_items) else len(blocks)
            item["snippet_blocks"] = blocks[idx:next_idx]

        page_events = []
        for item in items:  # preserve original (page, order) sequence, not the by-block_idx sort
            h = item["heading"]
            reasons = [item["warn"]] if item["warn"] else []
            if item["block_idx"] is not None:
                snippet_blocks = item.get("snippet_blocks") or [blocks[item["block_idx"]]]
                snippet = "\n\n".join(snippet_blocks)
                fingerprint = blocks[item["block_idx"]][:30]
                confidence = "high" if not reasons else "low"
                trimmed = snippet.rstrip()
                truncated = bool(trimmed) and trimmed[-1] not in _SENTENCE_END_CHARS
            else:
                reasons.append("ai_unlocated")
                snippet = h.heading_text
                fingerprint = ""
                confidence = "low"
                truncated = False

            page_events.append({
                "heading_text": h.heading_text,
                "depth": item["depth"],
                "end_page": h.end_page if not h.still_open_at_chunk_end else None,
                "fingerprint": fingerprint,
                "snippet": snippet,
                "truncated": truncated and has_next_page_fn(page_num),
                "heading_confidence": confidence,
                "heading_confidence_reasons": reasons,
                "detection_model": detection_model,
                "start_line_hint": h.start_line_hint,
                "family_hint": h.family_hint,
                "number_hint": h.number_hint,
            })
        events_by_page[page_num] = page_events

    return events_by_page, warning_counts


async def reindex_job_with_ai(
    job_id: str, start_page: Optional[int] = None, text_source: str = "clean",
) -> Dict[str, Any]:
    """Full-job AI reindex, chunked, with per-chunk fallback to the
    rule-based detector. `start_page`, when given, is a targeted re-run
    (triggered after a single page's manual retry) rather than a full
    rebuild — see the bounded/early-stop logic below.

    `text_source` ("clean" default, or "raw") picks which stored per-page
    text becomes the AI's input: the cleaned OCR text (`text`), or the raw,
    pre-clean_detection_tags() text (`raw_ocr_text`) that still carries
    Unlimited-OCR's `<|det|>type [bbox]<|/det|>` layout tags when the job ran
    with grounding enabled. A page OCR'd without grounding has no tags in
    `raw_ocr_text` at all, so this is a silent no-op (falls back to `text`)
    for any non-grounded page — added to let a reviewer A/B the two inputs
    on the same job, not because raw is known to be better.

    Checkpoint/resume (start_page=None only — a targeted retry always runs
    from its own explicit start_page): a prior run of THIS function that
    was paused mid-way is resumed from `ai_index_report["ai_last_persisted_
    page"]` rather than restarting at page 1. This is deliberately its own
    tracked value, NOT `database.get_reference_index_frontier()` — that
    frontier is shared with the always-rule-based LIVE per-page hook, which
    already advances it to total_pages during ordinary OCR, so reusing it
    here would make a genuinely fresh AI run think it was already done."""
    job = database.get_job(job_id, include_pages=False)
    if not job:
        return {}
    total_pages = job.get("total_pages") or 0
    engine_params = job.get("engine_params") or {}
    chunk_pages_size = engine_params.get("ai_index_chunk_pages") or config.AI_INDEX_CHUNK_PAGES

    try:
        resolved = ai_indexing.resolve_active_config()
    except ai_indexing.AIConfigError as e:
        database.update_ai_index_report(job_id, status="failed", error=str(e))
        return {"status": "failed", "error": str(e)}

    indexer = ai_indexing.StructureIndexer(resolved)

    all_pages = database.get_pages(job_id)
    resume_from = None
    if start_page is None:
        prior_report = database.get_ai_index_report(job_id)
        if prior_report.get("status") == "paused" and prior_report.get("ai_last_persisted_page"):
            resume_from = prior_report["ai_last_persisted_page"] + 1

    effective_start = start_page if start_page is not None else resume_from
    if effective_start is not None:
        pages = [p for p in all_pages if p["page_num"] >= effective_start]
        seed_scope = database.get_end_of_page_scope(job_id, effective_start - 1) if effective_start > 1 else None
    else:
        pages = all_pages
        seed_scope = None
        database.set_reference_index_frontier(job_id, 0)

    if not pages:
        return {"status": "done", "pages_done": 0}

    def _page_text(p):
        if text_source == "raw":
            return p.get("raw_ocr_text") or p.get("text") or ""
        return p.get("text") or ""
    page_inputs = [ai_indexing.PageInput(page_num=p["page_num"], text=_page_text(p)) for p in pages]
    chunks = ai_indexing.plan_chunks(
        page_inputs,
        max_pages=chunk_pages_size,
        overlap_pages=config.AI_INDEX_CHUNK_OVERLAP_PAGES,
    )
    total_chunks = len(chunks)
    bounded_stop = start_page is not None

    continuing = start_page is not None or resume_from is not None
    report = database.get_ai_index_report(job_id)
    report.update({
        "status": "running", "provider": resolved.provider_config.provider,
        "model": resolved.provider_config.model, "chunk_pages": chunk_pages_size,
        "text_source": text_source,
        "current_chunk": 0, "total_chunks": total_chunks,
        "pages_done": report.get("pages_done", 0) if continuing else 0,
        "chunks": report.get("chunks") if continuing else [],
    })
    database.set_ai_index_report(job_id, report)

    last_page_text = ""
    if effective_start and effective_start > 1:
        prev = database.get_page(job_id, effective_start - 1)
        last_page_text = (prev or {}).get("text") or ""

    final_status = "done"
    final_error = ""
    pages_done_total = report.get("pages_done", 0)
    last_persisted_page_num: Optional[int] = None
    carried_headings: Dict[int, List[Any]] = {}
    highest_processed_this_run: int = 0

    def _cancel_check():
        return task_queue.is_job_paused(job_id)

    for chunk_idx, chunk in enumerate(chunks):
        if task_queue.is_job_paused(job_id):
            final_status = "paused"
            break

        if chunk_idx > 0:
            await asyncio.sleep(config.AI_INDEX_MIN_CHUNK_INTERVAL_SECONDS)

        if chunk_idx > 0:
            first_page_num = chunk.pages[0].page_num
            if first_page_num > 1:
                seed_scope = database.get_end_of_page_scope(job_id, first_page_num - 1)
                prev_page = database.get_page(job_id, first_page_num - 1)
                last_page_text = (prev_page or {}).get("text") or ""
            else:
                seed_scope, last_page_text = None, ""

        carry_context = _carry_in_summary(seed_scope, last_page_text)
        try:
            result = await indexer.extract_structure_for_chunk(
                chunk, carry_context=carry_context, cancel_check=_cancel_check,
            )
        except ai_indexing.AIConfigError as e:
            final_status, final_error = "failed", str(e)
            break

        if result.outcome == ai_indexing.ChunkOutcome.CANCELLED:
            final_status = "paused"
            break

        source = "rule" if result.outcome == ai_indexing.ChunkOutcome.FAILED else "ai"
        events_by_page: Dict[int, List[Dict[str, Any]]] = {}
        warning_counts: Dict[str, int] = {}
        if source == "ai":
            page_blocks = {p.page_num: reference_index.parse_page_blocks(p.text) for p in chunk.pages}

            # Raw heading merge for overlap pages before grounding
            chunk_headings_by_page: Dict[int, List[Any]] = {}
            for h in result.headings:
                chunk_headings_by_page.setdefault(h.start_page, []).append(h)

            final_headings: List[Any] = []
            for p in chunk.pages:
                pn = p.page_num
                cur_headings = chunk_headings_by_page.get(pn, [])
                carried_for_page = carried_headings.get(pn, [])
                if carried_for_page:
                    cur_norm_texts = {_norm_for_match(h.heading_text) for h in cur_headings}
                    missed_headings = [
                        h for h in carried_for_page if _norm_for_match(h.heading_text) not in cur_norm_texts
                    ]
                    if missed_headings:
                        merged_group = list(cur_headings) + list(missed_headings)
                        blocks = page_blocks.get(pn, [])

                        def _rank_heading(h):
                            idx = locate_heading_block(blocks, h.heading_text, used_block_idxs=set())
                            return (idx is None, idx if idx is not None else 0)

                        merged_group.sort(key=_rank_heading)
                        final_headings.extend(merged_group)
                    else:
                        final_headings.extend(cur_headings)
                else:
                    final_headings.extend(cur_headings)

            if config.AI_INDEX_CHUNK_OVERLAP_PAGES > 0:
                trailing_overlap_pages = [p.page_num for p in chunk.pages[-config.AI_INDEX_CHUNK_OVERLAP_PAGES:]]
                carried_headings = {
                    pn: [h for h in final_headings if h.start_page == pn]
                    for pn in trailing_overlap_pages
                }
            else:
                carried_headings = {}

            events_by_page, warning_counts = _ground_and_resolve_chunk(
                final_headings, page_blocks, carry_context, resolved.provider_config.model,
                has_next_page_fn=lambda pn: pn < total_pages,
            )
        else:
            carried_headings = {}

        chunk_page_texts = {p.page_num: p.text for p in chunk.pages}
        stopped_early = False
        pages_processed_this_chunk = 0

        if source == "rule":
            overlap_count = config.AI_INDEX_CHUNK_OVERLAP_PAGES if chunk_idx > 0 else 0
            if overlap_count > 0 and len(chunk.pages) > overlap_count:
                last_overlap_page_num = chunk.pages[overlap_count - 1].page_num
                seed_scope = database.get_end_of_page_scope(job_id, last_overlap_page_num)
                prev_p = database.get_page(job_id, last_overlap_page_num)
                last_page_text = (prev_p or {}).get("text") or ""
            pages_to_process = chunk.pages[overlap_count:]
        else:
            pages_to_process = chunk.pages

        for p in pages_to_process:
            page_num = p.page_num
            text = chunk_page_texts[page_num]
            is_revisit = page_num <= highest_processed_this_run
            previous_scope = (
                database.get_end_of_page_scope(job_id, page_num)
                if (bounded_stop and not is_revisit)
                else None
            )

            if source == "ai":
                events = events_by_page.get(page_num, [])
                cands, new_scope = reference_index.apply_heading_events_to_scope(
                    events, seed_scope, page_num, has_next_page=page_num < total_pages,
                )
            else:
                cands, new_scope = reference_index.build_targets_for_page(
                    text, seed_scope, has_next_page=page_num < total_pages,
                )

            reference_index.validate_scope_shape(new_scope)
            reference_index._persist_page_index(
                job_id, page_num, text, cands, new_scope, provisional=False,
                detection_source=source, detection_model=resolved.provider_config.model,
            )
            current_frontier = database.get_reference_index_frontier(job_id) or 0
            database.set_reference_index_frontier(job_id, max(current_frontier, page_num))
            seed_scope = new_scope
            last_page_text = text
            last_persisted_page_num = page_num
            pages_processed_this_chunk += 1
            highest_processed_this_run = max(highest_processed_this_run, page_num)

            if bounded_stop and not is_revisit and previous_scope is not None and previous_scope == new_scope:
                stopped_early = True
                break

        overlap_count_ai = config.AI_INDEX_CHUNK_OVERLAP_PAGES if (chunk_idx > 0 and source == "ai") else 0
        new_in_chunk = max(pages_processed_this_chunk - overlap_count_ai, 0)
        pages_done_total += new_in_chunk
        rep = database.get_ai_index_report(job_id)
        chunks_log = rep.get("chunks") or []
        chunks_log.append({
            "first_page": chunk.page_nums[0], "last_page": chunk.page_nums[-1],
            "status": "ok" if source == "ai" else "fallback",
            "error_kind": result.error_kind, "error": result.error_message,
            "heading_count": len(result.headings) if source == "ai" else None,
            "warnings": warning_counts,
        })
        database.update_ai_index_report(
            job_id, current_chunk=chunk_idx + 1, total_chunks=total_chunks,
            pages_done=pages_done_total, chunks=chunks_log,
            ai_last_persisted_page=last_persisted_page_num,
        )

        if stopped_early:
            break

    database.update_ai_index_report(job_id, status=final_status, error=final_error)
    return {"status": final_status, "pages_done": pages_done_total, "total_chunks": total_chunks}
