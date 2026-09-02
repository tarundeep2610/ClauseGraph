import os
import io
import re
import sys
import json
import time
import signal
import uuid
import math
import collections
import urllib.parse
import asyncio
from contextlib import asynccontextmanager
import pypdfium2 as pdfium
from fastapi import FastAPI, UploadFile, File, HTTPException, Depends, Query, BackgroundTasks
from fastapi.responses import HTMLResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.datastructures import MutableHeaders
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

import config
import database
import task_queue
import gpu_ocr_client
import chunking
import confidence
import seam_stitching
from auth_deps import Principal, UserRole, require_role
from auth_router import router as auth_router

_require_admin = require_role([UserRole.ADMIN])
_require_user = require_role([UserRole.ADMIN, UserRole.USER])

# ─── Unlimited-OCR native engine ────────────────────────────────────────────
try:
    import unlimited_ocr_engine
    _UNLIMITED_OCR_AVAILABLE = True
except ImportError:
    unlimited_ocr_engine = None  # type: ignore
    _UNLIMITED_OCR_AVAILABLE = False
    print(
        "[Service] ⚠️  unlimited_ocr_engine not found or failed to import. "
        "Baidu Unlimited-OCR will be unavailable.",
        file=sys.stderr
    )

# _lifespan is defined here so it can be passed to FastAPI() below.
# It references helpers defined later in this file (_load_settings, run_ocr_for_image, etc.);
# Python resolves those names at call time (server startup), not at definition time, so
# there is no forward-reference error at import.
@asynccontextmanager
async def _lifespan(app: FastAPI):
    global _idle_watcher_task
    database.init_db()
    _load_custom_prompts()
    settings = _load_settings()
    await task_queue.resume_interrupted_jobs(
        _build_runners,
        dict(PROMPTS),
        OCR_TIMEOUT,
        settings.get("output_path", "")
    )
    _idle_watcher_task = asyncio.create_task(_idle_watcher())
    yield
    if _idle_watcher_task and not _idle_watcher_task.done():
        _idle_watcher_task.cancel()
        try:
            await _idle_watcher_task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="GLM-OCR Service",
    description="A standalone, production-ready OCR service with fail-safe background persistence.",
    version="1.1.0",
    lifespan=_lifespan,
)

_FRONTEND_DIST_DIR = os.path.join(os.path.dirname(__file__), "frontend", "dist")
_ASSETS_DIR = os.path.join(_FRONTEND_DIST_DIR, "assets")
if os.path.isdir(_ASSETS_DIR):
    app.mount("/assets", StaticFiles(directory=_ASSETS_DIR), name="assets")

app.include_router(auth_router)

# CORS: restrict to localhost origins only (wildcard + credentials is a spec violation)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class _ActivityMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        global _last_activity
        if scope["type"] == "http":
            _last_activity = time.time()
        await self.app(scope, receive, send)

app.add_middleware(_ActivityMiddleware)

_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' https://cdnjs.cloudflare.com 'unsafe-inline'; "
    "font-src https://cdnjs.cloudflare.com; "
    "img-src 'self' data: blob:; "
    "frame-src 'self' blob:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "object-src 'none'; "
    "base-uri 'self'"
)

