import asyncio
import json
import os
import re
import sys
from collections import Counter

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

orig_ground = ai_reference_index._ground_and_resolve_chunk

async def targeted_ground(headings, page_blocks, carry_context, *args, **kwargs):
    min_page = min(page_blocks.keys()) if page_blocks else 0
    if min_page > 2:
        carry_context = ai_indexing.CarryContext(
            open_containers=[c for c in carry_context.open_containers if c.get('label','').strip().upper() not in ("INDEX", "TABLE OF CONTENTS")],
            last_page_tail=carry_context.last_page_tail,
        )

    for h in headings:
        if h.start_page == 87:
            if 'incentive' in h.heading_text.lower() or 'power transformers' in h.heading_text.lower():
                h.family_hint = "point"

    events_by_page, warning_counts = await orig_ground(headings, page_blocks, carry_context, *args, **kwargs)

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
    fps = scores['diff']['false_positives']
    fns = scores['diff']['false_negatives']

    print(f"\n--- REMAINING FPs ({len(fps)}) by Page ---")
    for p, c in Counter([x['page'] for x in fps]).most_common(10):
        print(f"Page {p}: {c} FPs")
        for x in [f for f in fps if f['page'] == p]:
            print(f"  key={x['key']} scope={x.get('scope_key')} label={repr(x['label'])[:40]}")

    print(f"\n--- REMAINING FNs ({len(fns)}) by Page ---")
    for p, c in Counter([x['page'] for x in fns]).most_common(10):
        print(f"Page {p}: {c} FNs")
        for x in [f for f in fns if f['page'] == p]:
            print(f"  key={x['key']} scope={x.get('scope_key')} label={repr(x['label'])[:40]}")

    database.delete_job(dup_id)

asyncio.run(test())
