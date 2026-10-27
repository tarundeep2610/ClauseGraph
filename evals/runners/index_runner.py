"""Structure & cross-reference index evaluation runner.

Orchestrates multi-trial evaluations against gold fixtures, computes precision/recall/scope accuracy/
family drift, tracks chunk progression, and enforces isolated duplicate job execution.
"""

import asyncio
from contextlib import nullcontext
from dataclasses import dataclass
import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import database
import structure_overrides
from evals.metrics.structure import (
    RULE_FAMILIES,
    extract_family,
    score_content_dual_match,
    score_target_slice,
    target_identifier,
)
from evals.runners.base import BaseEvalRunner, cleanup_duplicate_job


@dataclass
class IndexTrialConfig:
    """Configuration knobs for an AI indexing evaluation trial."""

    trials: int = 1
    chunk_pages: Optional[int] = None
    overlap_pages: Optional[int] = None
    chunk_chars_max: Optional[int] = None
    adaptive_chunking: Optional[bool] = None
    two_pass: Optional[bool] = None
    pleading_points_via_ai: Optional[bool] = None
    compact_prompt: bool = False
    record_dir: Optional[str] = None
    replay_file: Optional[str] = None
    # Matches config.AI_INDEX_REVIEW_RULE_PRIOR's real default (false) — this
    # previously defaulted to True, silently forcing every evals.cli index
    # run into the exact "stale engine_params" config the investigation doc
    # (§11.3) already proved collapses precision on the appeal fixture
    # (0.219-0.253 vs 0.376 corrected). Never exposed via a CLI flag before
    # either, so there was no way to override it short of editing this file.
    enable_review: bool = False
    dump_diff: Optional[str] = None
    resumable: bool = False
    page_from: Optional[int] = None
    page_to: Optional[int] = None


def load_actual_targets(job_id: str) -> List[Dict[str, Any]]:
    """Loads override-resolved structure targets for a job."""
    resolved = structure_overrides.resolve_structure_for_job(job_id)
    return [
        {
            "page": r.get("page"),
            "key": r.get("key"),
            "occurrence_index": r.get("occurrence_index", 0),
            "scope_key": r.get("resolved_scope_key")
            if r.get("resolved_scope_key") is not None
            else (r.get("scope_key") or ""),
            "label": r.get("own_label") or "",
            "detection_source": r.get("detection_source") or "rule",
        }
        for r in resolved
    ]


def build_labeled_diff(
    gold_targets: List[Dict[str, Any]],
    actual_targets: List[Dict[str, Any]],
    matched_ids: set,
) -> Dict[str, Any]:
    """Builds false positive / false negative lists and page x level diff grid."""
    fps = []
    for a in actual_targets:
        if target_identifier(a) not in matched_ids:
            sk = a.get("scope_key") or ""
            fps.append({
                "page": a.get("page"),
                "key": a.get("key"),
                "family": extract_family(a.get("key")),
                "label": a.get("label") or "",
                "scope_key": sk,
                "detection_source": a.get("detection_source", "ai"),
                "level": sk.count("/") + 1 if sk else 0,
            })

    fns = []
    for g in gold_targets:
        if target_identifier(g) not in matched_ids:
            sk = g.get("scope_key") or ""
            fns.append({
                "page": g.get("page"),
                "key": g.get("key"),
                "family": extract_family(g.get("key")),
                "label": g.get("label") or "",
                "scope_key": sk,
                "detection_source": "rule" if extract_family(g.get("key")) in RULE_FAMILIES else "ai",
                "level": sk.count("/") + 1 if sk else 0,
            })

    grid: Dict[Tuple[int, int], Dict[str, int]] = {}
    for g in gold_targets:
        p = g.get("page", 0)
        sk = g.get("scope_key") or ""
        lvl = sk.count("/") + 1 if sk else 0
        cell = grid.setdefault((p, lvl), {"gold": 0, "act": 0, "matched": 0, "fp": 0, "fn": 0})
        cell["gold"] += 1
        if target_identifier(g) in matched_ids:
            cell["matched"] += 1
        else:
            cell["fn"] += 1

    for a in actual_targets:
        p = a.get("page", 0)
        sk = a.get("scope_key") or ""
        lvl = sk.count("/") + 1 if sk else 0
        cell = grid.setdefault((p, lvl), {"gold": 0, "act": 0, "matched": 0, "fp": 0, "fn": 0})
        cell["act"] += 1
        if target_identifier(a) not in matched_ids:
            cell["fp"] += 1

    grid_list = []
    for (p, lvl), counts in sorted(grid.items()):
        act_c = counts["act"]
        gold_c = counts["gold"]
        m_c = counts["matched"]
        grid_list.append({
            "page": p,
            "level": lvl,
            "gold": gold_c,
            "actual": act_c,
            "matched": m_c,
            "fp": counts["fp"],
            "fn": counts["fn"],
            "precision": m_c / max(1, act_c),
            "recall": m_c / max(1, gold_c),
        })

    return {
        "false_positives": fps,
        "false_negatives": fns,
        "grid": grid_list,
    }


