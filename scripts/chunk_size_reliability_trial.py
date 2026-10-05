"""Reliability trial: re-runs the full AI-indexing + review pipeline N times
on fresh duplicates of a source job, with a given ai_index_chunk_pages
override, and reports per-chunk success/fallback rates across all trials.

Each duplicate is deleted right after its result is collected (same
cleanup-after-checking pattern as scripts/test_ai_parent_resolution.py) —
run-to-run consistency is the question here, not any single run's result.

Must run inside the CPU app container:

    docker cp scripts/chunk_size_reliability_trial.py \\
        ocr-services-glm-ocr-api-1:/app/scripts/chunk_size_reliability_trial.py
    docker exec ocr-services-glm-ocr-api-1 \\
        python3 /app/scripts/chunk_size_reliability_trial.py \\
        108ba949-bb7b-4f29-b582-ce4f4a44631b --chunk-pages 2 --trials 3
"""

import argparse
import asyncio
import json
import os
import sqlite3
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import database
from compare_ai_indexing_qwen_vs_gemini import duplicate_job


def _set_engine_params(job_id: str, updates: dict) -> None:
    conn = sqlite3.connect(database.DB_PATH)
    cursor = conn.cursor()
    row = cursor.execute("SELECT engine_params FROM ocr_jobs WHERE job_id = ?", (job_id,)).fetchone()
    params = json.loads(row[0]) if row and row[0] else {}
    params.update(updates)
    cursor.execute("UPDATE ocr_jobs SET engine_params = ? WHERE job_id = ?", (json.dumps(params), job_id))
    conn.commit()
    conn.close()


async def one_trial(source_job_id: str, trial_idx: int, chunk_pages: int) -> dict:
    dup_id = duplicate_job(source_job_id, chunk_pages=chunk_pages)
    try:
        _set_engine_params(dup_id, {
            "ai_index_review_rule_prior": True,
            "ai_index_review_mentions": True,
            "ai_index_resolve_parent_via_ai": False,
        })
        import ai_reference_index
        result = await ai_reference_index.reindex_job_with_ai(dup_id)
        report = database.get_ai_index_report(dup_id)
        chunks = report.get("chunks") or []
        ok = sum(1 for c in chunks if c.get("status") == "ok")
        fallback = sum(1 for c in chunks if c.get("status") == "fallback")
        fail_kinds = [c.get("error_kind") for c in chunks if c.get("status") == "fallback"]
        warning_totals = Counter()
        for c in chunks:
            warning_totals.update(c.get("warnings") or {})
        print(f"Trial {trial_idx}: status={result.get('status')} chunks={len(chunks)} "
              f"ok={ok} fallback={fallback} fail_kinds={fail_kinds} warnings={dict(warning_totals)}")
        return {
            "trial": trial_idx, "total_chunks": len(chunks), "ok": ok, "fallback": fallback,
            "fail_kinds": fail_kinds, "warnings": dict(warning_totals),
        }
    finally:
        database.delete_job(dup_id)


async def main(source_job_id: str, chunk_pages: int, trials: int) -> None:
    print(f"Source job: {source_job_id} | provider={config.AI_INDEX_PROVIDER} model={config.AI_INDEX_MODEL} "
          f"| chunk_pages override={chunk_pages} | trials={trials}")
    results = []
    for i in range(1, trials + 1):
        results.append(await one_trial(source_job_id, i, chunk_pages))

    total_chunks = sum(r["total_chunks"] for r in results)
    total_ok = sum(r["ok"] for r in results)
    total_fallback = sum(r["fallback"] for r in results)
    all_fail_kinds = Counter()
    for r in results:
        all_fail_kinds.update(r["fail_kinds"])
    all_warnings = Counter()
    for r in results:
        all_warnings.update(r["warnings"])

    print("\n=== Summary across all trials ===")
    print(f"Total chunks: {total_chunks} | ok: {total_ok} ({100*total_ok/max(1,total_chunks):.0f}%) "
          f"| fallback: {total_fallback} ({100*total_fallback/max(1,total_chunks):.0f}%)")
    if all_fail_kinds:
        print(f"Fallback error kinds: {dict(all_fail_kinds)}")
    if all_warnings:
        print(f"Review-pass warning totals across all successful chunks: {dict(all_warnings)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_id")
    parser.add_argument("--chunk-pages", type=int, required=True)
    parser.add_argument("--trials", type=int, default=3)
    args = parser.parse_args()
    asyncio.run(main(args.job_id, args.chunk_pages, args.trials))
