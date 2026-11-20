"""Re-run the 32000/16000/8000 chunk_chars_max sweep (chunk_pages=20) via
a local Ollama instance instead of RokkaAI, for a comparative view.

Scoring-harness-only: monkeypatches ai_indexing.providers._PROVIDERS with
OllamaNativeProvider (scratch/_ollama_native_provider.py) for the lifetime
of this process only. Never touches ai_indexing/providers.py or any
production config — same pattern score_reference_index.py's --resumable
flag already uses for RokkaAI.

Usage (run inside the container, same as score_reference_index.py):
    python scratch/run_ollama_chunk_size_sweep.py <job_id> --fixture <gold.json>
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config

# Must happen before resolve_active_config() is ever called.
config.AI_INDEX_PROVIDER = "ollama_native"
config.AI_INDEX_MODEL = "qwen3:8b"
# NOT os.environ.get("OLLAMA_BASE_URL", ...) — the container already has an
# OLLAMA_BASE_URL env var set to a different Ollama instance (port 11610);
# this sweep targets the specific instance the user gave us (port 11600),
# so this one env var is read under its own name to avoid that collision.
config.AI_INDEX_BASE_URL = os.environ.get("SWEEP_OLLAMA_BASE_URL", "http://100.124.212.108:11600")
if not config.AI_INDEX_API_KEY:
    config.AI_INDEX_API_KEY = "unused-ollama-native"  # resolve_active_config() only checks truthiness

import ai_indexing.providers as _providers
from _ollama_native_provider import OllamaNativeProvider
_providers._PROVIDERS["ollama_native"] = OllamaNativeProvider

from score_reference_index import load_gold_fixture, run_trial, _family, RULE_FAMILIES  # noqa: E402

SIZES = [32000, 16000]  # 8000 dropped: weak AI-quality results and heaviest timeout exposure (more, smaller chunks)


async def main():
    parser = argparse.ArgumentParser(description="Ollama chunk-size sweep")
    parser.add_argument("job_id")
    parser.add_argument("--fixture", default="tests/fixtures/gold_index_108ba949.json")
    parser.add_argument("--chunk-pages", type=int, default=20)
    parser.add_argument("--record-dir", default=None)
    args = parser.parse_args()

    print(f"Provider: {config.AI_INDEX_PROVIDER} model={config.AI_INDEX_MODEL} base_url={config.AI_INDEX_BASE_URL}")

    gold = load_gold_fixture(args.fixture)
    gold_targets = gold.get("targets", [])
    gold_ai_cnt = sum(1 for t in gold_targets if _family(t.get("key")) not in RULE_FAMILIES)
    gold_rule_cnt = sum(1 for t in gold_targets if _family(t.get("key")) in RULE_FAMILIES)
    print(f"Loaded gold fixture with {len(gold_targets)} targets ({gold_ai_cnt} AI-family, {gold_rule_cnt} Rule-family)")

    results = {}
    for size in SIZES:
        print(f"\n=== chunk_chars_max={size} (chunk_pages={args.chunk_pages}) ===", flush=True)
        scores = await run_trial(
            args.job_id, gold, trial_idx=size, review_flag=False,
            chunk_pages=args.chunk_pages, chunk_chars_max=size,
            record_dir=args.record_dir,
        )
        results[size] = scores

    print("\n--- Ollama sweep summary (chunk_pages={}) ---".format(args.chunk_pages))
    for size in SIZES:
        s = results[size]
        print(
            f"chunk_chars_max={size:<6} Merged P={s['target_precision']:.3f} R={s['target_recall']:.3f} | "
            f"AI P={s['ai_precision']:.3f} R={s['ai_recall']:.3f}"
        )


if __name__ == "__main__":
    asyncio.run(main())
