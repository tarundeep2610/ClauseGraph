"""Score the actionable-items extraction pass (ai_action_items.py) against a gold fixture across N trials.

Delegates core scoring and runner execution to the evals package, maintaining full CLI backward compatibility.
"""

import argparse
import asyncio
import json
import os
import sys
from typing import Any, Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from evals.metrics.grounding import (
    action_item_identifier as _ident,
    normalize_quote as _norm_quote,
    score_action_items as score_items,
)
from evals.runners.action_items_runner import (
    reset_action_item_state as _reset_action_item_state,
    run_action_items_trial,
)


def load_gold_fixture(fixture_path: str) -> Dict[str, Any]:
    with open(fixture_path, "r", encoding="utf-8") as f:
        return json.load(f)


async def run_trial(
    source_job_id: str, gold: Dict[str, Any], trial_idx: int,
    page_from: int = None, page_to: int = None,
    two_pass: bool = None, chunk_pages: int = None, adaptive_chunking: bool = None,
) -> Dict[str, Any]:
    scores = await run_action_items_trial(
        source_job_id, gold.get("action_items", []), trial_idx=trial_idx,
        page_from=page_from, page_to=page_to,
        two_pass=two_pass, chunk_pages=chunk_pages, adaptive_chunking=adaptive_chunking,
    )
    print(
        f"Trial {trial_idx}: status={scores.get('status')} | "
        f"P={scores['precision']:.3f} R={scores['recall']:.3f} "
        f"(gold={scores['gold_count']} act={scores['actual_count']} matched={scores['matched_count']})",
        flush=True,
    )
    for t, s in scores["by_type"].items():
        if s.get("gold") or s.get("actual"):
            print(f"    {t:<10} P={s['precision']:.3f} R={s['recall']:.3f} (gold={s['gold']} act={s['actual']})")
    return scores


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
    parser.add_argument("--page-from", type=int, default=None, help="Start page for sliced evaluation")
    parser.add_argument("--page-to", type=int, default=None, help="End page for sliced evaluation")
    parser.add_argument("--chunk-pages", type=int, default=None, help="Override ai_index_chunk_pages for this trial")
    two_pass_group = parser.add_mutually_exclusive_group()
    two_pass_group.add_argument("--two-pass", dest="two_pass", action="store_true", default=None, help="Force two-pass macro-context on for this trial")
    two_pass_group.add_argument("--no-two-pass", dest="two_pass", action="store_false", help="Force two-pass macro-context off for this trial")
    adaptive_group = parser.add_mutually_exclusive_group()
    adaptive_group.add_argument("--adaptive-chunking", dest="adaptive_chunking", action="store_true", default=None, help="Force adaptive chunking on for this trial")
    adaptive_group.add_argument("--no-adaptive-chunking", dest="adaptive_chunking", action="store_false", help="Force adaptive chunking off for this trial")
    args = parser.parse_args()

    gold = load_gold_fixture(args.fixture)
    gold_items = gold.get("action_items", [])
    print(f"Loaded gold fixture with {len(gold_items)} action items.")
    if args.page_from is not None or args.page_to is not None:
        print(f"[SLICE MODE] Running targeted evaluation on Pages {args.page_from} to {args.page_to}")
    if args.two_pass is not None:
        print(f"[CONFIG] two_pass={args.two_pass}")
    if args.chunk_pages is not None:
        print(f"[CONFIG] chunk_pages={args.chunk_pages}")
    print(f"Running {args.trials} trial(s) against job {args.job_id}...")

    results = []
    last_scores = None
    for i in range(1, args.trials + 1):
        scores = await run_trial(
            args.job_id, gold, i, page_from=args.page_from, page_to=args.page_to,
            two_pass=args.two_pass, chunk_pages=args.chunk_pages, adaptive_chunking=args.adaptive_chunking,
        )
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