class _SecurityHeadersMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")

        async def _send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Content-Security-Policy"] = _CSP
                headers["X-Content-Type-Options"] = "nosniff"
                headers["X-Frame-Options"] = "DENY"
                headers["Referrer-Policy"] = "no-referrer"
                headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=(), payment=()"
                if not path.startswith("/assets/"):
                    headers["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, _send_with_headers)

app.add_middleware(_SecurityHeadersMiddleware)

PROMPTS = {
    "captcha": (
        "Read the exact text shown in this image. "
        "Output only the visible characters — letters, numbers, symbols. "
        "CRITICAL SHAPE DISTINCTION: V (uppercase) has NO vertical stem below the junction "
        "of its two diagonal strokes. Y (uppercase) HAS a short vertical stem descending "
        "from that junction. Examine carefully before choosing between V and Y. "
        "Do not describe the image or add any explanation."
    ),
    "generic": (
        "Perform OCR on this image. Transcribe all visible text exactly as it appears, "
        "preserving structure. Use Markdown for tables if present."
    ),
    "document": (
        "Perform OCR on this image. Follow these guidelines strictly:\n"
        "1. Identify and transcribe all document metadata, including Certificate Numbers, Dates, and Names.\n"
        "2. Format all tables as standard Markdown tables (using '|' borders). Match each row's values to their correct column headers. "
        "   *CRITICAL TABLE RULE*: The table contains a row of printed column index numbers (e.g. 1, 2, 3, 4, 5, 6, 7, 8, 9) inside the headers. The ACTUAL quantities (e.g. '6 KM', 'NIL') are written in the row underneath these index numbers. Do NOT output the index numbers (1 to 9) as the table values. Write the actual quantities under their respective column headers instead. Do NOT use JSON formatting or phonetic transliterations for headers.\n"
        "3. Translate and transcribe Hindi (Devanagari) words accurately. Do not repeat words or hallucinate translations.\n"
        "4. The document is written strictly in Hindi (Devanagari script) and English. Do NOT output characters from other regional scripts (e.g., Bengali, Gurmukhi). Transcribe Hindi text only using standard Devanagari characters.\n"
        "5. Do NOT output raw JSON blocks or curly braces '{}'. Format all outputs using standard Markdown (lists or markdown tables).\n"
        "6. Do not write consecutive separator lines (e.g. '---') or repeat characters/symbols/templates endlessly."
    ),
}

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
_SETTINGS_FILE = os.path.join(_DATA_DIR, "settings.json")
_CUSTOM_PROMPTS_FILE = os.path.join(_DATA_DIR, "custom_prompts.json")

# Default hardcoded prompts — kept separate so reset works correctly
_DEFAULT_PROMPTS = {k: v for k, v in PROMPTS.items()}

_last_activity = time.time()

_settings_cache: dict = {}
_settings_cache_ts: float = 0.0
_SETTINGS_TTL = 5.0


def _load_settings() -> dict:
    global _settings_cache, _settings_cache_ts
    now = time.time()
    if _settings_cache and now - _settings_cache_ts < _SETTINGS_TTL:
        return dict(_settings_cache)
    defaults = {
        "output_path": "", "idle_timeout_minutes": 15,
        "enable_unlimited_long_prompts": config.ENABLE_UNLIMITED_LONG_PROMPTS,
        "audit_sample_rate": config.OCR_AUDIT_SAMPLE_RATE,
        "audit_high_confidence_min": config.OCR_AUDIT_HIGH_CONFIDENCE_MIN,
        "audit_sample_cap_per_job": config.OCR_AUDIT_SAMPLE_CAP_PER_JOB,
    }
    try:
        if os.path.exists(_SETTINGS_FILE):
            with open(_SETTINGS_FILE, "r", encoding="utf-8") as f:
                result = {**defaults, **json.load(f)}
                _settings_cache = result
                _settings_cache_ts = now
                return dict(result)
    except Exception:
        pass
    _settings_cache = defaults
    _settings_cache_ts = now
    return dict(defaults)


def _save_settings(data: dict):
    global _settings_cache, _settings_cache_ts
    os.makedirs(_DATA_DIR, exist_ok=True)
    with open(_SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    _settings_cache = dict(data)
    _settings_cache_ts = time.time()


def _load_custom_prompts():
    try:
        if os.path.exists(_CUSTOM_PROMPTS_FILE):
            with open(_CUSTOM_PROMPTS_FILE, "r", encoding="utf-8") as f:
                PROMPTS.update(json.load(f))
    except Exception:
        pass


def _save_custom_prompts():
    os.makedirs(_DATA_DIR, exist_ok=True)
    with open(_CUSTOM_PROMPTS_FILE, "w", encoding="utf-8") as f:
        json.dump(PROMPTS, f, indent=2)


# ─── Rate limiting ────────────────────────────────────────────────────────────
_rate_buckets: dict = {}  # rate_limit_key -> deque of submission timestamps

def _check_rate_limit(rate_limit_key: str):
    now = time.time()
    dq = _rate_buckets.setdefault(rate_limit_key, collections.deque())
    while dq and now - dq[0] > config.RATE_LIMIT_WINDOW:
        dq.popleft()
    if len(dq) >= config.RATE_LIMIT_MAX:
        retry_after = max(1, math.ceil(dq[0] + config.RATE_LIMIT_WINDOW - now))
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: max {config.RATE_LIMIT_MAX} OCR requests per {config.RATE_LIMIT_WINDOW}s.",
            headers={"Retry-After": str(retry_after)},
        )
    dq.append(now)


# ─── Content-Disposition helper ───────────────────────────────────────────────
def _content_disposition(disposition: str, filename: str) -> str:
    """Return a Content-Disposition header value with RFC 5987 encoded filename."""
    # Strip control characters to prevent header injection
    safe = re.sub(r'[\x00-\x1f\x7f]', '', filename)
    # ASCII fallback: replace non-ASCII with underscore
    ascii_name = safe.encode('ascii', errors='replace').decode('ascii').replace('?', '_').replace('"', '_')
    # RFC 5987 percent-encoded value (allow safe chars through unencoded)
    encoded = urllib.parse.quote(safe, safe="!#$&+-.^_`|~")
    return f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded}"


async def _idle_watcher():
    global _last_activity
    while True:
        await asyncio.sleep(30)
        settings = _load_settings()
        timeout_mins = settings.get("idle_timeout_minutes", 15)
        if timeout_mins <= 0:
            continue
        # Never shut down while OCR is actively running
        if task_queue.has_active_jobs():
            _last_activity = time.time()
            continue
        if time.time() - _last_activity >= timeout_mins * 60:
            print(f"[Service] Idle timeout reached — shutting down server.")
            signal.raise_signal(signal.SIGINT)
            break


