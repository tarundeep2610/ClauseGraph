import gpu_ocr_client


def test_health_endpoint_passes_through_model_loaded_and_active_jobs(client, monkeypatch):
    async def _fake_check_health():
        return {"ollama": "online", "gpu_ocr_service": "online", "model_loaded": True, "active_jobs": 2}

    monkeypatch.setattr(gpu_ocr_client, "check_health", _fake_check_health)

    resp = client.get("/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["ollama_connection"] == "online"
    assert body["gpu_ocr_service_connection"] == "online"
    assert body["gpu_model_loaded"] is True
    assert body["gpu_active_jobs"] == 2


def test_health_endpoint_reflects_gpu_service_being_unreachable(client, monkeypatch):
    async def _fake_check_health():
        return {"ollama": "unreachable", "gpu_ocr_service": "unreachable", "model_loaded": False, "active_jobs": None}

    monkeypatch.setattr(gpu_ocr_client, "check_health", _fake_check_health)

    resp = client.get("/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["gpu_ocr_service_connection"] == "unreachable"
    assert body["gpu_model_loaded"] is False
    assert body["gpu_active_jobs"] is None
