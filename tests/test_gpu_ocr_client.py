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


class TestUnlimitedOcrMulti:
    @pytest.mark.asyncio
    async def test_submit_multi_sends_image_b64s_not_image_b64(self):
        with respx.mock:
            route = respx.post("https://gpu.example.test/api/v1/jobs").mock(
                return_value=httpx.Response(202, json={"job_id": "batch-1"})
            )
            respx.get("https://gpu.example.test/api/v1/jobs/batch-1").mock(
                return_value=httpx.Response(200, json={"status": "done", "result": "page1\n---\npage2", "error": None})
            )
            result = await goc.run_unlimited_ocr_multi([_tiny_image(), _tiny_image()], "Multi page parsing.", timeout=5)
            assert result == "page1\n---\npage2"
            sent_payload = __import__("json").loads(route.calls[0].request.content)
            assert "image_b64s" in sent_payload
            assert len(sent_payload["image_b64s"]) == 2
            assert "image_b64" not in sent_payload or sent_payload.get("image_b64") is None


class TestGlmOcrWithLogprobs:
    @pytest.mark.asyncio
    async def test_requests_logprobs_and_returns_them(self):
        with respx.mock:
            respx.get("http://localhost:11434/api/tags").mock(
                return_value=httpx.Response(200, json={"models": [{"name": "glm-ocr"}]})
            )
            gen_route = respx.post("http://localhost:11434/api/generate").mock(
                return_value=httpx.Response(200, json={"response": "hello", "logprobs": [{"token": "he", "logprob": -0.1}]})
            )
            text, logprobs = await goc.run_glm_ocr_with_logprobs(_tiny_image(), "prompt", timeout=5)
            assert text == "hello"
            assert logprobs == [{"token": "he", "logprob": -0.1}]
            sent_payload = __import__("json").loads(gen_route.calls[0].request.content)
            assert sent_payload["logprobs"] is True

    @pytest.mark.asyncio
    async def test_run_glm_ocr_without_tiling_does_not_request_logprobs(self):
        with respx.mock:
            respx.get("http://localhost:11434/api/tags").mock(
                return_value=httpx.Response(200, json={"models": [{"name": "glm-ocr"}]})
            )
            gen_route = respx.post("http://localhost:11434/api/generate").mock(
                return_value=httpx.Response(200, json={"response": "hello"})
            )
            result = await goc.run_glm_ocr(_tiny_image(), "prompt", tiling=False, timeout=5)
            assert result == "hello"
            sent_payload = __import__("json").loads(gen_route.calls[0].request.content)
            assert "logprobs" not in sent_payload


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
