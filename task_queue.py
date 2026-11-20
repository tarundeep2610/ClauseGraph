import asyncio
import io
import os
import sys
import time
import traceback
import pypdfium2 as pdfium
from PIL import Image, ImageEnhance, ImageFilter, ImageOps
import database
from gpu_ocr_client import GPUServiceAuthError, GPUServiceError

# Attempt to import unlimited_ocr_engine if available
try:
    import unlimited_ocr_engine
    _UNLIMITED_OCR_AVAILABLE = True
except ImportError:
    unlimited_ocr_engine = None
    _UNLIMITED_OCR_AVAILABLE = False

# Active background tasks dictionary to prevent duplicate runs
_active_tasks = {}

# Set of job IDs requested to pause at the next page boundary
_paused_jobs: set = set()


def pause_job(job_id: str):
    """Request a running job to pause after its current page finishes."""
    _paused_jobs.add(job_id)


def cancel_all_jobs() -> int:
    """Cancel all running OCR tasks immediately. Returns number of tasks cancelled."""
    count = 0
    for job_id, task in list(_active_tasks.items()):
        if not task.done():
            _paused_jobs.add(job_id)
            task.cancel()
            count += 1
    return count


def has_active_jobs() -> bool:
    """Return True if any OCR task is currently running."""
    return any(not t.done() for t in list(_active_tasks.values()))

def is_job_active(job_id: str) -> bool:
    task = _active_tasks.get(job_id)
    return task is not None and not task.done()

def cancel_job(job_id: str) -> bool:
    task = _active_tasks.get(job_id)
    if task and not task.done():
        _paused_jobs.discard(job_id)
        task.cancel()
        return True
    return False

def _parse_page_range(range_str: str, total_pages: int) -> set:
    """Parse '1-10,25,50-100' into a set of valid 1-indexed page numbers."""
    if not range_str or not range_str.strip():
        return set(range(1, total_pages + 1))
    pages = set()
    for part in range_str.split(','):
        part = part.strip()
        if not part:
            continue
        if '-' in part:
            try:
                a, b = part.split('-', 1)
                pages.update(range(int(a.strip()), int(b.strip()) + 1))
            except ValueError:
                pass
        elif part.isdigit():
            pages.add(int(part))
    valid = {p for p in pages if 1 <= p <= total_pages}
    return valid if valid else set(range(1, total_pages + 1))


