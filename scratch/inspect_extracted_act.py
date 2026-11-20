import sys, os, json, asyncio
sys.path.insert(0, os.path.abspath('.'))
sys.stdout.reconfigure(encoding='utf-8')

import database
import dotenv
dotenv.load_dotenv('.env.local-test')
from scripts.compare_ai_indexing_qwen_vs_gemini import duplicate_job
from evals.runners.base import cleanup_duplicate_job
from evals.runners.action_items_runner import reset_action_item_state
import ai_action_items

async def inspect_extracted_action_items(source_job_id):
    dup_id = duplicate_job(source_job_id)
    try:
        reset_action_item_state(dup_id)
        report = await ai_action_items.reindex_action_items_for_job(
            dup_id, chunk_pages_override=3
        )
        items = database.get_all_action_items_for_job(dup_id)
        print(f"Extracted {len(items)} grounded items:")
        for idx, it in enumerate(items, 1):
            print(f"{idx}. P{it.get('page')}: [{it.get('item_type')}] {it.get('item_text')} (quote: '{it.get('evidence_quote')[:50]}...')")
    finally:
        cleanup_duplicate_job(dup_id)

asyncio.run(inspect_extracted_action_items('0a42f322-858a-45c3-90af-30438476884c'))
