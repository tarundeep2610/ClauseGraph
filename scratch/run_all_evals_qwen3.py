import os
import sys
import json
import asyncio
import time
from datetime import datetime

# Enforce UTF-8 encoding for console on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Ensure repo root is on sys.path
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# Configure environment for Ollama Qwen3:8B winning config
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
from evals.runners.action_items_runner import ActionItemsEvalRunner
from evals.datasets.registry import get_benchmark_dataset
from evals.reporters.json_artifact import save_json_artifact
from evals.gates import evaluate_index_gates, evaluate_action_items_gates

DATASETS = [
    "dmrc-nit-5p",
    "dmrc-oew-16p",
    "dmrc-jcdd-27p",
    "iocl-mathura-59p",
    "gail-jsw-61p"
]

RESULTS_FILE = os.path.join(os.path.dirname(__file__), "eval_results_qwen3.json")


def save_progress(results_data):
    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(results_data, f, indent=2)


async def run_suite():
    print("=" * 70)
    print("STARTING FULL 5-PDF EVALUATION SUITE FOR QWEN3:8B")
    print(f"Provider: {config.AI_INDEX_PROVIDER} | Base URL: {config.AI_INDEX_BASE_URL}")
    print(f"Index Config: chunk_pages=20, chunk_chars_max=32000, two_pass=True")
    print(f"Action Items Config: chunk_pages=3, adaptive_chunking=True")
    print("=" * 70)

    suite_results = {
        "timestamp": datetime.utcnow().isoformat(),
        "model": "qwen3:8b",
        "provider": "ollama_native",
        "datasets": {}
    }

    index_runner = IndexEvalRunner()
    action_runner = ActionItemsEvalRunner()

    for idx, ds_id in enumerate(DATASETS, 1):
        ds = get_benchmark_dataset(ds_id)
        print(f"\n[{idx}/5] Processing Benchmark: {ds.name} ({ds.page_count} pages)")
        suite_results["datasets"][ds_id] = {
            "name": ds.name,
            "page_count": ds.page_count,
            "domain": ds.domain,
            "index": None,
            "action_items": None
        }
        save_progress(suite_results)

        # 1. RUN ACTION ITEMS EVALUATION
        print(f"\n--- [1/2] Action Items Extraction: {ds_id} ---")
        t0 = time.time()
        try:
            act_results = await action_runner.run(
                ds_id,
                trials=1,
                chunk_pages=3,
                adaptive_chunking=True
            )
            act_dur = round(time.time() - t0, 1)
            mean_scores = {k: v["mean"] for k, v in act_results.get("spread", {}).items()}
            gate_res = evaluate_action_items_gates(mean_scores, gates=ds.action_items_gates)
            
            trial0 = act_results.get("trials", [{}])[0]
            by_type = trial0.get("by_type", {})

            act_summary = {
                "duration_seconds": act_dur,
                "precision": mean_scores.get("precision", 0.0),
                "recall": mean_scores.get("recall", 0.0),
                "f1": mean_scores.get("f1", 0.0),
                "grounding_rate": mean_scores.get("grounding_rate", 0.0),
                "matched_count": trial0.get("matched_count", 0),
                "actual_count": trial0.get("actual_count", 0),
                "gold_count": trial0.get("gold_count", 0),
                "by_type": by_type,
                "gates_passed": gate_res.passed,
                "violations": gate_res.violations
            }
            suite_results["datasets"][ds_id]["action_items"] = act_summary
            save_json_artifact(f"eval_{ds_id}_action_items_{int(time.time())}", "action-items", ds_id, act_results, gate_res.passed)

            print(f"-> Action Items Done ({act_dur}s): Precision={act_summary['precision']:.3f}, Recall={act_summary['recall']:.3f}, F1={act_summary['f1']:.3f}, Grounding={act_summary['grounding_rate']:.3f} | Gates Passed: {gate_res.passed}")
        except Exception as e:
            print(f"[ERROR in Action Items for {ds_id}]: {e}")
            suite_results["datasets"][ds_id]["action_items"] = {"error": str(e)}
        save_progress(suite_results)

        # 2. RUN STRUCTURE INDEXING EVALUATION
        print(f"\n--- [2/2] Structure Indexing: {ds_id} ---")
        t0 = time.time()
        try:
            idx_config = IndexTrialConfig(
                trials=1,
                chunk_pages=20,
                chunk_chars_max=32000,
                two_pass=True,
                adaptive_chunking=True
            )
            idx_results = await index_runner.run(ds_id, config=idx_config)
            idx_dur = round(time.time() - t0, 1)
            mean_scores = {k: v["mean"] for k, v in idx_results.get("spread", {}).items()}
            gate_res = evaluate_index_gates(mean_scores, gates=ds.index_gates)

            idx_summary = {
                "duration_seconds": idx_dur,
                "merged_precision": mean_scores.get("target_precision", 0.0),
                "merged_recall": mean_scores.get("target_recall", 0.0),
                "scope_accuracy": mean_scores.get("scope_accuracy", 0.0),
                "mention_precision": mean_scores.get("mention_precision", 0.0),
                "mention_recall": mean_scores.get("mention_recall", 0.0),
                "rule_recall": mean_scores.get("rule_recall", 0.0),
                "family_drift": mean_scores.get("family_drift_count", 0),
                "gates_passed": gate_res.passed,
                "violations": gate_res.violations
            }
            suite_results["datasets"][ds_id]["index"] = idx_summary
            save_json_artifact(f"eval_{ds_id}_index_{int(time.time())}", "index", ds_id, idx_results, gate_res.passed)

            print(f"-> Indexing Done ({idx_dur}s): Precision={idx_summary['merged_precision']:.3f}, Recall={idx_summary['merged_recall']:.3f}, ScopeAcc={idx_summary['scope_accuracy']:.3f}, MentionRecall={idx_summary['mention_recall']:.3f} | Gates Passed: {gate_res.passed}")
        except Exception as e:
            print(f"[ERROR in Indexing for {ds_id}]: {e}")
            suite_results["datasets"][ds_id]["index"] = {"error": str(e)}
        save_progress(suite_results)

    print("\n" + "=" * 70)
    print("ALL 5 BENCHMARK EVALUATIONS FINISHED!")
    print(f"Results saved to: {RESULTS_FILE}")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(run_suite())
