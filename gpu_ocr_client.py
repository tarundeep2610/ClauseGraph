import io
import time
import base64
import asyncio

import httpx

import config


class GPUServiceError(Exception):
    pass


class GPUServiceUnreachable(GPUServiceError):
    pass


class GPUServiceAuthError(GPUServiceError):
    pass


class GPUServiceTimeout(GPUServiceError):
    pass


class GPUServiceJobFailed(GPUServiceError):
    pass


def _headers() -> dict:
    return {"x-gpu-ocr-key": config.GPU_OCR_SHARED_SECRET}


async def check_health() -> dict:
    result = {"ollama": "unreachable", "gpu_ocr_service": "unreachable"}
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(config.OLLAMA_BASE_URL, headers=_headers())
        result["ollama"] = "online" if resp.status_code == 200 else "offline"
    except Exception:
        pass
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{config.GPU_OCR_BASE_URL}/health")
        result["gpu_ocr_service"] = "online" if resp.status_code == 200 else "offline"
    except Exception:
        pass
    return result


# ─── Unlimited-OCR (job-queue API on the GPU box) ─────────────────────────────

async def submit_unlimited_job(image_bytes: bytes, prompt: str, base_size: int, timeout: int) -> str:
    payload = {
        "engine": "unlimited",
        "image_b64": base64.b64encode(image_bytes).decode("ascii"),
        "prompt": prompt,
        "base_size": base_size,
        "timeout": timeout,
    }
    try:
        async with httpx.AsyncClient(timeout=config.GPU_OCR_CONNECT_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                f"{config.GPU_OCR_BASE_URL}/api/v1/jobs", json=payload, headers=_headers()
            )
    except httpx.RequestError as e:
        raise GPUServiceUnreachable(f"Cannot reach GPU OCR service: {e}") from e

    if resp.status_code == 401:
        raise GPUServiceAuthError("GPU OCR service rejected the shared secret.")
    if resp.status_code >= 400:
        raise GPUServiceUnreachable(f"GPU OCR service returned {resp.status_code}: {resp.text}")

    return resp.json()["job_id"]


async def poll_until_done(job_id: str, poll_timeout: float) -> str:
    deadline = time.monotonic() + poll_timeout
    async with httpx.AsyncClient(timeout=config.GPU_OCR_CONNECT_TIMEOUT_SECONDS) as client:
        while True:
            if time.monotonic() > deadline:
                raise GPUServiceTimeout(f"GPU OCR job {job_id} did not complete within {poll_timeout}s.")
            try:
                resp = await client.get(
                    f"{config.GPU_OCR_BASE_URL}/api/v1/jobs/{job_id}", headers=_headers()
                )
            except httpx.RequestError as e:
                raise GPUServiceUnreachable(f"Cannot reach GPU OCR service while polling: {e}") from e

            if resp.status_code == 401:
                raise GPUServiceAuthError("GPU OCR service rejected the shared secret.")
            if resp.status_code == 404:
                raise GPUServiceJobFailed(f"GPU OCR job {job_id} vanished (not found).")

            data = resp.json()
            status = data.get("status")
            if status == "done":
                return data.get("result") or ""
            if status == "failed":
                raise GPUServiceJobFailed(data.get("error") or "GPU OCR job failed with no error detail.")

            await asyncio.sleep(config.GPU_OCR_POLL_INTERVAL_SECONDS)


async def run_unlimited_ocr(pil_image, prompt: str, timeout: int, base_size: int = 1024) -> str:
    if pil_image.mode != "RGB":
        pil_image = pil_image.convert("RGB")
    buf = io.BytesIO()
    pil_image.save(buf, format="JPEG", quality=85)
    job_id = await submit_unlimited_job(buf.getvalue(), prompt, base_size, timeout)
    poll_timeout = max(timeout, config.GPU_OCR_POLL_TIMEOUT_SECONDS)
    return await poll_until_done(job_id, poll_timeout)


# ─── GLM (direct call to the Caddy-fronted Ollama on the GPU box) ─────────────

async def _ollama_generate(image_b64: str, prompt: str, options: dict, timeout: int) -> str:
    payload = {"model": "glm-ocr", "prompt": prompt, "images": [image_b64], "stream": False, "options": options}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{config.OLLAMA_BASE_URL}/api/generate", json=payload, headers=_headers()
            )
    except httpx.RequestError as e:
        raise GPUServiceUnreachable(f"Cannot reach Ollama proxy: {e}") from e

    if resp.status_code == 401:
        raise GPUServiceAuthError("Ollama proxy rejected the shared secret.")
    if resp.status_code != 200:
        raise GPUServiceJobFailed(f"Ollama returned an error: {resp.text}")
    return resp.json().get("response", "").strip()


async def ensure_ollama_model_ready() -> None:
    """Best-effort on-demand pull of the glm-ocr model — mirrors the old synchronous check."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            tags_resp = await client.get(f"{config.OLLAMA_BASE_URL}/api/tags", headers=_headers())
        if tags_resp.status_code == 200:
            models = [m.get("name") for m in tags_resp.json().get("models", [])]
            if any("glm-ocr" in m for m in models):
                return
        async with httpx.AsyncClient(timeout=300.0) as client:
            await client.post(
                f"{config.OLLAMA_BASE_URL}/api/pull", json={"name": "glm-ocr", "stream": False},
                headers=_headers(),
            )
    except httpx.RequestError:
        pass  # best-effort — the subsequent generate call will surface a clearer error


async def run_glm_ocr(pil_image, prompt: str, tiling: bool, timeout: int, options: dict | None = None) -> str:
    default_options = {"num_ctx": 8192, "temperature": 0.0, "num_predict": 2048, "repeat_penalty": 1.2}
    resolved_options = options if options is not None else default_options

    await ensure_ollama_model_ready()

    if not tiling:
        buf = io.BytesIO()
        pil_image.save(buf, format="JPEG", quality=85)
        img_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return await _ollama_generate(img_b64, prompt, resolved_options, timeout)

    width, height = pil_image.size
    slice_1 = pil_image.crop((0, 0, width, int(height * 0.42)))
    slice_2 = pil_image.crop((0, int(height * 0.30), width, int(height * 0.72)))
    slice_3 = pil_image.crop((0, int(height * 0.60), width, height))
    tiling_options = {"num_ctx": 4096, "temperature": 0.0, "num_predict": 1024, "repeat_penalty": 1.2}

    results = []
    for half in (slice_1, slice_2, slice_3):
        buf = io.BytesIO()
        half.save(buf, format="JPEG", quality=85)
        img_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        results.append(await _ollama_generate(img_b64, prompt, tiling_options, timeout))

    return (
        f"### Document Section: Top Half (Metadata)\n\n{results[0]}\n\n---\n\n"
        f"### Document Section: Middle (Table Grid)\n\n{results[1]}\n\n---\n\n"
        f"### Document Section: Bottom Half (Descriptions & Verification)\n\n{results[2]}"
    )
