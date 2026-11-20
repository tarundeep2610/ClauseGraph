import asyncio
import database
import ai_indexing
import reference_index
from ai_indexing.client import StructureIndexer
from ai_indexing.prompt import PageInput, ChunkPlan
import ai_indexing.prompt

async def main():
    p7 = database.get_page("89cfa529-2390-464c-9c3e-2446ef68c588", 7)
    ann_text = reference_index.build_layout_annotated_text(p7["text"], p7.get("raw_ocr_text"))
    chunk = ChunkPlan(pages=[PageInput(page_num=7, text=ann_text)])
    resolved = ai_indexing.resolve_active_config()
    indexer = StructureIndexer(resolved)

    addition = """
   - Essential Tender Information and Particulars tables: When a table lists key tender parameters or essential information under a section heading (e.g. with columns like "S#" / "Particulars" / "Details"), report each numbered row as a heading under that section (e.g. "1. Name of works/services to be procured", "2. Purchaser", "3. TENDER/TENDER Specification Number", ..., "15. Purchaser address for Correspondence").
"""
    orig_prompt = ai_indexing.prompt.STRUCTURE_SYSTEM_PROMPT
    target_str = 'contains a verb like "shall", "must", or "is".'
    assert target_str in orig_prompt, "target_str not found!"
    ai_indexing.prompt.STRUCTURE_SYSTEM_PROMPT = orig_prompt.replace(target_str, target_str + "\n" + addition)
    assert "Essential Tender Information" in ai_indexing.prompt.STRUCTURE_SYSTEM_PROMPT, "addition not in prompt!"
    print("Successfully injected addition into STRUCTURE_SYSTEM_PROMPT!")

    try:
        res = await indexer.extract_structure_for_chunk(chunk)
        print(f"Outcome: {res.outcome}, Headings found: {len(res.headings)}")
        for h in res.headings:
            print(f"  {h.heading_text!r} (parent: {h.parent_ref})")
    finally:
        ai_indexing.prompt.STRUCTURE_SYSTEM_PROMPT = orig_prompt

if __name__ == "__main__":
    asyncio.run(main())