async def process_ocr_job(job_id: str, run_ocr_func, run_unlimited_func, prompts_dict, ollama_timeout: int = 600, output_path: str = "", page_range: str = ""):
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

        pdf_doc = pdfium.PdfDocument(file_path) if is_pdf else None
        try:
            total_pages = len(pdf_doc) if pdf_doc else 1

            pages_data = job['pages_data'] if isinstance(job['pages_data'], list) else []
            already_completed_pages = {p.get('page') for p in pages_data if p.get('status') == 'success'}

            selected_pages = _parse_page_range(page_range, total_pages)

            for i in range(total_pages):
                page_num = i + 1

                if page_num not in selected_pages:
                    continue

                # Check for pause request before each page
                if job_id in _paused_jobs:
                    _paused_jobs.discard(job_id)
                    print(f"[TaskQueue] ⏸️ Job {job_id} paused before page {page_num}.")
                    database.mark_job_paused(job_id, pages_data)
                    return

                if page_num in already_completed_pages:
                    print(f"[TaskQueue] Job {job_id}: Page {page_num} already processed. Skipping.")
                    continue

                print(f"[TaskQueue] Job {job_id}: Processing page {page_num}/{total_pages}...")

                # Render or open page image
                if is_pdf:
                    pdf_page = pdf_doc.get_page(i)
                    bitmap = pdf_page.render(scale=scale)
                    pil_img = bitmap.to_pil()
                    pdf_page.close()
                    bitmap.close()
                else:
                    with Image.open(file_path) as img:
                        pil_img = img.convert("RGB") if img.mode != "RGB" else img.copy()

                # Captcha preprocessing if needed
                if not is_pdf and is_captcha:
                    pil_img = ImageOps.expand(pil_img, border=15, fill=(255, 255, 255))
                    w, h = pil_img.size
                    if w < 400:
                        factor = max(3, 400 // w)
                        pil_img = pil_img.resize((w * factor, h * factor), Image.LANCZOS)
                    pil_img = ImageEnhance.Contrast(pil_img).enhance(1.8)
                    pil_img = pil_img.filter(ImageFilter.SHARPEN)

                # Retry loop for OCR execution (up to 3 attempts, except auth
                # failures which are a config problem and never succeed on retry)
                max_retries = 3
                page_text = ""
                page_status = "failed"
                page_error = ""

                for attempt in range(1, max_retries + 1):
                    try:
                        if engine == "unlimited":
                            page_text = await run_unlimited_func(pil_img, prompt, ollama_timeout)
                        else:
                            page_text = await run_ocr_func(pil_img, prompt, tiling, ollama_timeout, ocr_options)
                        page_status = "success"
                        page_error = ""
                        break
                    except GPUServiceAuthError as e:
                        page_error = f"GPU service rejected authentication — check shared secret: {e}"
                        print(f"[TaskQueue] Job {job_id} Page {page_num}: {page_error} (not retrying)")
                        break
                    except GPUServiceError as e:
                        page_error = f"{type(e).__name__}: {e}"
                        print(f"[TaskQueue] Job {job_id} Page {page_num} attempt {attempt}/{max_retries} failed: {page_error}")
                        if attempt < max_retries:
                            await asyncio.sleep(2 * attempt)
                    except Exception as e:
                        page_error = str(e)
                        print(f"[TaskQueue] Job {job_id} Page {page_num} attempt {attempt}/{max_retries} failed: {e}")
                        if attempt < max_retries:
                            await asyncio.sleep(2 * attempt)

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

            # Auto-export to output path if configured
            if output_path and os.path.isdir(output_path):
                try:
                    base_name = os.path.splitext(job['filename'])[0]
                    out_file = os.path.join(output_path, f"{base_name}_ocr.md")
                    lines = [f"# OCR Result: {job['filename']}\n"]
                    for p in sorted(pages_data, key=lambda x: x.get('page', 0)):
                        lines.append(f"## Page {p.get('page')}\n\n{p.get('text','')}\n\n---\n")
                    with open(out_file, "w", encoding="utf-8") as f:
                        f.write("\n".join(lines))
                    print(f"[TaskQueue] 📁 Auto-exported result to {out_file}")
                except Exception as _ex:
                    print(f"[TaskQueue] ⚠️ Auto-export failed: {_ex}")

        finally:
            if pdf_doc is not None:
                pdf_doc.close()

    except asyncio.CancelledError:
        print(f"[TaskQueue] ⏸️ Job {job_id} was cancelled — marking paused.")
        _paused_jobs.discard(job_id)
        try:
            pages_data
        except NameError:
            pages_data = []
        database.mark_job_paused(job_id, pages_data)
        raise
    except Exception as outer_e:
        err_msg = f"Task worker error: {str(outer_e)}\n{traceback.format_exc()}"
        print(f"[TaskQueue] ❌ Job {job_id} failed completely: {err_msg}")
        database.mark_job_failed(job_id, err_msg)
    finally:
        _active_tasks.pop(job_id, None)

async def resume_interrupted_jobs(run_ocr_func, run_unlimited_func, prompts_dict, ollama_timeout: int = 600, output_path: str = ""):
    """
    Scans SQLite DB on startup and dispatches background tasks for any job left in
    'pending' or 'processing' state. Must be called from an async context (startup event).
    """
    jobs = database.list_jobs(limit=100)
    interrupted = [j for j in jobs if j['status'] in ('pending', 'processing')]
    if not interrupted:
        print("[TaskQueue] No interrupted or pending jobs found on startup.")
        return

    print(f"[TaskQueue] 🔄 Resuming {len(interrupted)} pending/interrupted OCR job(s)...")
    for job in interrupted:
        asyncio.create_task(
            process_ocr_job(
                job['job_id'],
                run_ocr_func,
                run_unlimited_func,
                prompts_dict,
                ollama_timeout,
                output_path,
                job.get('page_range', '')
            )
        )
