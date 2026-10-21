import asyncio
import json
import os
import sys
import time

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/scripts")

import database
import ai_indexing
import ai_reference_index
import reference_index
from ai_indexing.client import StructureIndexer
from ai_indexing.prompt import PageInput, plan_adaptive_chunks, _render_macro_context
from score_reference_index import load_gold_fixture, _score_target_slice, _ident

SRC_JOB_ID = "89cfa529-2390-464c-9c3e-2446ef68c588"
FIXTURE_PATH = "/app/tests/fixtures/gold_index_89cfa529.json"

async def main():
    gold = load_gold_fixture(FIXTURE_PATH)
    all_gold_targets = gold.get("targets", [])
    chunk2_gold = [t for t in all_gold_targets if 13 <= t.get("page", 0) <= 19]
    print(f"Loaded Gold Targets for pages 13-19: {len(chunk2_gold)}")

    all_pages = database.get_pages(SRC_JOB_ID)
    raw_text_by_page = {p["page_num"]: (p.get("raw_ocr_text") or "") for p in all_pages}
    page_inputs = [PageInput(page_num=p["page_num"], text=p.get("text") or "") for p in all_pages]

    resolved = ai_indexing.resolve_active_config()
    print(f"Active Provider: {resolved.provider_config.provider}, Model: {resolved.provider_config.model}")
    indexer = StructureIndexer(resolved)

    print("\n--- Running Pass 1: Macro Skeleton Extraction ---")
    t0 = time.time()
    # Macro on first 60 pages
    macro_inputs = [p for p in page_inputs if p.page_num <= 60]
    macro_skeleton = await ai_reference_index._extract_macro_skeleton(
        macro_inputs, indexer, raw_text_by_page=raw_text_by_page, coarse_chunk_pages=20
    )
    t_macro = time.time() - t0
    print(f"Pass 1 Macro completed in {t_macro:.1f}s, found {len(macro_skeleton)} containers:")
    for c in macro_skeleton:
        print(f"  p{c['start_page']}-p{c['end_page']} (lvl {c['level']}): {c['heading_text']}")

    # Now run Pass 2 on pages 13-19 using adaptive chunks
    chunk2_inputs = [p for p in page_inputs if 13 <= p.page_num <= 19]
    adaptive_chunks = plan_adaptive_chunks(chunk2_inputs, dense_page_limit=2)
    print(f"\n--- Pass 2: Adaptive Chunking on pages 13-19 produced {len(adaptive_chunks)} chunks: ---")
    for i, ac in enumerate(adaptive_chunks):
        print(f"  Chunk {i+1}: pages {ac.page_nums} ({ac.total_chars} chars)")

    # Execute Pass 2 chunks with seed scope at end of page 12
    seed_scope = database.get_end_of_page_scope(SRC_JOB_ID, 12)
    prev_page = database.get_page(SRC_JOB_ID, 12)
    last_page_text = (prev_page or {}).get("text") or ""

    act_targets = []
    current_scope = seed_scope

    for i, chunk in enumerate(adaptive_chunks):
        print(f"\nProcessing Chunk {i+1}/{len(adaptive_chunks)}: pages {chunk.page_nums}...")
        t_c = time.time()
        carry_ctx = ai_reference_index._carry_in_summary(current_scope, last_page_text)
        macro_ctx = ai_reference_index._get_active_macro_context(macro_skeleton, chunk.page_nums[0])
        print(f"  Macro Context: {macro_ctx}")

        annotated_chunk = ai_indexing.ChunkPlan(
            pages=[
                PageInput(
                    page_num=p.page_num,
                    text=reference_index.build_layout_annotated_text(p.text, raw_text_by_page.get(p.page_num)),
                )
                for p in chunk.pages
            ],
            total_chars=chunk.total_chars,
        )

        res = await indexer.extract_structure_for_chunk(
            annotated_chunk,
            carry_context=carry_ctx,
            macro_context=macro_ctx,
            include_pleading_points=True,
        )
        print(f"  Extracted {len(res.headings)} raw headings in {time.time() - t_c:.1f}s")

        page_blocks = {}
        page_block_types = {}
        page_row_ids = {}
        for p in chunk.pages:
            t_list, ty_list, r_list = reference_index.parse_page_blocks_with_row_ids(p.text)
            page_blocks[p.page_num] = t_list
            page_block_types[p.page_num] = ty_list
            page_row_ids[p.page_num] = r_list

        events_by_page, warnings = await ai_reference_index._ground_and_resolve_chunk(
            res.headings, page_blocks, carry_ctx, resolved.provider_config.model,
            has_next_page_fn=lambda pn: pn < 135,
            page_block_types=page_block_types, page_row_ids=page_row_ids,
        )

        events_by_page = ai_reference_index._merge_macro_and_micro_events(
            macro_skeleton, events_by_page, page_blocks, resolved.provider_config.model
        )

        for p in chunk.pages:
            pn = p.page_num
            evs = events_by_page.get(pn, [])
            cands, current_scope = reference_index.apply_heading_events_to_scope(
                evs, current_scope, pn, has_next_page=pn < 135
            )
            last_page_text = p.text
            for c in cands:
                c["page"] = pn
                act_targets.append(c)
            print(f"    Page {pn}: {len(evs)} events -> {len(cands)} targets (Scope: {[s['key'] for s in current_scope.get('stack', [])]})")

    print("\n--- Final Score for Pages 13-19 ---")
    score = _score_target_slice(chunk2_gold, act_targets)
    print(f"Gold Targets: {len(chunk2_gold)}")
    print(f"Actual Targets: {len(act_targets)}")
    print(f"Matched Targets: {score['matched_count']}")
    print(f"Precision: {score['precision']:.3f}")
    print(f"Recall: {score['recall']:.3f}")
    print(f"Scope Accuracy: {score['scope_accuracy']:.3f}")

if __name__ == "__main__":
    asyncio.run(main())