def clean_captcha_result(text: str, expected_len: int = None) -> str:
    text = re.sub(r'```[^\n]*', '', text)
    lines = [l.strip() for l in text.strip().split('\n') if l.strip()]
    if not lines:
        return ''
    preamble_starts = ('i will', 'here is', 'the text', 'the captcha',
                       'the image', 'this is', 'below is')
    answer_lines = [l for l in lines if not l.lower().startswith(preamble_starts)]
    combined = ''.join(answer_lines if answer_lines else lines)
    result = re.sub(r'[^A-Za-z0-9]', '', combined)
    if expected_len and len(result) > expected_len:
        result = result[:expected_len]
    return result

# Configuration
DEFAULT_RENDER_SCALE = config.DEFAULT_RENDER_SCALE
OCR_TIMEOUT = config.OCR_TIMEOUT

async def run_ocr_for_image(pil_image, prompt: str, tiling: bool, timeout: int, options: dict = None) -> str:
    """GLM engine — always a network call to Ollama on the GPU box (via gpu_ocr_client)."""
    return await gpu_ocr_client.run_glm_ocr(pil_image, prompt, tiling, timeout, options)


async def run_unlimited_ocr_for_image(pil_image: Image.Image, prompt: str, timeout: int, base_size: int = 1024, long_prompts_enabled: bool = False) -> str:
    # GLM already receives the full prompt unmodified (proven safe in production).
    # Unlimited-OCR's own default is a short, fixed task-label rather than a sentence
    # of instructions, so its behavior under a long prompt is unproven — gated behind
    # a runtime setting rather than removed unconditionally, since this is the
    # default engine and an unvalidated change here would hit every job immediately.
    #
    # `long_prompts_enabled` must be resolved ONCE by the caller (frozen into the
    # job's engine_params at creation time for job-based callers) — never read live
    # from settings inside this function, or a PATCH mid-job would split one
    # document's pages across two different prompt behaviors.
    effective_prompt = prompt if (long_prompts_enabled or len(prompt) <= 80) else "document parsing."
    if config.OCR_LOCAL_ENGINE:
        # Dev-only fallback: run in-process on this machine without a GPU server.
        if not _UNLIMITED_OCR_AVAILABLE or unlimited_ocr_engine is None:
            raise Exception("unlimited_ocr_engine is not installed.")
        return await asyncio.to_thread(
            unlimited_ocr_engine.run_ocr, pil_image, effective_prompt, base_size, timeout
        )
    return await gpu_ocr_client.run_unlimited_ocr(pil_image, effective_prompt, timeout, base_size=base_size)


def _build_runners(engine_params: dict):
    """Builds the GLM/Unlimited/batch runner closures from a job's frozen
    engine_params snapshot — used identically by submit, retry, and resume so
    retry/resume honor the job's original settings instead of reverting to
    defaults (an existing bug this fixes)."""
    glm_opts = {
        "temperature": engine_params.get("glm_temperature", 0.0),
        "num_ctx": engine_params.get("glm_num_ctx", 8192),
        "num_predict": engine_params.get("glm_num_predict", 2048),
        "repeat_penalty": engine_params.get("glm_repeat_penalty", 1.2),
    }

    def _glm_runner(pil_image, prompt, _tiling, timeout, captcha_override=None):
        opts = captcha_override if captcha_override is not None else glm_opts
        return run_ocr_for_image(pil_image, prompt, _tiling, timeout, opts)

    u_base = engine_params.get("unlimited_base_size", 1024)
    long_prompts_enabled = engine_params.get("long_prompts_enabled", False)

    def _unlimited_runner(pil_image, prompt, timeout):
        return run_unlimited_ocr_for_image(pil_image, prompt, timeout, base_size=u_base, long_prompts_enabled=long_prompts_enabled)

    async def _unlimited_multi_runner(pil_images, prompt, timeout):
        return await gpu_ocr_client.run_unlimited_ocr_multi(pil_images, prompt, timeout)

    return _glm_runner, _unlimited_runner, _unlimited_multi_runner


_idle_watcher_task: asyncio.Task | None = None


# ─── SETTINGS & PROMPTS API ───────────────────────────────────────────────────

@app.get("/api/settings")
def get_settings(principal: Principal = Depends(_require_admin)):
    return _load_settings()

@app.post("/api/settings")
def save_settings(body: dict, principal: Principal = Depends(_require_admin)):
    current = _load_settings()
    if "output_path" in body:
        op = body["output_path"]
        if op and not os.path.isdir(op):
            raise HTTPException(
                status_code=400,
                detail=f"output_path must be an existing directory: {op!r}"
            )
    allowed_keys = (
        "output_path", "idle_timeout_minutes", "enable_unlimited_long_prompts",
        "audit_sample_rate", "audit_high_confidence_min", "audit_sample_cap_per_job",
    )
    current.update({k: v for k, v in body.items() if k in allowed_keys})
    _save_settings(current)
    return current

