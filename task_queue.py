import asyncio
import contextlib
import os
import time
import traceback
import pypdfium2 as pdfium
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

import config
import chunking
import confidence
import context_primer
import database
import reference_index
from gpu_ocr_client import GPUServiceAuthError, GPUServiceError

# Shown in place of a raw traceback/engine error string for any page the OCR
# engine couldn't process - the real detail is kept in the page's `error`
# column instead of cluttering the exported transcript.
MANUAL_REVIEW_TEXT = "⚠️ Please review this page manually — the OCR engine could not process it."

# Active background tasks dictionary to prevent duplicate runs
_active_tasks = {}

# Set of job IDs requested to pause at the next page boundary
_paused_jobs: set = set()

# Module-level, shared across every job this process ever handles — this is what
# makes the GPU concurrency budget genuinely global rather than per-job. Escalation
# (automatic or manual-retry) additionally needs the escalation-share semaphore, so
# it can never exceed its configured share even when the main semaphore has room,
# while base-pass work can use the full budget whenever escalation is idle.
_gpu_semaphore = asyncio.Semaphore(max(1, config.OCR_JOB_CONCURRENCY))
_escalation_share_semaphore = asyncio.Semaphore(max(1, config.OCR_ESCALATION_CONCURRENCY_SHARE))

# pypdfium2 is not safe for concurrent access to a single PdfDocument.
_render_lock = asyncio.Lock()


async def _update_reference_index_safe(job_id: str, page_num: int, text: str, total_pages: int, raw_text: str = None):
    """Recomputes this page's cross-reference targets right after its OCR text is
    (re)written. Never allowed to take the OCR pipeline down — a detection bug
    should degrade to "this page's links are stale," not fail the whole job.

    `raw_text` (optional) is this page's raw, pre-clean_detection_tags() OCR
    output — see reference_index.build_targets_for_page's layout-tag signal."""
    try:
        await reference_index.update_reference_index_for_page(job_id, page_num, text or "", total_pages, raw_text=raw_text)
    except Exception as e:
        print(f"[TaskQueue] ⚠️ Job {job_id} page {page_num} reference-index update failed (non-fatal): {e}\n{traceback.format_exc()}")


async def trigger_ai_reindex_safe(job_id: str, start_page: "int | None" = None):
    """Kicks off a full (or, with start_page, a targeted single-page-forward)
    AI reindex when the job's frozen engine_params opted into it — same
    defensive shape as _update_reference_index_safe above: an indexing
    failure must never take the OCR job down. `start_page` is used after a
    manual page retry (see retry_single_page/bulk retry below) — the live
    per-page hook above is unconditionally rule-based, so without this a
    retried page would silently and permanently revert from AI-derived rows
    back to rule-derived ones with no re-AI-detection."""
    try:
        job = database.get_job(job_id, include_pages=False)
        if not job or not (job.get("engine_params") or {}).get("ai_indexing_enabled"):
            return
        await reference_index.reindex_job(job_id, start_page=start_page)
    except Exception as e:
        print(f"[TaskQueue] ⚠️ Job {job_id} AI reindex failed (non-fatal): {e}\n{traceback.format_exc()}")


@contextlib.asynccontextmanager
async def _gpu_slot(is_escalation: bool = False):
    if is_escalation:
        async with _escalation_share_semaphore:
            async with _gpu_semaphore:
                yield
    else:
        async with _gpu_semaphore:
            yield


def pause_job(job_id: str):
    """Request a running job to pause after its current page finishes."""
    _paused_jobs.add(job_id)


def is_job_paused(job_id: str) -> bool:
    """Public read of the pause/cancel signal — used by
    ai_reference_index.py's AI-reindex loop (which isn't itself an OCR page
    task) to stop cleanly between chunks, and mid-poll for a provider whose
    single call can run long, the same way pausing OCR already works."""
    return job_id in _paused_jobs


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


