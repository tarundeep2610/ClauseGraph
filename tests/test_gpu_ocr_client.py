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
    monkeypatch.setattr(config, "GPU_OCR_CLIENT_SAFETY_TIMEOUT_SECONDS", 1)
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


class TestCheckHealth:
    """check_health() backs the frontend's GPU status indicator - it must
    read model_loaded/active_jobs out of the GPU service's response body,
    not just its HTTP status code, and must never raise regardless of
    whether the GPU service (or Ollama) is reachable."""

    @pytest.mark.asyncio
    async def test_reads_model_loaded_and_active_jobs_from_200_response(self):
        with respx.mock:
            respx.get("http://localhost:11434").mock(return_value=httpx.Response(200))
            respx.get("https://gpu.example.test/health").mock(
                return_value=httpx.Response(
                    200, json={"status": "healthy", "unlimited_ocr_available": True, "model_loaded": True, "active_jobs": 2}
                )
            )
            result = await goc.check_health()
            assert result["gpu_ocr_service"] == "online"
            assert result["model_loaded"] is True
            assert result["active_jobs"] == 2

    @pytest.mark.asyncio
    async def test_model_loaded_false_when_gpu_reports_it_false(self):
        with respx.mock:
            respx.get("http://localhost:11434").mock(return_value=httpx.Response(200))
            respx.get("https://gpu.example.test/health").mock(
                return_value=httpx.Response(
                    200, json={"status": "healthy", "unlimited_ocr_available": True, "model_loaded": False, "active_jobs": 0}
                )
            )
            result = await goc.check_health()
            assert result["gpu_ocr_service"] == "online"
            assert result["model_loaded"] is False

    @pytest.mark.asyncio
    async def test_defaults_when_gpu_service_unreachable(self):
        with respx.mock:
            respx.get("http://localhost:11434").mock(return_value=httpx.Response(200))
            respx.get("https://gpu.example.test/health").mock(side_effect=httpx.ConnectError("refused"))
            result = await goc.check_health()
            assert result["gpu_ocr_service"] == "unreachable"
            assert result["model_loaded"] is False
            assert result["active_jobs"] is None

    @pytest.mark.asyncio
    async def test_defaults_when_gpu_service_returns_non_200(self):
        with respx.mock:
            respx.get("http://localhost:11434").mock(return_value=httpx.Response(200))
            respx.get("https://gpu.example.test/health").mock(return_value=httpx.Response(503))
            result = await goc.check_health()
            assert result["gpu_ocr_service"] == "offline"
            assert result["model_loaded"] is False
            assert result["active_jobs"] is None

    @pytest.mark.asyncio
    async def test_ollama_check_unaffected_by_gpu_check_failure(self):
        with respx.mock:
            respx.get("http://localhost:11434").mock(return_value=httpx.Response(200))
            respx.get("https://gpu.example.test/health").mock(side_effect=httpx.ConnectError("refused"))
            result = await goc.check_health()
            assert result["ollama"] == "online"
            assert result["gpu_ocr_service"] == "unreachable"


