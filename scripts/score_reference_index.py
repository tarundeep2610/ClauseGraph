"""Score reference index (targets and mentions) against a gold fixture across N trials.

Emits per run:
- Target precision & recall (AI-only, Merged, and Rule-only control)
- Scope-path exact-match rate
- Family-drift count
- Dual-match content scoring & Ordinal-drift tax
- Mention precision & recall (exact / approximate resolutions)
- Optional page x level diff grid

Scores against structure_overrides.resolve_structure_for_job(job_id).
"""

import argparse
import asyncio
from contextlib import nullcontext
import json
import os
import re
import sqlite3
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
import database
import structure_overrides
import ai_indexing
from compare_ai_indexing_qwen_vs_gemini import duplicate_job
from qwen_compact_prompt import apply_compact_prompt


def _set_engine_params(job_id: str, updates: Dict[str, Any]) -> None:
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
    params["ai_index_review_rule_prior"] = bool(flag_val)
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
RULE_FAMILIES = {"page", "term", "docref"}


def _norm_label(label: Any) -> str:
    return _WS_RE.sub(" ", str(label or "")).strip().lower()[:40]


def _count_family_drift(gold_targets: List[Dict[str, Any]], act_targets: List[Dict[str, Any]], matched_ids: set) -> int:
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


def _score_target_slice(g_targets: List[Dict[str, Any]], a_targets: List[Dict[str, Any]]) -> Dict[str, Any]:
    g_ids = {_ident(t) for t in g_targets}
    a_ids = {_ident(t) for t in a_targets}
    matched = g_ids & a_ids
    precision = len(matched) / max(1, len(a_ids))
    recall = len(matched) / max(1, len(g_ids))

    gold_scope_map = {_ident(t): (t.get("scope_key") or "") for t in g_targets}
    act_scope_map = {_ident(t): (t.get("scope_key") or "") for t in a_targets}
    scope_matches = sum(1 for tid in matched if gold_scope_map.get(tid) == act_scope_map.get(tid))
    scope_acc = scope_matches / max(1, len(matched))

    family_drift = _count_family_drift(g_targets, a_targets, matched)
    return {
        "precision": precision,
        "recall": recall,
        "scope_accuracy": scope_acc,
        "family_drift": family_drift,
        "matched_count": len(matched),
        "act_count": len(a_ids),
        "gold_count": len(g_ids),
        "matched_ids": matched,
    }


def _score_content_dual_match(
    gold_targets: List[Dict[str, Any]],
    act_targets: List[Dict[str, Any]],
    matched_key_ids: set,
) -> Dict[str, Any]:
    labeled_gold = [g for g in gold_targets if _norm_label(g.get("label"))]
    used_act_idx = set()
    content_matched = 0

    for g in labeled_gold:
        g_label = _norm_label(g.get("label"))
        for i, a in enumerate(act_targets):
            if i in used_act_idx or a.get("page") != g.get("page"):
                continue
            if _norm_label(a.get("label")) == g_label:
                content_matched += 1
                used_act_idx.add(i)
                break

    labeled_key_matched = sum(1 for g in labeled_gold if _ident(g) in matched_key_ids)
    labeled_count = len(labeled_gold)
    key_r = labeled_key_matched / max(1, labeled_count)
    content_r = content_matched / max(1, labeled_count)
    drift_tax = content_r - key_r

    return {
        "labeled_gold_count": labeled_count,
        "labeled_key_matched": labeled_key_matched,
        "labeled_content_matched": content_matched,
        "labeled_key_recall": key_r,
        "labeled_content_recall": content_r,
        "drift_tax": drift_tax,
    }


