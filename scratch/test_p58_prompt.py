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

    orig_prompt = ai_indexing.prompt.STRUCTURE_SYSTEM_PROMPT

    # 1. Add exception to self-check
    target1 = "remove\n  it from your answer before responding."
    repl1 = "remove\n  it from your answer before responding (except in contracts, tenders, and conditions of contract where numbered clauses like 1.11.1, 1.11.2 are genuine contract divisions)."

    # 2. Update contract clause exception
    target2 = 'EXCEPTION FOR CONTRACT / TENDER NUMBERED CLAUSES IN TABLES:'
    repl2 = """EXCEPTION FOR CONTRACT / TENDER NUMBERED CLAUSES AND SUB-CLAUSES:
   In contracts, tenders, specifications, or conditions of contract (e.g. GCC, SCC),
   clauses and sub-clauses are sequentially numbered (e.g. "1.9.", "1.10.", "1.11.1", "1.11.2", "1.15.2", "1.19.1", "9.1", "24.2.1", whether in tables or in numbered paragraphs).
   In such contractual documents, these numbered clauses and sub-clauses ARE the primary structural divisions of the contract.
   You MUST report every numbered clause and decimal sub-clause (e.g. "1.11.1. It is a condition of this Bid", "1.11.2. Information relating to examination") under its parent clause (e.g. parent_ref="1.11. Confidentiality of data or information")!
   Do NOT exclude a numbered contract sub-clause under the noun-phrase/verb test merely because its text contains a verb like "shall", "must", or "is"."""

    new_prompt = orig_prompt.replace(target1, repl1).replace(target2, repl2)
    ai_indexing.prompt.STRUCTURE_SYSTEM_PROMPT = new_prompt

    try:
        res = await indexer.extract_structure_for_chunk(chunk)
        print(f"Outcome: {res.outcome}, Headings found: {len(res.headings)}")
        for h in res.headings:
            print(f"  {h.heading_text[:60]!r} | parent: {h.parent_ref!r}")
    finally:
        ai_indexing.prompt.STRUCTURE_SYSTEM_PROMPT = orig_prompt

if __name__ == "__main__":
    asyncio.run(main())
