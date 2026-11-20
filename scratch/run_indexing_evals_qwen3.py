import os
import sys
import json
import asyncio
import time

# Enforce UTF-8 encoding
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

os.environ["AI_INDEX_PROVIDER"] = "ollama_native"
os.environ["AI_INDEX_MODEL"] = "qwen3:8b"
os.environ["AI_INDEX_BASE_URL"] = "http://100.124.212.108:11600"
os.environ["AI_INDEX_API_KEY"] = "ollama"
os.environ["AI_INDEX_CHUNK_CHARS_MAX"] = "32000"
os.environ["AI_INDEX_CHUNK_PAGES"] = "20"
os.environ["AI_INDEX_REQUEST_TIMEOUT_SECONDS"] = "600"

import config
config.AI_INDEX_PROVIDER = "ollama_native"
config.AI_INDEX_MODEL = "qwen3:8b"
config.AI_INDEX_BASE_URL = "http://100.124.212.108:11600"
config.AI_INDEX_API_KEY = "ollama"
config.AI_INDEX_CHUNK_CHARS_MAX = 32000
config.AI_INDEX_CHUNK_PAGES = 20
config.AI_INDEX_REQUEST_TIMEOUT_SECONDS = 600

from evals.runners.index_runner import IndexEvalRunner, IndexTrialConfig
from evals.datasets.registry import get_benchmark_dataset
from evals.reporters.json_artifact import save_json_artifact
from evals.gates import evaluate_index_gates

DATASETS = [
    "dmrc-nit-5p",
    "dmrc-oew-16p",
    "dmrc-jcdd-27p",
    "iocl-mathura-59p",
    "gail-jsw-61p"
]

RESULTS_FILE = os.path.join(os.path.dirname(__file__), "eval_results_qwen3.json")


def load_results():
    if os.path.exists(RESULTS_FILE):
        with open(RESULTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"model": "qwen3:8b", "provider": "ollama_native", "datasets": {}}


def save_results(data):
    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


async def main():
    print("=" * 70)
    print("RUNNING STRUCTURE INDEXING FOR 5 PDFS (QWEN3:8B, 20 PAGES, 32K CHARS, TWO-PASS)")
    print("=" * 70)

    results_data = load_results()
    runner = IndexEvalRunner()

    for idx, ds_id in enumerate(DATASETS, 1):
        ds = get_benchmark_dataset(ds_id)
        print(f"\n[{idx}/5] Evaluating Index: {ds.name} ({ds.page_count} pages)")
        t0 = time.time()
        try:
            config_trial = IndexTrialConfig(
                trials=1,
                chunk_pages=20,
                chunk_chars_max=32000,
                two_pass=True,
                adaptive_chunking=True
            )
            idx_results = await runner.run(ds_id, config=config_trial)
            dur = round(time.time() - t0, 1)

            mean_scores = {k: v["mean"] for k, v in idx_results.get("spread", {}).items()}
            gate_res = evaluate_index_gates(mean_scores)

            trial0 = idx_results.get("trials", [{}])[0]
            summary = {
                "duration_seconds": dur,
                "merged_precision": mean_scores.get("target_precision", 0.0),
                "merged_recall": mean_scores.get("target_recall", 0.0),
                "scope_accuracy": mean_scores.get("scope_accuracy", 0.0),
                "mention_precision": mean_scores.get("mention_precision", 0.0),
                "mention_recall": mean_scores.get("mention_recall", 0.0),
                "rule_recall": mean_scores.get("rule_recall", 0.0),
                "family_drift": mean_scores.get("family_drift_count", 0),
                "matched_targets_count": trial0.get("matched_targets_count", 0),
                "total_actual_targets": trial0.get("total_actual_targets", 0),
                "total_gold_targets": trial0.get("total_gold_targets", 0),
                "matched_mentions_count": trial0.get("matched_mentions_count", 0),
                "total_actual_mentions": trial0.get("total_actual_mentions", 0),
                "total_gold_mentions": trial0.get("total_gold_mentions", 0),
                "gates_passed": gate_res.passed,
                "violations": gate_res.violations
            }
            if ds_id not in results_data["datasets"]:
                results_data["datasets"][ds_id] = {}
            results_data["datasets"][ds_id]["index"] = summary
            save_results(results_data)
            save_json_artifact(f"eval_{ds_id}_index_{int(time.time())}", "index", ds_id, idx_results, gate_res.passed)

            print(f"-> DONE ({dur}s): Precision={summary['merged_precision']:.3f}, Recall={summary['merged_recall']:.3f}, ScopeAcc={summary['scope_accuracy']:.3f}, MentionR={summary['mention_recall']:.3f}")
        except Exception as e:
            print(f"[ERROR on {ds_id}]: {e}")
            results_data["datasets"][ds_id]["index"] = {"error": str(e)}
            save_results(results_data)

    print("\nALL INDEXING RUNS COMPLETE!")


if __name__ == "__main__":
    asyncio.run(main())