def _labeled_diff(
    gold_targets: List[Dict[str, Any]],
    act_targets: List[Dict[str, Any]],
    matched_ids: set,
) -> Dict[str, Any]:
    fps = []
    for a in act_targets:
        if _ident(a) not in matched_ids:
            sk = a.get("scope_key") or ""
            fps.append({
                "page": a.get("page"),
                "key": a.get("key"),
                "family": _family(a.get("key")),
                "label": a.get("label") or "",
                "scope_key": sk,
                "detection_source": a.get("detection_source", "ai"),
                "level": sk.count('/') + 1 if sk else 0,
            })

    fns = []
    for g in gold_targets:
        if _ident(g) not in matched_ids:
            sk = g.get("scope_key") or ""
            fns.append({
                "page": g.get("page"),
                "key": g.get("key"),
                "family": _family(g.get("key")),
                "label": g.get("label") or "",
                "scope_key": sk,
                "detection_source": "rule" if _family(g.get("key")) in RULE_FAMILIES else "ai",
                "level": sk.count('/') + 1 if sk else 0,
            })

    grid: Dict[Tuple[int, int], Dict[str, int]] = {}
    for g in gold_targets:
        p = g.get("page", 0)
        sk = g.get("scope_key") or ""
        lvl = sk.count('/') + 1 if sk else 0
        cell = grid.setdefault((p, lvl), {"gold": 0, "act": 0, "matched": 0, "fp": 0, "fn": 0})
        cell["gold"] += 1
        if _ident(g) in matched_ids:
            cell["matched"] += 1
        else:
            cell["fn"] += 1

    for a in act_targets:
        p = a.get("page", 0)
        sk = a.get("scope_key") or ""
        lvl = sk.count('/') + 1 if sk else 0
        cell = grid.setdefault((p, lvl), {"gold": 0, "act": 0, "matched": 0, "fp": 0, "fn": 0})
        cell["act"] += 1
        if _ident(a) not in matched_ids:
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


def _load_actual_targets(job_id: str) -> List[Dict[str, Any]]:
    resolved = structure_overrides.resolve_structure_for_job(job_id)
    return [{
        "page": r.get("page"),
        "key": r.get("key"),
        "occurrence_index": r.get("occurrence_index", 0),
        "scope_key": r.get("resolved_scope_key") if r.get("resolved_scope_key") is not None else (r.get("scope_key") or ""),
        "label": r.get("own_label") or "",
        "detection_source": r.get("detection_source") or "rule",
    } for r in resolved]