@app.get("/api/prompts")
def get_prompts(principal: Principal = Depends(_require_user)):
    return {"prompts": PROMPTS, "defaults": _DEFAULT_PROMPTS}

@app.post("/api/prompts/{mode}")
def save_prompt(mode: str, body: dict, principal: Principal = Depends(_require_admin)):
    allowed = set(_DEFAULT_PROMPTS.keys())
    if mode not in allowed:
        raise HTTPException(status_code=400, detail=f"Unknown prompt mode '{mode}'. Allowed: {allowed}")
    text = body.get("text", "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Prompt text cannot be empty")
    PROMPTS[mode] = text
    _save_custom_prompts()
    return {"mode": mode, "text": text}

@app.delete("/api/prompts/{mode}")
def reset_prompt(mode: str, principal: Principal = Depends(_require_admin)):
    if mode not in _DEFAULT_PROMPTS:
        raise HTTPException(status_code=400, detail=f"Unknown prompt mode '{mode}'")
    PROMPTS[mode] = _DEFAULT_PROMPTS[mode]
    _save_custom_prompts()
    return {"mode": mode, "text": PROMPTS[mode], "reset": True}

@app.get("/api/idle-status")
def idle_status():
    settings = _load_settings()
    timeout_mins = settings.get("idle_timeout_minutes", 15)
    idle_secs = time.time() - _last_activity
    remaining = max(0.0, timeout_mins * 60 - idle_secs)
    return {
        "idle_seconds": round(idle_secs),
        "remaining_seconds": round(remaining),
        "timeout_minutes": timeout_mins,
        "enabled": timeout_mins > 0,
    }

@app.get("/health")
async def health_check():
    gpu_health = await gpu_ocr_client.check_health()
    return {
        "status": "healthy",
        "ollama_connection": gpu_health["ollama"],
        "gpu_ocr_service_connection": gpu_health["gpu_ocr_service"],
    }

# ─── FAIL-SAFE PERSISTENT JOB API ENDPOINTS ───────────────────────────────────