def evaluate_job_structure(
    job_id: str,
    gold: Dict[str, Any],
    return_diff: bool = False,
    page_from: Optional[int] = None,
    page_to: Optional[int] = None,
) -> Dict[str, Any]:
    """Pure scoring of a job's persisted structure against a gold fixture dictionary."""
    actual_mentions = database.get_all_reference_mentions_for_job(job_id)
    gold_targets = gold.get("targets", [])
    gold_mentions = gold.get("mentions", [])

    act_t_list = load_actual_targets(job_id)

    if page_from is not None or page_to is not None:
        p_min = page_from or 1
        p_max = page_to or 999999
        gold_targets = [t for t in gold_targets if p_min <= t.get("page", 0) <= p_max]
        act_t_list = [t for t in act_t_list if p_min <= t.get("page", 0) <= p_max]
        gold_mentions = [m for m in gold_mentions if p_min <= m.get("page", 0) <= p_max]
        actual_mentions = [m for m in actual_mentions if p_min <= m.get("page", 0) <= p_max]

    gold_rule = [t for t in gold_targets if extract_family(t.get("key")) in RULE_FAMILIES]
    gold_ai = [t for t in gold_targets if extract_family(t.get("key")) not in RULE_FAMILIES]

    act_rule = [t for t in act_t_list if t.get("detection_source") == "rule"]
    act_ai = [t for t in act_t_list if t.get("detection_source") == "ai"]

    merged_scores = score_target_slice(gold_targets, act_t_list)
    ai_scores = score_target_slice(gold_ai, act_ai)
    rule_scores = score_target_slice(gold_rule, act_rule)

    dual_match = score_content_dual_match(gold_ai, act_ai, ai_scores["matched_ids"])

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
    if len(gold_m_resolving) == 0:
        m_precision = 1.0
        m_recall = 1.0
    elif len(act_m_resolving) == 0:
        m_precision = 0.0
        m_recall = 0.0
    else:
        m_precision = len(matched_mentions) / len(act_m_resolving)
        m_recall = len(matched_mentions) / len(gold_m_resolving)

    res: Dict[str, Any] = {
        "target_precision": merged_scores["precision"],
        "target_recall": merged_scores["recall"],
        "scope_accuracy": merged_scores["scope_accuracy"],
        "family_drift_count": merged_scores["family_drift"],
        "mention_precision": m_precision,
        "mention_recall": m_recall,
        "matched_targets_count": merged_scores["matched_count"],
        "total_actual_targets": merged_scores["actual_count"],
        "total_gold_targets": merged_scores["gold_count"],
        "matched_mentions_count": len(matched_mentions),
        "total_actual_mentions": len(act_m_resolving),
        "total_gold_mentions": len(gold_m_resolving),
        "ai_precision": ai_scores["precision"],
        "ai_recall": ai_scores["recall"],
        "ai_scope_accuracy": ai_scores["scope_accuracy"],
        "ai_family_drift": ai_scores["family_drift"],
        "ai_matched_count": ai_scores["matched_count"],
        "ai_actual_targets": ai_scores["actual_count"],
        "ai_gold_targets": ai_scores["gold_count"],
        "rule_precision": rule_scores["precision"],
        "rule_recall": rule_scores["recall"],
        "rule_scope_accuracy": rule_scores["scope_accuracy"],
        "rule_family_drift": rule_scores["family_drift"],
        "rule_matched_count": rule_scores["matched_count"],
        "rule_actual_targets": rule_scores["actual_count"],
        "rule_gold_targets": rule_scores["gold_count"],
        "labeled_gold_targets": dual_match["labeled_gold_count"],
        "labeled_key_recall": dual_match["labeled_key_recall"],
        "labeled_content_recall": dual_match["labeled_content_recall"],
        "drift_tax": dual_match["drift_tax"],
    }

    if return_diff:
        res["diff"] = build_labeled_diff(gold_targets, act_t_list, merged_scores["matched_ids"])

    return res


def _set_engine_params(job_id: str, updates: Dict[str, Any]) -> None:
    conn = database.get_db_connection()
    cursor = conn.cursor()
    row = cursor.execute("SELECT engine_params FROM ocr_jobs WHERE job_id = ?", (job_id,)).fetchone()
    params = json.loads(row[0]) if row and row[0] else {}
    params.update(updates)
    cursor.execute("UPDATE ocr_jobs SET engine_params = ? WHERE job_id = ?", (json.dumps(params), job_id))
    conn.commit()
    conn.close()


