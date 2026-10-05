"""Exports a job's FULLY-CORRECTED structure (detector output merged with
every override — edit/delete/manual-item/parent — via
structure_overrides.resolve_structure_for_job) as a gold fixture, in the
same schema as tests/fixtures/gold_index_9c3dc6e6.json.

Why this exists: database.get_reference_targets_for_job() is raw detector
output with NO overrides applied — scoring against it (as
scripts/score_reference_index.py used to) silently ignores every manual
correction a user made in the Structure tree UI. This script is what makes
a hand-corrected job usable as ground truth.

Must run inside the CPU app container (needs `database`/`structure_overrides`
importable):

    docker cp scripts/export_gold_fixture.py \\
        ocr-services-glm-ocr-api-1:/app/scripts/export_gold_fixture.py
    docker exec ocr-services-glm-ocr-api-1 \\
        python3 /app/scripts/export_gold_fixture.py 108ba949-bb7b-4f29-b582-ce4f4a44631b \\
        --out tests/fixtures/gold_index_108ba949.json
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database
import structure_overrides


def export_gold_fixture(job_id: str) -> dict:
    job = database.get_job(job_id, include_pages=False)
    if not job:
        raise SystemExit(f"Job not found: {job_id}")

    resolved = structure_overrides.resolve_structure_for_job(job_id)
    mentions = database.get_all_reference_mentions_for_job(job_id)

    targets = []
    for r in resolved:
        key = r.get("key") or ""
        is_page_self_ref = key.startswith("page:")
        targets.append({
            "page": r.get("page"),
            "key": key,
            "occurrence_index": r.get("occurrence_index", 0),
            "label": None if is_page_self_ref else (r.get("own_label") or None),
            # resolved_scope_key is the override-corrected ancestor chain —
            # see structure_overrides.resolve_structure_for_job's own
            # docstring for why this differs from the stored scope_key
            # wherever a parent override applies. A page:N self-reference
            # row carries no real scope.
            "scope_key": None if is_page_self_ref else (r.get("resolved_scope_key") or ""),
        })
    # Stable order: by page, then key, then occurrence_index — matches the
    # existing fixture's own ordering convention.
    targets.sort(key=lambda t: (t["page"] or 0, t["key"], t["occurrence_index"]))

    mention_rows = [{
        "page": m.get("page"),
        "occurrence_index": m.get("occurrence_index", 0),
        "key": m.get("key"),
        "raw_text": m.get("raw_text"),
        "resolution": m.get("resolution"),
        "suppressed_reason": m.get("suppressed_reason"),
    } for m in mentions]
    mention_rows.sort(key=lambda m: (m["page"] or 0, m["occurrence_index"]))

    return {
        "job_id": job_id,
        "source_job_id": job_id,
        "total_pages": job.get("total_pages"),
        "targets": targets,
        "mentions": mention_rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_id")
    parser.add_argument("--out", required=True, help="Output path for the gold fixture JSON")
    args = parser.parse_args()

    fixture = export_gold_fixture(args.job_id)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(fixture, f, indent=2)
    print(f"Wrote {args.out}: {len(fixture['targets'])} targets, {len(fixture['mentions'])} mentions")


if __name__ == "__main__":
    main()
