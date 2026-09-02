import asyncio
import uuid

import pypdfium2 as pdfium
import pytest
from PIL import Image

import database
import task_queue
from gpu_ocr_client import GPUServiceUnreachable


def _make_multipage_pdf_job(tmp_path, n_pages=6, chunk_size=5, escalate_before_flagging=True,
                             processing_mode="single", enable_overlap=True):
    job_id = str(uuid.uuid4())
    pdf_path = tmp_path / f"{job_id}.pdf"
    doc = pdfium.PdfDocument.new()
    for _ in range(n_pages):
        doc.new_page(200, 200)
    doc.save(str(pdf_path))
    doc.close()
    database.create_job(
        job_id=job_id, filename="test.pdf", file_path=str(pdf_path), file_type="pdf",
        engine="unlimited", scale=1.0, tiling=False, prompt_mode="document", custom_prompt=None,
        total_pages=n_pages, chunk_size=chunk_size, concurrency=2,
        escalate_before_flagging=escalate_before_flagging,
        processing_mode=processing_mode, enable_overlap=enable_overlap,
    )
    return job_id


def _make_image_job(tmp_path, escalate_before_flagging=True):
    job_id = str(uuid.uuid4())
    img_path = tmp_path / f"{job_id}.png"
    Image.new("RGB", (10, 10), color="white").save(img_path)
    database.create_job(
        job_id=job_id, filename="test.png", file_path=str(img_path), file_type="image",
        engine="unlimited", scale=1.5, tiling=False, prompt_mode="document", custom_prompt=None,
        total_pages=1, escalate_before_flagging=escalate_before_flagging,
    )
    return job_id


class _ScriptedRunner:
    def __init__(self, effects=None, fixed_result=None):
        self.effects = list(effects) if effects is not None else None
        self.fixed_result = fixed_result
        self.calls = 0
        self.call_args = []

    async def __call__(self, pil_img, prompt, timeout, *rest):
        self.calls += 1
        self.call_args.append((prompt, timeout))
        if self.effects is not None:
            effect = self.effects.pop(0)
            if isinstance(effect, Exception):
                raise effect
            return effect
        return self.fixed_result


class _AlwaysCleanRunner:
    """Returns clean, well-formed text — used so the base pass doesn't trigger
    escalation by accident when a test isn't specifically about escalation."""
    def __init__(self):
        self.calls = 0

    async def __call__(self, pil_img, prompt, timeout, *rest):
        self.calls += 1
        return "This is a perfectly clean page of ordinary transcribed text content here."


@pytest.mark.asyncio
async def test_multipage_job_processes_all_pages_across_chunks(tmp_path):
    job_id = _make_multipage_pdf_job(tmp_path, n_pages=6, chunk_size=5)
    runner = _AlwaysCleanRunner()

    await task_queue.process_ocr_job(job_id, run_ocr_func=None, run_unlimited_func=runner, prompts_dict={"document": "p"})

    job = database.get_job(job_id)
    assert job["status"] == "completed"
    pages = database.get_pages(job_id)
    assert len(pages) == 6
    assert all(p["status"] == "success" for p in pages)
    assert runner.calls == 6


@pytest.mark.asyncio
async def test_low_confidence_page_escalates_exactly_once_then_succeeds(tmp_path):
    job_id = _make_image_job(tmp_path)
    # Base pass: garbled/repetitive text (low confidence). Escalation attempt: clean text.
    runner = _ScriptedRunner(effects=["AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", "A clean and well-formed escalated result for this page."])

    await task_queue.process_ocr_job(job_id, run_ocr_func=None, run_unlimited_func=runner, prompts_dict={"document": "p"})

    job = database.get_job(job_id)
    assert job["status"] == "completed"
    page = database.get_page(job_id, 1)
    assert runner.calls == 2, "exactly one base attempt + one combined-recovery escalation attempt, never a ladder"
    assert page["text"] == "A clean and well-formed escalated result for this page."
    assert page["escalated"] == 1

    attempts = database.get_page_attempts(job_id, 1)
    assert len(attempts) == 2
    assert attempts[0]["escalation_level"] == "base"
    assert attempts[1]["escalation_level"] == "combined_recovery"


