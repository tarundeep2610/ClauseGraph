"""Actionable items extraction evaluation runner."""

from typing import Any, Dict, List

import database
from evals.metrics.grounding import score_action_items
from evals.runners.base import BaseEvalRunner, cleanup_duplicate_job


def reset_action_item_state(job_id: str) -> None:
    """Resets action items version and report on a duplicate job."""
    conn = database.get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE ocr_jobs SET action_item_index_version = 0, action_item_report = '{}', "
        "action_items_built_at = NULL WHERE job_id = ?",
        (job_id,),
    )
    cursor.execute("DELETE FROM ocr_action_items WHERE job_id = ?", (job_id,))
    conn.commit()
    conn.close()


def evaluate_job_action_items(
    job_id: str,
    gold_items: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Pure evaluation of a job's persisted action items against gold annotations."""
    act_items = database.get_all_action_items_for_job(job_id)
    return score_action_items(gold_items, act_items)


async def run_action_items_trial(
    source_job_id: str,
    gold_items: List[Dict[str, Any]],
    trial_idx: int = 1,
) -> Dict[str, Any]:
    """Executes a single action item extraction trial on an isolated duplicate job."""
    from compare_ai_indexing_qwen_vs_gemini import duplicate_job
    import ai_action_items

    dup_id = duplicate_job(source_job_id)
    try:
        reset_action_item_state(dup_id)
        report = await ai_action_items.reindex_action_items_for_job(dup_id)
        scores = evaluate_job_action_items(dup_id, gold_items)
        scores["trial_idx"] = trial_idx
        scores["status"] = report.get("status")
        return scores
    finally:
        cleanup_duplicate_job(dup_id)


async def run_action_items_benchmark(
    source_job_id: str,
    gold_items: List[Dict[str, Any]],
    trials: int = 1,
) -> Dict[str, Any]:
    """Executes multi-trial action items evaluation and computes score spreads."""
    results: List[Dict[str, Any]] = []
    for i in range(1, trials + 1):
        scores = await run_action_items_trial(source_job_id, gold_items, trial_idx=i)
        results.append(scores)

    summary_spread = {}
    for metric in ("precision", "recall", "f1"):
        vals = [r[metric] for r in results if metric in r]
        if vals:
            summary_spread[metric] = {
                "min": min(vals),
                "mean": sum(vals) / len(vals),
                "max": max(vals),
            }

    return {
        "trials_count": len(results),
        "trials": results,
        "spread": summary_spread,
    }


class ActionItemsEvalRunner(BaseEvalRunner):
    """Eval runner implementation for actionable items extraction."""

    async def run(self, dataset_id: str, **kwargs: Any) -> Dict[str, Any]:
        from evals.datasets.registry import get_benchmark_dataset
        from evals.datasets.loader import load_gold_action_items_fixture

        dataset = get_benchmark_dataset(dataset_id)
        if not dataset.gold_action_items_path:
            raise ValueError(f"Dataset '{dataset_id}' does not have an action items gold fixture")

        fixture = load_gold_action_items_fixture(dataset.gold_action_items_path)
        gold_items = [item.model_dump() for item in fixture.action_items]

        trials = kwargs.get("trials", dataset.default_trials)
        return await run_action_items_benchmark(
            source_job_id=dataset.source_job_id,
            gold_items=gold_items,
            trials=trials,
        )
