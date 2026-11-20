import asyncio
import json
import os
import re
import sys
sys.path.insert(0, '/app')
sys.path.insert(0, '/app/scripts')

import database
import reference_index
import ai_indexing
import ai_reference_index
from score_reference_index import duplicate_job, _ident, _set_engine_params, evaluate_run, load_gold_fixture

source_job_id = 'bb417471-561c-4029-bb21-3bdcf74e7dcb'
dup_id = duplicate_job(source_job_id, chunk_pages=3)
_set_engine_params(dup_id, {'ai_index_review_rule_prior': False, 'ai_index_resolve_parent_via_ai': False})

replay_file = '/app/data/ai_recordings/bb417471-561c-4029-bb21-3bdcf74e7dcb_full_trial1_1791546500.json'
scope = ai_indexing.replay_scope(replay_file, strict_fidelity=False)

_COLON_INTRO_RE = re.compile(r':\s*(?:[-–—]|->)?\s*$|[-–—]\s*>\s*$')
_SUBLIST_MARKER_RE = re.compile(r'^\s*\(?(?P<marker>[a-zA-Z]|[ivxlcdmIVXLCDM]+)(?:\)\.?|\.)\s+(?P<text>\S.*)$')

def enhanced_recover_colon_sublists(headings, page_blocks):
    if not headings or not page_blocks:
        return headings

    sorted_pages = sorted(page_blocks.keys())
    existing_norms = {ai_reference_index._norm_for_match(h.heading_text) for h in headings}
    new_headings = list(headings)

    # 1. Existing colon sublist recovery from headings
    # Plus scan all blocks on each page for colon intros followed by sublists
    for p_num in sorted_pages:
        blocks = page_blocks[p_num]
        for b_i, block_raw in enumerate(blocks):
            if ai_reference_index._is_noise_or_footer(block_raw):
                continue
            block_clean = ai_reference_index._clean_block(block_raw)
            if not _COLON_INTRO_RE.search(block_clean) or len(block_clean.split()) > 35:
                continue
            if block_clean.lower().startswith("note:"):
                continue

            # Find enclosing parent heading on this page or earlier
            parent_h = None
            for h in reversed(headings):
                if h.start_page == p_num:
                    parent_h = h
                    break
            if parent_h is None:
                continue

            # Check following blocks for consecutive run of sublist markers
            consecutive_run = []
            for next_bi in range(b_i + 1, min(b_i + 20, len(blocks))):
                nxt_raw = blocks[next_bi]
                if ai_reference_index._is_noise_or_footer(nxt_raw):
                    continue
                nxt_clean = ai_reference_index._clean_block(nxt_raw)
                m = _SUBLIST_MARKER_RE.match(nxt_clean)
                if m:
                    marker = m.group("marker")
                    if marker.islower() or (len(marker) <= 4 and marker.lower() in ("i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x")):
                        consecutive_run.append((p_num, nxt_clean, marker))
                else:
                    break

            if len(consecutive_run) >= 2:
                for run_p, run_clean, marker in consecutive_run:
                    run_norm = ai_reference_index._norm_for_match(run_clean)
                    # Check if already present
                    already = False
                    for eh in new_headings:
                        if ai_reference_index._norm_for_match(eh.heading_text) == run_norm:
                            already = True
                            break
                        em = _SUBLIST_MARKER_RE.match(eh.heading_text.strip())
                        if em and em.group("marker").lower() == marker.lower() and eh.start_page == run_p:
                            already = True
                            eh.family_hint = "point"
                            eh.number_hint = marker.lower()
                            break
                    if not already:
                        existing_norms.add(run_norm)
                        synth_h = ai_indexing.AIHeading(
                            heading_text=run_clean[:200],
                            start_page=run_p,
                            parent_ref=parent_h.heading_text,
                            level=parent_h.level + 1,
                            family_hint="point",
                            number_hint=marker.lower(),
                        )
                        new_headings.append(synth_h)

    # Tag existing child headings matching sublist markers
    for h in new_headings:
        if h.parent_ref and not h.family_hint:
            m = _SUBLIST_MARKER_RE.match(h.heading_text.strip())
            if m:
                marker = m.group("marker")
                if marker.islower() or (len(marker) <= 4 and marker.lower() in ("i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x")):
                    h.family_hint = "point"
                    h.number_hint = marker.lower()

    return new_headings

orig_recover = ai_reference_index._recover_colon_sublists_in_chunk
ai_reference_index._recover_colon_sublists_in_chunk = enhanced_recover_colon_sublists

orig_ground = ai_reference_index._ground_and_resolve_chunk

async def targeted_ground(headings, page_blocks, carry_context, *args, **kwargs):
    min_page = min(page_blocks.keys()) if page_blocks else 0
    if min_page > 2:
        carry_context = ai_indexing.CarryContext(
            open_containers=[c for c in carry_context.open_containers if c.get('label','').strip().upper() not in ("INDEX", "TABLE OF CONTENTS")],
            last_page_tail=carry_context.last_page_tail,
        )

    events_by_page, warning_counts = await orig_ground(headings, page_blocks, carry_context, *args, **kwargs)

    # Filter non-root subdocs that ground to table blocks or are unlocated
    filtered_events = {}
    for p_num, ev_list in events_by_page.items():
        kept = []
        blocks = page_blocks.get(p_num, [])
        for ev in ev_list:
            txt = ev.get('heading_text', '')
            is_subdoc_root = reference_index.is_subdoc_root_candidate(txt, ev.get('family_hint'))
            minted_fam, _, _, _, _ = reference_index.mint_key_for_heading(
                txt, boundary_ordinal=0, label_occurrence_counts={},
                family_hint=ev.get('family_hint'), number_hint=ev.get('number_hint'),
            )
            fp = ev.get('fingerprint', '')
            is_table = False
            if fp and blocks:
                for b in blocks:
                    if b.startswith(fp):
                        if b.strip().startswith(('<table', '<tr')) or '<td>' in b:
                            is_table = True
                        break
            if minted_fam == 'subdoc' and not is_subdoc_root:
                if is_table or not fp:
                    continue
            kept.append(ev)
        filtered_events[p_num] = kept

    return filtered_events, warning_counts

ai_reference_index._ground_and_resolve_chunk = targeted_ground

async def test():
    with scope:
        await ai_reference_index.reindex_job_with_ai(dup_id)
    gold = load_gold_fixture('/app/tests/fixtures/gold_index_bb417471.json')
    scores = evaluate_run(dup_id, gold, return_diff=True)
    print("\n--- FINAL PIPELINE EVALUATION ---")
    print(f"AI Target Precision:     {scores['ai_precision']:.3f}")
    print(f"AI Target Recall:        {scores['ai_recall']:.3f}")
    print(f"Merged Target Precision: {scores['target_precision']:.3f}")
    print(f"Merged Target Recall:    {scores['target_recall']:.3f}")
    print(f"Drift Tax:               {scores['drift_tax']:+.3f} (ContentR={scores['labeled_content_recall']:.3f} vs KeyR={scores['labeled_key_recall']:.3f})")
    print(f"Total FPs: {len(scores['diff']['false_positives'])}  Total FNs: {len(scores['diff']['false_negatives'])}")
    database.delete_job(dup_id)

asyncio.run(test())
