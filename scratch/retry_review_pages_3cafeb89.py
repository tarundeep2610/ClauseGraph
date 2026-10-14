"""One-off: backfill raw_ocr_text for job 3cafeb89's 3 review-status pages
(88, 89, 90) by calling the real retry_pages_bulk pipeline directly, bypassing
the authenticated HTTP API (this is an internal maintenance script, not a
user-facing request) and bypassing main.py's asyncio.create_task fire-and-forget
wrapper so this script blocks until done and can report the result.

Run inside the CPU app container:
    docker cp scratch/retry_review_pages_3cafeb89.py ocr-services-glm-ocr-api-1:/app/scratch_retry.py
    docker exec ocr-services-glm-ocr-api-1 python3 /app/scratch_retry.py
"""
import asyncio

import database
import task_queue
import main

JOB_ID = "3cafeb89-e2e0-428b-9fd1-73471232deae"


async def run():
    job = database.get_job(JOB_ID, include_pages=False)
    if not job:
        raise SystemExit(f"Job not found: {JOB_ID}")

    engine_params = job.get("engine_params") or {}
    glm_runner, unlimited_runner, _ = main._build_runners(engine_params)
    prompt = job.get("custom_prompt") or main.PROMPTS.get(job.get("prompt_mode"), main.PROMPTS["document"])

    before = {p["page_num"]: bool(p.get("raw_ocr_text")) for p in database.get_pages(JOB_ID) if p["status"] == "review"}
    print("Review pages before:", before)

    await task_queue.retry_pages_bulk(
        JOB_ID, job["engine"], glm_runner, unlimited_runner, prompt, job["tiling"],
        main.OCR_TIMEOUT, None, "review",
    )

    pages = database.get_pages(JOB_ID)
    for p in pages:
        if p["page_num"] in before:
            print(f"page {p['page_num']}: status={p['status']} raw_ocr_text_len={len(p.get('raw_ocr_text') or '')}")

    total_missing = sum(1 for p in pages if not (p.get("raw_ocr_text") or "").strip())
    print(f"Pages still missing raw_ocr_text out of {len(pages)}: {total_missing}")


asyncio.run(run())
