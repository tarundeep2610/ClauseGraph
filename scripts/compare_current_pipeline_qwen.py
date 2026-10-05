"""One-off comparison tool: duplicates a source job and re-indexes the
duplicate with TODAY's shipped pipeline (rule-based detection -> AI
structure indexing -> rule-prior AI review -> rule-based mention detection
-> AI mention review), using whatever AI_INDEX_* provider this container's
environment names (expected: RokkaAI / qwen3:8b). Diffs the result against
the SOURCE job's own already-persisted structure/mentions — never touches
the source job's data.

Must run inside the CPU app container (needs `config`/`database`/
`ai_indexing`/`ai_reference_index`/`reference_index` importable):

    docker cp scripts/compare_current_pipeline_qwen.py \\
        ocr-services-glm-ocr-api-1:/app/scripts/compare_current_pipeline_qwen.py
    docker exec ocr-services-glm-ocr-api-1 \\
        python3 /app/scripts/compare_current_pipeline_qwen.py 108ba949-bb7b-4f29-b582-ce4f4a44631b

The duplicate is NOT deleted afterward — it's left in place so its result
can be inspected in the viewer, same as compare_ai_indexing_qwen_vs_gemini.py.
"""

import argparse
import asyncio
import json
import os
import re
import sqlite3
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import database
from compare_ai_indexing_qwen_vs_gemini import duplicate_job, _heading_list


def _set_engine_params(job_id: str, updates: dict) -> None:
    """Direct-SQL engine_params patch — same pattern as
    scripts/score_reference_index.py's _enable_review_flag, generalized to
    an arbitrary key/value dict so this one call can flip every flag this
    comparison cares about."""
    conn = sqlite3.connect(database.DB_PATH)
    cursor = conn.cursor()
    row = cursor.execute("SELECT engine_params FROM ocr_jobs WHERE job_id = ?", (job_id,)).fetchone()
    params = json.loads(row[0]) if row and row[0] else {}
    params.update(updates)
    cursor.execute("UPDATE ocr_jobs SET engine_params = ? WHERE job_id = ?", (json.dumps(params), job_id))
    conn.commit()
    conn.close()


def load_mentions(job_id: str) -> "list[dict]":
    return database.get_all_reference_mentions_for_job(job_id)


def _mention_resolving_set(mentions: "list[dict]") -> set:
    return {(m["page"], m["key"]) for m in mentions if m.get("resolution") in ("exact", "approximate")}


