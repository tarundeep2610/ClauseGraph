import uuid

import pytest
from PIL import Image

import database
import task_queue
from gpu_ocr_client import GPUServiceUnreachable, GPUServiceAuthError


def _make_image_job(tmp_path):
    job_id = str(uuid.uuid4())
    img_path = tmp_path / f"{job_id}.png"
    Image.new("RGB", (10, 10), color="white").save(img_path)
    database.create_job(
        job_id=job_id,
        filename="test.png",
        file_path=str(img_path),
        file_type="image",
        engine="unlimited",
        scale=1.5,
        tiling=False,
        prompt_mode="document",
        custom_prompt=None,
        total_pages=1,
        page_range="",
    )
    return job_id


class _ScriptedRunner:
    """Async test double: pops one scripted effect per call (an exception to raise, or a return value)."""

    def __init__(self, effects):
        self.effects = list(effects)
        self.calls = 0

    async def __call__(self, *args, **kwargs):
        self.calls += 1
        effect = self.effects.pop(0)
        if isinstance(effect, Exception):
            raise effect
        return effect


@pytest.mark.asyncio
async def test_transient_failure_then_success(tmp_path):
    job_id = _make_image_job(tmp_path)
    runner = _ScriptedRunner([GPUServiceUnreachable("down"), GPUServiceUnreachable("down"), "OCR RESULT TEXT"])

    await task_queue.process_ocr_job(job_id, run_ocr_func=None, run_unlimited_func=runner, prompts_dict={"document": "p"})

    job = database.get_job(job_id)
    assert job["status"] == "completed"
    assert job["pages_data"][0]["status"] == "success"
    assert job["pages_data"][0]["text"] == "OCR RESULT TEXT"
    assert runner.calls == 3


@pytest.mark.asyncio
async def test_unreachable_exhausts_all_retries_with_clear_error(tmp_path):
    job_id = _make_image_job(tmp_path)
    runner = _ScriptedRunner([GPUServiceUnreachable("gpu down")] * 3)

    await task_queue.process_ocr_job(job_id, run_ocr_func=None, run_unlimited_func=runner, prompts_dict={"document": "p"})

    job = database.get_job(job_id)
    assert runner.calls == 3
    page = job["pages_data"][0]
    assert page["status"] == "failed"
    assert "GPUServiceUnreachable" in page["error"]


@pytest.mark.asyncio
async def test_auth_error_fails_fast_with_no_retry(tmp_path):
    job_id = _make_image_job(tmp_path)
    runner = _ScriptedRunner([GPUServiceAuthError("bad secret")])

    await task_queue.process_ocr_job(job_id, run_ocr_func=None, run_unlimited_func=runner, prompts_dict={"document": "p"})

    job = database.get_job(job_id)
    assert runner.calls == 1, "auth failures must not be retried — they can't succeed on retry"
    page = job["pages_data"][0]
    assert page["status"] == "failed"
    assert "authentication" in page["error"].lower()
