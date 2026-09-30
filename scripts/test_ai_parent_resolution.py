"""One-off experiment: re-run AI structure-indexing on a duplicate of
9c3dc6e6 with ai_index_resolve_parent_via_ai=True (the new per-heading AI
micro-decision that replaces _resolve_depth_for_heading's fuzzy/positional
fallback — see ai_reference_index._resolve_parent_via_ai and the approved
plan) N times in a row, deleting each duplicate right after checking it,
and printing a pass/fail scorecard for the 4 headline entities per trial —
run-to-run consistency is the whole question at this point, not any single
run's result.

Must run inside the CPU app container:

    docker cp scripts/test_ai_parent_resolution.py \\
        ocr-services-glm-ocr-api-1:/app/scripts/test_ai_parent_resolution.py
    docker exec ocr-services-glm-ocr-api-1 \\
        python3 /app/scripts/test_ai_parent_resolution.py 9c3dc6e6-9e74-4088-b7f0-638022e4ab84 --trials 4
"""

import argparse
import asyncio
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import database
from compare_ai_indexing_qwen_vs_gemini import duplicate_job

# key -> expected scope_key SUFFIX (checked with .endswith so the exact
# subdoc:N numbering, which shifts per duplicate job, doesn't matter).
EXPECTED = {
    "JUDGMENT": "",          # root
    "FACTS": "judgment",     # child of JUDGMENT (checked via label lookup below, not raw key)
    "Challenge to the Arbitral Award.": "judgment",  # sibling of FACTS -> also child of JUDGMENT
    "Respondent's case": "judgment",                 # sibling of FACTS/Challenge -> also child of JUDGMENT
}


def _enable_resolve_parent_via_ai(job_id: str) -> None:
    conn = sqlite3.connect(database.DB_PATH)
    cursor = conn.cursor()
    row = cursor.execute("SELECT engine_params FROM ocr_jobs WHERE job_id = ?", (job_id,)).fetchone()
    params = json.loads(row[0]) if row and row[0] else {}
    params["ai_index_resolve_parent_via_ai"] = True
    cursor.execute("UPDATE ocr_jobs SET engine_params = ? WHERE job_id = ?", (json.dumps(params), job_id))
    conn.commit()
    conn.close()


async def run_reindex(job_id: str) -> dict:
    import ai_reference_index
    return await ai_reference_index.reindex_job_with_ai(job_id)


def collect_relevant(job_id: str) -> dict:
    """Returns {label: (key, scope_key)} for the 4 headline rows, keyed by
    an exact label match (not substring) so JUDGMENT doesn't also match as
    a substring of nothing else here, but stays precise either way."""
    structure = database.get_reference_targets_since(job_id, since_version=-1)
    found = {}
    for page_num in sorted(structure.keys(), key=lambda x: int(x)):
        for key, candidates in structure[page_num].items():
            for c in candidates:
                label = (c.get("own_label") or c.get("scope_label") or "").strip()
                for target in EXPECTED:
                    if label == target and target not in found:
                        found[target] = (key, c.get("scope_key") or "")
    return found


def score(found: dict) -> dict:
    """Resolves each row's PARENT LABEL (via key->label lookup among the
    other found rows) so the check is "is this JUDGMENT's child/sibling",
    not a specific subdoc:N number which shifts per duplicate job."""
    judgment_key = found.get("JUDGMENT", (None, None))[0]
    results = {}
    for label, (key, scope_key) in found.items():
        if label == "JUDGMENT":
            results[label] = scope_key == ""
            continue
        # scope_key is a "/"-joined chain of ancestor keys — correct here
        # means JUDGMENT's key is the LAST segment (i.e. its direct parent).
        segments = scope_key.split("/") if scope_key else []
        results[label] = bool(segments) and segments[-1] == judgment_key
    return results


async def one_trial(source_job_id: str, trial_num: int) -> dict:
    new_job_id = duplicate_job(source_job_id)
    try:
        _enable_resolve_parent_via_ai(new_job_id)
        result = await run_reindex(new_job_id)
        found = collect_relevant(new_job_id)
        results = score(found)
        missing = [label for label in EXPECTED if label not in found]
        print(f"\n--- Trial {trial_num}: job {new_job_id} — {result.get('status')} ---")
        for label in EXPECTED:
            if label in results:
                mark = "PASS" if results[label] else "FAIL"
                print(f"  [{mark}] {label!r} -> scope_key={found[label][1]!r}")
            else:
                print(f"  [MISSING] {label!r} was never reported at all this run")
        return {"passes": sum(1 for v in results.values() if v), "total": len(EXPECTED), "missing": missing}
    finally:
        database.delete_job(new_job_id)


async def main(source_job_id: str, trials: int) -> None:
    print(f"Source job: {source_job_id} (provider={config.AI_INDEX_PROVIDER} model={config.AI_INDEX_MODEL})")
    print(f"Running {trials} trial(s)...")
    tallies = []
    for i in range(1, trials + 1):
        tallies.append(await one_trial(source_job_id, i))

    print("\n=== Summary across all trials ===")
    for i, t in enumerate(tallies, start=1):
        print(f"  Trial {i}: {t['passes']}/{t['total']} correct" + (f", missing: {t['missing']}" if t["missing"] else ""))
    total_passes = sum(t["passes"] for t in tallies)
    total_possible = sum(t["total"] for t in tallies)
    print(f"  TOTAL: {total_passes}/{total_possible} correct across {trials} trial(s)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("job_id")
    parser.add_argument("--trials", type=int, default=1)
    args = parser.parse_args()
    asyncio.run(main(args.job_id, args.trials))
