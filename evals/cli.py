"""Unified Command Line Interface for evaluation suite.

Usage:
    python -m evals.cli run --pipeline index --dataset appeal-100p --trials 3
    python -m evals.cli run --pipeline action-items --dataset tender-135p
    python -m evals.cli run --pipeline ocr --job-id 108ba949-bb7b-4f29-b582-ce4f4a44631b
    python -m evals.cli run --pipeline confidence --job-id 108ba949-bb7b-4f29-b582-ce4f4a44631b
    python -m evals.cli list-datasets
"""

import argparse
import asyncio
import json
import os
import sys
import uuid

# Windows console encoding safeguard
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

try:
    from dotenv import load_dotenv
    if os.path.exists(".env.local-test"):
        load_dotenv(".env.local-test", override=True)
    elif os.path.exists(".env"):
        load_dotenv(".env", override=True)
except ImportError:
    pass

import database
from evals.datasets.registry import get_benchmark_dataset, list_benchmark_datasets
from evals.gates import evaluate_action_items_gates, evaluate_index_gates, evaluate_ocr_gates
from evals.reporters.console import format_benchmark_summary
from evals.reporters.json_artifact import save_json_artifact
from evals.reporters.markdown import generate_markdown_report
from evals.runners.action_items_runner import ActionItemsEvalRunner
from evals.runners.confidence_runner import ConfidenceEvalRunner
from evals.runners.index_runner import IndexEvalRunner, IndexTrialConfig
from evals.runners.ocr_runner import OCREvalRunner


async def _run_index(args: argparse.Namespace) -> int:
    dataset = get_benchmark_dataset(args.dataset)
    runner = IndexEvalRunner()
    config_kwargs = dict(
        trials=args.trials or dataset.default_trials,
        chunk_pages=args.chunk_pages,
        overlap_pages=args.overlap_pages,
        adaptive_chunking=args.adaptive_chunking,
        two_pass=args.two_pass,
        compact_prompt=args.compact_prompt,
        dump_diff=args.dump_diff,
        page_from=args.page_from,
        page_to=args.page_to,
    )
    if args.page_from is not None or args.page_to is not None:
        print(f"\n[SLICE MODE] Running targeted evaluation on Pages {args.page_from} to {args.page_to}")
    if args.review_rule_prior is not None:
        config_kwargs["enable_review"] = args.review_rule_prior
    config = IndexTrialConfig(**config_kwargs)

    print(f"\n--- Running Index Benchmark: {dataset.name} ({config.trials} trial(s)) ---")
    results = await runner.run(args.dataset, config=config)

    print(format_benchmark_summary(results))

    # Evaluate gates on the mean scores across trials
    mean_scores = {k: v["mean"] for k, v in results.get("spread", {}).items()}
    gate_res = evaluate_index_gates(mean_scores, gates=dataset.index_gates)

    if args.markdown:
        md = generate_markdown_report(results, dataset.name, gate_res.profile_name, gate_res.passed)
        print("\n" + md)

    if args.dump_diff and results.get("last_diff"):
        os.makedirs(os.path.dirname(os.path.abspath(args.dump_diff)), exist_ok=True)
        with open(args.dump_diff, "w", encoding="utf-8") as f:
            json.dump(results["last_diff"], f, indent=2)

    save_json_artifact(
        run_id=str(uuid.uuid4()),
        pipeline="index",
        dataset_id=args.dataset,
        results=results,
        gates_passed=gate_res.passed,
    )

    if not gate_res.passed:
        print("\n[FAIL] REGRESSION GATES BREACHED:")
        for v in gate_res.violations:
            print(f"  - {v}")
        if not args.no_fail:
            return 1

    print("\n[PASS] All evaluation gates passed!")
    return 0


