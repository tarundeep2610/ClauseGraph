import pytest
import respx
import httpx
from PIL import Image

import config
import gpu_ocr_client as goc


@pytest.fixture(autouse=True)
def _gpu_config(monkeypatch):
    monkeypatch.setattr(config, "GPU_OCR_BASE_URL", "https://gpu.example.test")
    monkeypatch.setattr(config, "GPU_OCR_SHARED_SECRET", "test-secret")
    monkeypatch.setattr(config, "GPU_OCR_POLL_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(config, "GPU_OCR_POLL_TIMEOUT_SECONDS", 1)
    monkeypatch.setattr(config, "GPU_OCR_CONNECT_TIMEOUT_SECONDS", 2)


def _tiny_image():
    return Image.new("RGB", (10, 10), color="white")


class TestUnlimitedOcrHappyPath:
    @pytest.mark.asyncio
    async def test_submit_then_poll_returns_result(self):
        with respx.mock:
            respx.post("https://gpu.example.test/api/v1/jobs").mock(
                return_value=httpx.Response(202, json={"job_id": "job-1"})
            )
            respx.get("https://gpu.example.test/api/v1/jobs/job-1").mock(
                return_value=httpx.Response(200, json={"status": "done", "result": "hello world", "error": None})
            )
            result = await goc.run_unlimited_ocr(_tiny_image(), "prompt", timeout=5, base_size=1024)
            assert result == "hello world"


class TestUnlimitedOcrFailureModes:
    @pytest.mark.asyncio
    async def test_connection_error_raises_unreachable(self):
        with respx.mock:
            respx.post("https://gpu.example.test/api/v1/jobs").mock(
                side_effect=httpx.ConnectError("refused")
            )
            with pytest.raises(goc.GPUServiceUnreachable):
                await goc.submit_unlimited_job(b"fake", "prompt", 1024, 5)

    @pytest.mark.asyncio
    async def test_401_on_submit_raises_auth_error_after_exactly_one_call(self):
        with respx.mock:
            route = respx.post("https://gpu.example.test/api/v1/jobs").mock(
                return_value=httpx.Response(401)
            )
            with pytest.raises(goc.GPUServiceAuthError):
                await goc.submit_unlimited_job(b"fake", "prompt", 1024, 5)
            assert route.call_count == 1

    @pytest.mark.asyncio
    async def test_poll_timeout_raises_gpu_service_timeout(self):
        with respx.mock:
            respx.get("https://gpu.example.test/api/v1/jobs/job-2").mock(
                return_value=httpx.Response(200, json={"status": "processing", "result": None, "error": None})
            )
            with pytest.raises(goc.GPUServiceTimeout):
                await goc.poll_until_done("job-2", poll_timeout=0.05)

    @pytest.mark.asyncio
    async def test_failed_job_status_raises_job_failed_with_message(self):
        with respx.mock:
            respx.get("https://gpu.example.test/api/v1/jobs/job-3").mock(
                return_value=httpx.Response(
                    200, json={"status": "failed", "result": None, "error": "CUDA out of memory"}
                )
            )
            with pytest.raises(goc.GPUServiceJobFailed, match="CUDA out of memory"):
                await goc.poll_until_done("job-3", poll_timeout=1)
