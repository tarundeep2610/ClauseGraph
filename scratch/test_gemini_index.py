import sys, os, json, asyncio
sys.path.insert(0, os.path.abspath('.'))
sys.stdout.reconfigure(encoding='utf-8')

import dotenv
dotenv.load_dotenv('.env.local-test')
import database
from scripts.compare_ai_indexing_qwen_vs_gemini import duplicate_job
from evals.runners.base import cleanup_duplicate_job
from ai_reference_index import reindex_job_with_ai
from evals.runners.index_runner import load_actual_targets

async def inspect_indexing(source_job_id):
    dup_id = duplicate_job(source_job_id)
    try:
        report = await reindex_job_with_ai(
            dup_id, chunk_pages_override=20, two_pass_override=True, adaptive_chunking_override=True
        )
        targets = load_actual_targets(dup_id)
        print(f"Extracted {len(targets)} targets:")
        for t in targets:
            print(f"  P{t['page']}: {t['key']} | {t.get('label', '')[:50]} | scope={t.get('scope_key')}")
    finally:
        cleanup_duplicate_job(dup_id)

asyncio.run(inspect_indexing('de8764d2-4249-49bd-a271-e8ec2c02c869'))
