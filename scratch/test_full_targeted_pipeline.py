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

# Test the exact changes to _ground_and_resolve_chunk and _recover_colon_sublists_in_chunk
orig_ground = ai_reference_index._ground_and_resolve_chunk

async def targeted_ground(headings, page_blocks, carry_context, *args, **kwargs):
    # 1. Do not carry INDEX or TABLE OF CONTENTS past page 2
    min_page = min(page_blocks.keys()) if page_blocks else 0
    if min_page > 2:
        carry_context = ai_indexing.CarryContext(
            open_containers=[c for c in carry_context.open_containers if c.get('label','').strip().upper() not in ("INDEX", "TABLE OF CONTENTS")],
            last_page_tail=carry_context.last_page_tail,
        )

    # Call original with modified logic
    events_by_page, warning_counts = await orig_ground(headings, page_blocks, carry_context, *args, **kwargs)

    # Filter out events that are non-root subdocs inside table blocks
    filtered_events_by_page = {}
    for p_num, ev_list in events_by_page.items():
        kept_evts = []
        blocks = page_blocks.get(p_num, [])
        for ev in ev_list:
            txt = ev.get('heading_text', '')
            is_subdoc_root = reference_index.is_subdoc_root_candidate(txt, ev.get('family_hint'))
            minted_fam, _, _, _, _ = reference_index.mint_key_for_heading(
                txt, boundary_ordinal=0, label_occurrence_counts={},
                family_hint=ev.get('family_hint'), number_hint=ev.get('number_hint'),
            )
            fp = ev.get('fingerprint', '')
            # If it's a non-root subdoc and grounds to a table block, drop it
            is_table = False
            if fp and blocks:
                for b in blocks:
                    if b.startswith(fp):
                        if b.strip().startswith(('<table', '<tr')) or '<td>' in b:
                            is_table = True
                        break
            if minted_fam == 'subdoc' and not is_subdoc_root:
                if is_table:
                    continue
                if not fp: # unlocated
                    continue
            kept_evts.append(ev)
        filtered_events_by_page[p_num] = kept_evts

    return filtered_events_by_page, warning_counts

ai_reference_index._ground_and_resolve_chunk = targeted_ground

async def test():
    with scope:
        await ai_reference_index.reindex_job_with_ai(dup_id)
    gold = load_gold_fixture('/app/tests/fixtures/gold_index_bb417471.json')
    scores = evaluate_run(dup_id, gold, return_diff=True)
    print("\n--- TARGETED FIX EVALUATION ---")
    print(f"AI Target Precision:     {scores['ai_precision']:.3f}")
    print(f"AI Target Recall:        {scores['ai_recall']:.3f}")
    print(f"Merged Target Precision: {scores['target_precision']:.3f}")
    print(f"Merged Target Recall:    {scores['target_recall']:.3f}")
    print(f"Drift Tax:               {scores['drift_tax']:+.3f} (ContentR={scores['labeled_content_recall']:.3f} vs KeyR={scores['labeled_key_recall']:.3f})")
    print(f"Total FPs: {len(scores['diff']['false_positives'])}  Total FNs: {len(scores['diff']['false_negatives'])}")
    database.delete_job(dup_id)

asyncio.run(test())
