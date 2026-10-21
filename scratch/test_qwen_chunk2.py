import asyncio
import os
import sys

# Ensure app imports work
sys.path.insert(0, "/app")

import database
import ai_indexing
import ai_reference_index
import reference_index
from ai_indexing.client import StructureIndexer
from ai_indexing.prompt import CarryContext, ChunkPlan, PageInput

DUP_JOB_ID = "89cfa529-2390-464c-9c3e-2446ef68c588"

async def main():
    job = database.get_job(DUP_JOB_ID, include_pages=False)
    all_pages = database.get_pages(DUP_JOB_ID)
    
    # Filter pages for chunk 2: pages 13-19
    pages = [p for p in all_pages if 13 <= p["page_num"] <= 19]
    print(f"Selected {len(pages)} pages: {[p['page_num'] for p in pages]}")
    
    # Check seed scope at end of page 12
    seed_scope = database.get_end_of_page_scope(DUP_JOB_ID, 12)
    prev_page = database.get_page(DUP_JOB_ID, 12)
    last_page_text = (prev_page or {}).get("text") or ""
    carry_context = ai_reference_index._carry_in_summary(seed_scope, last_page_text)

    page_inputs = [
        PageInput(
            page_num=p["page_num"],
            text=reference_index.build_layout_annotated_text(p.get("text") or "", p.get("raw_ocr_text") or "")
        )
        for p in pages
    ]
    chunk = ChunkPlan(pages=page_inputs)

    resolved = ai_indexing.resolve_active_config()
    print(f"Provider: {resolved.provider_config.provider}, Model: {resolved.provider_config.model}, Base URL: {resolved.provider_config.base_url}")
    
    indexer = StructureIndexer(resolved)
    print("Calling extract_structure_for_chunk...")
    result = await indexer.extract_structure_for_chunk(
        chunk,
        carry_context=carry_context,
        include_pleading_points=True,
    )
    
    print(f"Outcome: {result.outcome}")
    print(f"Headings count: {len(result.headings)}")
    for i, h in enumerate(result.headings):
        print(f"  {i:2d} (p{h.start_page}, lvl={h.level}, parent={h.parent_ref}): {repr(h.heading_text)}")
        
    print("\n--- Raw Excerpt (first 1500 chars) ---")
    print(result.raw_excerpt[:1500])

if __name__ == "__main__":
    asyncio.run(main())