@pytest.mark.asyncio
async def test_escalate_before_flagging_false_skips_escalation_entirely(tmp_path):
    job_id = _make_image_job(tmp_path, escalate_before_flagging=False)
    runner = _ScriptedRunner(effects=["AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"])

    await task_queue.process_ocr_job(job_id, run_ocr_func=None, run_unlimited_func=runner, prompts_dict={"document": "p"})

    job = database.get_job(job_id)
    assert job["status"] == "completed"
    assert runner.calls == 1, "no automatic retry at all when the toggle is off"
    page = database.get_page(job_id, 1)
    assert page["status"] == "review"
    assert page["needs_review"] == 1


@pytest.mark.asyncio
async def test_job_transitions_through_escalating_status_when_needed(tmp_path):
    job_id = _make_image_job(tmp_path)
    runner = _ScriptedRunner(effects=["AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", "Fine clean result after escalation happens here."])
    seen_statuses = []
    orig_set_status = database.set_job_status

    def _spy(job_id_arg, status):
        seen_statuses.append(status)
        orig_set_status(job_id_arg, status)

    import database as db_module
    db_module.set_job_status = _spy
    try:
        await task_queue.process_ocr_job(job_id, run_ocr_func=None, run_unlimited_func=runner, prompts_dict={"document": "p"})
    finally:
        db_module.set_job_status = orig_set_status

    assert "processing" in seen_statuses
    assert "escalating" in seen_statuses
    assert seen_statuses[-1] == "completed"


@pytest.mark.asyncio
async def test_manual_retry_succeeds_and_does_not_touch_job_status_on_completed_job(tmp_path):
    job_id = _make_image_job(tmp_path)
    runner = _AlwaysCleanRunner()
    await task_queue.process_ocr_job(job_id, run_ocr_func=None, run_unlimited_func=runner, prompts_dict={"document": "p"})
    assert database.get_job(job_id, include_pages=False)["status"] == "completed"

    retry_runner = _AlwaysCleanRunner()
    updated = await task_queue.retry_single_page(
        job_id, 1, "unlimited", None, retry_runner, "document parsing.", False, 60,
    )
    assert updated["status"] == "success"
    assert retry_runner.calls == 1
    assert database.get_job(job_id, include_pages=False)["status"] == "completed", (
        "retrying one page must never revert the job's overall status"
    )


@pytest.mark.asyncio
async def test_manual_retry_rejects_concurrent_claim_on_same_page(tmp_path):
    job_id = _make_image_job(tmp_path)
    database.upsert_page(job_id, 1, status="review", confidence=0.5, needs_review=1)
    database.claim_page_for_processing(job_id, 1)  # simulate an in-flight attempt

    with pytest.raises(RuntimeError):
        await task_queue.retry_single_page(job_id, 1, "unlimited", None, _AlwaysCleanRunner(), "p", False, 60)


@pytest.mark.asyncio
async def test_transient_failure_still_retries_within_base_pass(tmp_path):
    """Regression: the original per-page transient-retry loop must still work
    inside the new pipeline."""
    job_id = _make_image_job(tmp_path)
    runner = _ScriptedRunner(effects=[GPUServiceUnreachable("down"), GPUServiceUnreachable("down"), "OCR RESULT TEXT"])

    await task_queue.process_ocr_job(job_id, run_ocr_func=None, run_unlimited_func=runner, prompts_dict={"document": "p"})

    job = database.get_job(job_id)
    assert job["status"] == "completed"
    assert job["pages_data"][0]["status"] == "success"
    assert job["pages_data"][0]["text"] == "OCR RESULT TEXT"
    assert runner.calls == 3


class _DelayedCleanRunner:
    """Like _AlwaysCleanRunner, but sleeps briefly per call so completion order
    alone (without a dispatch-order guarantee) would not naturally be ascending."""
    def __init__(self, delay=0.01):
        self.calls = 0
        self.delay = delay

    async def __call__(self, pil_img, prompt, timeout, *rest):
        self.calls += 1
        await asyncio.sleep(self.delay)
        return "This is a perfectly clean page of ordinary transcribed text content here."


class _MultiPageCleanRunner:
    """run_ocr_multi_func stand-in: joins clean per-page texts with the explicit
    <page> marker _split_batch_output looks for first."""
    def __init__(self):
        self.calls = 0

    async def __call__(self, images, prompt, timeout):
        self.calls += 1
        page_text = "A perfectly clean batch page of ordinary transcribed text content."
        return "\n<page>\n".join([page_text] * len(images))


