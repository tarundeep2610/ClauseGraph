import asyncio
import json
import sys

sys.path.insert(0, "/app")

import database
import ai_indexing
import reference_index
import ai_reference_index
from ai_indexing.client import StructureIndexer
from ai_indexing.prompt import ChunkPlan, PageInput

DUP_JOB_ID = "89cfa529-2390-464c-9c3e-2446ef68c588"
FIXTURE_PATH = "/app/tests/fixtures/gold_index_89cfa529.json"

with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
    gold = json.load(f)

gold_targets = [t for t in gold.get("targets", []) if 13 <= t.get("page", 0) <= 19]
print(f"Total Gold Targets on pages 13-19: {len(gold_targets)}")

# Split pages 13-19 into 2-page chunks: [13, 14], [15, 16], [17, 18], [19]
page_groups = [[13, 14], [15, 16], [17, 18], [19]]

async def run_group(page_nums):
    all_pages = database.get_pages(DUP_JOB_ID)
    pages = [p for p in all_pages if p["page_num"] in page_nums]
    chunk = ChunkPlan(pages=[
        PageInput(
            page_num=p["page_num"],
            text=reference_index.build_layout_annotated_text(p.get("text") or "", p.get("raw_ocr_text") or "")
        )
        for p in pages
    ])
    resolved = ai_indexing.resolve_active_config()
    indexer = StructureIndexer(resolved)
    result = await indexer.extract_structure_for_chunk(
        chunk,
        carry_context="",
        include_pleading_points=True,
    )
    
    page_blocks = {}
    page_block_types = {}
    page_row_ids = {}
    for p in pages:
        pn = p["page_num"]
        blks, typs, rids = reference_index.parse_page_blocks_with_row_ids(p.get("text") or "")
        page_blocks[pn] = blks
        page_block_types[pn] = typs
        page_row_ids[pn] = rids
        
    events_by_page, _ = await ai_reference_index._ground_and_resolve_chunk(
        headings=result.headings,
        page_blocks=page_blocks,
        carry_context=ai_indexing.CarryContext(),
        detection_model="test",
        has_next_page_fn=lambda p: False,
        page_block_types=page_block_types,
        page_row_ids=page_row_ids,
    )
    return events_by_page

async def main():
    all_grounded_events = []
    for grp in page_groups:
        print(f"\nProcessing pages {grp}...")
        evs_by_page = await run_group(grp)
        for pn, evs in evs_by_page.items():
            for ev in evs:
                all_grounded_events.append((pn, ev))
        print(f"  Pages {grp}: grounded {sum(len(v) for v in evs_by_page.values())} events")

    print(f"\n=== SUMMARY FOR PAGES 13-19 WITH 2-PAGE CHUNKS ===")
    print(f"Total Gold Targets: {len(gold_targets)}")
    print(f"Total Grounded Events: {len(all_grounded_events)}")
    
    gold_by_page = {}
    for t in gold_targets:
        gold_by_page.setdefault(t.get("page"), []).append(t)
        
    act_by_page = {}
    for pn, ev in all_grounded_events:
        act_by_page.setdefault(pn, []).append(ev)
        
    total_matched = 0
    for pn in sorted(set(list(gold_by_page.keys()) + list(act_by_page.keys()))):
        g_list = gold_by_page.get(pn, [])
        a_list = act_by_page.get(pn, [])
        print(f"  Page {pn}: Gold={len(g_list)}, Act={len(a_list)}")

if __name__ == "__main__":
    asyncio.run(main())
