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
    result = {"ollama": "unreachable", "gpu_ocr_service": "unreachable", "model_loaded": False, "active_jobs": None}
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
        if resp.status_code == 200:
            body = resp.json()
            result["model_loaded"] = bool(body.get("model_loaded"))
            result["active_jobs"] = body.get("active_jobs")
    except Exception:
        pass
    return result


# ─── Unlimited-OCR (job-queue API on the GPU box) ─────────────────────────────

async def submit_unlimited_job(
    image_bytes: bytes, prompt: str, base_size: int, timeout: int, crop_mode: bool = False,
) -> str:
    payload = {
        "engine": "unlimited",
        "image_b64": base64.b64encode(image_bytes).decode("ascii"),
        "prompt": prompt,
        "base_size": base_size,
        "timeout": timeout,
        "crop_mode": crop_mode,
    }
    return await _submit_job(payload)


async def submit_unlimited_multi_job(images_bytes: list, prompt: str, timeout: int) -> str:
    payload = {
        "engine": "unlimited",
        "image_b64s": [base64.b64encode(b).decode("ascii") for b in images_bytes],
        "prompt": prompt,
        "timeout": timeout,
    }
    return await _submit_job(payload)


async def _submit_job(payload: dict) -> str:
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


async def cancel_all_gpu_jobs() -> dict:
    """Best-effort request to stop every in-flight job on the GPU service right
    now (see gpu-ocr-service's POST /api/v1/jobs/cancel-all) - used by the
    CPU-side "Stop All" action so GPU compute is actually freed instead of
    running orphaned pages to completion for nobody. Deliberately never
    raises: the caller's own cancellation (task_queue.cancel_all_jobs()) must
    proceed regardless of whether the GPU service could be reached."""
    try:
        async with httpx.AsyncClient(timeout=config.GPU_OCR_CONNECT_TIMEOUT_SECONDS) as client:
            resp = await client.post(f"{config.GPU_OCR_BASE_URL}/api/v1/jobs/cancel-all", headers=_headers())
        return {"ok": resp.status_code < 400, "status_code": resp.status_code}
    except httpx.RequestError as e:
        return {"ok": False, "error": str(e)}


async def _poll_until_done_full(job_id: str, poll_timeout: float) -> dict:
    """Same polling loop as poll_until_done(), but returns the full response
    dict (status/result/raw_result/error) instead of just the "result"
    field. poll_until_done() is a thin wrapper around this, unchanged for
    its existing callers/tests; run_unlimited_ocr_with_raw() calls this
    directly to also recover "raw_result" without a second poll cycle."""
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
                return data
            if status == "failed":
                raise GPUServiceJobFailed(data.get("error") or "GPU OCR job failed with no error detail.")

            await asyncio.sleep(config.GPU_OCR_POLL_INTERVAL_SECONDS)


async def poll_until_done(job_id: str, poll_timeout: float) -> str:
    data = await _poll_until_done_full(job_id, poll_timeout)
    return data.get("result") or ""


async def run_unlimited_ocr(pil_image, prompt: str, timeout: int, base_size: int = 1024) -> str:
    if pil_image.mode != "RGB":
        pil_image = pil_image.convert("RGB")
    buf = io.BytesIO()
    pil_image.save(buf, format="JPEG", quality=85)
    job_id = await submit_unlimited_job(buf.getvalue(), prompt, base_size, timeout)
    # Deliberately NOT derived from `timeout` - the GPU service already
    # enforces `timeout` itself, including for calls queued behind another
    # one (it kills and respawns its worker rather than ever hanging
    # forever). Racing an independent client-side deadline against that only
    # abandons calls that are still legitimately in progress, orphaning them
    # and cascading into a backlog. This is purely a liveness safety net for
    # a genuinely dead/unreachable GPU service - see config.py.
    poll_timeout = config.GPU_OCR_CLIENT_SAFETY_TIMEOUT_SECONDS
    return await poll_until_done(job_id, poll_timeout)


