import json
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

orig_ground = ai_reference_index._ground_and_resolve_chunk

async def patched_ground(headings, page_blocks, carry_context, *args, **kwargs):
    min_page = min(page_blocks.keys()) if page_blocks else 0
    if min_page > 2:
        carry_context = ai_indexing.CarryContext(
            open_containers=[c for c in carry_context.open_containers if c.get('label','').strip().upper() not in ("INDEX", "TABLE OF CONTENTS")],
            last_page_tail=carry_context.last_page_tail,
        )
    return await orig_ground(headings, page_blocks, carry_context, *args, **kwargs)

ai_reference_index._ground_and_resolve_chunk = patched_ground

replay_file = '/app/data/ai_recordings/bb417471-561c-4029-bb21-3bdcf74e7dcb_full_trial1_1791546500.json'
scope = ai_indexing.replay_scope(replay_file, strict_fidelity=False)

import asyncio
from collections import Counter
async def test():
    with scope:
        await ai_reference_index.reindex_job_with_ai(dup_id)
    gold = load_gold_fixture('/app/tests/fixtures/gold_index_bb417471.json')
    scores = evaluate_run(dup_id, gold, return_diff=True)
    fps = scores['diff']['false_positives']
    print(f"Total FPs: {len(fps)}")
    fp_pages = Counter([x['page'] for x in fps])
    print("FPs by page (top 10):", fp_pages.most_common(10))
    for p, _ in fp_pages.most_common(5):
        print(f"\n--- FPs on page {p} ---")
        for x in [f for f in fps if f['page'] == p]:
            print(f"  key={x['key']} scope={x.get('scope_key')} label={repr(x['label'])[:40]}")
    database.delete_job(dup_id)

asyncio.run(test())
