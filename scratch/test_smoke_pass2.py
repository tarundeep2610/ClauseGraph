import asyncio
import time
import database
import ai_indexing
import ai_reference_index
import reference_index
from ai_indexing.client import StructureIndexer
from ai_indexing.prompt import PageInput, plan_adaptive_chunks, _render_macro_context
from score_reference_index import load_gold_fixture, _score_target_slice

SRC_JOB_ID = "89cfa529-2390-464c-9c3e-2446ef68c588"
FIXTURE_PATH = "/app/tests/fixtures/gold_index_89cfa529.json"

async def test():
    gold = load_gold_fixture(FIXTURE_PATH)
    all_gold_targets = gold.get("targets", [])
    gold_slice = [t for t in all_gold_targets if 1 <= t.get("page", 0) <= 14]
    print(f"Loaded Gold Targets for pages 1-14: {len(gold_slice)}")

    all_pages = database.get_pages(SRC_JOB_ID)
    raw_text_by_page = {p["page_num"]: (p.get("raw_ocr_text") or "") for p in all_pages}
    page_inputs = [PageInput(page_num=p["page_num"], text=p.get("text") or "") for p in all_pages if p["page_num"] <= 20]

    resolved = ai_indexing.resolve_active_config()
    indexer = StructureIndexer(resolved)

    # 1. Macro skeleton
    print("Pass 1: Macro skeleton...")
    macro_skeleton = await ai_reference_index._extract_macro_skeleton(
        page_inputs, indexer, raw_text_by_page=raw_text_by_page, coarse_chunk_pages=20
    )

    # 2. Plan adaptive chunks for pages 1-14
    p1_14_inputs = [p for p in page_inputs if p.page_num <= 14]
    adaptive_chunks = plan_adaptive_chunks(p1_14_inputs, dense_page_limit=2)
    print(f"Planned {len(adaptive_chunks)} adaptive chunks for pages 1-14:")
    for i, ac in enumerate(adaptive_chunks):
        print(f"  Chunk {i+1}: pages {ac.page_nums}")

    # 3. Ground and resolve chunk by chunk
    current_scope = None
    last_page_text = ""
    all_act_targets = []

    for i, ac in enumerate(adaptive_chunks):
        t0 = time.time()
        chunk_inputs = [
            ai_indexing.PageInput(
                page_num=p.page_num,
                text=reference_index.build_layout_annotated_text(p.text, raw_text_by_page.get(p.page_num)),
            )
            for p in ac.pages
        ]
        annotated_chunk = ai_indexing.ChunkPlan(
            pages=chunk_inputs,
            truncated_pages=ac.truncated_pages,
            total_chars=ac.total_chars,
        )
        macro_ctx = ai_reference_index._get_active_macro_context(macro_skeleton, ac.page_nums[0])
        carry_ctx = ai_reference_index._carry_in_summary(current_scope, last_page_text=last_page_text)
        chunk_res = await indexer.extract_structure_for_chunk(
            annotated_chunk,
            carry_context=carry_ctx,
            macro_context=macro_ctx,
        )
        page_blocks = {p.page_num: reference_index.parse_page_blocks_with_row_ids(p.text)[0] for p in ac.pages}
        page_row_ids = {p.page_num: reference_index.parse_page_blocks_with_row_ids(p.text)[1] for p in ac.pages}
        page_block_types = {p.page_num: (database.get_page(SRC_JOB_ID, p.page_num) or {}).get("block_types") or [] for p in ac.pages}

        resolved_events, current_scope = ai_reference_index._ground_and_resolve_chunk(
            chunk_res.headings,
            page_blocks,
            seed_scope=current_scope,
            carry_context=carry_ctx,
            page_block_types=page_block_types,
            row_ids=page_row_ids,
        )
        last_page_num = ac.page_nums[-1]
        prev_p = database.get_page(SRC_JOB_ID, last_page_num)
        last_page_text = (prev_p or {}).get("text") or ""

        # Flatten targets for this chunk
        chunk_targets = []
        for pnum, evs in resolved_events.items():
            for ev in evs:
                chunk_targets.append({
                    "page": pnum,
                    "key": ev.get("key"),
                    "family": ev.get("family"),
                    "label": ev.get("label"),
                    "level": ev.get("level"),
                    "scope_key": ev.get("scope_key", ""),
                    "detection_source": "ai",
                })
        all_act_targets.extend(chunk_targets)
        dt = time.time() - t0
        print(f"Chunk {i+1} (pages {ac.page_nums}) done in {dt:.1f}s -> {len(chunk_targets)} targets emitted")

    # Score against gold_slice
    scores = _score_target_slice(all_act_targets, gold_slice)
    print("\n=== RESULTS FOR PAGES 1-14 ===")
    print(f"Gold: {len(gold_slice)}, Detected: {len(all_act_targets)}")
    print(f"Precision: {scores['precision']:.4f}, Recall: {scores['recall']:.4f}, F1: {scores['f1']:.4f}")
    print(f"True Positives: {scores['tp_count']}, False Positives: {scores['fp_count']}, False Negatives: {scores['fn_count']}")

    # Check page 2 specifically
    p2_targets = [t for t in all_act_targets if t["page"] == 2]
    print(f"\nTargets emitted on page 2: {len(p2_targets)} (Expected: 0)")
    # Check page 12 & 13 targets
    p12_13_targets = [t for t in all_act_targets if t["page"] in (12, 13)]
    p12_13_gold = [t for t in gold_slice if t["page"] in (12, 13)]
    print(f"Targets on pages 12-13: {len(p12_13_targets)} vs Gold: {len(p12_13_gold)}")

if __name__ == "__main__":
    asyncio.run(test())