@app.post("/api/jobs")
async def submit_ocr_job(
    file: UploadFile = File(..., description="PDF or Image file for background OCR processing"),
    scale: float = Query(None, ge=0.1, le=4.0, description="Custom render scale (0.1–4.0)"),
    tiling: bool = Query(True, description="Enable tiling"),
    prompt_mode: str = Query("document", description="Prompt mode"),
    custom_prompt: str = Query(None, description="Custom prompt override"),
    engine: str = Query("unlimited", pattern="^(unlimited|glm)$", description="OCR engine ('unlimited' or 'glm')"),
    # GLM-OCR (Ollama) hyperparameters
    glm_temperature: float = Query(0.0, ge=0.0, le=2.0, description="GLM sampling temperature"),
    glm_num_ctx: int = Query(8192, ge=512, le=32768, description="GLM context window tokens"),
    glm_num_predict: int = Query(2048, ge=1, le=8192, description="GLM max output tokens"),
    glm_repeat_penalty: float = Query(1.2, ge=0.5, le=3.0, description="GLM repeat penalty"),
    # Unlimited-OCR hyperparameter
    unlimited_base_size: int = Query(1024, ge=128, le=2048, description="Unlimited-OCR base resolution"),
    # Page selection
    page_range: str = Query("", description="Page range to process, e.g. '1-10,25,50-100'. Empty = all pages."),
    # Chunking / batch mode (unlimited engine only) / escalation / concurrency
    chunk_size: int = Query(None, ge=2, le=config.OCR_MAX_CHUNK_SIZE, description="Pages per chunk; also the batch size in batch mode"),
    processing_mode: str = Query(None, pattern="^(batch|single)$", description="Unlimited-OCR only: 'batch' (infer_multi) or 'single' (today's behavior)"),
    enable_overlap: bool = Query(True, description="Batch mode only: share one boundary page between consecutive infer_multi() calls. Ignored for single-page mode, which never overlaps."),
    concurrency: int = Query(None, ge=1, le=16, description="Pages/chunks processed in parallel"),
    escalate_before_flagging: bool = Query(True, description="Auto-escalate a low-confidence page once before flagging for review"),
    confidence_threshold: float = Query(None, ge=0.0, le=1.0, description="Override the default needs_review threshold for this job"),
    principal: Principal = Depends(_require_user)
):
    """
    Submits a document for fail-safe background processing.
    Returns immediately with job_id. Processing continues even if browser tab is closed.
    """
    _check_rate_limit(principal.rate_limit_key)

    filename = file.filename
    filename_lower = filename.lower()
    supported_extensions = (".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp")
    if not filename_lower.endswith(supported_extensions):
        raise HTTPException(status_code=400, detail=f"Supported file extensions: {supported_extensions}")

    MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_MB", "200")) * 1024 * 1024
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"File exceeds {MAX_UPLOAD_BYTES // (1024*1024)} MB upload limit.")

    job_id = str(uuid.uuid4())
    is_pdf = filename_lower.endswith(".pdf")
    file_type = "pdf" if is_pdf else "image"

    # Save uploaded file to persistent storage — basename strips any path traversal attempts
    database.init_db()
    safe_filename = os.path.basename(filename).replace('"', '').replace("'", '')
    saved_filename = f"{job_id}_{safe_filename}"
    saved_filepath = os.path.join(database.UPLOADS_DIR, saved_filename)
    with open(saved_filepath, "wb") as f:
        f.write(content)

    # Determine page count
    if is_pdf:
        try:
            pdf = pdfium.PdfDocument(saved_filepath)
            total_pages = len(pdf)
            pdf.close()
        except HTTPException:
            raise
        except Exception as e:
            if os.path.exists(saved_filepath):
                os.remove(saved_filepath)
            raise HTTPException(status_code=422, detail=f"Invalid PDF file: {e}")
    else:
        total_pages = 1

    render_scale = scale if scale is not None else DEFAULT_RENDER_SCALE

    # processing_mode ships defaulting to "single" (today's proven behavior) until
    # S-5 validates infer_multi()'s batch-size/quality trade-offs on real pages —
    # only unlimited-engine jobs can use "batch" at all.
    effective_processing_mode = processing_mode or config.OCR_PROCESSING_MODE_DEFAULT
    if engine != "unlimited":
        effective_processing_mode = "single"
    effective_batch_size = chunk_size or config.OCR_BATCH_SIZE_DEFAULT
    effective_chunk_size = chunk_size or config.OCR_CHUNK_SIZE
    effective_concurrency = concurrency or config.OCR_JOB_CONCURRENCY

    # Resolved ONCE here and frozen into engine_params — the worker never re-reads
    # live settings mid-job, so a later settings PATCH can't split one document's
    # pages across two different prompt behaviors.
    settings = _load_settings()
    engine_params = {
        "glm_temperature": glm_temperature,
        "glm_num_ctx": glm_num_ctx,
        "glm_num_predict": glm_num_predict,
        "glm_repeat_penalty": glm_repeat_penalty,
        "unlimited_base_size": unlimited_base_size,
        "long_prompts_enabled": bool(settings.get("enable_unlimited_long_prompts", config.ENABLE_UNLIMITED_LONG_PROMPTS)),
        "confidence_threshold": confidence_threshold,
    }

    # Register job in DB (store original filename for display, safe_filename is the on-disk name)
    database.create_job(
        job_id=job_id,
        filename=safe_filename,
        file_path=saved_filepath,
        file_type=file_type,
        engine=engine,
        scale=render_scale,
        tiling=tiling,
        prompt_mode=prompt_mode,
        custom_prompt=custom_prompt,
        total_pages=total_pages,
        page_range=page_range,
        chunk_size=effective_chunk_size,
        concurrency=effective_concurrency,
        engine_params=engine_params,
        processing_mode=effective_processing_mode,
        batch_size=effective_batch_size,
        enable_overlap=enable_overlap,
        escalate_before_flagging=escalate_before_flagging,
    )

    _glm_runner, _unlimited_runner, _unlimited_multi_runner = _build_runners(engine_params)

    asyncio.create_task(
        task_queue.process_ocr_job(
            job_id,
            _glm_runner,
            _unlimited_runner,
            dict(PROMPTS),
            OCR_TIMEOUT,
            settings.get("output_path", ""),
            page_range,
            _unlimited_multi_runner,
        )
    )

    return {
        "job_id": job_id,
        "filename": safe_filename,
        "total_pages": total_pages,
        "status": "pending",
        "message": "OCR job submitted successfully and is running in background."
    }

@app.get("/api/jobs")
def list_ocr_jobs(limit: int = Query(50, ge=1, le=200), principal: Principal = Depends(_require_user)):
    """Lists all past and current OCR jobs for the UI Document History Panel."""
    return database.list_jobs(limit=limit)

@app.post("/api/jobs/stop-all")
def stop_all_ocr_jobs(principal: Principal = Depends(_require_admin)):
    """Cancel all currently running OCR tasks and mark them paused."""
    count = task_queue.cancel_all_jobs()
    return {"message": f"Requested cancellation of {count} active job(s).", "cancelled": count}

@app.post("/api/shutdown")
async def shutdown_server(principal: Principal = Depends(_require_admin)):
    """Stop all OCR tasks and shut down the server process to free memory."""
    task_queue.cancel_all_jobs()
    loop = asyncio.get_running_loop()
    loop.call_later(0.5, lambda: signal.raise_signal(signal.SIGINT))
    return {"message": "Server is shutting down."}

def _annotate_page_confidence(page: dict) -> dict:
    conf = page.get("confidence")
    page["confidence_pct"] = round(conf * 100) if conf is not None else None
    page["confidence_tier"] = confidence.tier_for_stored_confidence(conf, signals=_safe_json_loads(page.get("signals")))
    return page


def _safe_json_loads(raw):
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