def _compute_ink_ratio(pil_img) -> float:
    """Fraction of 'dark' pixels in a downscaled grayscale copy — used by the
    blank-page guard and the empty-output-on-inked-page signal."""
    try:
        small = pil_img.convert("L").resize((200, 200))
        pixels = small.tobytes()
        dark = sum(1 for p in pixels if p < 200)
        return dark / len(pixels)
    except Exception:
        return 1.0  # fail open: assume inked so a real empty result still gets flagged


async def _render_page(pdf_doc, index: int, scale: float):
    async with _render_lock:
        def _do_render():
            pdf_page = pdf_doc.get_page(index)
            bitmap = pdf_page.render(scale=scale)
            img = bitmap.to_pil()
            pdf_page.close()
            bitmap.close()
            return img
        return await asyncio.to_thread(_do_render)


async def _run_single_page_with_retries(
    engine, run_ocr_func, run_unlimited_func, pil_img, prompt, tiling, timeout, ocr_options, max_retries=3,
    raw_capture=None,
):
    """The original transient-failure retry loop, extracted so both the base pass
    and manual/escalation retries can reuse it. Returns (text, status, error, attempts, elapsed_s).

    `raw_capture` (optional, a plain dict the caller owns) recovers the
    unlimited engine's raw pre-clean_detection_tags() output without
    changing this function's own return arity — after a successful call,
    the caller reads `raw_capture.get("raw_text")`. Only ever populated on
    the "unlimited" engine path; the GLM path has no such concept. Passed
    as an extra positional arg to run_unlimited_func, which every existing
    test fake for that call already tolerates (*rest/*args, **kwargs)."""
    text = ""
    status = "failed"
    error = ""
    start = time.monotonic()
    for attempt in range(1, max_retries + 1):
        try:
            if engine == "unlimited":
                text = await run_unlimited_func(pil_img, prompt, timeout, raw_capture)
            else:
                text = await run_ocr_func(pil_img, prompt, tiling, timeout, ocr_options)
            status = "success"
            error = ""
            break
        except GPUServiceAuthError as e:
            error = f"GPU service rejected authentication — check shared secret: {e}"
            print(f"[TaskQueue] {error} (not retrying)")
            break
        except GPUServiceError as e:
            error = f"{type(e).__name__}: {e}"
            print(f"[TaskQueue] attempt {attempt}/{max_retries} failed: {error}")
            if attempt < max_retries:
                await asyncio.sleep(2 * attempt)
        except Exception as e:
            error = str(e)
            print(f"[TaskQueue] attempt {attempt}/{max_retries} failed: {e}")
            if attempt < max_retries:
                await asyncio.sleep(2 * attempt)
    elapsed_s = time.monotonic() - start
    return text, status, error, attempt if status == "success" else max_retries, elapsed_s


def _is_auth_error(error: str) -> bool:
    return "authentication" in (error or "").lower()