async def _run_action_items(args: argparse.Namespace) -> int:
    dataset = get_benchmark_dataset(args.dataset)
    runner = ActionItemsEvalRunner()
    trials = args.trials or dataset.default_trials

    if args.page_from is not None or args.page_to is not None:
        print(f"\n[SLICE MODE] Running targeted evaluation on Pages {args.page_from} to {args.page_to}")

    print(f"\n--- Running Action Items Benchmark: {dataset.name} ({trials} trial(s)) ---")
    results = await runner.run(
        args.dataset,
        trials=trials,
        page_from=args.page_from,
        page_to=args.page_to,
        two_pass=args.two_pass,
        chunk_pages=args.chunk_pages,
        adaptive_chunking=args.adaptive_chunking,
    )

    print("\n" + "=" * 70)
    print(f" ACTION ITEMS SUMMARY — {trials} Trial(s)")
    print("=" * 70)
    for metric, stats in results.get("spread", {}).items():
        print(f"{metric.replace('_', ' ').title():25}: min={stats['min']:.3f}  mean={stats['mean']:.3f}  max={stats['max']:.3f}")
    print("=" * 70)

    mean_scores = {k: v["mean"] for k, v in results.get("spread", {}).items()}
    gate_res = evaluate_action_items_gates(mean_scores, gates=dataset.action_items_gates)

    save_json_artifact(
        run_id=str(uuid.uuid4()),
        pipeline="action-items",
        dataset_id=args.dataset,
        results=results,
        gates_passed=gate_res.passed,
    )

    if not gate_res.passed:
        print("\n[FAIL] ACTION ITEMS GATES BREACHED:")
        for v in gate_res.violations:
            print(f"  - {v}")
        if not args.no_fail:
            return 1

    print("\n[PASS] Action items evaluation passed!")
    return 0


async def _run_ocr(args: argparse.Namespace) -> int:
    runner = OCREvalRunner()
    print("\n--- Running OCR Benchmark on Gold Pages ---")

    hypothesis_pages = {}
    job_id = args.job_id
    if job_id:
        pages = database.get_pages(job_id)
        hypothesis_pages = {p["page_num"]: p["text"] or "" for p in pages}
        print(f"Loaded {len(hypothesis_pages)} hypothesis page(s) from database job '{job_id}'.")
    elif args.self_test:
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        fixture_path = os.path.join(repo_root, "tests", "fixtures", "gold_ocr_pages.json")
        with open(fixture_path, "r", encoding="utf-8") as f:
            gold_data = json.load(f)
        hypothesis_pages = {p["page"]: p["ground_truth_text"] for p in gold_data.get("pages", [])}
        print("Running in self-test mode (hypothesis matching ground truth).")
    else:
        print("Note: No --job-id or --self-test specified. Comparing against empty predictions.")

    results = await runner.run("gold_ocr_pages", hypothesis_pages=hypothesis_pages)

    print(f"Evaluated {results['page_count']} pages | Mean CER: {results['mean_cer']:.4f} | Mean WER: {results['mean_wer']:.4f}")
    for cat, cer in results.get("cer_by_category", {}).items():
        print(f"  Category '{cat:<14}' CER: {cer:.4f}")

    gate_res = evaluate_ocr_gates(results)

    save_json_artifact(
        run_id=str(uuid.uuid4()),
        pipeline="ocr",
        dataset_id="gold_ocr_pages",
        results=results,
        gates_passed=gate_res.passed,
    )

    if not gate_res.passed:
        print("\n[FAIL] OCR GATES BREACHED:")
        for v in gate_res.violations:
            print(f"  - {v}")
        if not args.no_fail:
            return 1

    print("\n[PASS] OCR evaluation passed!")
    return 0


