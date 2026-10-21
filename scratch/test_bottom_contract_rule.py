import asyncio
import database
import ai_indexing
import reference_index
from ai_indexing.client import StructureIndexer
from ai_indexing.prompt import PageInput, ChunkPlan
import ai_indexing.prompt

async def main():
    p58 = database.get_page("89cfa529-2390-464c-9c3e-2446ef68c588", 58)
    ann_text = reference_index.build_layout_annotated_text(p58["text"], p58.get("raw_ocr_text"))
    chunk = ChunkPlan(pages=[PageInput(page_num=58, text=ann_text)])
    resolved = ai_indexing.resolve_active_config()
    indexer = StructureIndexer(resolved)

    contract_rule = """
- NUMBERED CLAUSES AND SUB-CLAUSES OF CONTRACTS, TENDERS, AND SPECIFICATIONS:
  In contracts, tenders, specifications, and conditions of contract (e.g. GCC, SCC), clauses and
  sub-clauses are sequentially numbered (e.g. "1.1.", "1.9.1.", "1.11.1.", "1.15.2.", "1.19.1.", "24.2.1.", or "1.", "2.", "3.").
  Report EVERY such numbered clause and sub-clause as a heading under its parent clause or section
  (e.g. report "1.11.1. It is a condition of this Bid that the information", "1.11.2. Information relating to examination", "1.9.1. The Purchaser shall be entitled to terminate").
  Even though a contract clause contains full sentences with verbs like "shall", "must", or "is",
  it is a numbered contract clause that must be indexed. Do NOT strip or exclude it under the noun-phrase test.
"""
    orig_prompt = ai_indexing.prompt.STRUCTURE_SYSTEM_PROMPT
    ai_indexing.prompt.STRUCTURE_SYSTEM_PROMPT = orig_prompt + "\n" + contract_rule

    try:
        res = await indexer.extract_structure_for_chunk(chunk)
        print(f"Outcome: {res.outcome}, Headings found: {len(res.headings)}")
        for h in res.headings:
            print(f"  {h.heading_text[:60]!r} | parent: {h.parent_ref!r}")
    finally:
        ai_indexing.prompt.STRUCTURE_SYSTEM_PROMPT = orig_prompt

if __name__ == "__main__":
    asyncio.run(main())
