import json
import sys
sys.path.insert(0, '.')

import database
import reference_index
import ai_indexing
import ai_reference_index
from ai_indexing.schema import AIStructureChunk, normalize_chunk
import asyncio

rec = json.load(open('data/ai_recordings/bb417471-561c-4029-bb21-3bdcf74e7dcb_full_trial1_1791546500.json'))
txt = rec['chunks']['7_8_9']['raw_text']
comp = json.loads(txt)
chunk_model = AIStructureChunk.model_validate(comp)
cleaned, dropped = normalize_chunk(chunk_model, [7, 8, 9])

job_id = "bb417471-561c-4029-bb21-3bdcf74e7dcb"
pages = {p: database.get_page(job_id, p) for p in [7, 8, 9]}
page_texts = {p: (pages[p] or {}).get('text', '') for p in [7, 8, 9]}
page_blocks = {p: reference_index.parse_page_blocks(txt) for p, txt in page_texts.items()}

carry_ctx = ai_indexing.CarryContext(open_containers=[])
events_by_page, warnings = asyncio.run(ai_reference_index._ground_and_resolve_chunk(
    cleaned.headings, page_blocks, carry_ctx, "qwen3:8b", lambda pn: pn < 100
))

for p, evts in events_by_page.items():
    print(f"=== Page {p} ({len(evts)} events) ===")
    for e in evts:
        print(f"  text={repr(e['heading_text'])[:40]} depth={e['depth']} end_page={e.get('end_page')}")

# Now let's trace scope across pages 7, 8, 9
seed = database.get_end_of_page_scope(job_id, 6)
for p in [7, 8, 9]:
    evts = events_by_page.get(p, [])
    cands, new_scope = reference_index.apply_heading_events_to_scope(evts, seed, p, has_next_page=True)
    print(f"\n--- End of page {p} scope ---")
    print(f"stack: {[s['key'] + ' ' + s['label'] for s in new_scope['stack']]}")
    print(f"closed_this_page: {[c['key'] + ' ' + c['label'] for c in new_scope.get('closed_this_page', [])]}")
    seed = new_scope
