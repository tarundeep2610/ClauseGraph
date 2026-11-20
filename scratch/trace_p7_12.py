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

# 1. Trace chunk 7_8_9
txt7 = rec['chunks']['7_8_9']['raw_text']
comp7 = json.loads(txt7)
model7 = AIStructureChunk.model_validate(comp7)
cleaned7, _ = normalize_chunk(model7, [7, 8, 9])

job_id = "bb417471-561c-4029-bb21-3bdcf74e7dcb"
pages = {p: database.get_page(job_id, p) for p in range(7, 13)}
page_texts = {p: (pages[p] or {}).get('text', '') for p in range(7, 13)}
page_blocks = {p: reference_index.parse_page_blocks(txt) for p, txt in page_texts.items()}

carry_ctx7 = ai_indexing.CarryContext(open_containers=[])
events7, _ = asyncio.run(ai_reference_index._ground_and_resolve_chunk(
    cleaned7.headings, {p: page_blocks[p] for p in [7, 8, 9]}, carry_ctx7, "qwen3:8b", lambda pn: pn < 100
))

seed = {"stack": [], "boundary_ordinal": 0, "label_occurrence_counts": {}}
for p in [7, 8, 9]:
    cands, seed = reference_index.apply_heading_events_to_scope(events7.get(p, []), seed, p, has_next_page=True)
    if not False: # pleading_points_via_ai
        extra, lg = reference_index.detect_pleading_points_for_page(page_texts[p], seed, cands)
        cands.extend(extra)
        seed["last_decimal_ground"] = lg

print("\n=== Scope at end of Page 9 ===")
print("stack:", [s['key'] + ' ' + s['label'] for s in seed['stack']])
print("last_decimal_ground:", seed.get("last_decimal_ground"))

# 2. Now trace chunk 10_11_12
txt10 = rec['chunks']['10_11_12']['raw_text']
comp10 = json.loads(txt10)
model10 = AIStructureChunk.model_validate(comp10)
cleaned10, _ = normalize_chunk(model10, [10, 11, 12])

# Carry context from end of page 9:
carry_containers = [
    {
        "ref_id": f"c{idx+1}",
        "label": s["label"],
        "level": idx+1,
        "page_num": 9,
        "heading_text": s["label"],
    }
    for idx, s in enumerate(seed["stack"])
]
carry_ctx10 = ai_indexing.CarryContext(open_containers=carry_containers)
print("\nCarry containers into chunk 10_11_12:")
for c in carry_containers:
    print(f"  {c['ref_id']}: {c['label']} (lvl {c['level']})")

events10, _ = asyncio.run(ai_reference_index._ground_and_resolve_chunk(
    cleaned10.headings, {p: page_blocks[p] for p in [10, 11, 12]}, carry_ctx10, "qwen3:8b", lambda pn: pn < 100
))

for p in [10, 11, 12]:
    evts = events10.get(p, [])
    cands, seed = reference_index.apply_heading_events_to_scope(evts, seed, p, has_next_page=True)
    extra, lg = reference_index.detect_pleading_points_for_page(page_texts[p], seed, cands)
    cands.extend(extra)
    seed["last_decimal_ground"] = lg
    print(f"\n--- Page {p} Candidates ({len(cands)}) ---")
    for c in cands:
        safe_lbl = c.get('own_label', '').encode('ascii', errors='replace').decode('ascii')
        print(f"  key={c['key']:<12} scope={c.get('scope_key',''):<30} label={safe_lbl[:40]}")