def score_chunks(job_id: str, gold: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Per-chunk precision/recall + the chunk's own processing metadata
    (status/error/timing), so a trial's weak spots are visible at page-range
    granularity instead of only as one whole-document score. A target is
    attributed to whichever chunk's page range contains its own `page`
    field, regardless of which chunk's AI call actually produced it — this
    separates "this page range is genuinely hard" from chunk-boundary
    artifacts like the documented subdoc-ordinal cascade."""
    report = database.get_ai_index_report(job_id)
    chunks_log = report.get("chunks") or []
    # The real planned total — NOT len(chunks_log), which only ever counts
    # chunks completed so far and made every live-progress line read "N/N"
    # (confirmed bug: every row showed total == its own index while a trial
    # was still running).
    total_chunks = report.get("total_chunks") or len(chunks_log)
    gold_targets = gold.get("targets", [])
    act_t_list = _load_actual_targets(job_id)

    rows = []
    for idx, c in enumerate(chunks_log, start=1):
        first_page, last_page = c["first_page"], c["last_page"]
        g_sub = [t for t in gold_targets if first_page <= (t.get("page") or -1) <= last_page]
        a_sub = [t for t in act_t_list if first_page <= (t.get("page") or -1) <= last_page]
        scores = _score_target_slice(g_sub, a_sub)
        rows.append({
            "chunk_index": idx, "total_chunks": total_chunks,
            "first_page": first_page, "last_page": last_page,
            "status": c.get("status"), "error_kind": c.get("error_kind"), "error": c.get("error"),
            "duration_seconds": c.get("duration_seconds"), "provider_duration_ms": c.get("provider_duration_ms"),
            "heading_count": c.get("heading_count"),
            "precision": scores["precision"], "recall": scores["recall"],
            "gold_count": scores["gold_count"], "act_count": scores["act_count"], "matched_count": scores["matched_count"],
        })
    return rows


def _format_chunk_row(row: Dict[str, Any]) -> str:
    reason = f" error_kind={row['error_kind']}" if row.get("error_kind") else ""
    if row.get("error"):
        reason += f" ({row['error']})"
    dur = row.get("duration_seconds")
    dur_str = f"{dur:.1f}s" if dur is not None else "?"
    return (
        f"[chunk {row['chunk_index']}/{row['total_chunks']}] pages {row['first_page']}-{row['last_page']} | "
        f"status={row['status']}{reason} duration={dur_str} | "
        f"gold={row['gold_count']} act={row['act_count']} matched={row['matched_count']} "
        f"P={row['precision']:.3f} R={row['recall']:.3f}"
    )


async def _monitor_chunks(job_id: str, gold: Dict[str, Any], printed: List[int]) -> None:
    """Polls the job's ai_index_report every 5s and prints any newly-completed
    chunk's status/timing/precision-recall immediately, so a long trial shows
    live progress instead of going silent until the whole run finishes."""
    while True:
        await asyncio.sleep(5)
        _print_new_chunk_rows(job_id, gold, printed)


def _print_new_chunk_rows(job_id: str, gold: Dict[str, Any], printed: List[int]) -> None:
    rows = score_chunks(job_id, gold)
    for row in rows[printed[0]:]:
        print(_format_chunk_row(row), flush=True)
        printed[0] += 1


def _print_worst_chunks(job_id: str, gold: Dict[str, Any]) -> None:
    rows = score_chunks(job_id, gold)
    if not rows:
        return
    failed = [r for r in rows if r["status"] != "ok"]
    if failed:
        print("  Failed/fallback chunks:")
        for r in failed:
            print("   ", _format_chunk_row(r))
    print("  Worst 10 chunks by recall:")
    for r in sorted(rows, key=lambda r: (r["recall"], r["first_page"]))[:10]:
        print("   ", _format_chunk_row(r))
    print("  Worst 10 chunks by precision:")
    for r in sorted(rows, key=lambda r: (r["precision"], r["first_page"]))[:10]:
        print("   ", _format_chunk_row(r))


def evaluate_run(
    job_id: str,
    gold: Dict[str, Any],
    return_diff: bool = False,
) -> Dict[str, Any]:
    actual_mentions = database.get_all_reference_mentions_for_job(job_id)

    gold_targets = gold.get("targets", [])
    gold_mentions = gold.get("mentions", [])

    act_t_list = _load_actual_targets(job_id)

    gold_rule = [t for t in gold_targets if _family(t.get("key")) in RULE_FAMILIES]
    gold_ai = [t for t in gold_targets if _family(t.get("key")) not in RULE_FAMILIES]

    act_rule = [t for t in act_t_list if t.get("detection_source") == "rule"]
    act_ai = [t for t in act_t_list if t.get("detection_source") == "ai"]

    merged_scores = _score_target_slice(gold_targets, act_t_list)
    ai_scores = _score_target_slice(gold_ai, act_ai)
    rule_scores = _score_target_slice(gold_rule, act_rule)

    dual_match = _score_content_dual_match(gold_ai, act_ai, ai_scores["matched_ids"])

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
    if len(act_m_resolving) == 0:
        m_precision = 1.0 if len(gold_m_resolving) == 0 else 0.0
    else:
        m_precision = len(matched_mentions) / len(act_m_resolving)

    if len(gold_m_resolving) == 0:
        m_recall = 1.0
    else:
        m_recall = len(matched_mentions) / len(gold_m_resolving)

    res: Dict[str, Any] = {
        "target_precision": merged_scores["precision"],
        "target_recall": merged_scores["recall"],
        "scope_accuracy": merged_scores["scope_accuracy"],
        "family_drift_count": merged_scores["family_drift"],
        "mention_precision": m_precision,
        "mention_recall": m_recall,
        "matched_targets_count": merged_scores["matched_count"],
        "total_actual_targets": merged_scores["act_count"],
        "total_gold_targets": merged_scores["gold_count"],
        "matched_mentions_count": len(matched_mentions),
        "total_actual_mentions": len(act_m_resolving),
        "total_gold_mentions": len(gold_m_resolving),

        "ai_precision": ai_scores["precision"],
        "ai_recall": ai_scores["recall"],
        "ai_scope_accuracy": ai_scores["scope_accuracy"],
        "ai_family_drift": ai_scores["family_drift"],
        "ai_matched_count": ai_scores["matched_count"],
        "ai_actual_targets": ai_scores["act_count"],
        "ai_gold_targets": ai_scores["gold_count"],

        "rule_precision": rule_scores["precision"],
        "rule_recall": rule_scores["recall"],
        "rule_scope_accuracy": rule_scores["scope_accuracy"],
        "rule_family_drift": rule_scores["family_drift"],
        "rule_matched_count": rule_scores["matched_count"],
        "rule_actual_targets": rule_scores["act_count"],
        "rule_gold_targets": rule_scores["gold_count"],

        "labeled_gold_targets": dual_match["labeled_gold_count"],
        "labeled_key_recall": dual_match["labeled_key_recall"],
        "labeled_content_recall": dual_match["labeled_content_recall"],
        "drift_tax": dual_match["drift_tax"],
    }

    if return_diff:
        res["diff"] = _labeled_diff(gold_targets, act_t_list, merged_scores["matched_ids"])

    return res


async def run_trial(
    source_job_id: str,
    gold: Dict[str, Any],
    trial_idx: int,
    review_flag: bool,
    chunk_pages: "int | None" = None,
    pleading_points_via_ai: "bool | None" = None,
    overlap_pages: "int | None" = None,
    chunk_chars_max: "int | None" = None,
    record_dir: "str | None" = None,
    replay_file: "str | None" = None,
    compact_prompt: bool = False,
    return_diff: bool = False,
) -> Dict[str, Any]:
    dup_id = duplicate_job(source_job_id, chunk_pages=chunk_pages)
    try:
        engine_param_updates = {}
        engine_param_updates["ai_index_review_rule_prior"] = bool(review_flag)
        engine_param_updates["ai_index_resolve_parent_via_ai"] = False
        if pleading_points_via_ai is not None:
            engine_param_updates["ai_index_pleading_points_via_ai"] = pleading_points_via_ai
        if overlap_pages is not None:
            engine_param_updates["ai_index_chunk_overlap_pages"] = overlap_pages
        if chunk_chars_max is not None:
            engine_param_updates["ai_index_chunk_chars_max"] = chunk_chars_max
        if engine_param_updates:
            _set_engine_params(dup_id, engine_param_updates)

        if replay_file:
            expected_fp = {
                "source_job_id": source_job_id,
                "chunk_pages": chunk_pages or (config.AI_INDEX_CHUNK_PAGES if hasattr(config, "AI_INDEX_CHUNK_PAGES") else 3),
                "prompt_variant": "compact" if compact_prompt else "full",
            }
            scope = ai_indexing.replay_scope(replay_file, expected_fingerprint=expected_fp, strict_fidelity=False)
        elif record_dir:
            os.makedirs(record_dir, exist_ok=True)
            rec_name = f"{source_job_id}_{'compact' if compact_prompt else 'full'}_trial{trial_idx}_{int(time.time())}.json"
            rec_path = os.path.join(record_dir, rec_name)
            resolved_cfg = ai_indexing.describe_active_config() if hasattr(ai_indexing, "describe_active_config") else {}
            fingerprint = {
                "source_job_id": source_job_id,
                "chunk_pages": chunk_pages or 3,
                "overlap_pages": overlap_pages or 0,
                "prompt_variant": "compact" if compact_prompt else "full",
                "model": resolved_cfg.get("model", ""),
                "provider": resolved_cfg.get("provider", ""),
            }
            scope = ai_indexing.record_scope(rec_path, fingerprint=fingerprint)
        else:
            scope = nullcontext()

        import ai_reference_index
        printed = [0]
        monitor_task = asyncio.create_task(_monitor_chunks(dup_id, gold, printed))
        try:
            with scope:
                res = await ai_reference_index.reindex_job_with_ai(dup_id)
        finally:
            monitor_task.cancel()
            try:
                await monitor_task
            except asyncio.CancelledError:
                pass
            _print_new_chunk_rows(dup_id, gold, printed)  # catch any chunks that finished after the last poll

        scores = evaluate_run(dup_id, gold, return_diff=return_diff)
        print(
            f"Trial {trial_idx}: status={res.get('status')} | "
            f"AI P={scores['ai_precision']:.3f} R={scores['ai_recall']:.3f} (ScopeAcc={scores['ai_scope_accuracy']:.3f}, Drift={scores['ai_family_drift']}) | "
            f"Merged P={scores['target_precision']:.3f} R={scores['target_recall']:.3f} | "
            f"Rule P={scores['rule_precision']:.3f} R={scores['rule_recall']:.3f} | "
            f"DriftTax={scores['drift_tax']:+.3f} (ContentR={scores['labeled_content_recall']:.3f} vs KeyR={scores['labeled_key_recall']:.3f})",
            flush=True,
        )
        _print_worst_chunks(dup_id, gold)
        return scores
    finally:
        database.delete_job(dup_id)


def _spread(results: List[Dict[str, float]], key: str) -> Tuple[float, float, float]:
    values = [r[key] for r in results]
    return min(values), sum(values) / len(values), max(values)


def _print_spread(label: str, results: List[Dict[str, float]], key: str, as_int: bool = False) -> None:
    lo, mean, hi = _spread(results, key)
    if as_int:
        print(f"{label:<25}min={lo:.0f}  mean={mean:.2f}  max={hi:.0f}")
    else:
        print(f"{label:<25}min={lo:.3f}  mean={mean:.3f}  max={hi:.3f}")


async def main_async():
    parser = argparse.ArgumentParser(description="Score reference index against gold fixture")
    parser.add_argument("job_id", help="Source job ID to evaluate")
    parser.add_argument("--fixture", default="tests/fixtures/gold_index_108ba949.json", help="Path to gold fixture")
    parser.add_argument("--trials", type=int, default=3, help="Number of trials per configuration")
    parser.add_argument("--enable-review", action="store_true", help="Enable ai_index_review_rule_prior flag")
    parser.add_argument("--chunk-pages", type=int, default=None, help="Override ai_index_chunk_pages on each duplicate")
    parser.add_argument("--overlap-pages", type=int, default=None, help="Override ai_index_chunk_overlap_pages on each duplicate (0 disables overlap)")
    parser.add_argument("--chunk-chars-max", type=int, default=None, help="Override ai_index_chunk_chars_max on each duplicate")
    parser.add_argument("--compact-prompt", action="store_true", help="Use condensed compact prompt variant")
    parser.add_argument("--record-dir", default=None, help="Directory to save AI completion recordings")
    parser.add_argument("--replay-file", default=None, help="Path to recording JSON to replay offline")
    parser.add_argument("--dump-diff", default=None, help="Path to write JSON diff from the final trial")
    parser.add_argument(
        "--resumable", action="store_true",
        help="Cache RokkaAI job_ids by prompt content so a re-run after this process dies "
             "(e.g. the laptop sleeping) polls already-submitted chunks instead of resubmitting "
             "them. Scoring-harness-only — never touches the real app's provider registry.",
    )
    pleading_group = parser.add_mutually_exclusive_group()
    pleading_group.add_argument("--pleading-points-via-ai", dest="pleading_points_via_ai", action="store_true", default=None, help="Force ai_index_pleading_points_via_ai on")
    pleading_group.add_argument("--no-pleading-points-via-ai", dest="pleading_points_via_ai", action="store_false", help="Force ai_index_pleading_points_via_ai off (tests the prompt-contradiction hypothesis)")
    args = parser.parse_args()

    if args.compact_prompt:
        apply_compact_prompt()
        print("Applied compact prompt variant (in-process monkeypatch).")

    if args.resumable:
        from _resumable_rokkaai_provider import ResumableRokkaAIProvider, STATE_FILE
        import ai_indexing.providers as _providers
        _providers._PROVIDERS["rokkaai"] = ResumableRokkaAIProvider
        print(f"Resumable mode: RokkaAI job_ids will be cached at {STATE_FILE} — "
              f"a re-run of this exact command after an interruption polls already-"
              f"submitted chunks instead of resubmitting them.")

    gold = load_gold_fixture(args.fixture)
    gold_targets = gold.get("targets", [])
    gold_ai_cnt = sum(1 for t in gold_targets if _family(t.get("key")) not in RULE_FAMILIES)
    gold_rule_cnt = sum(1 for t in gold_targets if _family(t.get("key")) in RULE_FAMILIES)
    print(
        f"Loaded gold fixture with {len(gold_targets)} targets ({gold_ai_cnt} AI-family, {gold_rule_cnt} Rule-family), "
        f"{len(gold.get('mentions', []))} mentions"
    )
    print(
        f"Running {args.trials} trial(s) with review_flag={args.enable_review}, "
        f"chunk_pages={args.chunk_pages}, overlap_pages={args.overlap_pages}, "
        f"chunk_chars_max={args.chunk_chars_max}, "
        f"pleading_points_via_ai={args.pleading_points_via_ai}, "
        f"compact_prompt={args.compact_prompt}, record_dir={args.record_dir}, replay_file={args.replay_file}..."
    )

    results: List[Dict[str, Any]] = []
    last_diff = None
    for i in range(1, args.trials + 1):
        need_diff = bool(args.dump_diff and i == args.trials)
        res = await run_trial(
            args.job_id, gold, i, args.enable_review, args.chunk_pages,
            args.pleading_points_via_ai, args.overlap_pages, args.chunk_chars_max,
            record_dir=args.record_dir, replay_file=args.replay_file,
            compact_prompt=args.compact_prompt, return_diff=need_diff,
        )
        if need_diff:
            last_diff = res.get("diff")
        results.append(res)

    print(f"\n--- Summary across {len(results)} trial(s) ---")
    print("\n[AI-Only Mode (Model Performance)]")
    _print_spread("AI Target Precision:", results, "ai_precision")
    _print_spread("AI Target Recall:", results, "ai_recall")
    _print_spread("AI Scope Accuracy:", results, "ai_scope_accuracy")
    _print_spread("AI Family Drift:", results, "ai_family_drift", as_int=True)

    print("\n[Merged Mode (End-to-End Pipeline)]")
    _print_spread("Merged Precision:", results, "target_precision")
    _print_spread("Merged Recall:", results, "target_recall")
    _print_spread("Merged Scope Accuracy:", results, "scope_accuracy")
    _print_spread("Merged Family Drift:", results, "family_drift_count", as_int=True)

    print("\n[Rule-Only Mode (Deterministic Control)]")
    _print_spread("Rule Precision:", results, "rule_precision")
    _print_spread("Rule Recall:", results, "rule_recall")
    _print_spread("Rule Scope Accuracy:", results, "rule_scope_accuracy")

    print("\n[Diagnostics: Ordinal Drift Tax on Labeled Targets]")
    _print_spread("Labeled Key Recall:", results, "labeled_key_recall")
    _print_spread("Labeled Content Recall:", results, "labeled_content_recall")
    _print_spread("Ordinal Drift Tax (Delta):", results, "drift_tax")

    print("\n[Mentions]")
    _print_spread("Mention Precision:", results, "mention_precision")
    _print_spread("Mention Recall:", results, "mention_recall")

    if args.dump_diff and last_diff:
        os.makedirs(os.path.dirname(os.path.abspath(args.dump_diff)), exist_ok=True)
        with open(args.dump_diff, "w", encoding="utf-8") as f:
            json.dump(last_diff, f, indent=2)
        print(f"\nWrote labeled diff to {args.dump_diff}")


if __name__ == "__main__":
    asyncio.run(main_async())
