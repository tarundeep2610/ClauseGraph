import gpu_ocr_client
import task_queue


def test_stop_all_cancels_cpu_tasks_and_tells_gpu_service_to_cancel(client, auth_headers, monkeypatch):
    """POST /api/jobs/stop-all must cancel CPU-side tasks (task_queue.cancel_all_jobs)
    AND tell the GPU service to abort whatever it's actively computing
    (gpu_ocr_client.cancel_all_gpu_jobs) - without the latter, a cancelled
    CPU-side task just abandons its in-flight GPU request, which keeps
    running orphaned until it times out on its own."""
    cpu_calls = {"n": 0}
    gpu_calls = {"n": 0}

    def _fake_cancel_all_jobs():
        cpu_calls["n"] += 1
        return 2

    async def _fake_cancel_all_gpu_jobs():
        gpu_calls["n"] += 1
        return {"ok": True, "status_code": 200}

    monkeypatch.setattr(task_queue, "cancel_all_jobs", _fake_cancel_all_jobs)
    monkeypatch.setattr(gpu_ocr_client, "cancel_all_gpu_jobs", _fake_cancel_all_gpu_jobs)

    resp = client.post("/api/jobs/stop-all", headers=auth_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["cancelled"] == 2
    assert body["gpu_cancelled"] is True
    assert cpu_calls["n"] == 1
    assert gpu_calls["n"] == 1


def test_stop_all_still_succeeds_when_gpu_service_is_unreachable(client, auth_headers, monkeypatch):
    """The GPU-side cancel is best-effort - if it fails, Stop All must still
    report success for the CPU-side cancellation that did happen, not 500."""
    monkeypatch.setattr(task_queue, "cancel_all_jobs", lambda: 1)

    async def _fake_unreachable():
        return {"ok": False, "error": "connection refused"}

    monkeypatch.setattr(gpu_ocr_client, "cancel_all_gpu_jobs", _fake_unreachable)

    resp = client.post("/api/jobs/stop-all", headers=auth_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["cancelled"] == 1
    assert body["gpu_cancelled"] is False
