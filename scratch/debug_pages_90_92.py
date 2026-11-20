import asyncio, sys
sys.path.insert(0, '/app')
sys.path.insert(0, '/app/scripts')
import database
import ai_reference_index
from compare_ai_indexing_qwen_vs_gemini import duplicate_job

async def main():
    dup_id = duplicate_job('bb417471-561c-4029-bb21-3bdcf74e7dcb', chunk_pages=3)
    try:
        import sqlite3, json
        conn = sqlite3.connect(database.DB_PATH)
        cur = conn.cursor()
        row = cur.execute("SELECT engine_params FROM ocr_jobs WHERE job_id = ?", (dup_id,)).fetchone()
        params = json.loads(row[0]) if row and row[0] else {}
        params["ai_index_chunk_chars_max"] = 8000
        params["ai_index_review_rule_prior"] = False
        cur.execute("UPDATE ocr_jobs SET engine_params = ? WHERE job_id = ?", (json.dumps(params), dup_id))
        conn.commit()
        conn.close()
        res = await ai_reference_index.reindex_job_with_ai(dup_id, start_page=90)
        print("RESULT:", res)
    finally:
        database.delete_job(dup_id)

asyncio.run(main())