def build_comparison(
    source_job_id: str, dup_job_id: str,
    source_structure: dict, dup_structure: dict,
    source_mentions: "list[dict]", dup_mentions: "list[dict]",
    source_report: dict, dup_report: dict,
) -> "tuple[str, dict]":
    source_headings = _heading_list(source_structure)
    dup_headings = _heading_list(dup_structure)

    source_by_page: "dict[int, set]" = {}
    for page, label in source_headings:
        source_by_page.setdefault(page, set()).add(label)
    dup_by_page: "dict[int, set]" = {}
    for page, label in dup_headings:
        dup_by_page.setdefault(page, set()).add(label)

    all_pages = sorted(set(source_by_page) | set(dup_by_page))
    matched = missing_in_dup = extra_in_dup = 0
    lines = []
    lines.append(f"# Current pipeline (qwen3:8b) vs. original — source job `{source_job_id}`, duplicate `{dup_job_id}`\n")
    lines.append(f"- Original: provider=`{source_report.get('provider')}` model=`{source_report.get('model')}` status=`{source_report.get('status')}`")
    lines.append(f"- Duplicate: provider=`{config.AI_INDEX_PROVIDER}` model=`{config.AI_INDEX_MODEL}` status=`{dup_report.get('status')}`")
    dup_chunks = dup_report.get("chunks") or []
    dup_fallbacks = sum(1 for c in dup_chunks if c.get("status") == "fallback")
    lines.append(f"- Duplicate chunks that fell back to the rule-based detector: {dup_fallbacks}/{len(dup_chunks)}")
    for c in dup_chunks:
        lines.append(
            f"  - pages {c.get('first_page')}-{c.get('last_page')}: status=`{c.get('status')}`"
            + (f" error_kind=`{c.get('error_kind')}` ({c.get('error')})" if c.get("status") == "fallback" else f" headings={c.get('heading_count')}")
        )
    lines.append(f"- Total targets — original: {len(source_headings)}, duplicate: {len(dup_headings)}")

    for page in all_pages:
        s = source_by_page.get(page, set())
        d = dup_by_page.get(page, set())
        both = s & d
        only_s = s - d
        only_d = d - s
        matched += len(both)
        missing_in_dup += len(only_s)
        extra_in_dup += len(only_d)

    lines.append(f"- Matched: {matched}  |  Original-only (duplicate missed): {missing_in_dup}  |  Duplicate-only (duplicate added): {extra_in_dup}\n")

    # Review-pass-specific stats: warning_counts logged per chunk, and
    # heading_confidence_reasons persisted on the duplicate's own targets —
    # these only exist when ai_index_review_rule_prior actually ran.
    warning_totals = Counter()
    for c in (dup_report.get("chunks") or []):
        warning_totals.update(c.get("warnings") or {})
    if warning_totals:
        lines.append("## Rule-prior review activity (duplicate)")
        for k, v in sorted(warning_totals.items()):
            lines.append(f"- `{k}`: {v}")
        lines.append("")

    dup_targets_flat = database.get_reference_targets_for_job(dup_job_id)
    reason_counts = Counter()
    for t in dup_targets_flat:
        raw = t.get("heading_confidence_reasons")
        if not raw:
            continue
        # Persisted as a JSON string, not a deserialized list — iterating it
        # directly would walk its characters, not its reason codes.
        reasons = raw if isinstance(raw, list) else json.loads(raw)
        for r in reasons:
            reason_counts[r] += 1
    if reason_counts:
        lines.append("## Per-target confidence reasons (duplicate)")
        for k, v in sorted(reason_counts.items()):
            lines.append(f"- `{k}`: {v}")
        lines.append("")

    source_detection = Counter(t.get("detection_source") for t in database.get_reference_targets_for_job(source_job_id))
    dup_detection = Counter(t.get("detection_source") for t in dup_targets_flat)
    lines.append("## Detection source breakdown")
    lines.append(f"- Original: {dict(source_detection)}")
    lines.append(f"- Duplicate: {dict(dup_detection)}\n")

    # Mentions
    source_resolving = _mention_resolving_set(source_mentions)
    dup_resolving = _mention_resolving_set(dup_mentions)
    mention_matched = source_resolving & dup_resolving
    mention_missing = source_resolving - dup_resolving
    mention_extra = dup_resolving - source_resolving
    source_mention_res = Counter(m.get("resolution") for m in source_mentions)
    dup_mention_res = Counter(m.get("resolution") for m in dup_mentions)
    lines.append("## Mentions")
    lines.append(f"- Original resolution breakdown: {dict(source_mention_res)}")
    lines.append(f"- Duplicate resolution breakdown: {dict(dup_mention_res)}")
    lines.append(f"- Matched resolving mentions: {len(mention_matched)}  |  Original-only: {len(mention_missing)}  |  Duplicate-only: {len(mention_extra)}\n")

    lines.append("## Page-by-page target diff")
    for page in all_pages:
        s = source_by_page.get(page, set())
        d = dup_by_page.get(page, set())
        both = s & d
        only_s = s - d
        only_d = d - s
        if not (s or d):
            continue
        lines.append(f"### Page {page}")
        for label in sorted(both):
            lines.append(f"- [x] {label}")
        for label in sorted(only_s):
            lines.append(f"- [ ] **original only** — {label}")
        for label in sorted(only_d):
            lines.append(f"- [+] **duplicate (qwen3:8b) only** — {label}")
        lines.append("")

    json_obj = {
        "source_job_id": source_job_id,
        "dup_job_id": dup_job_id,
        "source_report": source_report,
        "dup_report": dup_report,
        "summary": {
            "source_target_count": len(source_headings),
            "dup_target_count": len(dup_headings),
            "matched_targets": matched,
            "missing_in_dup": missing_in_dup,
            "extra_in_dup": extra_in_dup,
            "source_detection_source": dict(source_detection),
            "dup_detection_source": dict(dup_detection),
            "rule_prior_warning_totals": dict(warning_totals),
            "dup_confidence_reason_counts": dict(reason_counts),
            "source_mention_resolution": dict(source_mention_res),
            "dup_mention_resolution": dict(dup_mention_res),
            "mention_matched": len(mention_matched),
            "mention_missing_in_dup": len(mention_missing),
            "mention_extra_in_dup": len(mention_extra),
        },
    }
    return "\n".join(lines), json_obj