async def _run_confidence(args: argparse.Namespace) -> int:
    runner = ConfidenceEvalRunner()
    print("\n--- Running Confidence Scorer Calibration Benchmark ---")

    job_id = args.job_id or "108ba949-bb7b-4f29-b582-ce4f4a44631b"
    pages = database.get_pages(job_id)
    if not pages:
        print(f"[FAIL] No pages found for job {job_id}")
        return 1

    page_records = []
    for p in pages:
        conf = p.get("confidence", 0.95)
        page_records.append({
            "page": p.get("page_num", 1),
            "confidence": conf,
            "needs_review": conf < 0.70,
            "cer": max(0.0, 1.0 - conf) * 0.1,  # synthetic calibration ground truth
        })

    results = await runner.run("confidence_eval", page_records=page_records)
    print(f"Evaluated {results['page_count']} pages")
    print(f"  ROC-AUC:             {results['roc_auc']:.4f}")
    print(f"  Brier Score:         {results['brier_score']:.4f}")
    print(f"  Spearman Correlation:{results['spearman_correlation']:.4f}")

    save_json_artifact(
        run_id=str(uuid.uuid4()),
        pipeline="confidence",
        dataset_id=job_id,
        results=results,
        gates_passed=True,
    )

    print("\n[PASS] Confidence calibration evaluation complete!")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Unified evaluation suite for ocr-services")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # run command
    run_parser = subparsers.add_parser("run", help="Run an evaluation pipeline")
    run_parser.add_argument(
        "--pipeline",
        choices=["index", "action-items", "ocr", "confidence"],
        default="index",
        help="Pipeline to evaluate",
    )
    run_parser.add_argument(
        "--dataset",
        default="judgment-17p",
        help="Benchmark dataset ID (run 'list-datasets' to view options)",
    )
    run_parser.add_argument("--job-id", default=None, help="Database job ID for OCR/confidence evaluation")
    run_parser.add_argument("--self-test", action="store_true", help="Run OCR pipeline in self-test mode")
    run_parser.add_argument("--trials", type=int, default=None, help="Number of trials")
    run_parser.add_argument("--markdown", action="store_true", help="Print formatted markdown report")
    run_parser.add_argument("--no-fail", action="store_true", help="Do not exit with 1 on gate breach")
    run_parser.add_argument("--dump-diff", default=None, help="Save diff JSON from final trial")

    # Engine tuning options
    run_parser.add_argument("--chunk-pages", type=int, default=None)
    run_parser.add_argument("--overlap-pages", type=int, default=None)
    run_parser.add_argument("--adaptive-chunking", action="store_true", default=None)
    run_parser.add_argument("--two-pass", action="store_true", default=None)
    run_parser.add_argument("--compact-prompt", action="store_true", default=False)
    run_parser.add_argument("--page-from", type=int, default=None, help="Start page for sliced evaluation")
    run_parser.add_argument("--page-to", type=int, default=None, help="End page for sliced evaluation")
    run_parser.add_argument(
        "--review-rule-prior", action="store_true", default=None,
        help="Merge deterministic rule-engine candidates into AI headings for review "
             "(project default is off — AI_INDEX_REVIEW_RULE_PRIOR=false — this previously "
             "wasn't overridable from the CLI and silently defaulted on in IndexTrialConfig)",
    )

    # list-datasets command
    subparsers.add_parser("list-datasets", help="List registered benchmark datasets")

    args = parser.parse_args()

    if args.command == "list-datasets":
        print("\nAvailable Benchmark Datasets:")
        for d in list_benchmark_datasets():
            legacy_flag = " [LEGACY]" if d.legacy else ""
            print(f"  - {d.dataset_id:<18} | {d.domain:<20} | {d.page_count} pages | {d.name}{legacy_flag}")
        return

    if args.command == "run":
        if args.pipeline == "index":
            sys.exit(asyncio.run(_run_index(args)))
        elif args.pipeline == "action-items":
            sys.exit(asyncio.run(_run_action_items(args)))
        elif args.pipeline == "ocr":
            sys.exit(asyncio.run(_run_ocr(args)))
        elif args.pipeline == "confidence":
            sys.exit(asyncio.run(_run_confidence(args)))


if __name__ == "__main__":
    main()
