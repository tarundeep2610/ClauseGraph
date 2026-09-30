"""One-off experiment: re-run AI structure-indexing on a duplicate of an
already-indexed job with a SMALLER ai_index_chunk_pages, using the SAME
Gemini provider/model/credentials already configured on the live container.
Tests whether the "lost in the middle" pattern seen in job bb417471's
Round-2 Gemini run (headings missed only in the interior of long 10-page
chunks, never at chunk edges) is a chunk-length effect.

Must run inside the CPU app container (needs `config`/`database`/
`ai_reference_index` importable, DATA_DIR pointed at the real job data):

    docker cp scripts/test_ai_chunk_size.py \\
        ocr-services-glm-ocr-api-1:/app/scripts/test_ai_chunk_size.py
    docker exec ocr-services-glm-ocr-api-1 \\
        python3 /app/scripts/test_ai_chunk_size.py \\
        --chunk-pages 5 bb417471-561c-4029-bb21-3bdcf74e7dcb

Unlike scripts/compare_ai_indexing_qwen_vs_gemini.py, this does NOT require
(or want) a different AI_INDEX_PROVIDER — it deliberately reuses whatever
Gemini config the live container already has, since the point is to isolate
chunk size as the only changed variable.
"""

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import database
from compare_ai_indexing_qwen_vs_gemini import duplicate_job

CHECK_PAGES = {
    39: "Memo of Parties",
    40: "Judgment / Arbitration Case No.",
    68: "Bank Guarantee for Contract Performance",
    91: "Employee/addressee list",
}

OUT_DIR = os.path.join(database.DATA_DIR, "chunk_size_test")


async def run_reindex(job_id: str) -> dict:
    import ai_reference_index
    return await ai_reference_index.reindex_job_with_ai(job_id)


def load_structure(job_id: str) -> dict:
    return database.get_reference_targets_since(job_id, since_version=-1)


def heading_rows_by_page(structure: dict) -> "dict[int, list[dict]]":
    out = {}
    for page_num, by_key in structure.items():
        rows = []
        for key, candidates in by_key.items():
            for cand in candidates:
                rows.append({
                    "key": key,
                    "label": cand.get("own_label") or cand.get("scope_label"),
                    "confidence": cand.get("heading_confidence"),
                    "reasons": cand.get("heading_confidence_reasons"),
                    "scope_key": cand.get("scope_key"),
                })
        out[page_num] = rows
    return out


async def main(source_job_id: str, chunk_pages: int) -> None:
    print(f"Source job: {source_job_id}, testing chunk_pages={chunk_pages} "
          f"(provider={config.AI_INDEX_PROVIDER} model={config.AI_INDEX_MODEL})")

    source_structure = load_structure(source_job_id)
    source_report = database.get_ai_index_report(source_job_id)
    source_by_page = heading_rows_by_page(source_structure)

    new_job_id = duplicate_job(source_job_id, chunk_pages=chunk_pages)
    print(f"Duplicate created: {new_job_id}")

    result = await run_reindex(new_job_id)
    print(f"Reindex result: {result}")

    new_structure = load_structure(new_job_id)
    new_report = database.get_ai_index_report(new_job_id)
    new_by_page = heading_rows_by_page(new_structure)

    os.makedirs(OUT_DIR, exist_ok=True)
    tag = f"chunk{chunk_pages}"
    report_path = os.path.join(OUT_DIR, f"{source_job_id}_{tag}_report.json")
    targets_path = os.path.join(OUT_DIR, f"{source_job_id}_{tag}_targets.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump({"new_job_id": new_job_id, "ai_index_report": new_report}, f, indent=2)
    with open(targets_path, "w", encoding="utf-8") as f:
        json.dump(new_by_page, f, indent=2, default=str)
    print(f"Wrote {report_path} and {targets_path}")

    print("\n=== Specific page checks ===")
    for page, description in CHECK_PAGES.items():
        old_rows = source_by_page.get(page, [])
        new_rows = new_by_page.get(page, [])
        print(f"\nPage {page} ({description}):")
        print(f"  BEFORE (chunk_pages=default): {[r['label'] for r in old_rows]}")
        print(f"  AFTER  (chunk_pages={chunk_pages}):        {[r['label'] for r in new_rows]}")

    print("\n=== Overall heading count per page (old vs new) ===")
    all_pages = sorted(set(source_by_page) | set(new_by_page))
    for page in all_pages:
        old_n = len(source_by_page.get(page, []))
        new_n = len(new_by_page.get(page, []))
        if old_n != new_n:
            print(f"  page {page}: before={old_n} after={new_n}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_id", help="Source job_id to duplicate and re-test.")
    parser.add_argument("--chunk-pages", type=int, required=True,
                         help="ai_index_chunk_pages override for the duplicate job.")
    args = parser.parse_args()
    asyncio.run(main(args.job_id, args.chunk_pages))