async def run_one(source_job_id: str, trial_idx: int, chunk_pages: "int | None") -> dict:
    print(f"[{source_job_id}] trial {trial_idx}: duplicating job (PDF + OCR text only, zero indexing state)...")
    dup_job_id = duplicate_job(source_job_id, chunk_pages=chunk_pages)
    print(f"[{source_job_id}] trial {trial_idx}: duplicate created: {dup_job_id}")

    _set_engine_params(dup_job_id, {
        "ai_index_review_rule_prior": True,
        "ai_index_review_mentions": True,
        "ai_index_resolve_parent_via_ai": False,  # superseded experimental flag — keep this run clean
    })
    print(f"[{dup_job_id}] engine_params set: ai_index_review_rule_prior=True, "
          f"ai_index_review_mentions=True, ai_index_resolve_parent_via_ai=False, "
          f"ai_index_chunk_pages={chunk_pages!r}")

    import ai_reference_index
    print(f"[{dup_job_id}] running full pipeline with provider={config.AI_INDEX_PROVIDER} model={config.AI_INDEX_MODEL}...")
    result = await ai_reference_index.reindex_job_with_ai(dup_job_id)
    print(f"[{dup_job_id}] reindex result: {result}")

    source_structure = database.get_reference_targets_since(source_job_id, since_version=-1)
    dup_structure = database.get_reference_targets_since(dup_job_id, since_version=-1)
    source_mentions = load_mentions(source_job_id)
    dup_mentions = load_mentions(dup_job_id)
    source_report = database.get_ai_index_report(source_job_id)
    dup_report = database.get_ai_index_report(dup_job_id)

    md, js = build_comparison(
        source_job_id, dup_job_id, source_structure, dup_structure,
        source_mentions, dup_mentions, source_report, dup_report,
    )

    report_dir = os.path.join(database.DATA_DIR, "current_pipeline_comparison")
    os.makedirs(report_dir, exist_ok=True)
    md_path = os.path.join(report_dir, f"{source_job_id}_vs_{dup_job_id}_trial{trial_idx}.md")
    json_path = os.path.join(report_dir, f"{source_job_id}_vs_{dup_job_id}_trial{trial_idx}.json")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(js, f, indent=2)
    print(f"[{dup_job_id}] wrote {md_path} and {json_path}")
    return {"dup_job_id": dup_job_id, "summary": js["summary"], "md_path": md_path}


async def main(source_job_id: str, chunk_pages: "int | None", trials: int) -> None:
    if config.AI_INDEX_PROVIDER == "gemini" or not config.AI_INDEX_API_KEY:
        raise SystemExit(
            f"Refusing to run: this container's AI_INDEX_* env still points at "
            f"provider={config.AI_INDEX_PROVIDER!r}. Expected rokkaai/qwen3:8b."
        )

    all_summaries = []
    for i in range(1, trials + 1):
        r = await run_one(source_job_id, i, chunk_pages)
        all_summaries.append(r)
        print(f"Trial {i} summary: {json.dumps(r['summary'])}\n")

    print("=== All trials complete — duplicates kept (not deleted) for inspection ===")
    for r in all_summaries:
        print(f"  {r['dup_job_id']} -> {r['md_path']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_id")
    parser.add_argument("--chunk-pages", type=int, default=None,
                         help="Override ai_index_chunk_pages on each duplicate (default: the job's own frozen value).")
    parser.add_argument("--trials", type=int, default=1,
                         help="Number of independent duplicate+reindex+compare runs. Duplicates are kept, not deleted.")
    args = parser.parse_args()
    asyncio.run(main(args.job_id, args.chunk_pages, args.trials))
