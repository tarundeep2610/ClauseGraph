"""Score reference index (targets and mentions) against a gold fixture across N trials.

Emits per run:
- Target precision & recall
- Scope-path exact-match rate
- Family-drift count (same content, different key family — e.g. a ground/
  point leaf misclassified as a false subdoc root; the damaging error class
  that precision/recall alone can't tell apart from a genuine miss)
- Mention precision & recall (exact / approximate resolutions)

Scores against structure_overrides.resolve_structure_for_job(job_id), not
raw detector output — so a scored job's own manual corrections (if any)
are reflected, matching what resolve_structure_for_job does for the gold
fixture itself (see scripts/export_gold_fixture.py). Matches on
(page, key, occurrence_index) — the bare (page, key) pair used before this
is not unique whenever a key repeats in one document (the whole reason
Phase A's reparent-identity fix exists).

Usage (runs in container or environment with access to sqlite and ollama):
    python scripts/score_reference_index.py 108ba949-bb7b-4f29-b582-ce4f4a44631b --fixture tests/fixtures/gold_index_108ba949.json --trials 3
"""

import argparse
import asyncio
import json
import os
import re
import sqlite3
import sys
from typing import Any, Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database
import structure_overrides
from compare_ai_indexing_qwen_vs_gemini import duplicate_job


def _set_engine_params(job_id: str, updates: Dict[str, Any]) -> None:
    """Generalizes _enable_review_flag to an arbitrary key/value patch — one
    call covers ai_index_review_rule_prior AND ai_index_pleading_points_via_ai
    (needed for Phase C's arm 3, which explicitly turns the latter off)."""
    conn = sqlite3.connect(database.DB_PATH)
    cursor = conn.cursor()
    row = cursor.execute("SELECT engine_params FROM ocr_jobs WHERE job_id = ?", (job_id,)).fetchone()
    params = json.loads(row[0]) if row and row[0] else {}
    params.update(updates)
    cursor.execute("UPDATE ocr_jobs SET engine_params = ? WHERE job_id = ?", (json.dumps(params), job_id))
    conn.commit()
    conn.close()


def _enable_review_flag(job_id: str, flag_val: bool = True) -> None:
    conn = sqlite3.connect(database.DB_PATH)
    cursor = conn.cursor()
    row = cursor.execute("SELECT engine_params FROM ocr_jobs WHERE job_id = ?", (job_id,)).fetchone()
    params = json.loads(row[0]) if row and row[0] else {}
    params["ai_index_review_rule_prior"] = flag_val
    cursor.execute("UPDATE ocr_jobs SET engine_params = ? WHERE job_id = ?", (json.dumps(params), job_id))
    conn.commit()
    conn.close()


