"""One-off experiment: re-run AI structure-indexing on a duplicate of an
already-indexed job with Round 10's opt-in "full AI handoff" flag
(engine_params.ai_index_pleading_points_via_ai=True) — the AI itself
detects and nests numbered pleading points/grounds (via the new
NUMBERED PLEADING POINTS AND GROUNDS ARE HEADINGS TOO prompt section),
instead of the deterministic detect_pleading_points_for_page augmentation.

Compares the resulting Grounds-of-Appeal structure against the current
(deterministic-fix) job's own structure for the same page range, so the
two approaches can be judged side by side on the same real document.

Must run inside the CPU app container, same as scripts/test_ai_chunk_size.py:

    docker cp scripts/test_ai_pleading_points.py \\
        ocr-services-glm-ocr-api-1:/app/scripts/test_ai_pleading_points.py
    docker exec ocr-services-glm-ocr-api-1 \\
        python3 /app/scripts/test_ai_pleading_points.py \\
        bb417471-561c-4029-bb21-3bdcf74e7dcb --page-from 14 --page-to 40
"""

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database
from compare_ai_indexing_qwen_vs_gemini import duplicate_job

OUT_DIR = os.path.join(database.DATA_DIR, "pleading_points_test")


async def run_reindex(job_id: str) -> dict:
    import ai_reference_index
    return await ai_reference_index.reindex_job_with_ai(job_id)


def load_structure(job_id: str) -> dict:
    return database.get_reference_targets_since(job_id, since_version=-1)


def rows_in_range(structure: dict, page_from: int, page_to: int) -> "list[dict]":
    out = []
    for page_num, by_key in structure.items():
        if not (page_from <= page_num <= page_to):
            continue
        for key, candidates in by_key.items():
            for cand in candidates:
                out.append({
                    "page": page_num, "key": key,
                    "scope_key": cand.get("scope_key"),
                    "detection_source": cand.get("detection_source"),
                    "own_label": cand.get("own_label"),
                })
    return sorted(out, key=lambda r: (r["page"], r["key"]))


async def main(source_job_id: str, page_from: int, page_to: int) -> None:
    print(f"Source job: {source_job_id} — comparing pages {page_from}-{page_to}")

    before_structure = load_structure(source_job_id)
    before_rows = rows_in_range(before_structure, page_from, page_to)

    new_job_id = duplicate_job(source_job_id, pleading_points_via_ai=True)
    print(f"Duplicate created: {new_job_id} (ai_index_pleading_points_via_ai=True)")

    result = await run_reindex(new_job_id)
    print(f"Reindex result: {result}")

    after_structure = load_structure(new_job_id)
    after_rows = rows_in_range(after_structure, page_from, page_to)

    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, f"{source_job_id}_vs_{new_job_id}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"before": before_rows, "after": after_rows}, f, indent=2)
    print(f"Wrote {out_path}")

    print("\n=== BEFORE (current deterministic-fix job) ===")
    for r in before_rows:
        print(f"  p{r['page']:>3} {r['key']:<20} scope={r['scope_key']!r:<30} src={r['detection_source']}")

    print("\n=== AFTER (ai_index_pleading_points_via_ai=True) ===")
    for r in after_rows:
        print(f"  p{r['page']:>3} {r['key']:<20} scope={r['scope_key']!r:<30} src={r['detection_source']}")

    before_keys = {r["key"] for r in before_rows}
    after_keys = {r["key"] for r in after_rows}
    print(f"\nBefore: {len(before_rows)} rows, {len(before_keys)} unique keys")
    print(f"After:  {len(after_rows)} rows, {len(after_keys)} unique keys")
    print(f"Keys only in BEFORE (AI handoff missed): {sorted(before_keys - after_keys)}")
    print(f"Keys only in AFTER (AI handoff added):   {sorted(after_keys - before_keys)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_id", help="Source job_id to duplicate and re-test.")
    parser.add_argument("--page-from", type=int, default=1)
    parser.add_argument("--page-to", type=int, default=10_000)
    args = parser.parse_args()
    asyncio.run(main(args.job_id, args.page_from, args.page_to))
