import io
import time
import base64

import pytest
from PIL import Image


def _payload():
    img = Image.new("RGB", (10, 10), color="white")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return {
        "engine": "unlimited",
        "image_b64": base64.b64encode(buf.getvalue()).decode("ascii"),
        "prompt": "test prompt",
        "base_size": 1024,
        "timeout": 30,
    }


def test_submit_without_key_401(client):
    resp = client.post("/api/v1/jobs", json=_payload())
    assert resp.status_code == 401


def test_submit_with_wrong_key_401(client):
    resp = client.post("/api/v1/jobs", json=_payload(), headers={"x-gpu-ocr-key": "wrong"})
    assert resp.status_code == 401


def test_submit_with_correct_key_returns_job_id(client, auth_headers, fake_engine):
    fake_engine.script = ["hello result"]
    resp = client.post("/api/v1/jobs", json=_payload(), headers=auth_headers)
    assert resp.status_code == 202
    assert "job_id" in resp.json()


def test_job_transitions_to_done_with_result(client, auth_headers, fake_engine):
    fake_engine.script = ["extracted text"]
    job_id = client.post("/api/v1/jobs", json=_payload(), headers=auth_headers).json()["job_id"]

    deadline = time.time() + 5
    status = None
    while time.time() < deadline:
        resp = client.get(f"/api/v1/jobs/{job_id}", headers=auth_headers)
        status = resp.json()
        if status["status"] == "done":
            break
        time.sleep(0.05)

    assert status["status"] == "done"
    assert status["result"] == "extracted text"


def test_job_failure_reports_error_and_service_keeps_serving(client, auth_headers, fake_engine):
    fake_engine.script = [RuntimeError("CUDA out of memory")]
    job_id = client.post("/api/v1/jobs", json=_payload(), headers=auth_headers).json()["job_id"]

    deadline = time.time() + 5
    status = None
    while time.time() < deadline:
        resp = client.get(f"/api/v1/jobs/{job_id}", headers=auth_headers)
        status = resp.json()
        if status["status"] == "failed":
            break
        time.sleep(0.05)

    assert status["status"] == "failed"
    assert "CUDA out of memory" in status["error"]

    # the process itself must still be serving requests
    resp = client.get("/health")
    assert resp.status_code == 200


def test_get_unknown_job_404(client, auth_headers):
    resp = client.get("/api/v1/jobs/does-not-exist", headers=auth_headers)
    assert resp.status_code == 404


def test_concurrency_cap_is_respected(client, auth_headers, fake_engine, monkeypatch):
    """GPU_OCR_MAX_CONCURRENT_JOBS=1 (set in conftest) — a second job must not start
    inference until the first releases the semaphore."""
    max_concurrent_seen = {"value": 0}
    current = {"value": 0}

    def _slow_run_ocr(pil_image, prompt, base_size, timeout):
        current["value"] += 1
        max_concurrent_seen["value"] = max(max_concurrent_seen["value"], current["value"])
        time.sleep(0.3)
        current["value"] -= 1
        return "done text"

    fake_engine.run_ocr = _slow_run_ocr

    job_a = client.post("/api/v1/jobs", json=_payload(), headers=auth_headers).json()["job_id"]
    job_b = client.post("/api/v1/jobs", json=_payload(), headers=auth_headers).json()["job_id"]

    deadline = time.time() + 5
    while time.time() < deadline:
        sa = client.get(f"/api/v1/jobs/{job_a}", headers=auth_headers).json()["status"]
        sb = client.get(f"/api/v1/jobs/{job_b}", headers=auth_headers).json()["status"]
        if sa == "done" and sb == "done":
            break
        time.sleep(0.05)

    assert max_concurrent_seen["value"] == 1


def test_cancel_all_stops_queued_job_and_resets_worker(client, auth_headers, fake_engine):
    """POST /api/v1/jobs/cancel-all must: (1) cancel a job still queued behind
    the concurrency semaphore before it ever reaches run_ocr, and (2) call
    cancel_current_work() to stop whatever's actively running right now -
    used by the CPU side's Stop All action so GPU compute is actually freed
    instead of finishing an orphaned request nobody will read."""
    def _blocking_run_ocr(pil_image, prompt, base_size, timeout):
        fake_engine.calls.append((prompt, base_size, timeout))
        time.sleep(1.0)
        return "first job result"

    fake_engine.run_ocr = _blocking_run_ocr

    job_a = client.post("/api/v1/jobs", json=_payload(), headers=auth_headers).json()["job_id"]
    deadline = time.time() + 5
    while time.time() < deadline:
        if client.get(f"/api/v1/jobs/{job_a}", headers=auth_headers).json()["status"] == "processing":
            break
        time.sleep(0.02)

    # GPU_OCR_MAX_CONCURRENT_JOBS=1 (conftest) - job_b is now stuck waiting on
    # the semaphore, never having reached run_ocr.
    job_b = client.post("/api/v1/jobs", json=_payload(), headers=auth_headers).json()["job_id"]

    resp = client.post("/api/v1/jobs/cancel-all", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["cancelled"] == 2
    assert fake_engine.cancel_calls == 1

    # Regression: job_b was cancelled while still waiting to *acquire* the
    # semaphore (never entered the `async with` body) - it must still end up
    # marked "cancelled", not left stuck at "pending" forever (which made
    # /health's active_jobs count never come back down after Stop All).
    deadline = time.time() + 5
    status_b = None
    while time.time() < deadline:
        status_b = client.get(f"/api/v1/jobs/{job_b}", headers=auth_headers).json()["status"]
        if status_b == "cancelled":
            break
        time.sleep(0.02)
    assert status_b == "cancelled", f"job_b must be marked cancelled, not stuck at {status_b!r}"

    time.sleep(1.3)  # let job_a's blocking call finish naturally so nothing leaks past the test
    assert len(fake_engine.calls) == 1, "the still-queued job must never reach run_ocr after being cancelled"
