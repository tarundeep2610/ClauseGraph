import asyncio
import time
import database
import ai_indexing
import ai_reference_index
from ai_indexing.client import StructureIndexer
from ai_indexing.prompt import PageInput, plan_adaptive_chunks

SRC_JOB_ID = "89cfa529-2390-464c-9c3e-2446ef68c588"

async def test():
    all_pages = database.get_pages(SRC_JOB_ID)
    raw_text_by_page = {p["page_num"]: (p.get("raw_ocr_text") or "") for p in all_pages}
    page_inputs = [PageInput(page_num=p["page_num"], text=p.get("text") or "") for p in all_pages if p["page_num"] <= 20]
    
    resolved = ai_indexing.resolve_active_config()
    indexer = StructureIndexer(resolved)

    print("Testing Pass 1: Macro Skeleton on first 20 pages...")
    t0 = time.time()
    skeleton = await ai_reference_index._extract_macro_skeleton(
        page_inputs, indexer, raw_text_by_page=raw_text_by_page, coarse_chunk_pages=20
    )
    print(f"Pass 1 finished in {time.time()-t0:.1f}s. Macro containers found: {len(skeleton)}")
    for c in skeleton:
        print(f"  p{c['start_page']}-p{c['end_page']}: {c['heading_text']}")
    
    p2_containers = [c for c in skeleton if c["start_page"] == 2]
    print(f"Containers starting on page 2 (TOC page): {p2_containers}")
    assert len(p2_containers) == 0, f"Expected 0 containers on page 2, found {p2_containers}"
    print("SUCCESS: Pass 1 Macro Skeleton properly ignores page 2 (TOC)!")

if __name__ == "__main__":
    asyncio.run(test())
