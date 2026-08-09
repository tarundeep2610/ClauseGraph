import os
import io
import re
import sys
import json
import time
import signal
import base64
import uuid
import math
import collections
import urllib.parse
import requests
import secrets
import asyncio
from contextlib import asynccontextmanager
import pypdfium2 as pdfium
from fastapi import FastAPI, UploadFile, File, HTTPException, Depends, Query, BackgroundTasks
from fastapi.responses import HTMLResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

import database
import task_queue

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

@asynccontextmanager
async def _lifespan(app: FastAPI):
    global _idle_watcher_task
    database.init_db()
    _load_custom_prompts()
    settings = _load_settings()
    await task_queue.resume_interrupted_jobs(
        run_ocr_for_image,
        run_unlimited_ocr_for_image,
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

_ASSETS_DIR = os.path.join(os.path.dirname(__file__), "assets")
app.mount("/assets", StaticFiles(directory=_ASSETS_DIR), name="assets")

# CORS: restrict to localhost origins only (wildcard + credentials is a spec violation)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

security = HTTPBasic()

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

class _ActivityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        global _last_activity
        _last_activity = time.time()
        return await call_next(request)

app.add_middleware(_ActivityMiddleware)

_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' https://cdnjs.cloudflare.com 'unsafe-inline'; "
    "font-src https://cdnjs.cloudflare.com; "
    "img-src 'self' data: blob:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "object-src 'none'; "
    "base-uri 'self'"
)

class _SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = _CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=(), payment=()"
        if not request.url.path.startswith("/assets/"):
            response.headers["Cache-Control"] = "no-store"
        return response

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
    defaults = {"output_path": "", "idle_timeout_minutes": 15}
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
_RATE_LIMIT_MAX = int(os.environ.get("JOB_RATE_LIMIT", "10"))
_RATE_LIMIT_WINDOW = int(os.environ.get("JOB_RATE_LIMIT_WINDOW_SECONDS", "60"))
_rate_buckets: dict = {}  # username -> deque of submission timestamps

def _check_rate_limit(username: str):
    now = time.time()
    dq = _rate_buckets.setdefault(username, collections.deque())
    while dq and now - dq[0] > _RATE_LIMIT_WINDOW:
        dq.popleft()
    if len(dq) >= _RATE_LIMIT_MAX:
        retry_after = max(1, math.ceil(dq[0] + _RATE_LIMIT_WINDOW - now))
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: max {_RATE_LIMIT_MAX} OCR requests per {_RATE_LIMIT_WINDOW}s.",
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
OLLAMA_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434") + "/api/generate"
OCR_USER = os.environ.get("OCR_USER", "admin")
OCR_PASS = os.environ.get("OCR_PASS", "admin123")
DEFAULT_RENDER_SCALE = float(os.environ.get("RENDER_SCALE", "1.5"))
OCR_TIMEOUT = int(os.environ.get("OCR_TIMEOUT_SECONDS", "600"))

def authenticate_user(credentials: HTTPBasicCredentials = Depends(security)):
    correct_username = secrets.compare_digest(credentials.username, OCR_USER)
    correct_password = secrets.compare_digest(credentials.password, OCR_PASS)
    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=401,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username

_ollama_model_ready: bool = False


def ensure_ollama_model_on_demand():
    """Pulls glm-ocr in Ollama ONLY when a user actively runs GLM-OCR and the model is not yet pulled."""
    global _ollama_model_ready
    if _ollama_model_ready:
        return
    ollama_base = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    try:
        tags_resp = requests.get(f"{ollama_base}/api/tags", timeout=3)
        if tags_resp.status_code == 200:
            models = [m.get("name") for m in tags_resp.json().get("models", [])]
            if any("glm-ocr" in m for m in models):
                _ollama_model_ready = True
            else:
                print("[Service] 📥 On-demand GLM-OCR request received. Pulling 'glm-ocr' model in Ollama...")
                pull_resp = requests.post(f"{ollama_base}/api/pull", json={"name": "glm-ocr", "stream": False}, timeout=300)
                if pull_resp.status_code == 200:
                    print("[Service] ✅ 'glm-ocr' model successfully pulled into Ollama!")
                    _ollama_model_ready = True
    except Exception as e:
        print(f"[Service] On-demand Ollama model check note: {e}")