@app.get("/api/jobs/{job_id}")
def get_ocr_job(
    job_id: str,
    include_pages: bool = Query(True),
    page_from: int = Query(None, ge=1),
    page_to: int = Query(None, ge=1),
    accept_partial: bool = Query(False, description="Return base-pass results even if escalation is still pending"),
    principal: Principal = Depends(_require_user)
):
    """Retrieves full details, live progress, and extracted text for a specific job."""
    job = database.get_job(job_id, include_pages=False)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if include_pages:
        pages = database.get_pages(job_id, page_from=page_from, page_to=page_to)
        job["pages_data"] = [_annotate_page_confidence(p) for p in pages]

    job["chunks"] = chunking.compute_chunks(job["total_pages"], job.get("chunk_size") or config.OCR_CHUNK_SIZE)
    if job["status"] == "escalating" and not accept_partial:
        job["_escalation_pending"] = True
    return job

@app.get("/api/jobs/{job_id}/file")
def get_job_uploaded_file(
    job_id: str,
    download: bool = Query(False, description="Set to true to force file download"),
    principal: Principal = Depends(_require_user)
):
    """Serves the original uploaded PDF or image file for preview or downloading."""
    job = database.get_job(job_id)
    if not job or not os.path.exists(job['file_path']):
        raise HTTPException(status_code=404, detail="File not found")

    filename = job['filename']
    content_type = "application/pdf" if job['file_type'] == "pdf" else "image/png"
    disposition = "attachment" if download else "inline"

    return FileResponse(
        path=job['file_path'],
        media_type=content_type,
        headers={"Content-Disposition": _content_disposition(disposition, filename)}
    )

@app.get("/api/jobs/{job_id}/download-result")
def download_ocr_result(
    job_id: str,
    include: str = Query("all", pattern="^(all|clean|review)$", description="'all' (with review banners), 'clean' (flagged pages omitted), or 'review' (only flagged pages)"),
    clean_tables: bool = Query(False, description="Merge detected table continuations into one table for structured-data consumers, dropping page anchors at the seam"),
    principal: Principal = Depends(_require_user)
):
    """Downloads the extracted OCR result text as a formatted Markdown (.md) document."""
    job = database.get_job(job_id, include_pages=False)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    pages = database.get_pages(job_id)
    filename = job['filename']
    base_name = os.path.splitext(filename)[0]

    flagged = [p for p in pages if p.get('needs_review')]
    if include == "clean":
        pages = [p for p in pages if not p.get('needs_review')]
    elif include == "review":
        pages = flagged

    header_lines = [
        f"# OCR Extraction Report: {filename}",
        f"- **Engine**: {job['engine'].upper()}",
        f"- **Pages**: {job['total_pages']}",
        f"- **Status**: {job['status'].upper()}",
        f"- **Created At**: {job['created_at']}",
    ]
    if job['status'] == 'escalating':
        header_lines.append("- **⚠️ Note**: escalation is still in progress for some pages — this snapshot may improve once it finishes.")
    if include == "all" and flagged:
        header_lines.append(f"\n## Review Summary ({len(flagged)} page(s) flagged)\n")
        for p in flagged:
            header_lines.append(f"- Page {p['page_num']}: confidence {round((p.get('confidence') or 0) * 100)}% — {p.get('review_reason') or 'flagged'}")

    # Stitching follows the job's own overlap setting — single-page mode never has
    # overlap so it never stitches; batch mode stitches only when it opted in.
    stitch = job.get('processing_mode') == 'batch' and bool(job.get('enable_overlap', True))

    export_pages = [{"page": p["page_num"], "text": p.get("text", "")} for p in pages]
    body = seam_stitching.render_export(export_pages, clean_tables=clean_tables, stitch=stitch)

    if include == "all":
        # Banner each flagged page's own block so it's never silently blended in.
        for p in flagged:
            marker = f"## Page {p['page_num']}\n\n"
            banner = (
                f"## Page {p['page_num']}\n\n> ⚠️ **NEEDS HUMAN REVIEW** — confidence "
                f"{round((p.get('confidence') or 0) * 100)}% ({p.get('review_reason') or 'flagged'}). "
                f"This transcript may be incomplete.\n\n"
            )
            body = body.replace(marker, banner, 1)

    markdown_content = "\n".join(header_lines) + "\n\n---\n\n" + body
    out_filename = f"{base_name}_ocr_result.md"

    return Response(
        content=markdown_content.encode("utf-8"),
        media_type="text/markdown",
        headers={"Content-Disposition": _content_disposition("attachment", out_filename)}
    )

@app.post("/api/jobs/{job_id}/pause")
def pause_ocr_job(job_id: str, principal: Principal = Depends(_require_user)):
    """Pause a running OCR job after its current page completes."""
    job = database.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job['status'] not in ('processing', 'pending'):
        raise HTTPException(status_code=400, detail=f"Job is '{job['status']}', not pausable.")
    task_queue.pause_job(job_id)
    return {"message": f"Job {job_id} will pause after the current page.", "status": "pausing"}

