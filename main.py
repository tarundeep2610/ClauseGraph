import os
import io
import re
import sys
import base64
import uuid
import requests
import secrets
import asyncio
import pypdfium2 as pdfium
from fastapi import FastAPI, UploadFile, File, HTTPException, Depends, Query, BackgroundTasks
from fastapi.responses import HTMLResponse, FileResponse, Response
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

app = FastAPI(
    title="GLM-OCR Service",
    description="A standalone, production-ready OCR service with fail-safe background persistence.",
    version="1.1.0"
)

# Enable CORS for convenience
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

security = HTTPBasic()

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

def ensure_ollama_model_on_demand():
    """Pulls glm-ocr in Ollama ONLY when a user actively runs GLM-OCR and the model is not yet pulled."""
    ollama_base = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    try:
        tags_resp = requests.get(f"{ollama_base}/api/tags", timeout=3)
        if tags_resp.status_code == 200:
            models = [m.get("name") for m in tags_resp.json().get("models", [])]
            if not any("glm-ocr" in m for m in models):
                print("[Service] 📥 On-demand GLM-OCR request received. Pulling 'glm-ocr' model in Ollama...")
                pull_resp = requests.post(f"{ollama_base}/api/pull", json={"name": "glm-ocr", "stream": False}, timeout=300)
                if pull_resp.status_code == 200:
                    print("[Service] ✅ 'glm-ocr' model successfully pulled into Ollama!")
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

def run_unlimited_ocr_for_image(pil_image: Image.Image, prompt: str, timeout: int) -> str:
    if not _UNLIMITED_OCR_AVAILABLE or unlimited_ocr_engine is None:
        raise Exception("unlimited_ocr_engine is not installed.")
    if not unlimited_ocr_engine.is_loaded():
        err = unlimited_ocr_engine.get_load_error() or "Model not loaded."
        raise Exception(f"Unlimited-OCR model failed to load at startup: {err}")

    effective_prompt = "document parsing." if len(prompt) > 80 else prompt
    return unlimited_ocr_engine.run_ocr(pil_image, prompt=effective_prompt)

@app.on_event("startup")
async def startup_event():
    database.init_db()
    task_queue.resume_interrupted_jobs(
        run_ocr_for_image,
        run_unlimited_ocr_for_image,
        PROMPTS,
        OCR_TIMEOUT
    )

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
        "config": {
            "render_scale": DEFAULT_RENDER_SCALE,
            "ollama_endpoint": OLLAMA_URL
        }
    }

# ─── FAIL-SAFE PERSISTENT JOB API ENDPOINTS ───────────────────────────────────

@app.post("/api/jobs")
async def submit_ocr_job(
    file: UploadFile = File(..., description="PDF or Image file for background OCR processing"),
    scale: float = Query(None, description="Custom render scale"),
    tiling: bool = Query(True, description="Enable tiling"),
    prompt_mode: str = Query("document", description="Prompt mode"),
    custom_prompt: str = Query(None, description="Custom prompt override"),
    engine: str = Query("unlimited", description="OCR engine ('unlimited' or 'glm')"),
    username: str = Depends(authenticate_user)
):
    """
    Submits a document for fail-safe background processing.
    Returns immediately with job_id. Processing continues even if browser tab is closed.
    """
    filename = file.filename
    filename_lower = filename.lower()
    supported_extensions = (".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp")
    if not filename_lower.endswith(supported_extensions):
        raise HTTPException(status_code=400, detail=f"Supported file extensions: {supported_extensions}")

    content = await file.read()
    job_id = str(uuid.uuid4())
    is_pdf = filename_lower.endswith(".pdf")
    file_type = "pdf" if is_pdf else "image"

    # Save uploaded file to persistent storage
    database.init_db()
    saved_filename = f"{job_id}_{filename}"
    saved_filepath = os.path.join(database.UPLOADS_DIR, saved_filename)
    with open(saved_filepath, "wb") as f:
        f.write(content)

    # Determine page count
    if is_pdf:
        try:
            pdf = pdfium.PdfDocument(saved_filepath)
            total_pages = len(pdf)
        except Exception as e:
            if os.path.exists(saved_filepath):
                os.remove(saved_filepath)
            raise HTTPException(status_code=422, detail=f"Invalid PDF file: {e}")
    else:
        total_pages = 1

    render_scale = scale if scale is not None else DEFAULT_RENDER_SCALE

    # Register job in DB
    job = database.create_job(
        job_id=job_id,
        filename=filename,
        file_path=saved_filepath,
        file_type=file_type,
        engine=engine,
        scale=render_scale,
        tiling=tiling,
        prompt_mode=prompt_mode,
        custom_prompt=custom_prompt,
        total_pages=total_pages
    )

    # Launch non-blocking background processing task
    asyncio.create_task(
        task_queue.process_ocr_job(
            job_id,
            run_ocr_for_image,
            run_unlimited_ocr_for_image,
            PROMPTS,
            OCR_TIMEOUT
        )
    )

    return {
        "job_id": job_id,
        "filename": filename,
        "total_pages": total_pages,
        "status": "pending",
        "message": "OCR job submitted successfully and is running in background."
    }

@app.get("/api/jobs")
def list_ocr_jobs(limit: int = Query(50, ge=1, le=200), username: str = Depends(authenticate_user)):
    """Lists all past and current OCR jobs for the UI Document History Panel."""
    return database.list_jobs(limit=limit)

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
        filename=filename,
        headers={"Content-Disposition": f'{disposition}; filename="{filename}"'}
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
        headers={"Content-Disposition": f'attachment; filename="{out_filename}"'}
    )

@app.post("/api/jobs/{job_id}/retry")
def retry_ocr_job(job_id: str, username: str = Depends(authenticate_user)):
    """Retries or resumes a failed or stuck OCR job."""
    job = database.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    asyncio.create_task(
        task_queue.process_ocr_job(
            job_id,
            run_ocr_for_image,
            run_unlimited_ocr_for_image,
            PROMPTS,
            OCR_TIMEOUT
        )
    )

    return {"message": f"Job {job_id} retried successfully.", "status": "processing"}

@app.delete("/api/jobs/{job_id}")
def delete_ocr_job(job_id: str, username: str = Depends(authenticate_user)):
    """Deletes an OCR job and removes its associated uploaded file from disk."""
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
    engine: str = Query("glm"),
    username: str = Depends(authenticate_user)
):
    filename_lower = file.filename.lower()
    supported_extensions = (".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp")
    if not filename_lower.endswith(supported_extensions):
        raise HTTPException(status_code=400, detail=f"Supported extensions: {supported_extensions}")

    content = await file.read()
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
        try:
            pdf = pdfium.PdfDocument(content)
            num_pages = len(pdf)
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"Invalid PDF file: {str(e)}")

        if page > num_pages:
            raise HTTPException(status_code=400, detail=f"Requested page {page} out of {num_pages}.")

        try:
            pdf_page = pdf.get_page(page - 1)
            bitmap = pdf_page.render(scale=render_scale)
            pil_image = bitmap.to_pil()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed rendering PDF page: {str(e)}")

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
            raw_text = run_unlimited_ocr_for_image(pil_image, prompt, OCR_TIMEOUT)
        else:
            raw_text = run_ocr_for_image(pil_image, prompt, tiling, OCR_TIMEOUT, options=ocr_options)
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
