import asyncio
import json
import os
import sys
sys.path.insert(0, '/app')
sys.path.insert(0, '/app/scripts')

import database
import ai_indexing
import ai_reference_index
from score_reference_index import duplicate_job

source_job_id = "bb417471-561c-4029-bb21-3bdcf74e7dcb"
dup_id = duplicate_job(source_job_id, chunk_pages=3)
replay_file = "/app/data/ai_recordings/bb417471-561c-4029-bb21-3bdcf74e7dcb_full_trial1_1791546500.json"

expected_fp = {
    "source_job_id": source_job_id,
    "chunk_pages": 3,
    "prompt_variant": "full",
}

scope = ai_indexing.replay_scope(replay_file, expected_fingerprint=expected_fp, strict_fidelity=False)

async def test():
    with scope:
        res = await ai_reference_index.reindex_job_with_ai(dup_id)
        print("Status:", res)

    # Now inspect targets on pages 9, 10, 11, 12 in the database for dup_id!
    targets = database.get_reference_targets_for_job(dup_id)
    print(f"\nTotal targets for dup_id: {len(targets)}")
    for t in [x for x in targets if 9 <= x['page'] <= 12]:
        print(f"  p{t['page']} key={t['key']} scope={t.get('scope_key')} label={repr(t.get('own_label'))[:40]}")

    database.delete_job(dup_id)

asyncio.run(test())
