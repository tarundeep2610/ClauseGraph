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

job_id = "bb417471-561c-4029-bb21-3bdcf74e7dcb"
total_pages = 100

seed = {"stack": [], "boundary_ordinal": 0, "label_occurrence_counts": {}}
carry_context = ai_indexing.CarryContext(open_containers=[])

chunks = ['1_2_3', '4_5_6', '7_8_9', '10_11_12']
for chk_name in chunks:
    chk_data = rec['chunks'][chk_name]
    page_nums = chk_data['page_nums']
    print(f"\n=================== Chunk {chk_name} (pages {page_nums}) ===================")
    from ai_indexing.parsing import parse_structure_response
    model, dropped = parse_structure_response(chk_data['raw_text'], page_nums)
    cleaned = model
    print(f"Cleaned headings ({len(cleaned.headings)}):")
    for h in cleaned.headings:
        print(f"  p{h.start_page} text={repr(h.heading_text)[:40]} parent={repr(h.parent_ref)}")

    page_texts = {p: (database.get_page(job_id, p) or {}).get('text', '') for p in page_nums}
    page_blocks = {p: reference_index.parse_page_blocks(txt) for p, txt in page_texts.items()}

    events_by_page, _ = asyncio.run(ai_reference_index._ground_and_resolve_chunk(
        cleaned.headings, page_blocks, carry_context, "qwen3:8b", lambda pn: pn < total_pages
    ))

    for p in page_nums:
        evts = events_by_page.get(p, [])
        cands, seed = reference_index.apply_heading_events_to_scope(evts, seed, p, has_next_page=p < total_pages)
        extra, lg = reference_index.detect_pleading_points_for_page(page_texts[p], seed, cands)
        cands.extend(extra)
        seed["last_decimal_ground"] = lg
        print(f"\n--- Page {p} Candidates ({len(cands)}) ---")
        for c in cands:
            safe_lbl = c.get('own_label', '').encode('ascii', errors='replace').decode('ascii')
            print(f"  key={c['key']:<12} scope={c.get('scope_key',''):<30} label={safe_lbl[:40]}")
        print(f"End of page {p} stack: {[s['key'] + ' ' + s['label'] for s in seed['stack']]}")

    # Build carry_context for next chunk
    carry_containers = [
        {
            "ref_id": f"c{idx+1}",
            "label": s["label"],
            "level": idx+1,
            "page_num": page_nums[-1],
            "heading_text": s["label"],
        }
        for idx, s in enumerate(seed["stack"])
    ]
    carry_context = ai_indexing.CarryContext(open_containers=carry_containers)
