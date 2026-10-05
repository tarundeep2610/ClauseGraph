"""Regression tests for RokkaAIProvider's real HTTP contract (ai_indexing/
providers.py). These lock in the actual envelope shape confirmed against a
live RokkaAI server — a nested `data` object on every response, `x-rokkaai-key`
auth (not Bearer), a required `model_name` submit field, and `data.answer`
(not `data.result`) on a completed job — after an earlier version of this
provider got these details wrong and crashed with an unhandled KeyError
instead of a clean AIIndexError."""

import httpx
import pytest
import respx

from ai_indexing.errors import AIAuthError, AIRateLimitError, AIRemoteError
from ai_indexing.providers import ProviderConfig, RokkaAIProvider
from ai_indexing.redact import SecretStr

BASE_URL = "https://rokka.example.test"


def _provider(**overrides):
    kwargs = dict(
        provider="rokkaai",
        model="qwen3:8b",
        api_key=SecretStr("rokkaai_testkey123"),
        base_url=BASE_URL,
        poll_interval_s=0.01,
        poll_timeout_s=5,
    )
    kwargs.update(overrides)
    return RokkaAIProvider(ProviderConfig(**kwargs))


def _submit_response(job_id="job_6a2e4915", status_code=202):
    return httpx.Response(
        status_code,
        json={
            "success": True,
            "status_code": status_code,
            "prompt_msg": "Job queued",
            "data": {"job_id": job_id, "lane": "fast", "queue_position": 1},
            "meta": {"request_id": "req_1"},
        },
    )


def _job_response(status, answer=None, error=None):
    data = {"status": status, "lane": "fast"}
    if answer is not None:
        data["answer"] = answer
    if error is not None:
        data["error"] = error
    return httpx.Response(200, json={
        "success": True, "status_code": 200, "prompt_msg": "ok",
        "data": data, "meta": {"job_id": "job_6a2e4915"},
    })


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_submit_then_poll_returns_answer(self):
        provider = _provider()
        with respx.mock:
            respx.post(f"{BASE_URL}/api/v1/query").mock(return_value=_submit_response())
            respx.get(f"{BASE_URL}/api/v1/jobs/job_6a2e4915").mock(
                return_value=_job_response("done", answer="Artificial Intelligence")
            )
            result = await provider._complete_once("system prompt", "user query")
            assert result.text == "Artificial Intelligence"
            assert result.provider_job_id == "job_6a2e4915"

    @pytest.mark.asyncio
    async def test_sends_real_contract_headers_and_payload(self):
        provider = _provider()
        with respx.mock:
            submit_route = respx.post(f"{BASE_URL}/api/v1/query").mock(return_value=_submit_response())
            respx.get(f"{BASE_URL}/api/v1/jobs/job_6a2e4915").mock(
                return_value=_job_response("done", answer="hi")
            )
            await provider._complete_once("system prompt", "user query")

            sent = submit_route.calls[0].request
            assert sent.headers["x-rokkaai-key"] == "rokkaai_testkey123"
            assert sent.headers["x-rokkaai-mode"] == "general"
            assert "authorization" not in sent.headers

            import json as _json
            payload = _json.loads(sent.content)
            assert payload["model_name"] == "qwen3:8b"
            assert payload["query"] == "user query"


class TestMalformedResponses:
    @pytest.mark.asyncio
    async def test_missing_job_id_raises_remote_error_not_keyerror(self):
        provider = _provider()
        with respx.mock:
            respx.post(f"{BASE_URL}/api/v1/query").mock(
                return_value=httpx.Response(202, json={"success": True, "data": {}, "meta": {}})
            )
            with pytest.raises(AIRemoteError):
                await provider._complete_once("system", "query")

    @pytest.mark.asyncio
    async def test_missing_data_envelope_raises_remote_error_not_keyerror(self):
        provider = _provider()
        with respx.mock:
            respx.post(f"{BASE_URL}/api/v1/query").mock(
                return_value=httpx.Response(202, json={"success": True, "meta": {}})
            )
            with pytest.raises(AIRemoteError):
                await provider._complete_once("system", "query")


class TestJobFailure:
    @pytest.mark.asyncio
    async def test_failed_status_raises_immediately_not_polled_to_timeout(self):
        provider = _provider(poll_timeout_s=9999)  # would hang if this weren't checked
        with respx.mock:
            respx.post(f"{BASE_URL}/api/v1/query").mock(return_value=_submit_response())
            respx.get(f"{BASE_URL}/api/v1/jobs/job_6a2e4915").mock(
                return_value=_job_response("failed", error="model crashed")
            )
            with pytest.raises(AIRemoteError, match="model crashed"):
                await provider._complete_once("system", "query")


class TestAuthAndRateLimit:
    @pytest.mark.asyncio
    async def test_401_raises_auth_error(self):
        provider = _provider()
        with respx.mock:
            respx.post(f"{BASE_URL}/api/v1/query").mock(return_value=httpx.Response(401, json={}))
            with pytest.raises(AIAuthError):
                await provider._complete_once("system", "query")

    @pytest.mark.asyncio
    async def test_429_raises_rate_limit_error(self):
        provider = _provider()
        with respx.mock:
            respx.post(f"{BASE_URL}/api/v1/query").mock(return_value=httpx.Response(429, json={}))
            with pytest.raises(AIRateLimitError):
                await provider._complete_once("system", "query")