def load_gold_fixture(fixture_path: str) -> Dict[str, Any]:
    with open(fixture_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _ident(t: Dict[str, Any]) -> Tuple[Any, str, int]:
    return (t.get("page"), t.get("key"), t.get("occurrence_index") or 0)


def _family(key: str) -> str:
    return key.split(":", 1)[0] if key and ":" in key else (key or "")


_WS_RE = re.compile(r"\s+")


def _norm_label(label: Any) -> str:
    return _WS_RE.sub(" ", str(label or "")).strip().lower()[:40]


def _count_family_drift(gold_targets: List[Dict[str, Any]], act_targets: List[Dict[str, Any]], matched_ids: set) -> int:
    """Among targets that did NOT match by (page, key, occurrence_index),
    pairs a gold target with an actual target on the SAME page whose own
    label matches closely (same normalized first ~40 chars) — i.e. almost
    certainly the same real heading — but whose key FAMILY differs. That's
    a structural misclassification (e.g. ground:a became subdoc:6), not a
    recall/precision miss, and it's the error class an AI review pass can
    introduce silently (see the qwen trial findings this phase is built
    on) — precision/recall alone can't distinguish it from the target
    simply not being found at all."""
    unmatched_gold = [t for t in gold_targets if _ident(t) not in matched_ids]
    unmatched_act = [t for t in act_targets if _ident(t) not in matched_ids]
    used_act_idx = set()
    drift = 0
    for g in unmatched_gold:
        g_label = _norm_label(g.get("label"))
        if not g_label:
            continue
        for i, a in enumerate(unmatched_act):
            if i in used_act_idx or a.get("page") != g.get("page"):
                continue
            if _norm_label(a.get("label")) == g_label and _family(a.get("key")) != _family(g.get("key")):
                drift += 1
                used_act_idx.add(i)
                break
    return drift


def evaluate_run(
    job_id: str,
    gold: Dict[str, Any],
) -> Dict[str, float]:
    resolved = structure_overrides.resolve_structure_for_job(job_id)
    actual_mentions = database.get_all_reference_mentions_for_job(job_id)

    gold_targets = gold.get("targets", [])
    gold_mentions = gold.get("mentions", [])

    act_t_list = [{
        "page": r.get("page"),
        "key": r.get("key"),
        "occurrence_index": r.get("occurrence_index", 0),
        # resolved_scope_key reflects any override correction on the scored
        # job itself; falls back to the raw stored scope_key for a target
        # resolve_structure_for_job didn't annotate (shouldn't happen in
        # practice — defensive only).
        "scope_key": r.get("resolved_scope_key") if r.get("resolved_scope_key") is not None else (r.get("scope_key") or ""),
        "label": r.get("own_label") or "",
    } for r in resolved]

    gold_ids = {_ident(t) for t in gold_targets}
    act_ids = {_ident(t) for t in act_t_list}
    matched = gold_ids & act_ids
    t_precision = len(matched) / max(1, len(act_ids))
    t_recall = len(matched) / max(1, len(gold_ids))

    gold_scope_map = {_ident(t): (t.get("scope_key") or "") for t in gold_targets}
    act_scope_map = {_ident(t): t["scope_key"] for t in act_t_list}
    scope_matches = sum(1 for tid in matched if gold_scope_map.get(tid) == act_scope_map.get(tid))
    scope_accuracy = scope_matches / max(1, len(matched))

    family_drift = _count_family_drift(gold_targets, act_t_list, matched)

    # Compare mentions (resolving ones: exact & approximate) — mentions
    # don't repeat a (page, key) pair the way targets can collide, so no
    # occurrence_index disambiguation is needed here.
    gold_m_resolving = {
        (m["page"], m["key"])
        for m in gold_mentions
        if m.get("resolution") in ("exact", "approximate")
    }
    act_m_resolving = {
        (m["page"], m["key"])
        for m in actual_mentions
        if m.get("resolution") in ("exact", "approximate")
    }

    matched_mentions = gold_m_resolving.intersection(act_m_resolving)
    m_precision = len(matched_mentions) / max(1, len(act_m_resolving))
    m_recall = len(matched_mentions) / max(1, len(gold_m_resolving))

    return {
        "target_precision": t_precision,
        "target_recall": t_recall,
        "scope_accuracy": scope_accuracy,
        "family_drift_count": family_drift,
        "mention_precision": m_precision,
        "mention_recall": m_recall,
        "matched_targets_count": len(matched),
        "total_actual_targets": len(act_ids),
        "total_gold_targets": len(gold_ids),
        "matched_mentions_count": len(matched_mentions),
        "total_actual_mentions": len(act_m_resolving),
        "total_gold_mentions": len(gold_m_resolving),
    }


async def run_trial(
    source_job_id: str,
    gold: Dict[str, Any],
    trial_idx: int,
    review_flag: bool,
    chunk_pages: "int | None" = None,
    pleading_points_via_ai: "bool | None" = None,
    overlap_pages: "int | None" = None,
) -> Dict[str, float]:
    dup_id = duplicate_job(source_job_id, chunk_pages=chunk_pages)
    try:
        engine_param_updates = {}
        if review_flag:
            engine_param_updates["ai_index_review_rule_prior"] = True
        if pleading_points_via_ai is not None:
            engine_param_updates["ai_index_pleading_points_via_ai"] = pleading_points_via_ai
        # Explicit `is not None`, not `if overlap_pages:` — 0 ("disable
        # overlap") is itself one of the arms being measured and must not
        # be treated as "no override given" (the same or-chain trap this
        # plan's audit found and fixed in reindex_job_with_ai itself).
        if overlap_pages is not None:
            engine_param_updates["ai_index_chunk_overlap_pages"] = overlap_pages
        if engine_param_updates:
            _set_engine_params(dup_id, engine_param_updates)

        import ai_reference_index
        res = await ai_reference_index.reindex_job_with_ai(dup_id)
        scores = evaluate_run(dup_id, gold)
        print(
            f"Trial {trial_idx}: status={res.get('status')} | "
            f"Target P={scores['target_precision']:.3f} R={scores['target_recall']:.3f} "
            f"ScopeAcc={scores['scope_accuracy']:.3f} FamilyDrift={scores['family_drift_count']} | "
            f"Mention P={scores['mention_precision']:.3f} R={scores['mention_recall']:.3f}"
        )
        return scores
    finally:
        database.delete_job(dup_id)


def _spread(results: List[Dict[str, float]], key: str) -> Tuple[float, float, float]:
    values = [r[key] for r in results]
    return min(values), sum(values) / len(values), max(values)


def _print_spread(label: str, results: List[Dict[str, float]], key: str, as_int: bool = False) -> None:
    lo, mean, hi = _spread(results, key)
    if as_int:
        print(f"{label:<18}min={lo:.0f}  mean={mean:.2f}  max={hi:.0f}")
    else:
        print(f"{label:<18}min={lo:.3f}  mean={mean:.3f}  max={hi:.3f}")


async def main_async():
    parser = argparse.ArgumentParser(description="Score reference index against gold fixture")
    parser.add_argument("job_id", help="Source job ID to evaluate")
    parser.add_argument("--fixture", default="tests/fixtures/gold_index_108ba949.json", help="Path to gold fixture")
    parser.add_argument("--trials", type=int, default=3, help="Number of trials per configuration")
    parser.add_argument("--enable-review", action="store_true", help="Enable ai_index_review_rule_prior flag")
    parser.add_argument("--chunk-pages", type=int, default=None, help="Override ai_index_chunk_pages on each duplicate")
    parser.add_argument("--overlap-pages", type=int, default=None, help="Override ai_index_chunk_overlap_pages on each duplicate (0 disables overlap)")
    pleading_group = parser.add_mutually_exclusive_group()
    pleading_group.add_argument("--pleading-points-via-ai", dest="pleading_points_via_ai", action="store_true", default=None, help="Force ai_index_pleading_points_via_ai on")
    pleading_group.add_argument("--no-pleading-points-via-ai", dest="pleading_points_via_ai", action="store_false", help="Force ai_index_pleading_points_via_ai off (tests the prompt-contradiction hypothesis)")
    args = parser.parse_args()

    gold = load_gold_fixture(args.fixture)
    print(f"Loaded gold fixture with {len(gold.get('targets', []))} targets, {len(gold.get('mentions', []))} mentions")
    print(
        f"Running {args.trials} trials with review_flag={args.enable_review}, "
        f"chunk_pages={args.chunk_pages}, overlap_pages={args.overlap_pages}, "
        f"pleading_points_via_ai={args.pleading_points_via_ai}..."
    )

    results: List[Dict[str, float]] = []
    for i in range(1, args.trials + 1):
        res = await run_trial(
            args.job_id, gold, i, args.enable_review, args.chunk_pages,
            args.pleading_points_via_ai, args.overlap_pages,
        )
        results.append(res)

    # Spread across trials, not just the mean — a single run (good or bad)
    # proved nothing in every prior round of this investigation (see
    # AI_INDEXING_ACCURACY_INVESTIGATION.md §7.5); min/max surfaces the
    # run-to-run instability precision/recall means alone would hide.
    print(f"\n--- Summary across {len(results)} trial(s) ---")
    _print_spread("Target Precision:", results, "target_precision")
    _print_spread("Target Recall:", results, "target_recall")
    _print_spread("Scope Accuracy:", results, "scope_accuracy")
    _print_spread("Family Drift:", results, "family_drift_count", as_int=True)
    _print_spread("Mention Precision:", results, "mention_precision")
    _print_spread("Mention Recall:", results, "mention_recall")


if __name__ == "__main__":
    asyncio.run(main_async())