async def run_unlimited_ocr_with_raw(
    pil_image, prompt: str, timeout: int, base_size: int = 1024, crop_mode: bool = False,
) -> "tuple[str, str | None]":
    """Same contract as run_unlimited_ocr(), but also returns the GPU
    service's raw_result field — the model's raw, pre-clean_detection_tags()
    output (see unlimited_ocr_engine.run_ocr_with_raw()), forwarded through
    gpu-ocr-service/main.py's job result. None if the GPU service didn't
    send one (an older/unpatched GPU service instance, or a batch/failure
    path that never populates it). Deliberately NOT implemented by having
    run_unlimited_ocr() delegate here (or vice versa) — run_unlimited_ocr()
    must keep calling the public poll_until_done() unchanged, since an
    existing test monkeypatches exactly that name.

    crop_mode switches Unlimited-OCR into its "gundam" config
    (image_size=640, per-region crops) — confirmed (see the project plan)
    to be what actually makes the model emit <|det|> layout tags."""
    if pil_image.mode != "RGB":
        pil_image = pil_image.convert("RGB")
    buf = io.BytesIO()
    pil_image.save(buf, format="JPEG", quality=85)
    job_id = await submit_unlimited_job(buf.getvalue(), prompt, base_size, timeout, crop_mode=crop_mode)
    poll_timeout = config.GPU_OCR_CLIENT_SAFETY_TIMEOUT_SECONDS
    data = await _poll_until_done_full(job_id, poll_timeout)
    return data.get("result") or "", data.get("raw_result")


async def run_unlimited_ocr_multi(pil_images: list, prompt: str, timeout: int) -> str:
    """Batch-mode counterpart to run_unlimited_ocr() — submits several page images
    as one infer_multi() job. No base_size param: multi-page mode only supports
    base resolution (image_size=1024, fixed server-side) per Baidu's documentation
    — not configurable here, unlike single-page run_unlimited_ocr(). See
    unlimited_ocr_engine.run_ocr_multi() for the '<PAGE>'-delimited return-text
    contract that task_queue._split_batch_output() relies on."""
    images_bytes = []
    for pil_image in pil_images:
        if pil_image.mode != "RGB":
            pil_image = pil_image.convert("RGB")
        buf = io.BytesIO()
        pil_image.save(buf, format="JPEG", quality=85)
        images_bytes.append(buf.getvalue())
    job_id = await submit_unlimited_multi_job(images_bytes, prompt, timeout)
    # Deliberately NOT derived from `timeout` - the GPU service already
    # enforces `timeout` itself, including for calls queued behind another
    # one (it kills and respawns its worker rather than ever hanging
    # forever). Racing an independent client-side deadline against that only
    # abandons calls that are still legitimately in progress, orphaning them
    # and cascading into a backlog. This is purely a liveness safety net for
    # a genuinely dead/unreachable GPU service - see config.py.
    poll_timeout = config.GPU_OCR_CLIENT_SAFETY_TIMEOUT_SECONDS
    return await poll_until_done(job_id, poll_timeout)


# ─── GLM (direct call to the Caddy-fronted Ollama on the GPU box) ─────────────

async def _ollama_generate(image_b64: str, prompt: str, options: dict, timeout: int, want_logprobs: bool = False):
    payload = {"model": "glm-ocr", "prompt": prompt, "images": [image_b64], "stream": False, "options": options}
    if want_logprobs:
        payload["logprobs"] = True
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
    data = resp.json()
    text = data.get("response", "").strip()
    if want_logprobs:
        return text, data.get("logprobs")
    return text


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


async def run_glm_ocr_with_logprobs(pil_image, prompt: str, timeout: int, options: dict | None = None):
    """S-6 verification / future token_confidence wiring: requests real per-token
    log-probabilities from Ollama's /api/generate alongside the text. Kept separate
    from run_glm_ocr() (which stays untouched and returns plain text) since this is
    unverified for vision/multimodal calls and gated behind OCR_TOKEN_CONFIDENCE_ENABLED
    — not part of the main pipeline until S-6 confirms it works and the deployed
    Ollama version supports it. Returns (text, logprobs_or_none). No tiling support:
    merging per-slice logprobs isn't worth the complexity for a still-unverified signal."""
    default_options = {"num_ctx": 8192, "temperature": 0.0, "num_predict": 2048, "repeat_penalty": 1.2}
    resolved_options = options if options is not None else default_options
    await ensure_ollama_model_ready()
    buf = io.BytesIO()
    pil_image.save(buf, format="JPEG", quality=85)
    img_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return await _ollama_generate(img_b64, prompt, resolved_options, timeout, want_logprobs=True)


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
