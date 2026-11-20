"""Re-run run_trial() for specific chunk_chars_max sizes only — used to
retry sizes whose first pass had one or more chunks time out and fall
back to the rule-based detector, to see whether those specific chunks
succeed with the AI on a second, independent attempt.

Same scoring-harness-only monkeypatch pattern as run_ollama_chunk_size_sweep.py
(never touches ai_indexing/providers.py or production config).

Usage:
    python scripts/retry_ollama_sizes.py <job_id> --fixture <gold.json> --sizes 16000 8000
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config

config.AI_INDEX_PROVIDER = "ollama_native"
config.AI_INDEX_MODEL = "qwen3:8b"
config.AI_INDEX_BASE_URL = os.environ.get("SWEEP_OLLAMA_BASE_URL", "http://100.124.212.108:11600")
if not config.AI_INDEX_API_KEY:
    config.AI_INDEX_API_KEY = "unused-ollama-native"

import ai_indexing.providers as _providers
from _ollama_native_provider import OllamaNativeProvider
_providers._PROVIDERS["ollama_native"] = OllamaNativeProvider

from score_reference_index import load_gold_fixture, run_trial  # noqa: E402


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("job_id")
    parser.add_argument("--fixture", default="tests/fixtures/gold_index_108ba949.json")
    parser.add_argument("--chunk-pages", type=int, default=20)
    parser.add_argument("--sizes", type=int, nargs="+", required=True)
    parser.add_argument("--record-dir", default=None)
    args = parser.parse_args()

    print(f"Provider: {config.AI_INDEX_PROVIDER} model={config.AI_INDEX_MODEL} base_url={config.AI_INDEX_BASE_URL}")
    gold = load_gold_fixture(args.fixture)

    results = {}
    for size in args.sizes:
        print(f"\n=== RETRY chunk_chars_max={size} (chunk_pages={args.chunk_pages}) ===", flush=True)
        scores = await run_trial(
            args.job_id, gold, trial_idx=f"retry-{size}", review_flag=False,
            chunk_pages=args.chunk_pages, chunk_chars_max=size,
            record_dir=args.record_dir,
        )
        results[size] = scores

    print("\n--- Retry summary ---")
    for size in args.sizes:
        s = results[size]
        print(f"chunk_chars_max={size:<6} Merged P={s['target_precision']:.3f} R={s['target_recall']:.3f}")


if __name__ == "__main__":
    asyncio.run(main())
