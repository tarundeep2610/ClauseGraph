import io
import sys
import time
import base64
import uuid
import asyncio
import traceback
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Depends
from pydantic import BaseModel
from PIL import Image
import secrets as secrets_mod

import config

try:
    import unlimited_ocr_engine
    _UNLIMITED_OCR_AVAILABLE = True
except ImportError:
    unlimited_ocr_engine = None
    _UNLIMITED_OCR_AVAILABLE = False
    print("[GPU-OCR] unlimited_ocr_engine failed to import.", file=sys.stderr)

_jobs: dict = {}  # job_id -> {"status", "result", "error", "created_at"}
_inference_semaphore = asyncio.Semaphore(config.GPU_OCR_MAX_CONCURRENT_JOBS)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    yield


app = FastAPI(title="GPU OCR Inference Service", lifespan=_lifespan)


def verify_shared_secret(x_gpu_ocr_key: str = Header(None)):
    if not x_gpu_ocr_key or not secrets_mod.compare_digest(x_gpu_ocr_key, config.GPU_OCR_SHARED_SECRET):
        raise HTTPException(status_code=401, detail="Invalid or missing shared secret.")
    return True


class SubmitJobPayload(BaseModel):
    engine: str = "unlimited"
    image_b64: str
    prompt: str | None = None
    base_size: int = 1024
    timeout: int = 600


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "unlimited_ocr_available": _UNLIMITED_OCR_AVAILABLE,
        "model_loaded": bool(_UNLIMITED_OCR_AVAILABLE and unlimited_ocr_engine.is_loaded()),
        "active_jobs": sum(1 for j in _jobs.values() if j["status"] in ("pending", "processing")),
    }


async def _run_job(job_id: str, payload: SubmitJobPayload):
    async with _inference_semaphore:
        _jobs[job_id]["status"] = "processing"
        try:
            image_bytes = base64.b64decode(payload.image_b64)
            pil_image = Image.open(io.BytesIO(image_bytes))
            if pil_image.mode != "RGB":
                pil_image = pil_image.convert("RGB")

            result = await asyncio.to_thread(
                unlimited_ocr_engine.run_ocr,
                pil_image,
                payload.prompt,
                payload.base_size,
                payload.timeout,
            )
            _jobs[job_id]["status"] = "done"
            _jobs[job_id]["result"] = result
        except Exception as e:
            _jobs[job_id]["status"] = "failed"
            _jobs[job_id]["error"] = f"{e}\n{traceback.format_exc()}"


@app.post("/api/v1/jobs", status_code=202, dependencies=[Depends(verify_shared_secret)])
async def submit_job(payload: SubmitJobPayload):
    if payload.engine != "unlimited":
        raise HTTPException(status_code=400, detail="This service only serves engine='unlimited'.")
    if not _UNLIMITED_OCR_AVAILABLE:
        raise HTTPException(status_code=503, detail="unlimited_ocr_engine is not available on this host.")

    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"status": "pending", "result": None, "error": None, "created_at": time.time()}
    asyncio.create_task(_run_job(job_id, payload))
    return {"job_id": job_id}


@app.get("/api/v1/jobs/{job_id}", dependencies=[Depends(verify_shared_secret)])
async def get_job(job_id: str):
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return {"status": job["status"], "result": job["result"], "error": job["error"]}


@app.delete("/api/v1/jobs/{job_id}", dependencies=[Depends(verify_shared_secret)])
async def delete_job(job_id: str):
    if job_id not in _jobs:
        raise HTTPException(status_code=404, detail="Job not found.")
    del _jobs[job_id]
    return {"message": f"Job {job_id} removed."}
