import sys
sys.path.insert(0, "/app")
import asyncio
import database
import ai_indexing
import reference_index
from ai_indexing.client import StructureIndexer
from ai_indexing.prompt import CarryContext, ChunkPlan, PageInput

DUP_JOB_ID = "89cfa529-2390-464c-9c3e-2446ef68c588"

async def main():
    all_pages = database.get_pages(DUP_JOB_ID)
    pages = [p for p in all_pages if 13 <= p["page_num"] <= 19]
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
    print("--- RAW COMPLETION ---")
    print(result.raw_excerpt)

if __name__ == "__main__":
    asyncio.run(main())
