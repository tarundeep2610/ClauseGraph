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

_jobs: dict = {}  # job_id -> {"status", "result", "raw_result", "error", "created_at"}
_job_tasks: dict = {}  # job_id -> asyncio.Task, so cancel-all can stop anything not yet running
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
    image_b64: str | None = None
    image_b64s: list[str] | None = None  # batch mode: several page images, one infer_multi() call
    prompt: str | None = None
    base_size: int = 1024
    timeout: int = 600
    # Switches Unlimited-OCR into its "gundam" config (image_size=640,
    # per-region crops) instead of "base" — confirmed (see the project
    # plan) to be what actually makes the model emit <|det|> layout tags.
    # Single-image only; infer_multi() (image_b64s) has no crop_mode.
    crop_mode: bool = False


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "unlimited_ocr_available": _UNLIMITED_OCR_AVAILABLE,
        "model_loaded": bool(_UNLIMITED_OCR_AVAILABLE and unlimited_ocr_engine.is_loaded()),
        "active_jobs": sum(1 for j in _jobs.values() if j["status"] in ("pending", "processing")),
    }


def _decode_image(b64: str) -> Image.Image:
    pil_image = Image.open(io.BytesIO(base64.b64decode(b64)))
    if pil_image.mode != "RGB":
        pil_image = pil_image.convert("RGB")
    return pil_image


async def _run_job(job_id: str, payload: SubmitJobPayload):
    # The try/except must wrap the semaphore acquisition too, not just the
    # work inside it - a job cancelled while still waiting for a concurrency
    # slot (e.g. every "Stop All" while more jobs are queued than
    # GPU_OCR_MAX_CONCURRENT_JOBS allows to run at once) raises CancelledError
    # at the `async with` itself, before ever entering the block. With the
    # handlers scoped inside the `async with`, that case skipped them
    # entirely, leaving the job stuck at its initial "pending" status forever
    # - and /health's active_jobs count (which treats "pending" as active)
    # never came back down after Stop All.
    try:
        async with _inference_semaphore:
            _jobs[job_id]["status"] = "processing"
            if payload.image_b64s:
                pil_images = [_decode_image(b) for b in payload.image_b64s]
                result = await asyncio.to_thread(
                    unlimited_ocr_engine.run_ocr_multi,
                    pil_images,
                    payload.prompt,
                    payload.timeout,
                )
                _jobs[job_id]["status"] = "done"
                _jobs[job_id]["result"] = result
            else:
                pil_image = _decode_image(payload.image_b64)
                result, raw_result = await asyncio.to_thread(
                    unlimited_ocr_engine.run_ocr_with_raw,
                    pil_image,
                    payload.prompt,
                    payload.base_size,
                    payload.timeout,
                    payload.crop_mode,
                )
                _jobs[job_id]["status"] = "done"
                _jobs[job_id]["result"] = result
                _jobs[job_id]["raw_result"] = raw_result
    except asyncio.CancelledError:
        _jobs[job_id]["status"] = "cancelled"
        raise
    except Exception as e:
        _jobs[job_id]["status"] = "failed"
        _jobs[job_id]["error"] = f"{e}\n{traceback.format_exc()}"
    finally:
        _job_tasks.pop(job_id, None)


@app.post("/api/v1/jobs", status_code=202, dependencies=[Depends(verify_shared_secret)])
async def submit_job(payload: SubmitJobPayload):
    if payload.engine != "unlimited":
        raise HTTPException(status_code=400, detail="This service only serves engine='unlimited'.")
    if not _UNLIMITED_OCR_AVAILABLE:
        raise HTTPException(status_code=503, detail="unlimited_ocr_engine is not available on this host.")
    if not payload.image_b64 and not payload.image_b64s:
        raise HTTPException(status_code=400, detail="Provide either image_b64 or image_b64s.")

    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"status": "pending", "result": None, "raw_result": None, "error": None, "created_at": time.time()}
    _job_tasks[job_id] = asyncio.create_task(_run_job(job_id, payload))
    return {"job_id": job_id}


@app.get("/api/v1/jobs/{job_id}", dependencies=[Depends(verify_shared_secret)])
async def get_job(job_id: str):
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return {
        "status": job["status"], "result": job["result"],
        "raw_result": job.get("raw_result"), "error": job["error"],
    }


@app.delete("/api/v1/jobs/{job_id}", dependencies=[Depends(verify_shared_secret)])
async def delete_job(job_id: str):
    if job_id not in _jobs:
        raise HTTPException(status_code=404, detail="Job not found.")
    del _jobs[job_id]
    return {"message": f"Job {job_id} removed."}


@app.post("/api/v1/jobs/cancel-all", dependencies=[Depends(verify_shared_secret)])
async def cancel_all_jobs():
    """Stops every in-flight job on this service: cancels any job still
    waiting on the concurrency semaphore (never gets a chance to start), and
    kills whatever the inference worker is actively running right now (the
    in-flight call's own await then resolves within about one poll interval
    once the worker is gone - see unlimited_ocr_engine.cancel_current_work())."""
    count = 0
    for job_id, task in list(_job_tasks.items()):
        if not task.done():
            task.cancel()
            count += 1
    if _UNLIMITED_OCR_AVAILABLE:
        unlimited_ocr_engine.cancel_current_work()
    return {"message": f"Cancelled {count} in-flight job(s) and reset the inference worker.", "cancelled": count}
