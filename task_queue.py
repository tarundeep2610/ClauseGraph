import asyncio
import io
import os
import sys
import time
import traceback
import pypdfium2 as pdfium
from PIL import Image, ImageEnhance, ImageFilter, ImageOps
import database

# Attempt to import unlimited_ocr_engine if available
try:
    import unlimited_ocr_engine
    _UNLIMITED_OCR_AVAILABLE = True
except ImportError:
    unlimited_ocr_engine = None
    _UNLIMITED_OCR_AVAILABLE = False

# Active background tasks dictionary to prevent duplicate runs
_active_tasks = {}

async def process_ocr_job(job_id: str, run_ocr_func, run_unlimited_func, prompts_dict, ollama_timeout: int = 600):
    """
    Background worker loop that processes a document page by page, saving intermediate progress
    to SQLite. Implements automatic retries per page.
    """
    if job_id in _active_tasks and not _active_tasks[job_id].done():
        print(f"[TaskQueue] Job {job_id} is already being processed.")
        return

    _active_tasks[job_id] = asyncio.current_task()

    job = database.get_job(job_id)
    if not job:
        print(f"[TaskQueue] ❌ Job {job_id} not found in DB.")
        return

    print(f"[TaskQueue] 🔄 Starting background processing for Job {job_id} ({job['filename']})...")
    database.update_job_progress(job_id, job['processed_pages'], job['pages_data'], status="processing")

    try:
        file_path = job['file_path']
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Source file not found at {file_path}")

        engine = job['engine']
        scale = job['scale']
        tiling = job['tiling']
        prompt_mode = job['prompt_mode']
        custom_prompt = job['custom_prompt']
        prompt = custom_prompt if custom_prompt else prompts_dict.get(prompt_mode, prompts_dict.get("document", ""))
        is_captcha = prompt_mode == "captcha" or bool(custom_prompt)

        ocr_options = None
        if is_captcha:
            ocr_options = {
                "num_ctx": 2048,
                "temperature": 0.0,
                "num_predict": 15,
                "repeat_penalty": 2.0,
                "stop": ["\n", "```", "I will", "Here ", "The text"],
            }

        is_pdf = file_path.lower().endswith(".pdf")

        if is_pdf:
            pdf = pdfium.PdfDocument(file_path)
            total_pages = len(pdf)
        else:
            total_pages = 1

        pages_data = job['pages_data'] if isinstance(job['pages_data'], list) else []
        already_completed_pages = {p.get('page') for p in pages_data if p.get('status') == 'success'}

        for i in range(total_pages):
            page_num = i + 1
            if page_num in already_completed_pages:
                print(f"[TaskQueue] Job {job_id}: Page {page_num} already processed. Skipping.")
                continue

            print(f"[TaskQueue] Job {job_id}: Processing page {page_num}/{total_pages}...")

            # Render or open page image
            if is_pdf:
                pdf_page = pdf.get_page(i)
                bitmap = pdf_page.render(scale=scale)
                pil_img = bitmap.to_pil()
            else:
                pil_img = Image.open(file_path)
                if pil_img.mode != "RGB":
                    pil_img = pil_img.convert("RGB")

            # Captcha preprocessing if needed
            if not is_pdf and is_captcha:
                pil_img = ImageOps.expand(pil_img, border=15, fill=(255, 255, 255))
                w, h = pil_img.size
                if w < 400:
                    factor = max(3, 400 // w)
                    pil_img = pil_img.resize((w * factor, h * factor), Image.LANCZOS)
                pil_img = ImageEnhance.Contrast(pil_img).enhance(1.8)
                pil_img = pil_img.filter(ImageFilter.SHARPEN)

            # Retry loop for OCR execution (up to 3 attempts)
            max_retries = 3
            page_text = ""
            page_status = "failed"
            page_error = ""

            for attempt in range(1, max_retries + 1):
                try:
                    if engine == "unlimited":
                        page_text = await asyncio.to_thread(
                            run_unlimited_func, pil_img, prompt, ollama_timeout
                        )
                    else:
                        page_text = await asyncio.to_thread(
                            run_ocr_func, pil_img, prompt, tiling, ollama_timeout, ocr_options
                        )
                    page_status = "success"
                    page_error = ""
                    break
                except Exception as e:
                    page_error = str(e)
                    print(f"[TaskQueue] Job {job_id} Page {page_num} attempt {attempt}/{max_retries} failed: {e}")
                    if attempt < max_retries:
                        await asyncio.sleep(2 * attempt)  # Exponential backoff retry

            page_entry = {
                "page": page_num,
                "status": page_status,
                "text": page_text if page_status == "success" else f"### ❌ Error page {page_num}: {page_error}",
                "error": page_error
            }

            # Update pages_data list (replace existing if any)
            pages_data = [p for p in pages_data if p.get('page') != page_num]
            pages_data.append(page_entry)
            pages_data.sort(key=lambda x: x['page'])

            processed_count = len([p for p in pages_data if p.get('status') == 'success'])
            database.update_job_progress(job_id, processed_count, pages_data, status="processing")

        # Mark job complete if finished
        database.mark_job_completed(job_id, pages_data)
        print(f"[TaskQueue] ✅ Job {job_id} successfully completed!")

    except Exception as outer_e:
        err_msg = f"Task worker error: {str(outer_e)}\n{traceback.format_exc()}"
        print(f"[TaskQueue] ❌ Job {job_id} failed completely: {err_msg}")
        database.mark_job_failed(job_id, err_msg)
    finally:
        _active_tasks.pop(job_id, None)

def resume_interrupted_jobs(run_ocr_func, run_unlimited_func, prompts_dict, ollama_timeout: int = 600):
    """
    Scans SQLite DB on startup and dispatches background tasks for any job left in
    'pending' or 'processing' state.
    """
    jobs = database.list_jobs(limit=100)
    interrupted = [j for j in jobs if j['status'] in ('pending', 'processing')]
    if not interrupted:
        print("[TaskQueue] No interrupted or pending jobs found on startup.")
        return

    print(f"[TaskQueue] 🔄 Resuming {len(interrupted)} pending/interrupted OCR job(s)...")
    loop = asyncio.get_event_loop()
    for job in interrupted:
        loop.create_task(
            process_ocr_job(
                job['job_id'],
                run_ocr_func,
                run_unlimited_func,
                prompts_dict,
                ollama_timeout
            )
        )
