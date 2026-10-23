"""Score the actionable-items extraction pass (ai_action_items.py) against a
gold fixture across N trials.

Mirrors scripts/score_reference_index.py's duplicate-job / per-trial /
summary-spread pattern, simplified for action items' flat (no scope/
hierarchy) shape: identity for matching is (page, item_type, normalized
evidence_quote) — matching on the model's own paraphrased item_text would be
far too brittle across independent extraction runs, but the verbatim,
grounded evidence_quote is exactly the signal that's supposed to be stable.

Usage:
    python scripts/score_action_items.py 89cfa529-2390-464c-9c3e-2446ef68c588 \
        --fixture tests/fixtures/gold_action_items_89cfa529.json --trials 3
"""

import argparse
import asyncio
import json
import os
import re
import sqlite3
import sys
import time
from typing import Any, Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import database
import ai_action_items
from compare_ai_indexing_qwen_vs_gemini import duplicate_job


def _reset_action_item_state(job_id: str) -> None:
    """duplicate_job() copies every ocr_jobs column generically (including
    the action-item ones added alongside this script), but carries over the
    SOURCE job's values rather than resetting them — a scoring run must
    always start from a genuinely untouched duplicate, same as it already
    resets ai_index_report/reference_index_version for the structure pass."""
    conn = sqlite3.connect(database.DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE ocr_jobs SET action_item_index_version = 0, action_item_report = '{}', "
        "action_items_built_at = NULL WHERE job_id = ?",
        (job_id,),
    )
    conn.commit()
    conn.close()


def load_gold_fixture(fixture_path: str) -> Dict[str, Any]:
    with open(fixture_path, "r", encoding="utf-8") as f:
        return json.load(f)


_WS_RE = re.compile(r"\s+")


def _norm_quote(q: Any) -> str:
    return _WS_RE.sub(" ", str(q or "")).strip().casefold()


def _ident(item: Dict[str, Any]) -> Tuple[Any, str, str]:
    return (item.get("page"), item.get("item_type") or "", _norm_quote(item.get("evidence_quote")))


def score_items(gold_items: List[Dict[str, Any]], act_items: List[Dict[str, Any]]) -> Dict[str, Any]:
    g_ids = {_ident(i) for i in gold_items}
    a_ids = {_ident(i) for i in act_items}
    matched = g_ids & a_ids
    precision = len(matched) / max(1, len(a_ids))
    recall = len(matched) / max(1, len(g_ids))

    # Per-type breakdown — precision/recall collapsed across all four types
    # hides whether, say, "condition" items are systematically worse than
    # "deadline" ones.
    by_type: Dict[str, Dict[str, float]] = {}
    for t in ("deadline", "obligation", "condition", "other"):
        g_t = {i for i in g_ids if i[1] == t}
        a_t = {i for i in a_ids if i[1] == t}
        m_t = g_t & a_t
        by_type[t] = {
            "gold": len(g_t), "actual": len(a_t), "matched": len(m_t),
            "precision": len(m_t) / max(1, len(a_t)),
            "recall": len(m_t) / max(1, len(g_t)),
        }

    return {
        "precision": precision, "recall": recall,
        "matched_count": len(matched), "gold_count": len(g_ids), "act_count": len(a_ids),
        "by_type": by_type,
        "false_positives": [a for a in act_items if _ident(a) not in matched],
        "false_negatives": [g for g in gold_items if _ident(g) not in matched],
    }


async def run_trial(source_job_id: str, gold: Dict[str, Any], trial_idx: int) -> Dict[str, Any]:
    dup_id = duplicate_job(source_job_id)
    try:
        _reset_action_item_state(dup_id)
        report = await ai_action_items.reindex_action_items_for_job(dup_id)
        act_items = database.get_all_action_items_for_job(dup_id)
        scores = score_items(gold.get("action_items", []), act_items)
        print(
            f"Trial {trial_idx}: status={report.get('status')} | "
            f"P={scores['precision']:.3f} R={scores['recall']:.3f} "
            f"(gold={scores['gold_count']} act={scores['act_count']} matched={scores['matched_count']})",
            flush=True,
        )
        for t, s in scores["by_type"].items():
            if s["gold"] or s["actual"]:
                print(f"    {t:<10} P={s['precision']:.3f} R={s['recall']:.3f} (gold={s['gold']} act={s['actual']})")
        return scores
    finally:
        database.delete_job(dup_id)


def _spread(results: List[Dict[str, float]], key: str) -> Tuple[float, float, float]:
    values = [r[key] for r in results]
    return min(values), sum(values) / len(values), max(values)


def _print_spread(label: str, results: List[Dict[str, float]], key: str) -> None:
    lo, mean, hi = _spread(results, key)
    print(f"{label:<20}min={lo:.3f}  mean={mean:.3f}  max={hi:.3f}")


async def main_async():
    parser = argparse.ArgumentParser(description="Score actionable-items extraction against a gold fixture")
    parser.add_argument("job_id", help="Source job ID to evaluate")
    parser.add_argument("--fixture", required=True, help="Path to gold_action_items_*.json fixture")
    parser.add_argument("--trials", type=int, default=1, help="Number of trials")
    parser.add_argument("--dump-diff", default=None, help="Path to write the final trial's false positives/negatives as JSON")
    args = parser.parse_args()

    gold = load_gold_fixture(args.fixture)
    gold_items = gold.get("action_items", [])
    print(f"Loaded gold fixture with {len(gold_items)} action items.")
    print(f"Running {args.trials} trial(s) against job {args.job_id}...")

    results = []
    last_scores = None
    for i in range(1, args.trials + 1):
        scores = await run_trial(args.job_id, gold, i)
        results.append(scores)
        last_scores = scores

    print(f"\n--- Summary across {len(results)} trial(s) ---")
    _print_spread("Precision:", results, "precision")
    _print_spread("Recall:", results, "recall")

    if args.dump_diff and last_scores:
        os.makedirs(os.path.dirname(os.path.abspath(args.dump_diff)) or ".", exist_ok=True)
        with open(args.dump_diff, "w", encoding="utf-8") as f:
            json.dump({
                "false_positives": last_scores["false_positives"],
                "false_negatives": last_scores["false_negatives"],
            }, f, indent=2)
        print(f"\nWrote diff (last trial) to {args.dump_diff}")


if __name__ == "__main__":
    asyncio.run(main_async())