@app.post("/api/jobs/{job_id}/retry")
async def retry_ocr_job(job_id: str, principal: Principal = Depends(_require_user)):
    """Retries or resumes a failed or stuck OCR job."""
    _check_rate_limit(principal.rate_limit_key)
    job = database.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    settings = _load_settings()
    glm_runner, unlimited_runner, unlimited_multi_runner = _build_runners(job.get("engine_params") or {})
    asyncio.create_task(
        task_queue.process_ocr_job(
            job_id,
            glm_runner,
            unlimited_runner,
            dict(PROMPTS),
            OCR_TIMEOUT,
            settings.get("output_path", ""),
            job.get("page_range", ""),
            unlimited_multi_runner,
        )
    )

    return {"message": f"Job {job_id} retried successfully.", "status": "processing"}

# ─── Human review queue & manual per-page retry ───────────────────────────────

@app.get("/api/jobs/{job_id}/review-pages")
def get_job_review_pages(job_id: str, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), principal: Principal = Depends(_require_user)):
    """Flagged pages for one job, ordered by confidence ascending (worst first)."""
    if not database.get_job(job_id, include_pages=False):
        raise HTTPException(status_code=404, detail="Job not found")
    pages = database.list_review_pages(job_id=job_id, limit=limit, offset=offset)
    return {"pages": [_annotate_page_confidence(p) for p in pages]}

@app.get("/api/review-queue")
def get_review_queue(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), principal: Principal = Depends(_require_user)):
    """Cross-job 'needs human review' queue — backs the review UI. Audit-sampled
    pages are intentionally indistinguishable here from genuine low-confidence
    flags (see the confidence-calibration design notes) — review_reason is never
    surfaced as 'audit_sample' to this endpoint's caller for that reason."""
    pages = database.list_review_pages(limit=limit, offset=offset)
    for p in pages:
        _annotate_page_confidence(p)
        if p.get("review_reason") == "audit_sample":
            p["review_reason"] = ""  # blind: never let the reviewer distinguish audit samples from real flags
    return {"pages": pages}

@app.get("/api/jobs/{job_id}/pages/{page_num}/attempts")
def get_page_attempts(job_id: str, page_num: int, principal: Principal = Depends(_require_user)):
    if not database.get_job(job_id, include_pages=False):
        raise HTTPException(status_code=404, detail="Job not found")
    return {"attempts": database.get_page_attempts(job_id, page_num)}

@app.post("/api/jobs/{job_id}/pages/{page_num}/retry")
async def retry_ocr_page(
    job_id: str, page_num: int,
    escalate: bool = Query(False, description="true = try the other engine instead of the same one"),
    principal: Principal = Depends(_require_user)
):
    """Manual per-page retry — same engine by default, or the other engine with
    ?escalate=true. Safe to call on an already-completed job (only this page's row
    changes; the job's overall status is untouched)."""
    _check_rate_limit(principal.rate_limit_key)
    job = database.get_job(job_id, include_pages=False)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    engine_params = job.get("engine_params") or {}
    glm_runner, unlimited_runner, _ = _build_runners(engine_params)
    prompt = job.get("custom_prompt") or PROMPTS.get(job.get("prompt_mode"), PROMPTS["document"])

    try:
        updated = await task_queue.retry_single_page(
            job_id, page_num, job["engine"], glm_runner, unlimited_runner,
            prompt, job["tiling"], OCR_TIMEOUT, escalate_other_engine=escalate,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    if updated is None:
        raise HTTPException(status_code=404, detail="Page not found")
    return _annotate_page_confidence(updated)

@app.post("/api/jobs/{job_id}/pages/{page_num}/confirm-correct")
def confirm_page_correct(job_id: str, page_num: int, principal: Principal = Depends(_require_user)):
    """Fast 'nothing wrong here' close-out for a review-queue item — no reason
    required, so a reviewer who finds nothing wrong can close it out immediately,
    exactly as fast as if they'd known in advance it was fine."""
    page = database.get_page(job_id, page_num)
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")
    database.upsert_page(job_id, page_num, status="success", needs_review=0, reviewed_at=__import__("datetime").datetime.utcnow().isoformat())
    database.refresh_job_page_counts(job_id)
    return _annotate_page_confidence(database.get_page(job_id, page_num))

@app.delete("/api/jobs/{job_id}")
async def delete_ocr_job(job_id: str, principal: Principal = Depends(_require_user)):
    """Deletes an OCR job and removes its associated uploaded file from disk."""
    if task_queue.is_job_active(job_id):
        task_queue.cancel_job(job_id)
        await asyncio.sleep(0.15)
    success = database.delete_job(job_id)
    if not success:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"message": f"Job {job_id} deleted successfully."}

# ─── LEGACY SYNCHRONOUS ENDPOINTS (For backward compatibility) ───────────────

