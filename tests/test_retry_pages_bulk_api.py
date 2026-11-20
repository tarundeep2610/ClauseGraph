import time
import uuid

import task_queue
import database


def _job_with_pages(tmp_path):
    job_id = str(uuid.uuid4())
    img_path = tmp_path / f"{job_id}.png"
    from PIL import Image
    Image.new("RGB", (10, 10), color="white").save(img_path)
    database.create_job(
        job_id=job_id, filename="t.png", file_path=str(img_path), file_type="image",
        engine="unlimited", total_pages=2,
    )
    database.upsert_page(job_id, 1, status="success", text="clean text", confidence=0.95, needs_review=0)
    database.upsert_page(job_id, 2, status="failed", text=task_queue.MANUAL_REVIEW_TEXT, error="boom")
    database.set_job_status(job_id, "completed")
    return job_id


def test_retry_pages_missing_job_404s(client, auth_headers):
    resp = client.post("/api/jobs/does-not-exist/retry-pages?scope=failed", headers=auth_headers)
    assert resp.status_code == 404


def test_retry_pages_invalid_scope_422s(client, auth_headers, tmp_path):
    job_id = _job_with_pages(tmp_path)
    resp = client.post(f"/api/jobs/{job_id}/retry-pages?scope=everything", headers=auth_headers)
    assert resp.status_code == 422


def test_retry_pages_scope_failed_succeeds_even_while_job_is_active(client, auth_headers, tmp_path, monkeypatch):
    job_id = _job_with_pages(tmp_path)
    calls = []

    async def _fake_bulk(job_id_arg, engine, run_ocr_func, run_unlimited_func, prompt, tiling, timeout, ocr_options, scope):
        calls.append((job_id_arg, scope))

    monkeypatch.setattr(task_queue, "retry_pages_bulk", _fake_bulk)
    monkeypatch.setattr(task_queue, "is_job_active", lambda jid: True)

    resp = client.post(f"/api/jobs/{job_id}/retry-pages?scope=failed", headers=auth_headers)

    assert resp.status_code == 200
    assert resp.json()["status"] == "escalating"
    time.sleep(0.2)  # let the fire-and-forget asyncio.create_task actually run
    assert calls == [(job_id, "failed")], "scope=failed must dispatch even though the job is still 'active'"


def test_retry_pages_scope_review_succeeds_even_while_job_is_active(client, auth_headers, tmp_path, monkeypatch):
    job_id = _job_with_pages(tmp_path)
    calls = []

    async def _fake_bulk(job_id_arg, engine, run_ocr_func, run_unlimited_func, prompt, tiling, timeout, ocr_options, scope):
        calls.append((job_id_arg, scope))

    monkeypatch.setattr(task_queue, "retry_pages_bulk", _fake_bulk)
    monkeypatch.setattr(task_queue, "is_job_active", lambda jid: True)

    resp = client.post(f"/api/jobs/{job_id}/retry-pages?scope=review", headers=auth_headers)

    assert resp.status_code == 200
    assert resp.json()["status"] == "escalating"
    time.sleep(0.2)  # let the fire-and-forget asyncio.create_task actually run
    assert calls == [(job_id, "review")], "scope=review must dispatch even though the job is still 'active'"


def test_retry_pages_scope_all_409s_while_job_is_active(client, auth_headers, tmp_path, monkeypatch):
    job_id = _job_with_pages(tmp_path)
    monkeypatch.setattr(task_queue, "is_job_active", lambda jid: True)

    resp = client.post(f"/api/jobs/{job_id}/retry-pages?scope=all", headers=auth_headers)

    assert resp.status_code == 409
    assert "ALL pages" in resp.json()["detail"]


def test_retry_pages_scope_all_succeeds_once_job_is_no_longer_active(client, auth_headers, tmp_path, monkeypatch):
    job_id = _job_with_pages(tmp_path)
    calls = []

    async def _fake_bulk(job_id_arg, engine, run_ocr_func, run_unlimited_func, prompt, tiling, timeout, ocr_options, scope):
        calls.append((job_id_arg, scope))

    monkeypatch.setattr(task_queue, "retry_pages_bulk", _fake_bulk)
    monkeypatch.setattr(task_queue, "is_job_active", lambda jid: False)

    resp = client.post(f"/api/jobs/{job_id}/retry-pages?scope=all", headers=auth_headers)

    assert resp.status_code == 200
    time.sleep(0.2)
    assert calls == [(job_id, "all")]


def test_retry_pages_409s_when_a_bulk_retry_is_already_running(client, auth_headers, tmp_path, monkeypatch):
    job_id = _job_with_pages(tmp_path)
    monkeypatch.setattr(task_queue, "is_bulk_retry_active", lambda jid: True)

    resp_failed = client.post(f"/api/jobs/{job_id}/retry-pages?scope=failed", headers=auth_headers)
    resp_review = client.post(f"/api/jobs/{job_id}/retry-pages?scope=review", headers=auth_headers)
    resp_all = client.post(f"/api/jobs/{job_id}/retry-pages?scope=all", headers=auth_headers)

    assert resp_failed.status_code == 409
    assert resp_review.status_code == 409
    assert resp_all.status_code == 409
