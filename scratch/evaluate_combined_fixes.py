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

# Let's inspect what happens with our patches!
source_job_id = 'bb417471-561c-4029-bb21-3bdcf74e7dcb'
dup_id = duplicate_job(source_job_id, chunk_pages=3)
_set_engine_params(dup_id, {'ai_index_review_rule_prior': False, 'ai_index_resolve_parent_via_ai': False})

replay_file = '/app/data/ai_recordings/bb417471-561c-4029-bb21-3bdcf74e7dcb_full_trial1_1791546500.json'
scope = ai_indexing.replay_scope(replay_file, strict_fidelity=False)

# Patch locate_heading_block to ignore <table> and <tr> blocks
orig_locate_block = ai_reference_index.locate_heading_block

def patched_locate_block(blocks, heading_text, used_block_idxs=None):
    res = orig_locate_block(blocks, heading_text, used_block_idxs)
    if res is not None:
        idx, kind, ratio = res
        b_txt = blocks[idx].strip()
        if b_txt.startswith(("<table", "<tr")) or "<td>" in b_txt:
            # Don't ground AI section headings inside data tables
            return None
    return res

ai_reference_index.locate_heading_block = patched_locate_block

# Patch _ground_and_resolve_chunk for TOC and colon recovery
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

async def test():
    with scope:
        await ai_reference_index.reindex_job_with_ai(dup_id)
    gold = load_gold_fixture('/app/tests/fixtures/gold_index_bb417471.json')
    scores = evaluate_run(dup_id, gold, return_diff=True)
    print("\n--- RESULTS ---")
    print(f"AI Target Precision:     {scores['ai_precision']:.3f}")
    print(f"AI Target Recall:        {scores['ai_recall']:.3f}")
    print(f"Merged Target Precision: {scores['target_precision']:.3f}")
    print(f"Merged Target Recall:    {scores['target_recall']:.3f}")
    print(f"Drift Tax:               {scores['drift_tax']:+.3f} (ContentR={scores['labeled_content_recall']:.3f} vs KeyR={scores['labeled_key_recall']:.3f})")
    print(f"Total FPs: {len(scores['diff']['false_positives'])}  Total FNs: {len(scores['diff']['false_negatives'])}")
    database.delete_job(dup_id)

asyncio.run(test())
