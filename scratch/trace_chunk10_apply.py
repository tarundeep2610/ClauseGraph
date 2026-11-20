import json
import sys
sys.path.insert(0, '.')

import database
import reference_index
import ai_indexing
import ai_reference_index
from ai_indexing.schema import AIStructureChunk, normalize_chunk

rec = json.load(open('data/ai_recordings/bb417471-561c-4029-bb21-3bdcf74e7dcb_full_trial1_1791546500.json'))
txt = rec['chunks']['10_11_12']['raw_text']
comp = json.loads(txt)
chunk_model = AIStructureChunk.model_validate(comp)
cleaned, dropped = normalize_chunk(chunk_model, [10, 11, 12])

job_id = "bb417471-561c-4029-bb21-3bdcf74e7dcb"
pages = {p: database.get_page(job_id, p) for p in [10, 11, 12]}
page_texts = {p: (pages[p] or {}).get('text', '') for p in [10, 11, 12]}
page_blocks = {p: reference_index.parse_page_blocks(txt) for p, txt in page_texts.items()}

# Let's get the carry context from end of page 9 in database
scope_p9 = database.get_end_of_page_scope(job_id, 9)
print("Scope at end of page 9:", scope_p9)

carry_ctx = ai_indexing.CarryContext(open_containers=[])
events_by_page, warnings = ai_reference_index._ground_and_resolve_chunk_sync(
    cleaned.headings, page_blocks, carry_ctx, "qwen3:8b", lambda pn: pn < 100
) if hasattr(ai_reference_index, '_ground_and_resolve_chunk_sync') else ({}, {})

# Or run _ground_and_resolve_chunk with asyncio
import asyncio
events_by_page, warnings = asyncio.run(ai_reference_index._ground_and_resolve_chunk(
    cleaned.headings, page_blocks, carry_ctx, "qwen3:8b", lambda pn: pn < 100
))

print("\nEvents by page:")
for p, evts in events_by_page.items():
    print(f"  Page {p}: {len(evts)} events")
    for e in evts:
        print(f"    text={repr(e['heading_text'])[:40]} depth={e['depth']} reasons={e['heading_confidence_reasons']}")

# Now apply to page 10:
p10_evts = events_by_page.get(10, [])
cands_p10, new_scope_p10 = reference_index.apply_heading_events_to_scope(
    p10_evts, scope_p9, 10, has_next_page=True
)
print(f"\nPage 10 candidates ({len(cands_p10)}):")
for c in cands_p10:
    print(f"  key={c.get('key')} scope={c.get('scope_key')} label={repr(c.get('own_label'))[:40]}")
