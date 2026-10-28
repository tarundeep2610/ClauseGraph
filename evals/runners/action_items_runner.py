"""Actionable items extraction evaluation runner."""

from typing import Any, Dict, List, Optional

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
    page_from: Optional[int] = None,
    page_to: Optional[int] = None,
) -> Dict[str, Any]:
    """Pure evaluation of a job's persisted action items against gold annotations.

    `page_from`/`page_to`, when given, restrict both the gold and actual
    items considered to that page range — mirrors
    `evals/runners/index_runner.py::evaluate_job_structure`'s own
    page-range filtering, so a sliced trial only gets credited/penalized for
    the pages it was actually asked to process."""
    act_items = database.get_all_action_items_for_job(job_id)
    if page_from is not None or page_to is not None:
        p_min = page_from or 1
        p_max = page_to or 999999
        gold_items = [i for i in gold_items if p_min <= i.get("page", 0) <= p_max]
        act_items = [i for i in act_items if p_min <= i.get("page", 0) <= p_max]
    return score_action_items(gold_items, act_items)


async def run_action_items_trial(
    source_job_id: str,
    gold_items: List[Dict[str, Any]],
    trial_idx: int = 1,
    page_from: Optional[int] = None,
    page_to: Optional[int] = None,
    two_pass: Optional[bool] = None,
    chunk_pages: Optional[int] = None,
    adaptive_chunking: Optional[bool] = None,
) -> Dict[str, Any]:
    """Executes a single action item extraction trial on an isolated duplicate job.

    `page_from`/`page_to` restrict extraction to a page slice (via
    `ai_action_items.reindex_action_items_for_job`'s `start_page`/`end_page`)
    and scoring to the same range — cheap iteration on a large document
    without reprocessing every page.

    `two_pass`/`chunk_pages`/`adaptive_chunking`, when given, pass straight
    through as `reindex_action_items_for_job`'s own
    `two_pass_override`/`chunk_pages_override`/`adaptive_chunking_override`
    kwargs — no `engine_params` DB mutation needed (unlike the structure
    pass's `scripts/score_reference_index.py::_set_engine_params`), since
    `reindex_action_items_for_job` already exposes first-class overrides
    that win over both the duplicated job's own `engine_params` and the
    config default."""
    from compare_ai_indexing_qwen_vs_gemini import duplicate_job
    import ai_action_items

    dup_id = duplicate_job(source_job_id)
    try:
        reset_action_item_state(dup_id)
        report = await ai_action_items.reindex_action_items_for_job(
            dup_id, start_page=page_from, end_page=page_to,
            two_pass_override=two_pass, chunk_pages_override=chunk_pages,
            adaptive_chunking_override=adaptive_chunking,
        )
        scores = evaluate_job_action_items(dup_id, gold_items, page_from=page_from, page_to=page_to)
        scores["trial_idx"] = trial_idx
        scores["status"] = report.get("status")

        found = report.get("items_found", 0)
        dropped = report.get("items_dropped_ungrounded", 0)
        total = found + dropped
        scores["grounding_rate"] = (found / total) if total > 0 else 1.0
        return scores
    finally:
        cleanup_duplicate_job(dup_id)


async def run_action_items_benchmark(
    source_job_id: str,
    gold_items: List[Dict[str, Any]],
    trials: int = 1,
    page_from: Optional[int] = None,
    page_to: Optional[int] = None,
    two_pass: Optional[bool] = None,
    chunk_pages: Optional[int] = None,
    adaptive_chunking: Optional[bool] = None,
) -> Dict[str, Any]:
    """Executes multi-trial action items evaluation and computes score spreads."""
    results: List[Dict[str, Any]] = []
    for i in range(1, trials + 1):
        scores = await run_action_items_trial(
            source_job_id, gold_items, trial_idx=i, page_from=page_from, page_to=page_to,
            two_pass=two_pass, chunk_pages=chunk_pages, adaptive_chunking=adaptive_chunking,
        )
        results.append(scores)

    summary_spread = {}
    for metric in ("precision", "recall", "f1", "grounding_rate"):
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
            page_from=kwargs.get("page_from"),
            page_to=kwargs.get("page_to"),
            two_pass=kwargs.get("two_pass"),
            chunk_pages=kwargs.get("chunk_pages"),
            adaptive_chunking=kwargs.get("adaptive_chunking"),
        )
