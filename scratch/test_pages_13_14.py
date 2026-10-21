import asyncio
import os
import sys

sys.path.insert(0, "/app")

import database
import ai_indexing
import reference_index
from ai_indexing.client import StructureIndexer
from ai_indexing.prompt import ChunkPlan, PageInput

DUP_JOB_ID = "89cfa529-2390-464c-9c3e-2446ef68c588"

async def test_pages(page_range):
    all_pages = database.get_pages(DUP_JOB_ID)
    pages = [p for p in all_pages if p["page_num"] in page_range]
    page_inputs = [
        PageInput(
            page_num=p["page_num"],
            text=reference_index.build_layout_annotated_text(p.get("text") or "", p.get("raw_ocr_text") or "")
        )
        for p in pages
    ]
    chunk = ChunkPlan(pages=page_inputs)
    resolved = ai_indexing.resolve_active_config()
    indexer = StructureIndexer(resolved)
    result = await indexer.extract_structure_for_chunk(
        chunk,
        carry_context="",
        include_pleading_points=True,
    )
    print(f"\n=== Test Pages {page_range} ===")
    print(f"Outcome: {result.outcome}, Headings count: {len(result.headings)}")
    for i, h in enumerate(result.headings):
        print(f"  {i:2d} (p{h.start_page}): {repr(h.heading_text)}")

async def main():
    # Test just pages 13-14
    await test_pages([13, 14])

if __name__ == "__main__":
    asyncio.run(main())
