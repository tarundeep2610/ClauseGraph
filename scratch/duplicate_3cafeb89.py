"""One-off: duplicate job 3cafeb89 (now that pages 88-90 are backfilled) into
a fresh job with zero indexing state, for hand-authoring a gold fixture
against without ever touching the production job's live reference index.
Renames the duplicate's filename with a [GOLD] suffix, matching the existing
100-page fixture's browsable-job convention.

Run inside the CPU app container (PYTHONPATH must include /app/scripts so the
comparison script's duplicate_job() is importable):
    docker cp scratch/duplicate_3cafeb89.py ocr-services-glm-ocr-api-1:/app/scratch_dup.py
    docker exec -e PYTHONPATH=/app:/app/scripts ocr-services-glm-ocr-api-1 python3 /app/scratch_dup.py
"""
import sqlite3

import database
from compare_ai_indexing_qwen_vs_gemini import duplicate_job

SOURCE_JOB_ID = "3cafeb89-e2e0-428b-9fd1-73471232deae"

new_job_id = duplicate_job(SOURCE_JOB_ID)

conn = sqlite3.connect(database.DB_PATH)
cur = conn.cursor()
cur.execute("SELECT filename FROM ocr_jobs WHERE job_id = ?", (new_job_id,))
old_filename = cur.fetchone()[0]
new_filename = "Tenderdocument1759.pdf [GOLD]"
cur.execute("UPDATE ocr_jobs SET filename = ? WHERE job_id = ?", (new_filename, new_job_id))
conn.commit()
conn.close()

job = database.get_job(new_job_id, include_pages=False)
pages = database.get_pages(new_job_id)
print(f"source_job_id={SOURCE_JOB_ID}")
print(f"new_job_id={new_job_id}")
print(f"filename: {old_filename!r} -> {new_filename!r}")
print(f"total_pages={job.get('total_pages')} pages_row_count={len(pages)}")
print(f"reference_index_version={job.get('reference_index_version')} reference_index_frontier={job.get('reference_index_frontier')}")
print(f"raw_ocr_text present on {sum(1 for p in pages if (p.get('raw_ocr_text') or '').strip())}/{len(pages)} pages")