@pytest.mark.asyncio
async def test_single_mode_claims_pages_in_ascending_order_under_concurrency(tmp_path, monkeypatch):
    job_id = _make_multipage_pdf_job(tmp_path, n_pages=8, chunk_size=3)  # concurrency=2 from the helper
    claim_order = []
    real_claim = database.claim_page_for_processing

    def _tracking_claim(job_id_arg, page_num):
        claim_order.append(page_num)
        return real_claim(job_id_arg, page_num)

    monkeypatch.setattr(database, "claim_page_for_processing", _tracking_claim)
    runner = _DelayedCleanRunner()

    await task_queue.process_ocr_job(job_id, run_ocr_func=None, run_unlimited_func=runner, prompts_dict={"document": "p"})

    assert claim_order == list(range(1, 9)), (
        "pages must be claimed strictly ascending regardless of per-page OCR latency"
    )


@pytest.mark.asyncio
async def test_single_mode_pages_have_no_chunk_metadata(tmp_path):
    job_id = _make_multipage_pdf_job(tmp_path, n_pages=6, chunk_size=3)
    runner = _AlwaysCleanRunner()

    await task_queue.process_ocr_job(job_id, run_ocr_func=None, run_unlimited_func=runner, prompts_dict={"document": "p"})

    pages = database.get_pages(job_id)
    assert len(pages) == 6
    for p in pages:
        assert p["chunk_id"] is None
        assert not p["is_boundary"]


@pytest.mark.asyncio
async def test_batch_auto_export_stitches_when_overlap_enabled(tmp_path, monkeypatch):
    import seam_stitching
    captured = {}
    orig_render_export = seam_stitching.render_export

    def _capture(pages, clean_tables=False, stitch=True):
        captured["stitch"] = stitch
        return orig_render_export(pages, clean_tables=clean_tables, stitch=stitch)

    monkeypatch.setattr(seam_stitching, "render_export", _capture)

    job_id = _make_multipage_pdf_job(tmp_path, n_pages=4, chunk_size=2, processing_mode="batch", enable_overlap=True)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    await task_queue.process_ocr_job(
        job_id, run_ocr_func=None, run_unlimited_func=None, prompts_dict={"document": "p"},
        run_ocr_multi_func=_MultiPageCleanRunner(), output_path=str(out_dir),
    )

    assert captured["stitch"] is True


@pytest.mark.asyncio
async def test_batch_auto_export_skips_stitch_when_overlap_disabled(tmp_path, monkeypatch):
    import seam_stitching
    captured = {}
    orig_render_export = seam_stitching.render_export

    def _capture(pages, clean_tables=False, stitch=True):
        captured["stitch"] = stitch
        return orig_render_export(pages, clean_tables=clean_tables, stitch=stitch)

    monkeypatch.setattr(seam_stitching, "render_export", _capture)

    job_id = _make_multipage_pdf_job(tmp_path, n_pages=4, chunk_size=2, processing_mode="batch", enable_overlap=False)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    await task_queue.process_ocr_job(
        job_id, run_ocr_func=None, run_unlimited_func=None, prompts_dict={"document": "p"},
        run_ocr_multi_func=_MultiPageCleanRunner(), output_path=str(out_dir),
    )

    assert captured["stitch"] is False


@pytest.mark.asyncio
async def test_single_mode_auto_export_never_stitches(tmp_path, monkeypatch):
    import seam_stitching
    captured = {}
    orig_render_export = seam_stitching.render_export

    def _capture(pages, clean_tables=False, stitch=True):
        captured["stitch"] = stitch
        return orig_render_export(pages, clean_tables=clean_tables, stitch=stitch)

    monkeypatch.setattr(seam_stitching, "render_export", _capture)

    job_id = _make_multipage_pdf_job(tmp_path, n_pages=3, chunk_size=2)  # single mode (default)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    runner = _AlwaysCleanRunner()

    await task_queue.process_ocr_job(
        job_id, run_ocr_func=None, run_unlimited_func=runner, prompts_dict={"document": "p"},
        output_path=str(out_dir),
    )

    assert captured["stitch"] is False