def run_ocr_for_image(pil_image, prompt: str, tiling: bool, timeout: int, options: dict = None) -> str:
    ensure_ollama_model_on_demand()
    default_options = {"num_ctx": 8192, "temperature": 0.0, "num_predict": 2048, "repeat_penalty": 1.2}
    resolved_options = options if options is not None else default_options

    if not tiling:
        buffered = io.BytesIO()
        pil_image.save(buffered, format="JPEG", quality=85)
        img_base64 = base64.b64encode(buffered.getvalue()).decode("utf-8")

        payload = {
            "model": "glm-ocr",
            "prompt": prompt,
            "images": [img_base64],
            "stream": False,
            "options": resolved_options,
        }
        response = requests.post(OLLAMA_URL, json=payload, timeout=timeout)
        if response.status_code != 200:
            raise Exception(f"Ollama server returned an error: {response.text}")
        return response.json().get("response", "").strip()
    else:
        width, height = pil_image.size
        slice_1 = pil_image.crop((0, 0, width, int(height * 0.42)))
        slice_2 = pil_image.crop((0, int(height * 0.30), width, int(height * 0.72)))
        slice_3 = pil_image.crop((0, int(height * 0.60), width, height))

        tiling_options = {"num_ctx": 4096, "temperature": 0.0, "num_predict": 1024, "repeat_penalty": 1.2}

        results = []
        for idx, half in enumerate([slice_1, slice_2, slice_3]):
            buffered = io.BytesIO()
            half.save(buffered, format="JPEG", quality=85)
            img_base64 = base64.b64encode(buffered.getvalue()).decode("utf-8")

            payload = {
                "model": "glm-ocr",
                "prompt": prompt,
                "images": [img_base64],
                "stream": False,
                "options": tiling_options,
            }
            response = requests.post(OLLAMA_URL, json=payload, timeout=timeout)
            if response.status_code != 200:
                raise Exception(f"Ollama server returned an error on section {idx + 1}: {response.text}")
            results.append(response.json().get("response", "").strip())

        return (
            f"### Document Section: Top Half (Metadata)\n\n"
            f"{results[0]}\n\n"
            f"---\n\n"
            f"### Document Section: Middle (Table Grid)\n\n"
            f"{results[1]}\n\n"
            f"---\n\n"
            f"### Document Section: Bottom Half (Descriptions & Verification)\n\n"
            f"{results[2]}"
        )

def run_unlimited_ocr_for_image(pil_image: Image.Image, prompt: str, timeout: int, base_size: int = 1024) -> str:
    if not _UNLIMITED_OCR_AVAILABLE or unlimited_ocr_engine is None:
        raise Exception("unlimited_ocr_engine is not installed.")
    effective_prompt = "document parsing." if len(prompt) > 80 else prompt
    return unlimited_ocr_engine.run_ocr(pil_image, prompt=effective_prompt, base_size=base_size, timeout=timeout)


_idle_watcher_task: asyncio.Task | None = None


# ─── SETTINGS & PROMPTS API ───────────────────────────────────────────────────

@app.get("/api/settings")
def get_settings(username: str = Depends(authenticate_user)):
    return _load_settings()

@app.post("/api/settings")
def save_settings(body: dict, username: str = Depends(authenticate_user)):
    current = _load_settings()
    if "output_path" in body:
        op = body["output_path"]
        if op and not os.path.isdir(op):
            raise HTTPException(
                status_code=400,
                detail=f"output_path must be an existing directory: {op!r}"
            )
    current.update({k: v for k, v in body.items() if k in ("output_path", "idle_timeout_minutes")})
    _save_settings(current)
    return current

@app.get("/api/prompts")
def get_prompts(username: str = Depends(authenticate_user)):
    return {"prompts": PROMPTS, "defaults": _DEFAULT_PROMPTS}

@app.post("/api/prompts/{mode}")
def save_prompt(mode: str, body: dict, username: str = Depends(authenticate_user)):
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
def reset_prompt(mode: str, username: str = Depends(authenticate_user)):
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

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def get_ui(username: str = Depends(authenticate_user)):
    ui_path = os.path.join(os.path.dirname(__file__), "static", "index.html")
    if not os.path.exists(ui_path):
        return HTMLResponse(
            content="<h1>GLM-OCR Service</h1><p>UI file static/index.html is missing.</p>",
            status_code=404
        )
    with open(ui_path, "r", encoding="utf-8") as f:
        return f.read()