async def run_index_trial(
    source_job_id: str,
    gold: Dict[str, Any],
    trial_idx: int,
    config: IndexTrialConfig,
    return_diff: bool = False,
) -> Dict[str, Any]:
    """Executes a single indexing trial on an isolated duplicate job."""
    from compare_ai_indexing_qwen_vs_gemini import duplicate_job
    import ai_indexing
    import ai_reference_index

    dup_id = duplicate_job(source_job_id, chunk_pages=config.chunk_pages)
    try:
        engine_param_updates = {
            "ai_index_review_rule_prior": bool(config.enable_review),
            "ai_index_resolve_parent_via_ai": False,
        }
        if config.pleading_points_via_ai is not None:
            engine_param_updates["ai_index_pleading_points_via_ai"] = config.pleading_points_via_ai
        if config.overlap_pages is not None:
            engine_param_updates["ai_index_chunk_overlap_pages"] = config.overlap_pages
        if config.chunk_chars_max is not None:
            engine_param_updates["ai_index_chunk_chars_max"] = config.chunk_chars_max
        if config.adaptive_chunking is not None:
            engine_param_updates["ai_index_adaptive_chunking"] = config.adaptive_chunking
        if config.two_pass is not None:
            engine_param_updates["ai_index_two_pass"] = config.two_pass

        _set_engine_params(dup_id, engine_param_updates)

        if config.replay_file:
            expected_fp = {
                "source_job_id": source_job_id,
                "chunk_pages": config.chunk_pages or 3,
                "prompt_variant": "compact" if config.compact_prompt else "full",
            }
            scope = ai_indexing.replay_scope(config.replay_file, expected_fingerprint=expected_fp, strict_fidelity=False)
        elif config.record_dir:
            os.makedirs(config.record_dir, exist_ok=True)
            rec_name = f"{source_job_id}_{'compact' if config.compact_prompt else 'full'}_trial{trial_idx}_{int(time.time())}.json"
            rec_path = os.path.join(config.record_dir, rec_name)
            scope = ai_indexing.record_scope(rec_path)
        else:
            scope = nullcontext()

        with scope:
            await ai_reference_index.reindex_job_with_ai(
                dup_id,
                start_page=config.page_from,
                end_page=config.page_to,
            )

        scores = evaluate_job_structure(
            dup_id, gold, return_diff=return_diff,
            page_from=config.page_from,
            page_to=config.page_to,
        )
        return scores
    finally:
        cleanup_duplicate_job(dup_id)


async def run_index_benchmark(
    source_job_id: str,
    gold: Dict[str, Any],
    config: Optional[IndexTrialConfig] = None,
) -> Dict[str, Any]:
    """Runs N repeated indexing trials and computes statistical distribution spreads."""
    cfg = config or IndexTrialConfig()
    results: List[Dict[str, Any]] = []
    last_diff = None

    for i in range(1, cfg.trials + 1):
        need_diff = bool(cfg.dump_diff and i == cfg.trials)
        trial_scores = await run_index_trial(
            source_job_id=source_job_id,
            gold=gold,
            trial_idx=i,
            config=cfg,
            return_diff=need_diff,
        )
        if need_diff:
            last_diff = trial_scores.get("diff")
        results.append(trial_scores)

    # Compute spreads across trials
    metrics_to_spread = [
        "target_precision", "target_recall", "scope_accuracy", "family_drift_count",
        "ai_precision", "ai_recall", "ai_scope_accuracy", "ai_family_drift",
        "rule_precision", "rule_recall", "rule_scope_accuracy",
        "mention_precision", "mention_recall", "drift_tax",
    ]
    summary_spread: Dict[str, Dict[str, float]] = {}
    for m in metrics_to_spread:
        vals = [r[m] for r in results if m in r]
        if vals:
            summary_spread[m] = {
                "min": min(vals),
                "mean": sum(vals) / len(vals),
                "max": max(vals),
            }

    return {
        "trials_count": len(results),
        "trials": results,
        "spread": summary_spread,
        "last_diff": last_diff,
    }


class IndexEvalRunner(BaseEvalRunner):
    """Eval runner implementation for structure and cross-reference indexing."""

    async def run(self, dataset_id: str, **kwargs: Any) -> Dict[str, Any]:
        from evals.datasets.registry import get_benchmark_dataset
        from evals.datasets.loader import load_gold_index_fixture

        dataset = get_benchmark_dataset(dataset_id)
        fixture = load_gold_index_fixture(dataset.gold_index_path)
        gold_dict = fixture.model_dump()

        config = kwargs.get("config") or IndexTrialConfig(trials=dataset.default_trials)
        return await run_index_benchmark(
            source_job_id=dataset.source_job_id,
            gold=gold_dict,
            config=config,
        )
