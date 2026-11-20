import asyncio
import os
import sys

sys.path.insert(0, "/app")

import database
import ai_indexing
import reference_index
import ai_reference_index
from ai_indexing.client import StructureIndexer
from ai_indexing.prompt import ChunkPlan, PageInput

DUP_JOB_ID = "89cfa529-2390-464c-9c3e-2446ef68c588"

# Gold targets for pages 13-14
import json
with open("/app/tests/fixtures/gold_index_89cfa529.json", "r", encoding="utf-8") as f:
    gold = json.load(f)
gold_p13_14 = [t for t in gold.get("targets", []) if t.get("page") in (13, 14)]
print(f"Total Gold Targets on pages 13-14: {len(gold_p13_14)}")
for t in gold_p13_14:
    print(f"  GOLD: p{t.get('page')} key={t.get('key')} | {repr(t.get('label')[:40])}")

all_pages = database.get_pages(DUP_JOB_ID)
pages = [p for p in all_pages if p["page_num"] in (13, 14)]

# Run extraction on pages 13-14
chunk = ChunkPlan(pages=[
    PageInput(
        page_num=p["page_num"],
        text=reference_index.build_layout_annotated_text(p.get("text") or "", p.get("raw_ocr_text") or "")
    )
    for p in pages
])

async def test():
    resolved = ai_indexing.resolve_active_config()
    indexer = StructureIndexer(resolved)
    result = await indexer.extract_structure_for_chunk(
        chunk,
        carry_context="",
        include_pleading_points=True,
    )
    print(f"\nExtracted {len(result.headings)} headings.")
    
    # Ground them using ai_reference_index
    page_blocks = {}
    page_block_types = {}
    page_row_ids = {}
    for p in pages:
        pn = p["page_num"]
        blks, typs, rids = reference_index.parse_page_blocks_with_row_ids(p.get("text") or "")
        page_blocks[pn] = blks
        page_block_types[pn] = typs
        page_row_ids[pn] = rids
        
    events_by_page, warning_counts = await ai_reference_index._ground_and_resolve_chunk(
        headings=result.headings,
        page_blocks=page_blocks,
        carry_context=ai_indexing.CarryContext(),
        detection_model="test",
        has_next_page_fn=lambda p: False,
        page_block_types=page_block_types,
        page_row_ids=page_row_ids,
    )
    
    total_events = sum(len(evs) for evs in events_by_page.values())
    print(f"Total Grounded Events: {total_events}")
    grounded_keys = []
    for pn, evs in sorted(events_by_page.items()):
        for ev in evs:
            lbl = ev.get('label') or ev.get('heading_text') or ''
            print(f"  GROUNDED: p{pn} key={ev.get('key')} | label={repr(lbl[:40])}")
            grounded_keys.append(ev.get('key'))
            
    gold_keys = {t.get("key") for t in gold_p13_14}
    act_keys = set(grounded_keys)
    matched = gold_keys & act_keys
    print(f"\nRESULTS FOR PAGES 13-14:")
    print(f"Gold: {len(gold_keys)}, Grounded Act: {len(act_keys)}, Matched: {len(matched)}")
    print(f"Precision: {len(matched) / len(act_keys):.3f}")
    print(f"Recall:    {len(matched) / len(gold_keys):.3f}")

if __name__ == "__main__":
    asyncio.run(test())
