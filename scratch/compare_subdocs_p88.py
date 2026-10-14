import asyncio
import json
import sys
sys.path.insert(0, '/app')
sys.path.insert(0, '/app/scripts')

import database
import reference_index
import ai_indexing
import ai_reference_index
from score_reference_index import duplicate_job, _set_engine_params, load_gold_fixture

source_job_id = 'bb417471-561c-4029-bb21-3bdcf74e7dcb'
dup_id = duplicate_job(source_job_id, chunk_pages=3)
_set_engine_params(dup_id, {'ai_index_review_rule_prior': False, 'ai_index_resolve_parent_via_ai': False})

replay_file = '/app/data/ai_recordings/bb417471-561c-4029-bb21-3bdcf74e7dcb_full_trial1_1791546500.json'
scope = ai_indexing.replay_scope(replay_file, strict_fidelity=False)

async def test():
    with scope:
        await ai_reference_index.reindex_job_with_ai(dup_id)
    gold = load_gold_fixture('/app/tests/fixtures/gold_index_bb417471.json')
    g_subs = [t for t in gold['targets'] if t['key'].startswith('subdoc:') and t['page'] <= 88]
    targets = database.get_reference_targets_for_job(dup_id)
    a_subs = [t for t in targets if t['key'].startswith('subdoc:') and t['page'] <= 88 and t['detection_source'] == 'ai']

    print(f"Gold subdocs up to p88 ({len(g_subs)}):")
    for t in g_subs:
        print(f"  Gold p{t['page']:<3} {t['key']:<10} {repr(t.get('label'))[:40]}")

    print(f"\nActual subdocs up to p88 ({len(a_subs)}):")
    for t in a_subs:
        print(f"  Act  p{t['page']:<3} {t['key']:<10} {repr(t.get('own_label'))[:40]}")

    database.delete_job(dup_id)

asyncio.run(test())