@app.get("/health")
def health_check():
    try:
        ollama_base = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        resp = requests.get(ollama_base, timeout=2)
        ollama_status = "online" if resp.status_code == 200 else "offline"
    except Exception:
        ollama_status = "unreachable"

    return {
        "status": "healthy",
        "ollama_connection": ollama_status,
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
    username: str = Depends(authenticate_user)
):
    """
    Submits a document for fail-safe background processing.
    Returns immediately with job_id. Processing continues even if browser tab is closed.
    """
    _check_rate_limit(username)

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

    # Register job in DB (store original filename for display, safe_filename is the on-disk name)
    job = database.create_job(
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
        page_range=page_range
    )

    # Build engine-specific runners with user's hyperparameters baked in
    _glm_opts = {
        "temperature": glm_temperature,
        "num_ctx": glm_num_ctx,
        "num_predict": glm_num_predict,
        "repeat_penalty": glm_repeat_penalty,
    }
    def _glm_runner(pil_image, prompt, _tiling, timeout, captcha_override=None):
        opts = captcha_override if captcha_override is not None else _glm_opts
        return run_ocr_for_image(pil_image, prompt, _tiling, timeout, opts)

    _u_base = unlimited_base_size
    def _unlimited_runner(pil_image, prompt, timeout):
        return run_unlimited_ocr_for_image(pil_image, prompt, timeout, base_size=_u_base)

    settings = _load_settings()
    asyncio.create_task(
        task_queue.process_ocr_job(
            job_id,
            _glm_runner,
            _unlimited_runner,
            dict(PROMPTS),
            OCR_TIMEOUT,
            settings.get("output_path", ""),
            page_range
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
def list_ocr_jobs(limit: int = Query(50, ge=1, le=200), username: str = Depends(authenticate_user)):
    """Lists all past and current OCR jobs for the UI Document History Panel."""
    return database.list_jobs(limit=limit)

@app.post("/api/jobs/stop-all")
def stop_all_ocr_jobs(username: str = Depends(authenticate_user)):
    """Cancel all currently running OCR tasks and mark them paused."""
    count = task_queue.cancel_all_jobs()
    return {"message": f"Requested cancellation of {count} active job(s).", "cancelled": count}

@app.post("/api/shutdown")
async def shutdown_server(username: str = Depends(authenticate_user)):
    """Stop all OCR tasks and shut down the server process to free memory."""
    task_queue.cancel_all_jobs()
    loop = asyncio.get_running_loop()
    loop.call_later(0.5, lambda: signal.raise_signal(signal.SIGINT))
    return {"message": "Server is shutting down."}

@app.get("/api/jobs/{job_id}")
def get_ocr_job(job_id: str, username: str = Depends(authenticate_user)):
    """Retrieves full details, live progress, and extracted text for a specific job."""
    job = database.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job

@app.get("/api/jobs/{job_id}/file")
def get_job_uploaded_file(
    job_id: str,
    download: bool = Query(False, description="Set to true to force file download"),
    username: str = Depends(authenticate_user)
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
def download_ocr_result(job_id: str, username: str = Depends(authenticate_user)):
    """Downloads the extracted OCR result text as a formatted Markdown (.md) document."""
    job = database.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    pages = job.get('pages_data', [])
    filename = job['filename']
    base_name = os.path.splitext(filename)[0]

    md_lines = [
        f"# OCR Extraction Report: {filename}",
        f"- **Engine**: {job['engine'].upper()}",
        f"- **Pages**: {job['total_pages']}",
        f"- **Status**: {job['status'].upper()}",
        f"- **Created At**: {job['created_at']}",
        "\n---\n"
    ]

    for p in pages:
        md_lines.append(f"## Page {p.get('page')}")
        md_lines.append(p.get('text', ''))
        md_lines.append("\n---\n")

    markdown_content = "\n".join(md_lines)
    out_filename = f"{base_name}_ocr_result.md"

    return Response(
        content=markdown_content.encode("utf-8"),
        media_type="text/markdown",
        headers={"Content-Disposition": _content_disposition("attachment", out_filename)}
    )

@app.post("/api/jobs/{job_id}/pause")
def pause_ocr_job(job_id: str, username: str = Depends(authenticate_user)):
    """Pause a running OCR job after its current page completes."""
    job = database.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job['status'] not in ('processing', 'pending'):
        raise HTTPException(status_code=400, detail=f"Job is '{job['status']}', not pausable.")
    task_queue.pause_job(job_id)
    return {"message": f"Job {job_id} will pause after the current page.", "status": "pausing"}

@app.post("/api/jobs/{job_id}/retry")
async def retry_ocr_job(job_id: str, username: str = Depends(authenticate_user)):
    """Retries or resumes a failed or stuck OCR job."""
    _check_rate_limit(username)
    job = database.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    settings = _load_settings()
    asyncio.create_task(
        task_queue.process_ocr_job(
            job_id,
            run_ocr_for_image,
            run_unlimited_ocr_for_image,
            dict(PROMPTS),
            OCR_TIMEOUT,
            settings.get("output_path", ""),
            job.get("page_range", "")
        )
    )

    return {"message": f"Job {job_id} retried successfully.", "status": "processing"}

@app.delete("/api/jobs/{job_id}")
async def delete_ocr_job(job_id: str, username: str = Depends(authenticate_user)):
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
    username: str = Depends(authenticate_user)
):
    _check_rate_limit(username)

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
            raw_text = await asyncio.to_thread(
                run_unlimited_ocr_for_image, pil_image, prompt, OCR_TIMEOUT
            )
        else:
            raw_text = await asyncio.to_thread(
                run_ocr_for_image, pil_image, prompt, tiling, OCR_TIMEOUT, ocr_options
            )
            if is_captcha:
                len_match = re.search(r'\b(\d+)\s+(?:alphanumeric\s+)?char', prompt, re.IGNORECASE)
                raw_text = clean_captcha_result(raw_text, expected_len=int(len_match.group(1)) if len_match else None)
    except requests.exceptions.Timeout:
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