class TestClientSafetyTimeoutDoesNotRaceThePerJobTimeout:
    """Regression coverage for the cascading-backlog incident (see the project
    plan): the client's poll deadline used to be max(timeout, ...), which
    raced the GPU service's own per-job timeout enforcement and could abandon
    a call that was still legitimately queued behind another one, orphaning
    it. The client's deadline must now be a fixed safety net, completely
    independent of the per-job `timeout` value."""

    @pytest.mark.asyncio
    async def test_poll_timeout_is_fixed_regardless_of_the_per_job_timeout_value(self, monkeypatch):
        seen_poll_timeouts = []

        async def _fake_poll_until_done(job_id, poll_timeout):
            seen_poll_timeouts.append(poll_timeout)
            return "ok"

        monkeypatch.setattr(goc, "poll_until_done", _fake_poll_until_done)

        with respx.mock:
            respx.post("https://gpu.example.test/api/v1/jobs").mock(
                return_value=httpx.Response(202, json={"job_id": "job-short"})
            )
            await goc.run_unlimited_ocr(_tiny_image(), "prompt", timeout=5, base_size=1024)

            respx.post("https://gpu.example.test/api/v1/jobs").mock(
                return_value=httpx.Response(202, json={"job_id": "job-long"})
            )
            await goc.run_unlimited_ocr(_tiny_image(), "prompt", timeout=900, base_size=1024)

        assert seen_poll_timeouts == [
            config.GPU_OCR_CLIENT_SAFETY_TIMEOUT_SECONDS,
            config.GPU_OCR_CLIENT_SAFETY_TIMEOUT_SECONDS,
        ], "a short timeout=5 and a long timeout=900 must produce the identical, fixed safety-net deadline"

    @pytest.mark.asyncio
    async def test_slow_but_eventually_successful_job_is_not_prematurely_abandoned(self, monkeypatch):
        # config.GPU_OCR_CLIENT_SAFETY_TIMEOUT_SECONDS is monkeypatched to 1s by
        # the _gpu_config fixture; GPU_OCR_POLL_INTERVAL_SECONDS to 0.01s. A
        # job that stays "processing" for several polls - longer than a tiny
        # per-job timeout=0.05 would have allowed under the old max()
        # formula - must still succeed once it reports "done", since the
        # client no longer bounds its patience by that per-job timeout at all.
        with respx.mock:
            respx.post("https://gpu.example.test/api/v1/jobs").mock(
                return_value=httpx.Response(202, json={"job_id": "job-slow"})
            )
            responses = [
                httpx.Response(200, json={"status": "processing", "result": None, "error": None})
                for _ in range(5)
            ] + [httpx.Response(200, json={"status": "done", "result": "finally done", "error": None})]
            respx.get("https://gpu.example.test/api/v1/jobs/job-slow").mock(side_effect=responses)

            result = await goc.run_unlimited_ocr(_tiny_image(), "prompt", timeout=0.05, base_size=1024)
            assert result == "finally done"

    @pytest.mark.asyncio
    async def test_still_raises_gpu_service_timeout_if_truly_stuck_past_the_safety_net(self, monkeypatch):
        # The safety net itself must still work - this isn't turning into an
        # infinite wait, just decoupling it from the per-job `timeout`.
        monkeypatch.setattr(config, "GPU_OCR_CLIENT_SAFETY_TIMEOUT_SECONDS", 0.05)
        with respx.mock:
            respx.post("https://gpu.example.test/api/v1/jobs").mock(
                return_value=httpx.Response(202, json={"job_id": "job-stuck"})
            )
            respx.get("https://gpu.example.test/api/v1/jobs/job-stuck").mock(
                return_value=httpx.Response(200, json={"status": "processing", "result": None, "error": None})
            )
            with pytest.raises(goc.GPUServiceTimeout):
                await goc.run_unlimited_ocr(_tiny_image(), "prompt", timeout=900, base_size=1024)


class TestCancelAllGpuJobs:
    """cancel_all_gpu_jobs() backs the CPU side's Stop All action - it must
    never raise, since task_queue.cancel_all_jobs() (the CPU-side half of
    Stop All) has to proceed regardless of whether the GPU service could be
    reached."""

    @pytest.mark.asyncio
    async def test_success_posts_to_cancel_all_and_reports_ok(self):
        with respx.mock:
            route = respx.post("https://gpu.example.test/api/v1/jobs/cancel-all").mock(
                return_value=httpx.Response(200, json={"message": "Cancelled 2 in-flight job(s).", "cancelled": 2})
            )
            result = await goc.cancel_all_gpu_jobs()
            assert result == {"ok": True, "status_code": 200}
            assert route.call_count == 1
            assert route.calls[0].request.headers["x-gpu-ocr-key"] == "test-secret"

    @pytest.mark.asyncio
    async def test_unreachable_service_returns_cleanly_instead_of_raising(self):
        with respx.mock:
            respx.post("https://gpu.example.test/api/v1/jobs/cancel-all").mock(
                side_effect=httpx.ConnectError("refused")
            )
            result = await goc.cancel_all_gpu_jobs()
            assert result["ok"] is False
            assert "refused" in result["error"]

    @pytest.mark.asyncio
    async def test_error_status_reports_not_ok_without_raising(self):
        with respx.mock:
            respx.post("https://gpu.example.test/api/v1/jobs/cancel-all").mock(
                return_value=httpx.Response(500)
            )
            result = await goc.cancel_all_gpu_jobs()
            assert result == {"ok": False, "status_code": 500}