@app.post("/api/ocr")
async def ocr_single_page(
    file: UploadFile = File(...),
    page: int = Query(1, ge=1),
    scale: float = Query(None),
    tiling: bool = Query(True),
    prompt_mode: str = Query("document"),
    custom_prompt: str = Query(None),
    engine: str = Query("glm", pattern="^(unlimited|glm)$"),
    principal: Principal = Depends(_require_user)
):
    _check_rate_limit(principal.rate_limit_key)

    filename_lower = file.filename.lower()
    supported_extensions = (".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp")
    if not filename_lower.endswith(supported_extensions):
        raise HTTPException(status_code=400, detail=f"Supported extensions: {supported_extensions}")

    MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_MB", "200")) * 1024 * 1024
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"File exceeds {MAX_UPLOAD_BYTES // (1024*1024)} MB upload limit.")

    is_image = not filename_lower.endswith(".pdf")
    render_scale = scale if scale is not None else DEFAULT_RENDER_SCALE

    if is_image:
        if page > 1:
            raise HTTPException(status_code=400, detail="Image files only contain 1 page.")
        num_pages = 1
        try:
            pil_image = Image.open(io.BytesIO(content))
            if pil_image.mode != "RGB":
                pil_image = pil_image.convert("RGB")
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"Invalid Image file: {str(e)}")
    else:
        def _render_pdf():
            try:
                pdf = pdfium.PdfDocument(content)
            except Exception as e:
                raise HTTPException(status_code=422, detail=f"Invalid PDF file: {str(e)}")
            try:
                n = len(pdf)
                if page > n:
                    raise HTTPException(status_code=400, detail=f"Requested page {page} out of {n}.")
                pdf_page = pdf.get_page(page - 1)
                bitmap = pdf_page.render(scale=render_scale)
                img = bitmap.to_pil()
                pdf_page.close()
                bitmap.close()
                return img, n
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Failed rendering PDF page: {str(e)}")
            finally:
                pdf.close()

        pil_image, num_pages = await asyncio.to_thread(_render_pdf)

    if is_image and (prompt_mode == "captcha" or custom_prompt):
        pil_image = ImageOps.expand(pil_image, border=15, fill=(255, 255, 255))
        w, h = pil_image.size
        if w < 400:
            factor = max(3, 400 // w)
            pil_image = pil_image.resize((w * factor, h * factor), Image.LANCZOS)
        pil_image = ImageEnhance.Contrast(pil_image).enhance(1.8)
        pil_image = pil_image.filter(ImageFilter.SHARPEN)

    prompt = custom_prompt if custom_prompt else PROMPTS.get(prompt_mode, PROMPTS["document"])
    is_captcha = prompt_mode == "captcha" or bool(custom_prompt)
    if prompt_mode == "document":
        ocr_options = None
    elif is_captcha:
        ocr_options = {
            "num_ctx": 2048,
            "temperature": 0.0,
            "num_predict": 15,
            "repeat_penalty": 2.0,
            "stop": ["\n", "```", "I will", "Here ", "The text"],
        }
    else:
        ocr_options = {"num_ctx": 2048, "temperature": 0.0, "num_predict": 256, "repeat_penalty": 1.3}

    try:
        if engine == "unlimited":
            _legacy_long_prompts = bool(_load_settings().get("enable_unlimited_long_prompts", config.ENABLE_UNLIMITED_LONG_PROMPTS))
            raw_text = await run_unlimited_ocr_for_image(pil_image, prompt, OCR_TIMEOUT, long_prompts_enabled=_legacy_long_prompts)
        else:
            raw_text = await run_ocr_for_image(pil_image, prompt, tiling, OCR_TIMEOUT, ocr_options)
            if is_captcha:
                len_match = re.search(r'\b(\d+)\s+(?:alphanumeric\s+)?char', prompt, re.IGNORECASE)
                raw_text = clean_captcha_result(raw_text, expected_len=int(len_match.group(1)) if len_match else None)
    except gpu_ocr_client.GPUServiceTimeout:
        raise HTTPException(status_code=504, detail=f"OCR request timed out after {OCR_TIMEOUT}s.")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"OCR engine error ({engine}): {str(e)}")

    return {
        "filename": file.filename,
        "page": page,
        "total_pages": num_pages,
        "render_scale": render_scale,
        "text": raw_text
    }

# ─── SPA catch-all (must stay LAST so it never shadows an /api/* route) ──────
# Public and unauthenticated by design: the React shell needs to render its own
# login redirect and handle the OIDC /callback route before any token exists.
# The real security boundary is every /api/* call the shell makes, not this route.
_FRONTEND_INDEX_HTML = os.path.join(_FRONTEND_DIST_DIR, "index.html")


@app.get("/{full_path:path}", response_class=HTMLResponse, include_in_schema=False)
def spa_catch_all(full_path: str):
    if not os.path.exists(_FRONTEND_INDEX_HTML):
        return HTMLResponse(
            content="<h1>GLM-OCR Service</h1><p>Frontend build missing — run `npm run build` in frontend/.</p>",
            status_code=404
        )
    with open(_FRONTEND_INDEX_HTML, "r", encoding="utf-8") as f:
        return f.read()