async def process_ocr_job(
    job_id: str,
    run_ocr_func,
    run_unlimited_func,
    prompts_dict,
    ollama_timeout: int = 600,
    output_path: str = "",
    page_range: str = "",
    run_ocr_multi_func=None,
):
    """
    Background worker loop. Base pass processes every selected page (single-page
    calls, or one infer_multi() call per chunk in batch mode), scores confidence,
    then a decoupled escalation pass runs exactly one combined recovery attempt on
    any page that still needs it, before the job is marked completed.
    """
    if job_id in _active_tasks and not _active_tasks[job_id].done():
        print(f"[TaskQueue] Job {job_id} is already being processed.")
        return

    _active_tasks[job_id] = asyncio.current_task()

    job = database.get_job(job_id, include_pages=False)
    if not job:
        print(f"[TaskQueue] ❌ Job {job_id} not found in DB.")
        return

    print(f"[TaskQueue] 🔄 Starting background processing for Job {job_id} ({job['filename']})...")
    database.set_job_status(job_id, "processing")

    try:
        file_path = job['file_path']
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Source file not found at {file_path}")

        engine = job['engine']
        scale = job['scale']
        tiling = job['tiling']
        prompt_mode = job['prompt_mode']
        custom_prompt = job['custom_prompt']
        prompt_base = custom_prompt if custom_prompt else prompts_dict.get(prompt_mode, prompts_dict.get("document", ""))
        is_captcha = prompt_mode == "captcha" or bool(custom_prompt)
        processing_mode = job.get('processing_mode') or 'single'
        if engine != 'unlimited' or run_ocr_multi_func is None:
            processing_mode = 'single'  # batch mode is unlimited-only and needs the callback
        escalate_before_flagging = bool(job.get('escalate_before_flagging', True))
        concurrency = job.get('concurrency') or config.OCR_JOB_CONCURRENCY
        chunk_size = job.get('chunk_size') or job.get('batch_size') or config.OCR_CHUNK_SIZE
        enable_overlap = bool(job.get('enable_overlap', True))  # batch-mode-only; single-page mode never overlaps
        job_confidence_threshold = (job.get('engine_params') or {}).get('confidence_threshold')
        score_cfg = {"confidence_threshold": job_confidence_threshold} if job_confidence_threshold is not None else None

        ocr_options = None
        if is_captcha:
            ocr_options = {
                "num_ctx": 2048, "temperature": 0.0, "num_predict": 15,
                "repeat_penalty": 2.0, "stop": ["\n", "```", "I will", "Here ", "The text"],
            }

        is_pdf = file_path.lower().endswith(".pdf")
        pdf_doc = pdfium.PdfDocument(file_path) if is_pdf else None
        try:
            total_pages = len(pdf_doc) if pdf_doc else 1
            selected_pages = _parse_page_range(page_range, total_pages)

            # Single-page mode has no chunk concept at all (no chunk_of callback —
            # every page gets chunk_id=None, is_boundary=0). Only batch mode groups
            # pages into chunks, since a chunk there is a literal infer_multi() call.
            if processing_mode == "batch":
                chunk_lookup = chunking.build_chunk_lookup(total_pages, chunk_size, overlap=enable_overlap)
                database.init_pages_for_job(job_id, total_pages, selected_pages, chunk_of=chunk_lookup)
            else:
                database.init_pages_for_job(job_id, total_pages, selected_pages)
            database.reset_stuck_processing_pages(job_id)

            # On a resume, only genuinely never-attempted ('pending') pages get
            # picked up automatically - otherwise every resume walks back
            # through the whole document from page 1, silently redoing
            # already-completed pages before ever reaching the genuinely
            # pending ones (claim_page_for_processing's guard only blocks
            # re-claiming a page that's *currently* processing, not one that's
            # already done). This deliberately excludes 'failed' pages too -
            # a resume doesn't auto-retry those, only the existing manual
            # per-page retry does.
            already_done_pages = {
                p["page_num"] for p in database.get_pages(job_id)
                if p["status"] != "pending"
            }

            local_sem = asyncio.Semaphore(max(1, concurrency))

            async def load_image(page_num):
                if is_pdf:
                    return await _render_page(pdf_doc, page_num - 1, scale)
                with Image.open(file_path) as img:
                    return img.convert("RGB") if img.mode != "RGB" else img.copy()

            def build_prompt(page_num, primer):
                p = prompt_base
                if config.OCR_CONTEXT_PRIMER_ENABLED and not is_captcha:
                    p = context_primer.render_primer(p, primer, enabled=True)
                return p

            async def score_and_store(page_num, pil_img, text, status, error, attempts, elapsed_s, source="single", overlap_alt_text=None, raw_text=None):
                if status != "success":
                    score = confidence.score_page(text or f"### ❌ Error page {page_num}: {error}", hard_failure=True, cfg=score_cfg)
                else:
                    ink_ratio = await asyncio.to_thread(_compute_ink_ratio, pil_img) if pil_img is not None else None
                    prev_page = database.get_last_completed_page_before(job_id, page_num, config.OCR_CONTEXT_MAX_GAP)
                    primer_for_score = context_primer.build_primer(prev_page['text']) if prev_page else None
                    score = confidence.score_page(
                        text, attempts=attempts, ink_ratio=ink_ratio, elapsed_s=elapsed_s,
                        timeout_s=ollama_timeout, primer=primer_for_score,
                        overlap_alt_text=overlap_alt_text, cfg=score_cfg,
                    )
                final_status = "success" if status == "success" and not score.needs_review else (
                    "review" if status == "success" else "failed"
                )
                # A page the engine couldn't process shows one clean, consistent
                # message instead of a raw traceback/error string in the exported
                # transcript - the real detail is preserved in the `error` column.
                stored_text = text
                if status != "success" or score.signals.get("engine_error_in_output"):
                    error = error or text or ''
                    stored_text = MANUAL_REVIEW_TEXT
                database.upsert_page(
                    job_id, page_num, status=final_status, text=stored_text, confidence=score.confidence,
                    needs_review=int(score.needs_review), review_reason=(score.reasons[0] if score.reasons else ''),
                    signals=__import__("json").dumps(score.signals), attempts=attempts, source=source,
                    error=error or '', raw_ocr_text=raw_text,
                )
                database.record_page_attempt(
                    job_id, page_num, attempt_no=1, escalation_level="base", engine=engine, scale=scale,
                    tiling=int(tiling), outcome=final_status, error=error or '', confidence=score.confidence,
                    signals=__import__("json").dumps(score.signals), text_len=len(text or ''), text=None,
                )
                await _update_reference_index_safe(job_id, page_num, stored_text, total_pages, raw_text=raw_text)
                return score, final_status

            async def process_single_page(page_num):
                if job_id in _paused_jobs:
                    return "paused"
                if not database.claim_page_for_processing(job_id, page_num):
                    return "skipped"

                try:
                    pil_img = await load_image(page_num)
                    if not is_pdf and is_captcha:
                        pil_img = ImageOps.expand(pil_img, border=15, fill=(255, 255, 255))
                        w, h = pil_img.size
                        if w < 400:
                            factor = max(3, 400 // w)
                            pil_img = pil_img.resize((w * factor, h * factor), Image.LANCZOS)
                        pil_img = ImageEnhance.Contrast(pil_img).enhance(1.8)
                        pil_img = pil_img.filter(ImageFilter.SHARPEN)

                    prev_page = database.get_last_completed_page_before(job_id, page_num, config.OCR_CONTEXT_MAX_GAP)
                    primer = context_primer.build_primer(prev_page['text']) if prev_page else None
                    prompt = build_prompt(page_num, primer)

                    raw_capture = {}
                    async with _gpu_slot(is_escalation=False):
                        text, status, error, attempts, elapsed_s = await _run_single_page_with_retries(
                            engine, run_ocr_func, run_unlimited_func, pil_img, prompt, tiling, ollama_timeout, ocr_options,
                            raw_capture=raw_capture,
                        )
                    await score_and_store(page_num, pil_img, text, status, error, attempts, elapsed_s, raw_text=raw_capture.get("raw_text"))
                    return "done"
                except Exception as e:
                    # Anything unexpected here (image rendering, DB writes, confidence
                    # scoring, ...) must never propagate to asyncio.gather() and take
                    # the whole job down with a raw traceback - only the OCR call
                    # itself is already retried/absorbed by _run_single_page_with_retries.
                    # Fail just this page cleanly and let the worker move on.
                    print(f"[TaskQueue] ❌ Job {job_id} page {page_num} hit an unexpected error: {e}\n{traceback.format_exc()}")
                    database.upsert_page(
                        job_id, page_num, status="failed", text=MANUAL_REVIEW_TEXT,
                        needs_review=1, error=f"{type(e).__name__}: {e}",
                    )
                    return "error"

            async def run_chunk_batch_mode(chunk):
                async with local_sem:
                    owned = [
                        p for p in range(chunk["owned_start"], chunk["owned_end"] + 1)
                        if p in selected_pages and p not in already_done_pages
                    ]
                    if not owned:
                        return
                    if job_id in _paused_jobs:
                        return
                    claimed = [p for p in owned if database.claim_page_for_processing(job_id, p)]
                    if not claimed:
                        return
                    # No "<image>" prefix here - run_ocr_multi() (gpu-ocr-service) adds
                    # it exactly once itself; a prefix here would double it up and,
                    # per the model's 2-part prompt.split('<image>') parsing, silently
                    # drop the actual instruction text from what the model receives.
                    prompt = "Multi page parsing." if not custom_prompt else prompt_base
                    fallback_reason = None
                    try:
                        # Image loading is inside this try too - a rendering failure
                        # for any page in the chunk must degrade to the single-page
                        # fallback below, not raise out and take the whole job down.
                        images = [await load_image(p) for p in claimed]
                        async with _gpu_slot(is_escalation=False):
                            combined_text = await run_ocr_multi_func(images, prompt, ollama_timeout)
                        texts, ok = _split_batch_output(combined_text, len(claimed))
                        if not ok:
                            fallback_reason = (
                                f"batch output split mismatch: got {len(texts)} page(s), "
                                f"expected {len(claimed)}"
                            )
                        else:
                            for p, img, text in zip(claimed, images, texts):
                                await score_and_store(p, img, text, "success", "", 1, None, source="batch")
                    except Exception as e:
                        fallback_reason = f"{type(e).__name__}: {e}"

                    if fallback_reason is not None:
                        print(f"[TaskQueue] Job {job_id} batch chunk {chunk['chunk_index']} failed, falling back to single-page: {fallback_reason}")
                        for p in claimed:
                            database.upsert_page(job_id, p, status="pending")  # release claim, let single-page path pick it up
                        for p in claimed:
                            await process_single_page(p)

            if processing_mode == "batch":
                chunks = chunking.compute_chunks(total_pages, chunk_size, overlap=enable_overlap)
                await asyncio.gather(*(run_chunk_batch_mode(c) for c in chunks))
            else:
                # No chunking at all for single-page mode — a single shared
                # ascending-order queue drained by `concurrency` workers. Claiming
                # happens synchronously the instant a page is popped (before any
                # await), so dispatch order is always non-decreasing regardless of
                # any individual page's OCR latency.
                page_queue = asyncio.Queue()
                for p in sorted(selected_pages - already_done_pages):
                    page_queue.put_nowait(p)

                async def single_mode_worker():
                    while True:
                        try:
                            page_num = page_queue.get_nowait()
                        except asyncio.QueueEmpty:
                            return
                        result = await process_single_page(page_num)
                        if result == "paused":
                            return

                await asyncio.gather(*(single_mode_worker() for _ in range(max(1, concurrency))))

            if job_id in _paused_jobs:
                _paused_jobs.discard(job_id)
                print(f"[TaskQueue] ⏸️ Job {job_id} paused.")
                database.refresh_job_page_counts(job_id)
                database.set_job_status(job_id, "paused")
                return

            database.refresh_job_page_counts(job_id)

            if escalate_before_flagging:
                pending_escalation = [
                    p for p in database.get_pages(job_id)
                    if p["status"] == "review" and not p.get("escalated")
                ]
                if pending_escalation:
                    database.set_job_status(job_id, "escalating")
                    for p in pending_escalation:
                        if job_id in _paused_jobs:
                            break
                        await _escalate_page(
                            job_id, p["page_num"], engine, run_ocr_func, run_unlimited_func,
                            prompt_base, tiling, ollama_timeout, ocr_options, pdf_doc, is_pdf,
                            file_path, scale, score_cfg,
                        )
                    database.refresh_job_page_counts(job_id)

            database.set_job_status(job_id, "completed")
            print(f"[TaskQueue] ✅ Job {job_id} successfully completed!")
            asyncio.create_task(trigger_ai_reindex_safe(job_id))

            if output_path and os.path.isdir(output_path):
                try:
                    import seam_stitching
                    pages = [{"page": p["page_num"], "text": p["text"]} for p in database.get_pages(job_id)]
                    base_name = os.path.splitext(job['filename'])[0]
                    out_file = os.path.join(output_path, f"{base_name}_ocr.md")
                    # Stitching follows the job's overlap setting — single-page mode
                    # never has overlap, so it never stitches; batch mode stitches
                    # only when it opted in.
                    stitch = processing_mode == "batch" and enable_overlap
                    with open(out_file, "w", encoding="utf-8") as f:
                        f.write(seam_stitching.render_export(pages, stitch=stitch))
                    print(f"[TaskQueue] 📁 Auto-exported result to {out_file}")
                except Exception as _ex:
                    print(f"[TaskQueue] ⚠️ Auto-export failed: {_ex}")

        finally:
            if pdf_doc is not None:
                pdf_doc.close()

    except asyncio.CancelledError:
        print(f"[TaskQueue] ⏸️ Job {job_id} was cancelled — marking paused.")
        _paused_jobs.discard(job_id)
        database.refresh_job_page_counts(job_id)
        database.set_job_status(job_id, "paused")
        raise
    except Exception as outer_e:
        print(f"[TaskQueue] ❌ Job {job_id} failed completely: {outer_e}\n{traceback.format_exc()}")
        # Only the short reason reaches the user (see ViewerPanel.jsx, which renders
        # error_message raw) - the full traceback is preserved in the server logs above.
        friendly = f"Processing stopped due to an unexpected error: {outer_e}. See server logs for details."
        database.mark_job_failed(job_id, friendly)
    finally:
        _active_tasks.pop(job_id, None)


def _split_batch_output(combined_text: str, expected_pages: int):
    """Splits infer_multi()'s combined output back into per-page text, mirroring
    the model's own reference split exactly (see baidu/Unlimited-OCR's
    modeling_unlimitedocr.py, infer_multi(), save_results branch):
    `pages = outputs.split('<PAGE>')[1:]` — the model delimits pages with the
    literal string '<PAGE>' (no guaranteed surrounding newlines), and any text
    before the first marker is discarded, not attached to page 1.

    Returns (texts, ok): texts is a list of length expected_pages (best-effort;
    only meaningful when ok is True). ok is False when the number of `<PAGE>`
    -delimited segments doesn't match expected_pages — the caller must not
    attribute this text to any specific page in that case."""
    import re
    parts = re.split(r"<PAGE>", combined_text or "", flags=re.IGNORECASE)[1:]
    texts = [p.strip() for p in parts]
    if len(texts) != expected_pages:
        return texts, False
    return texts, True


async def _escalate_page(job_id, page_num, engine, run_ocr_func, run_unlimited_func, prompt, tiling, timeout, ocr_options, pdf_doc, is_pdf, file_path, base_scale, score_cfg=None):
    """The unified escalation model: exactly one combined recovery attempt for any
    page in either mode — max scale + tiling, via single-page processing. No ladder,
    no automatic cross-engine step (that's manual-only, see retry_single_page)."""
    page = database.get_page(job_id, page_num)
    if not page:
        return
    base_text = page["text"]

    escalated_scale = min(base_scale * config.OCR_ESCALATION_SCALE_FACTOR, config.OCR_MAX_RENDER_SCALE)
    if is_pdf:
        pil_img = await _render_page(pdf_doc, page_num - 1, escalated_scale)
    else:
        with Image.open(file_path) as img:
            pil_img = img.convert("RGB") if img.mode != "RGB" else img.copy()

    raw_capture = {}
    async with _gpu_slot(is_escalation=True):
        text, status, error, attempts, elapsed_s = await _run_single_page_with_retries(
            engine, run_ocr_func, run_unlimited_func, pil_img, prompt, True, int(timeout * 1.5), ocr_options,
            raw_capture=raw_capture,
        )

    if status != "success":
        database.record_page_attempt(
            job_id, page_num, attempt_no=2, escalation_level="combined_recovery", engine=engine,
            scale=escalated_scale, tiling=1, outcome="failed", error=error,
        )
        # "review" matches this page's status going in for the automatic pass
        # (a no-op there) - but retry_single_page/retry_pages_bulk claim the
        # page to 'processing' before calling this, and never touch it again
        # themselves, so a manual retry that hits this branch must still land
        # on a terminal status here or it's stuck at 'processing' forever
        # (confirmed live: exactly what happened to page 99 after a retry).
        database.upsert_page(job_id, page_num, status="review", escalated=1)
        return

    ink_ratio = await asyncio.to_thread(_compute_ink_ratio, pil_img)
    new_score = confidence.score_page(text, ink_ratio=ink_ratio, escalation_alt_text=base_text, cfg=score_cfg)
    base_score_confidence = page.get("confidence") or 0.0

    database.record_page_attempt(
        job_id, page_num, attempt_no=2, escalation_level="combined_recovery", engine=engine,
        scale=escalated_scale, tiling=1, outcome="success", confidence=new_score.confidence,
        signals=__import__("json").dumps(new_score.signals), text_len=len(text), text=None,
    )

    if new_score.confidence >= base_score_confidence:
        final_status = "success" if not new_score.needs_review else "review"
        # The OCR call itself doesn't raise on a timeout/crash - it returns a
        # formatted "### ❌ ... failed" string instead (see unlimited_ocr_engine.py),
        # so a repeat failure lands here as a "successful" call whose text IS
        # the raw error. Swap it for the same friendly placeholder score_and_store
        # already uses for the base pass, and preserve the real detail in `error`
        # (which this branch otherwise never touched, leaving a stale value from
        # whatever attempt came before this one).
        stored_text = text
        stored_error = error or ''
        if new_score.signals.get("engine_error_in_output"):
            stored_error = error or text or ''
            stored_text = MANUAL_REVIEW_TEXT
        database.upsert_page(
            job_id, page_num, status=final_status, text=stored_text, confidence=new_score.confidence,
            needs_review=int(new_score.needs_review),
            review_reason=(new_score.reasons[0] if new_score.reasons else ''),
            signals=__import__("json").dumps(new_score.signals), escalated=1,
            error=stored_error, raw_ocr_text=raw_capture.get("raw_text"),
        )
        job_for_total = database.get_job(job_id, include_pages=False)
        total_pages = (job_for_total or {}).get('total_pages') or page_num
        await _update_reference_index_safe(job_id, page_num, stored_text, total_pages, raw_text=raw_capture.get("raw_text"))
    else:
        # Escalation attempt scored worse than the base pass — keep the base text,
        # just record that escalation was attempted. Same reasoning as the
        # failure branch above: must still write a terminal status, or a
        # manually-claimed page is stuck at 'processing' forever.
        database.upsert_page(job_id, page_num, status="review", escalated=1)


async def retry_single_page(job_id: str, page_num: int, engine: str, run_ocr_func, run_unlimited_func,
                             prompt: str, tiling: bool, timeout: int, ocr_options=None,
                             escalate_other_engine: bool = False):
    """Manual per-page retry, usable independently of whether the job's main task is
    still running (including on an already-`completed` job — the job's own status is
    never touched here, only this one page's row). Gives the page the same
    "highest quality possible" treatment as the automatic escalation pass
    (_escalate_page: max scale + tiling + 1.5x timeout, keeping whichever
    result - new vs. what was already there - scores higher) rather than a
    plain re-attempt at the job's original settings."""
    if not database.claim_page_for_processing(job_id, page_num):
        raise RuntimeError(f"Page {page_num} of job {job_id} is currently being processed.")

    job = database.get_job(job_id, include_pages=False)
    file_path = job["file_path"]
    is_pdf = file_path.lower().endswith(".pdf")
    scale = job["scale"]

    retry_engine = engine
    if escalate_other_engine:
        retry_engine = "glm" if engine == "unlimited" else "unlimited"

    pdf_doc = pdfium.PdfDocument(file_path) if is_pdf else None
    try:
        await _escalate_page(
            job_id, page_num, retry_engine, run_ocr_func, run_unlimited_func,
            prompt, tiling, timeout, ocr_options, pdf_doc, is_pdf, file_path, scale,
        )
    finally:
        if pdf_doc is not None:
            pdf_doc.close()

    database.refresh_job_page_counts(job_id)
    return database.get_page(job_id, page_num)


_active_bulk_retries: set = set()


def is_bulk_retry_active(job_id: str) -> bool:
    return job_id in _active_bulk_retries


async def retry_pages_bulk(job_id: str, engine: str, run_ocr_func, run_unlimited_func,
                            prompt: str, tiling: bool, timeout: int, ocr_options, scope: str):
    """Bulk manual retry across many pages at once - same "highest quality
    possible" treatment as retry_single_page (both delegate to _escalate_page),
    just looped over every page in scope instead of one. scope="failed" only
    touches pages that hard-failed (status="failed"); scope="review" only
    touches low-confidence pages flagged for review (status="review") - both
    are safe to run alongside an active base pass, since those pages are
    terminal from its point of view (see the resume-skip logic in
    process_ocr_job). scope="all" touches every page regardless of status -
    the caller (main.py) is responsible for not starting this while the job's
    own base pass is still active, since that would race both over
    not-yet-attempted pages."""
    if job_id in _active_bulk_retries:
        return
    _active_bulk_retries.add(job_id)
    try:
        job = database.get_job(job_id, include_pages=False)
        if not job:
            return
        file_path = job["file_path"]
        is_pdf = file_path.lower().endswith(".pdf")
        scale = job["scale"]

        database.reset_stuck_processing_pages(job_id)
        pages = database.get_pages(job_id)
        if scope == "failed":
            target_pages = [p["page_num"] for p in pages if p["status"] == "failed"]
        elif scope == "review":
            target_pages = [p["page_num"] for p in pages if p["status"] == "review"]
        else:
            target_pages = [p["page_num"] for p in pages]
        if not target_pages:
            return

        database.set_job_status(job_id, "escalating")
        pdf_doc = pdfium.PdfDocument(file_path) if is_pdf else None
        try:
            for page_num in target_pages:
                if job_id in _paused_jobs:
                    break
                if not database.claim_page_for_processing(job_id, page_num):
                    continue
                await _escalate_page(
                    job_id, page_num, engine, run_ocr_func, run_unlimited_func,
                    prompt, tiling, timeout, ocr_options, pdf_doc, is_pdf, file_path, scale,
                )
        finally:
            if pdf_doc is not None:
                pdf_doc.close()

        if job_id in _paused_jobs:
            _paused_jobs.discard(job_id)
            database.refresh_job_page_counts(job_id)
            database.set_job_status(job_id, "paused")
            return

        database.refresh_job_page_counts(job_id)
        database.set_job_status(job_id, "completed")
        # Targeted from the earliest retried page — not a full-document
        # AI reindex — since a bulk retry only ever changes target_pages'
        # own text; everything before the earliest one is untouched and
        # its already-persisted scope is still valid as the seed.
        asyncio.create_task(trigger_ai_reindex_safe(job_id, start_page=min(target_pages)))
    finally:
        _active_bulk_retries.discard(job_id)


async def resume_interrupted_jobs(build_runners_func, prompts_dict, ollama_timeout: int = 600, output_path: str = ""):
    """
    Scans SQLite DB on startup and dispatches background tasks for any job left in
    'pending', 'processing', or 'escalating' state. Must be called from an async
    context (startup event).

    `build_runners_func(engine_params) -> (glm_runner, unlimited_runner, unlimited_multi_runner)`
    is called PER JOB with that job's own frozen engine_params — resuming with one
    shared runner pair for every job would silently revert each job to default
    engine params, exactly the bug this fixes.
    """
    jobs = database.list_jobs(limit=100, include_pages=False)
    interrupted = [j for j in jobs if j['status'] in ('pending', 'processing', 'escalating')]
    if not interrupted:
        print("[TaskQueue] No interrupted or pending jobs found on startup.")
        return

    print(f"[TaskQueue] 🔄 Resuming {len(interrupted)} pending/interrupted OCR job(s)...")
    for job in interrupted:
        glm_runner, unlimited_runner, unlimited_multi_runner = build_runners_func(job.get('engine_params') or {})
        asyncio.create_task(
            process_ocr_job(
                job['job_id'],
                glm_runner,
                unlimited_runner,
                prompts_dict,
                ollama_timeout,
                output_path,
                job.get('page_range', ''),
                unlimited_multi_runner,
            )
        )
