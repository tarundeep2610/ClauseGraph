import asyncio
import uuid

import pypdfium2 as pdfium
import pytest
from PIL import Image

import config
import database
import task_queue
from gpu_ocr_client import GPUServiceUnreachable


def _make_multipage_pdf_job(tmp_path, n_pages=6, chunk_size=5, escalate_before_flagging=True,
                             processing_mode="single", enable_overlap=True, concurrency=2):
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
        total_pages=n_pages, chunk_size=chunk_size, concurrency=concurrency,
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


class _RawCapturingRunner:
    """Mirrors main.py's real _unlimited_runner contract: the 4th positional
    arg (raw_capture) is an optional dict this runner populates with the
    model's raw pre-clean_detection_tags() output, same as production's
    baidu/Unlimited-OCR path — used to verify task_queue.py actually
    threads it through to ocr_pages.raw_ocr_text end to end."""
    def __init__(self, text, raw_text):
        self.text = text
        self.raw_text = raw_text
        self.calls = 0

    async def __call__(self, pil_img, prompt, timeout, raw_capture=None):
        self.calls += 1
        if raw_capture is not None:
            raw_capture["raw_text"] = self.raw_text
        return self.text


@pytest.mark.asyncio
async def test_raw_ocr_text_is_persisted_alongside_the_cleaned_text(tmp_path):
    """The layout-tag confidence signal (reference_index.py) depends on
    ocr_pages.raw_ocr_text being populated from the OCR runner's raw_capture
    out-param — this is the one thing the additive wiring exists for."""
    job_id = _make_image_job(tmp_path)
    raw = "<|det|>header [1,1,1,1]<|/det|>Annexure A-2"
    runner = _RawCapturingRunner(text="Annexure A-2", raw_text=raw)

    await task_queue.process_ocr_job(job_id, run_ocr_func=None, run_unlimited_func=runner, prompts_dict={"document": "p"})

    page = database.get_page(job_id, 1)
    assert page["text"] == "Annexure A-2"
    assert page["raw_ocr_text"] == raw


@pytest.mark.asyncio
async def test_formatted_text_is_built_from_raw_text_and_text_is_unchanged(tmp_path):
    """layout_format.build_formatted_markdown() output lands in its own
    formatted_text column — `text` (what reference_index.py indexes) must
    stay byte-identical to the plain OCR output, never the formatted
    rewrite (a formatted blockquote/raw-HTML block yields zero blocks in
    reference_index.py's parser, confirmed directly — writing it into
    `text` would silently delete content from the cross-reference index)."""
    job_id = _make_image_job(tmp_path)
    raw = "<|det|>title [10, 10, 400, 40]<|/det|>3.3 TIME SCHEDULE"
    runner = _RawCapturingRunner(text="3.3 TIME SCHEDULE", raw_text=raw)

    await task_queue.process_ocr_job(job_id, run_ocr_func=None, run_unlimited_func=runner, prompts_dict={"document": "p"})

    page = database.get_page(job_id, 1)
    assert page["text"] == "3.3 TIME SCHEDULE"  # unchanged, indexing-safe
    assert page["formatted_text"] == "## 3.3 TIME SCHEDULE"


@pytest.mark.asyncio
async def test_formatted_text_is_none_when_raw_text_has_no_tags(tmp_path):
    """The GLM engine (and any Unlimited-OCR job run without grounding)
    never emits <|det|> tags at all — formatted_text must stay NULL, the
    documented fallback-to-`text` contract for the viewer/export."""
    job_id = _make_image_job(tmp_path)
    runner = _RawCapturingRunner(text="plain GLM text, no tags", raw_text="plain GLM text, no tags")

    await task_queue.process_ocr_job(job_id, run_ocr_func=None, run_unlimited_func=runner, prompts_dict={"document": "p"})

    page = database.get_page(job_id, 1)
    assert page["formatted_text"] is None


@pytest.mark.asyncio
async def test_formatted_text_is_none_for_a_failed_page_placeholder(tmp_path):
    """A failed page stores MANUAL_REVIEW_TEXT in `text` — there's nothing
    real to format, so formatted_text must be None rather than formatting
    the placeholder (or, worse, stale raw tags from a prior attempt)."""
    job_id = _make_image_job(tmp_path)
    runner = _ScriptedRunner(effects=[RuntimeError("boom")] * 10)

    await task_queue.process_ocr_job(job_id, run_ocr_func=None, run_unlimited_func=runner, prompts_dict={"document": "p"})

    page = database.get_page(job_id, 1)
    assert page["formatted_text"] is None


@pytest.mark.asyncio
async def test_ocr_completion_triggers_no_automatic_indexing(tmp_path):
    """Indexing (structure detection + cross-reference citations) is fully
    opt-in, built only by the explicit POST .../reindex-references action
    ("Build Index & Citations") — normal OCR processing (including the
    automatic escalation pass) must never touch reference_index_version or
    ocr_reference_targets on its own. Regression coverage for a deliberate
    architecture change: this app used to run a per-page rule-based
    structure detector automatically on every page, plus an automatic
    reindex at job completion — both removed so a freshly-OCR'd job has no
    structure/citations until the user asks for them."""
    job_id = _make_multipage_pdf_job(tmp_path, n_pages=3, chunk_size=5)
    runner = _AlwaysCleanRunner()

    await task_queue.process_ocr_job(job_id, run_ocr_func=None, run_unlimited_func=runner, prompts_dict={"document": "p"})

    job = database.get_job(job_id)
    assert job["status"] == "completed"
    assert job["reference_index_version"] == 0
    assert job["index_built_at"] is None
    assert database.get_reference_targets_for_job(job_id) == []


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


class _SequencedRawCapturingRunner:
    """Like _RawCapturingRunner, but returns a different (text, raw_text)
    pair on each successive call — used to confirm the combined-recovery
    escalation path (task_queue.py's own separate upsert_page call, not
    score_and_store) builds formatted_text from *its own* raw_text, not a
    stale value left over from the base pass."""
    def __init__(self, pairs):
        self.pairs = list(pairs)
        self.calls = 0

    async def __call__(self, pil_img, prompt, timeout, raw_capture=None):
        text, raw_text = self.pairs[self.calls]
        self.calls += 1
        if raw_capture is not None:
            raw_capture["raw_text"] = raw_text
        return text


@pytest.mark.asyncio
async def test_combined_recovery_escalation_builds_its_own_formatted_text(tmp_path):
    job_id = _make_image_job(tmp_path)
    runner = _SequencedRawCapturingRunner([
        ("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
         "<|det|>title [10, 10, 400, 40]<|/det|>AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"),
        ("A clean and well-formed escalated result for this page.",
         "<|det|>title [10, 10, 400, 40]<|/det|>A clean and well-formed escalated result for this page."),
    ])

    await task_queue.process_ocr_job(job_id, run_ocr_func=None, run_unlimited_func=runner, prompts_dict={"document": "p"})

    page = database.get_page(job_id, 1)
    assert page["text"] == "A clean and well-formed escalated result for this page."
    assert page["formatted_text"] == "## A clean and well-formed escalated result for this page."


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


class _FailsOncePerPageRunner:
    """run_unlimited_func stand-in: calls 2-4 (page 2's three retry attempts,
    with concurrency=1 so call order exactly matches page dispatch order) all
    raise; every other call succeeds with clean, distinct text."""
    def __init__(self):
        self.calls = 0

    async def __call__(self, pil_img, prompt, timeout, *rest):
        self.calls += 1
        if self.calls in (2, 3, 4):
            raise RuntimeError("simulated engine crash for this page")
        return f"Clean text for call {self.calls}."


@pytest.mark.asyncio
async def test_failed_page_shows_manual_review_text_and_job_continues(tmp_path):
    """Regression: a page the engine can't process must show one clean,
    consistent manual-review message (not a raw error/traceback) and must
    never prevent other pages in the same job from getting their own genuine
    attempt - see the project plan for the incident this guards against."""
    job_id = _make_multipage_pdf_job(
        tmp_path, n_pages=3, chunk_size=5, concurrency=1,
        escalate_before_flagging=False,
    )
    runner = _FailsOncePerPageRunner()

    await task_queue.process_ocr_job(
        job_id, run_ocr_func=None, run_unlimited_func=runner, prompts_dict={"document": "p"},
    )

    job = database.get_job(job_id)
    assert job["status"] == "completed"
    pages = {p["page_num"]: p for p in database.get_pages(job_id)}

    assert pages[1]["text"] == "Clean text for call 1."
    assert pages[3]["text"] == "Clean text for call 5.", (
        "the page after the failure must still get its own genuine attempt"
    )

    failed = pages[2]
    assert failed["text"] == task_queue.MANUAL_REVIEW_TEXT, (
        "a failed page must show the clean manual-review message, not a raw error/traceback"
    )
    assert failed["needs_review"] == 1
    assert "simulated engine crash" in failed["error"], (
        "the real error detail must still be preserved in the error column"
    )


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
    """run_ocr_multi_func stand-in: emits distinct per-page text using the real
    infer_multi() convention — a leading '<PAGE>' marker before page 1's text,
    one '<PAGE>' between each subsequent page, no forced newlines around it —
    so tests can verify each page landed under its own page number rather than
    just that some split happened to succeed."""
    def __init__(self):
        self.calls = 0
        self.prompts_seen = []

    async def __call__(self, images, prompt, timeout):
        self.calls += 1
        self.prompts_seen.append(prompt)
        return "".join(
            f"<PAGE>Batch-sourced text unique to slot {i} of call {self.calls}."
            for i in range(len(images))
        )


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


# ─── _split_batch_output: matches infer_multi()'s real '<PAGE>'-delimited output ──

def test_split_batch_output_well_formed():
    text = "<PAGE>text1<PAGE>text2<PAGE>text3"
    texts, ok = task_queue._split_batch_output(text, 3)
    assert ok is True
    assert texts == ["text1", "text2", "text3"]


def test_split_batch_output_lowercase_marker():
    text = "<page>text1<page>text2"
    texts, ok = task_queue._split_batch_output(text, 2)
    assert ok is True
    assert texts == ["text1", "text2"]


def test_split_batch_output_discards_noise_before_first_marker():
    text = "Setting `pad_token_id`...\n<PAGE>text1<PAGE>text2"
    texts, ok = task_queue._split_batch_output(text, 2)
    assert ok is True
    assert texts == ["text1", "text2"], "leading noise must be dropped, not glued onto page 1"


def test_split_batch_output_too_few_segments_is_a_mismatch():
    text = "<PAGE>text1<PAGE>text2"  # 2 markers, 3 pages expected
    texts, ok = task_queue._split_batch_output(text, 3)
    assert ok is False


def test_split_batch_output_too_many_segments_is_a_mismatch():
    text = "<PAGE>text1<PAGE>text2<PAGE>text3<PAGE>text4"  # 4 markers, 3 pages expected
    texts, ok = task_queue._split_batch_output(text, 3)
    assert ok is False


def test_split_batch_output_no_markers_is_a_mismatch():
    text = "### ❌ Unlimited-OCR inference failed\nsome traceback"
    texts, ok = task_queue._split_batch_output(text, 3)
    assert ok is False


def test_split_batch_output_empty_text_is_a_mismatch():
    texts, ok = task_queue._split_batch_output("", 2)
    assert ok is False
    texts, ok = task_queue._split_batch_output(None, 2)
    assert ok is False


def test_split_batch_output_single_page_chunk():
    text = "<PAGE>only page text"
    texts, ok = task_queue._split_batch_output(text, 1)
    assert ok is True
    assert texts == ["only page text"]


# ─── run_chunk_batch_mode: correct attribution + no-misattribution safety net ────

class _TrackedSinglePageRunner:
    """run_unlimited_func stand-in for the single-page fallback path — returns
    text that's unmistakably NOT batch-sourced."""
    async def __call__(self, pil_img, prompt, timeout, *rest):
        return "SINGLE-PAGE FALLBACK TEXT"


@pytest.mark.asyncio
async def test_batch_mode_attributes_each_page_its_own_text(tmp_path):
    # overlap=False, chunk_size=3, 6 pages -> chunk1 owns 1-3, chunk2 owns 4-6.
    # escalate_before_flagging=False: this test is about batch attribution, not
    # confidence scoring/escalation, and run_unlimited_func is None here so a
    # confidence-triggered escalation attempt would crash trying to call it.
    job_id = _make_multipage_pdf_job(
        tmp_path, n_pages=6, chunk_size=3, processing_mode="batch", enable_overlap=False,
        escalate_before_flagging=False,
    )
    multi_runner = _MultiPageCleanRunner()

    await task_queue.process_ocr_job(
        job_id, run_ocr_func=None, run_unlimited_func=None, prompts_dict={"document": "p"},
        run_ocr_multi_func=multi_runner,
    )

    job = database.get_job(job_id)
    assert job["status"] == "completed"
    pages = {p["page_num"]: p for p in database.get_pages(job_id)}
    assert len(pages) == 6
    for chunk_start, owned in ((1, [1, 2, 3]), (4, [4, 5, 6])):
        for slot, page_num in enumerate(owned):
            assert pages[page_num]["source"] == "batch", (
                f"page {page_num} must be attributed straight from the batch "
                "response, not kicked to single-page fallback"
            )
            assert f"slot {slot} of call" in pages[page_num]["text"], (
                f"page {page_num} must carry its own slot's text, not another page's"
            )
    # Every page's text must be unique - nothing got duplicated across pages.
    assert len({p["text"] for p in pages.values()}) == 6
    # No "<image>" prefix here: run_ocr_multi() (gpu-ocr-service) adds it itself.
    # A prefix at this layer would double it up and, per the model's 2-part
    # prompt.split('<image>') parsing, silently drop the instruction text.
    assert multi_runner.prompts_seen == ["Multi page parsing."] * len(multi_runner.prompts_seen)


@pytest.mark.asyncio
async def test_batch_mode_malformed_response_falls_back_every_page_in_chunk(tmp_path):
    job_id = _make_multipage_pdf_job(
        tmp_path, n_pages=3, chunk_size=3, processing_mode="batch", enable_overlap=False,
        escalate_before_flagging=False,
    )

    async def _malformed_multi(images, prompt, timeout):
        return "<PAGE>only one segment, but 3 pages were sent"  # count mismatch

    single_runner = _TrackedSinglePageRunner()

    await task_queue.process_ocr_job(
        job_id, run_ocr_func=None, run_unlimited_func=single_runner, prompts_dict={"document": "p"},
        run_ocr_multi_func=_malformed_multi,
    )

    job = database.get_job(job_id)
    assert job["status"] == "completed"
    pages = database.get_pages(job_id)
    assert len(pages) == 3
    for p in pages:
        assert p["text"] == "SINGLE-PAGE FALLBACK TEXT", (
            "a malformed batch response must never leave misattributed batch text "
            "or a false-empty page behind - every page must be retried single-page"
        )
        assert p["source"] != "batch"


@pytest.mark.asyncio
async def test_batch_mode_only_the_malformed_chunk_falls_back(tmp_path):
    # overlap=False, chunk_size=3, 5 pages -> chunk1 owns 1-3 (3 images),
    # chunk2 owns 4-5 (2 images). Discriminate on image count so the test is
    # deterministic regardless of which chunk's coroutine the event loop runs first.
    job_id = _make_multipage_pdf_job(
        tmp_path, n_pages=5, chunk_size=3, processing_mode="batch", enable_overlap=False,
        escalate_before_flagging=False,
    )

    async def _mixed_multi(images, prompt, timeout):
        if len(images) == 3:
            return "<PAGE>only one segment"  # chunk1: malformed
        return "<PAGE>text4<PAGE>text5"  # chunk2: well-formed

    single_runner = _TrackedSinglePageRunner()

    await task_queue.process_ocr_job(
        job_id, run_ocr_func=None, run_unlimited_func=single_runner, prompts_dict={"document": "p"},
        run_ocr_multi_func=_mixed_multi,
    )

    pages = {p["page_num"]: p for p in database.get_pages(job_id)}
    for page_num in (1, 2, 3):
        assert pages[page_num]["text"] == "SINGLE-PAGE FALLBACK TEXT"
    assert pages[4]["text"] == "text4"
    assert pages[5]["text"] == "text5"
    assert pages[4]["source"] == "batch"
    assert pages[5]["source"] == "batch"


@pytest.mark.asyncio
async def test_unexpected_db_error_fails_only_that_page_and_job_continues(tmp_path, monkeypatch):
    """Regression: an exception outside the OCR call itself (e.g. a DB write
    failure inside score_and_store) must not escape to asyncio.gather() and
    crash the whole job - only that one page should fail, cleanly, and the
    page must never be left stuck at status 'processing'."""
    job_id = _make_multipage_pdf_job(
        tmp_path, n_pages=3, chunk_size=5, concurrency=1,
        escalate_before_flagging=False,
    )
    runner = _AlwaysCleanRunner()

    real_upsert_page = database.upsert_page
    raised = {"done": False}

    def _flaky_upsert_page(job_id_arg, page_num, **fields):
        if page_num == 2 and not raised["done"]:
            raised["done"] = True
            raise RuntimeError("simulated DB write failure")
        return real_upsert_page(job_id_arg, page_num, **fields)

    monkeypatch.setattr(database, "upsert_page", _flaky_upsert_page)

    await task_queue.process_ocr_job(
        job_id, run_ocr_func=None, run_unlimited_func=runner, prompts_dict={"document": "p"},
    )

    job = database.get_job(job_id)
    assert job["status"] == "completed"
    pages = {p["page_num"]: p for p in database.get_pages(job_id)}

    assert pages[1]["status"] == "success"
    assert pages[3]["status"] == "success", (
        "the page after the failure must still get its own genuine attempt"
    )

    failed = pages[2]
    assert failed["status"] == "failed"
    assert failed["text"] == task_queue.MANUAL_REVIEW_TEXT
    assert failed["needs_review"] == 1
    assert "simulated DB write failure" in failed["error"]


@pytest.mark.asyncio
async def test_batch_mode_image_load_failure_falls_back_only_that_chunk(tmp_path, monkeypatch):
    """Regression: an image-rendering failure for one page in a batch chunk
    must degrade to the single-page fallback (same as a malformed batch
    response already does), not raise out of run_chunk_batch_mode and crash
    the whole job."""
    job_id = _make_multipage_pdf_job(
        tmp_path, n_pages=5, chunk_size=3, processing_mode="batch", enable_overlap=False,
        escalate_before_flagging=False,
    )

    real_render_page = task_queue._render_page
    call_count = {"n": 0}

    async def _flaky_render_page(pdf_doc, index, scale):
        if index == 1 and call_count["n"] == 0:  # page 2 (0-based), first attempt only
            call_count["n"] += 1
            raise RuntimeError("simulated render failure")
        return await real_render_page(pdf_doc, index, scale)

    monkeypatch.setattr(task_queue, "_render_page", _flaky_render_page)

    async def _mixed_multi(images, prompt, timeout):
        if len(images) == 3:
            return "<PAGE>should never be reached: chunk1's image load fails first"
        return "<PAGE>text4<PAGE>text5"  # chunk2: well-formed, unaffected

    single_runner = _TrackedSinglePageRunner()

    await task_queue.process_ocr_job(
        job_id, run_ocr_func=None, run_unlimited_func=single_runner, prompts_dict={"document": "p"},
        run_ocr_multi_func=_mixed_multi,
    )

    job = database.get_job(job_id)
    assert job["status"] == "completed"
    pages = {p["page_num"]: p for p in database.get_pages(job_id)}
    for page_num in (1, 2, 3):
        assert pages[page_num]["text"] == "SINGLE-PAGE FALLBACK TEXT", (
            "an image-load failure anywhere in the chunk must fall back to "
            "single-page for the whole chunk, not crash the job"
        )
    assert pages[4]["text"] == "text4"
    assert pages[5]["text"] == "text5"


@pytest.mark.asyncio
async def test_resume_skips_already_successful_pages(tmp_path):
    """Regression: resuming a partially-completed job must not silently walk
    back through the whole document from page 1 re-processing pages that
    already succeeded - it must pick up only pages that haven't reached a
    terminal success/review state yet. (Production incident: a paused 955-page
    job was resumed and spent ~15 minutes re-doing pages 1-97, which had
    already completed, before it would ever have reached the genuinely
    pending page 98 - see the project plan.)"""
    job_id = _make_multipage_pdf_job(
        tmp_path, n_pages=5, chunk_size=5, concurrency=1,
        escalate_before_flagging=False,
    )
    # Simulate an earlier partial run: pages 1-3 already succeeded.
    for p in (1, 2, 3):
        database.upsert_page(job_id, p, status="success", text=f"already done page {p}", confidence=0.99)

    runner = _AlwaysCleanRunner()

    await task_queue.process_ocr_job(
        job_id, run_ocr_func=None, run_unlimited_func=runner, prompts_dict={"document": "p"},
    )

    job = database.get_job(job_id)
    assert job["status"] == "completed"
    assert runner.calls == 2, "only the two never-attempted pages (4, 5) should be attempted on resume"

    pages = {p["page_num"]: p for p in database.get_pages(job_id)}
    for p in (1, 2, 3):
        assert pages[p]["text"] == f"already done page {p}", (
            f"an already-successful page {p} must be left untouched by a resume"
        )
    for p in (4, 5):
        assert pages[p]["status"] == "success"


@pytest.mark.asyncio
async def test_resume_does_retry_a_previously_failed_page(tmp_path):
    """A page that ended in 'failed' status is NOT auto-retried by a resume
    (only the existing manual per-page retry does that) - only 'pending' pages
    get picked up automatically."""
    job_id = _make_multipage_pdf_job(
        tmp_path, n_pages=3, chunk_size=5, concurrency=1,
        escalate_before_flagging=False,
    )
    database.upsert_page(job_id, 1, status="failed", text=task_queue.MANUAL_REVIEW_TEXT, error="boom")

    runner = _AlwaysCleanRunner()

    await task_queue.process_ocr_job(
        job_id, run_ocr_func=None, run_unlimited_func=runner, prompts_dict={"document": "p"},
    )

    assert runner.calls == 2, "the failed page must not be auto-retried by a resume"
    pages = {p["page_num"]: p for p in database.get_pages(job_id)}
    assert pages[1]["status"] == "failed", "a failed page is left alone for manual retry, not silently redone"
    assert pages[2]["status"] == "success"
    assert pages[3]["status"] == "success"


@pytest.mark.asyncio
async def test_resume_skips_already_done_pages_in_batch_mode(tmp_path):
    """Batch mode must skip already-successful pages the same way single-page
    mode does - otherwise a resumed batch job would re-submit whole chunks
    that were already completed."""
    job_id = _make_multipage_pdf_job(
        tmp_path, n_pages=6, chunk_size=3, processing_mode="batch", enable_overlap=False,
        escalate_before_flagging=False,
    )
    # Simulate an earlier partial run: the whole first chunk (pages 1-3) already succeeded.
    for p in (1, 2, 3):
        database.upsert_page(job_id, p, status="success", text=f"already done page {p}", confidence=0.99)

    multi_runner = _MultiPageCleanRunner()

    await task_queue.process_ocr_job(
        job_id, run_ocr_func=None, run_unlimited_func=None, prompts_dict={"document": "p"},
        run_ocr_multi_func=multi_runner,
    )

    job = database.get_job(job_id)
    assert job["status"] == "completed"
    # Only the second chunk (pages 4-6) should ever have been submitted for
    # inference - the already-done first chunk must not be resubmitted.
    assert multi_runner.calls == 1
    pages = {p["page_num"]: p for p in database.get_pages(job_id)}
    for p in (1, 2, 3):
        assert pages[p]["text"] == f"already done page {p}"
    for p in (4, 5, 6):
        assert pages[p]["status"] == "success"
        assert pages[p]["source"] == "batch"


@pytest.mark.asyncio
async def test_job_level_failure_does_not_store_a_raw_traceback(tmp_path):
    """Regression: a genuinely fatal error outside the per-page loop (e.g. the
    source file is missing) must still fail the job, but the message shown to
    the user (ocr_jobs.error_message, rendered raw by the frontend) must be a
    short, clean reason - never a raw Python traceback."""
    job_id = str(uuid.uuid4())
    missing_path = tmp_path / "does_not_exist.pdf"
    database.create_job(
        job_id=job_id, filename="does_not_exist.pdf", file_path=str(missing_path), file_type="pdf",
        engine="unlimited", scale=1.0, tiling=False, prompt_mode="document", custom_prompt=None,
        total_pages=1,
    )

    await task_queue.process_ocr_job(
        job_id, run_ocr_func=None, run_unlimited_func=_AlwaysCleanRunner(), prompts_dict={"document": "p"},
    )

    job = database.get_job(job_id, include_pages=False)
    assert job["status"] == "failed"
    assert "Traceback (most recent call last)" not in job["error_message"], (
        "the raw traceback must never reach ocr_jobs.error_message / the frontend - "
        "only a short, friendly reason belongs there"
    )
    assert "Source file not found" in job["error_message"]


# ─── retry_single_page: now delegates to _escalate_page for "highest quality" ────

@pytest.mark.asyncio
async def test_manual_retry_uses_escalation_quality_settings(tmp_path):
    """retry_single_page must force max scale + tiling on and use 1.5x the
    timeout - the same "highest quality possible" treatment the automatic
    escalation pass already gives 'review' pages - not the job's original
    scale/tiling/timeout."""
    job_id = _make_multipage_pdf_job(tmp_path, n_pages=1, chunk_size=5)  # helper's job scale is 1.0
    database.upsert_page(job_id, 1, status="failed", text=task_queue.MANUAL_REVIEW_TEXT, error="boom")

    seen = {}

    async def glm_runner(pil_img, prompt, tiling, timeout, ocr_options):
        seen["tiling"] = tiling
        seen["size"] = pil_img.size
        seen["timeout"] = timeout
        return "A perfectly clean retried result for this page right here."

    await task_queue.retry_single_page(job_id, 1, "glm", glm_runner, None, "document parsing.", False, 100)

    assert seen["tiling"] is True, "manual retry must force tiling on regardless of the job's original tiling setting"
    assert seen["timeout"] == int(100 * 1.5), "manual retry must use 1.5x the requested timeout, same as automatic escalation"
    expected_scale = min(1.0 * config.OCR_ESCALATION_SCALE_FACTOR, config.OCR_MAX_RENDER_SCALE)
    expected_px = 200 * expected_scale  # helper's pages are 200x200pt
    assert seen["size"][0] == pytest.approx(expected_px, abs=2), (
        "manual retry must render at the escalated scale, not the job's original scale"
    )


@pytest.mark.asyncio
async def test_manual_retry_keeps_old_text_when_new_attempt_scores_worse(tmp_path):
    job_id = _make_image_job(tmp_path)
    good_text = "This is a perfectly clean page of ordinary transcribed text content here."
    database.upsert_page(job_id, 1, status="review", text=good_text, confidence=0.9, needs_review=1)

    async def bad_runner(pil_img, prompt, timeout, *rest):
        return "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"

    updated = await task_queue.retry_single_page(job_id, 1, "unlimited", None, bad_runner, "document parsing.", False, 60)

    assert updated["text"] == good_text, "a retry that scores worse than the existing text must not overwrite it"
    assert updated["status"] != "processing", (
        "a manual retry must always land on a terminal status - never leave the page "
        "stuck at 'processing' (the claim made before this attempt started)"
    )
    assert database.claim_page_for_processing(job_id, 1) is True, (
        "the page must be claimable again immediately - a stuck 'processing' status "
        "would block any further retry"
    )


@pytest.mark.asyncio
async def test_manual_retry_adopts_new_text_when_it_scores_better(tmp_path):
    job_id = _make_image_job(tmp_path)
    database.upsert_page(
        job_id, 1, status="review", confidence=0.1, needs_review=1,
        text="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    )
    good_text = "A clean and well-formed retried result for this page."

    async def good_runner(pil_img, prompt, timeout, *rest):
        return good_text

    updated = await task_queue.retry_single_page(job_id, 1, "unlimited", None, good_runner, "document parsing.", False, 60)

    assert updated["text"] == good_text, "a retry that scores better than the existing text must replace it"


@pytest.mark.asyncio
async def test_manual_retry_that_raises_does_not_leave_the_page_stuck_processing(tmp_path):
    """Regression: a manual retry claims the page (status='processing') before
    calling _escalate_page. If the attempt exhausts its retries via a real
    exception, _escalate_page's failure branch must still land the page on a
    terminal status - leaving it at 'processing' means the frontend shows no
    outcome at all, and a further retry is refused (the page can't be
    reclaimed) - exactly what happened live to page 99 after a retry."""
    job_id = _make_image_job(tmp_path)
    database.upsert_page(job_id, 1, status="failed", text=task_queue.MANUAL_REVIEW_TEXT, error="boom")

    async def always_raises(pil_img, prompt, timeout, *rest):
        raise RuntimeError("simulated engine crash for this retry")

    updated = await task_queue.retry_single_page(job_id, 1, "unlimited", None, always_raises, "document parsing.", False, 60)

    assert updated["status"] == "review", "a failed retry must land on a terminal status, not stay 'processing'"
    assert database.claim_page_for_processing(job_id, 1) is True, (
        "the page must be claimable again immediately after a failed retry"
    )


@pytest.mark.asyncio
async def test_manual_retry_stores_friendly_placeholder_not_raw_error_when_it_fails_again(tmp_path):
    """Regression: the OCR engine doesn't raise on a timeout/crash - it returns
    a formatted "### ❌ ... failed" string instead (see unlimited_ocr_engine.py),
    so a repeat failure must not land in the page's displayed text verbatim -
    it must be swapped for the same friendly MANUAL_REVIEW_TEXT placeholder the
    base pass already uses, with the real detail preserved in the error column."""
    job_id = _make_image_job(tmp_path)
    database.upsert_page(
        job_id, 1, status="review", text=task_queue.MANUAL_REVIEW_TEXT,
        confidence=0.0, needs_review=1, error="old stale error from a previous attempt",
    )
    raw_error_text = (
        "### ❌ Unlimited-OCR inference failed\n```\n"
        "TimeoutError: Unlimited-OCR inference timed out after 720s. The stalled worker process was terminated.\n```"
    )

    async def timeout_like_runner(pil_img, prompt, timeout, *rest):
        return raw_error_text  # no exception raised - matches the real timeout/crash behavior

    updated = await task_queue.retry_single_page(job_id, 1, "unlimited", None, timeout_like_runner, "document parsing.", False, 60)

    assert updated["text"] == task_queue.MANUAL_REVIEW_TEXT, "the raw error text must never be stored as the page's displayed content"
    assert raw_error_text in updated["error"], "the real error detail must still be preserved in the error column"


@pytest.mark.asyncio
async def test_manual_retry_clears_stale_error_on_a_genuine_clean_success(tmp_path):
    """Regression: _escalate_page's success branch never passed error= to
    upsert_page at all, so a page that previously failed and was then
    successfully retried kept showing its old, now-irrelevant error message."""
    job_id = _make_image_job(tmp_path)
    database.upsert_page(
        job_id, 1, status="review", text="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        confidence=0.1, needs_review=1, error="stale error from a previous failed attempt",
    )
    good_text = "A clean and well-formed retried result for this page."

    async def good_runner(pil_img, prompt, timeout, *rest):
        return good_text

    updated = await task_queue.retry_single_page(job_id, 1, "unlimited", None, good_runner, "document parsing.", False, 60)

    assert updated["text"] == good_text
    assert updated["error"] == "", "a genuine clean success must clear any stale error left over from a previous attempt"


# ─── retry_pages_bulk: scope=failed vs scope=review vs scope=all, double-start guard, pause ──

@pytest.mark.asyncio
async def test_retry_pages_bulk_failed_scope_only_touches_failed(tmp_path):
    job_id = _make_multipage_pdf_job(tmp_path, n_pages=4, chunk_size=5, concurrency=1, escalate_before_flagging=False)
    database.upsert_page(job_id, 1, status="success", text="already good", confidence=0.95)
    database.upsert_page(job_id, 2, status="failed", text=task_queue.MANUAL_REVIEW_TEXT, error="boom")
    database.upsert_page(
        job_id, 3, status="review", confidence=0.1, needs_review=1,
        text="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    )
    database.upsert_page(job_id, 4, status="pending")
    runner = _AlwaysCleanRunner()

    await task_queue.retry_pages_bulk(job_id, "unlimited", None, runner, "document parsing.", False, 60, None, "failed")

    pages = {p["page_num"]: p for p in database.get_pages(job_id)}
    assert pages[1]["text"] == "already good", "an untouched success page must not be retried under scope=failed"
    assert pages[2]["status"] == "success"
    assert pages[3]["status"] == "review", "a low-confidence page must not be swept up by scope=failed"
    assert pages[4]["status"] == "pending", "a not-yet-attempted page must not be swept up by scope=failed"
    assert runner.calls == 1
    assert database.get_job(job_id, include_pages=False)["status"] == "completed"


@pytest.mark.asyncio
async def test_retry_pages_bulk_review_scope_only_touches_review(tmp_path):
    job_id = _make_multipage_pdf_job(tmp_path, n_pages=4, chunk_size=5, concurrency=1, escalate_before_flagging=False)
    database.upsert_page(job_id, 1, status="success", text="already good", confidence=0.95)
    database.upsert_page(job_id, 2, status="failed", text=task_queue.MANUAL_REVIEW_TEXT, error="boom")
    database.upsert_page(
        job_id, 3, status="review", confidence=0.1, needs_review=1,
        text="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    )
    database.upsert_page(job_id, 4, status="pending")
    runner = _AlwaysCleanRunner()

    await task_queue.retry_pages_bulk(job_id, "unlimited", None, runner, "document parsing.", False, 60, None, "review")

    pages = {p["page_num"]: p for p in database.get_pages(job_id)}
    assert pages[1]["text"] == "already good", "an untouched success page must not be retried under scope=review"
    assert pages[2]["status"] == "failed", "a hard-failed page must not be swept up by scope=review"
    assert pages[3]["status"] == "success"
    assert pages[4]["status"] == "pending", "a not-yet-attempted page must not be swept up by scope=review"
    assert runner.calls == 1
    assert database.get_job(job_id, include_pages=False)["status"] == "completed"


@pytest.mark.asyncio
async def test_retry_pages_bulk_all_scope_touches_every_page(tmp_path):
    job_id = _make_multipage_pdf_job(tmp_path, n_pages=3, chunk_size=5, concurrency=1, escalate_before_flagging=False)
    database.upsert_page(job_id, 1, status="success", text="already good", confidence=0.95)
    database.upsert_page(job_id, 2, status="success", text="also good", confidence=0.95)
    database.upsert_page(job_id, 3, status="pending")
    runner = _AlwaysCleanRunner()

    await task_queue.retry_pages_bulk(job_id, "unlimited", None, runner, "document parsing.", False, 60, None, "all")

    assert runner.calls == 3, "scope=all must attempt every page, including already-successful ones"


@pytest.mark.asyncio
async def test_retry_pages_bulk_second_call_is_a_noop_while_first_is_running(tmp_path):
    job_id = _make_multipage_pdf_job(tmp_path, n_pages=2, chunk_size=5, concurrency=1, escalate_before_flagging=False)
    database.upsert_page(job_id, 1, status="failed", text=task_queue.MANUAL_REVIEW_TEXT, error="boom")
    database.upsert_page(job_id, 2, status="failed", text=task_queue.MANUAL_REVIEW_TEXT, error="boom")

    started = asyncio.Event()
    proceed = asyncio.Event()
    calls = {"n": 0}

    async def blocking_runner(pil_img, prompt, timeout, *rest):
        calls["n"] += 1
        started.set()
        await proceed.wait()
        return "Clean text."

    task1 = asyncio.create_task(
        task_queue.retry_pages_bulk(job_id, "unlimited", None, blocking_runner, "document parsing.", False, 60, None, "failed")
    )
    await started.wait()
    assert task_queue.is_bulk_retry_active(job_id) is True

    # A second call while the first is still mid-flight must be a no-op.
    await task_queue.retry_pages_bulk(job_id, "unlimited", None, blocking_runner, "document parsing.", False, 60, None, "failed")

    proceed.set()
    await task1

    assert calls["n"] == 2, "only the first call's own loop should ever run (one call per target page)"
    assert task_queue.is_bulk_retry_active(job_id) is False


@pytest.mark.asyncio
async def test_retry_pages_bulk_paused_jobs_interrupts_mid_loop(tmp_path):
    job_id = _make_multipage_pdf_job(tmp_path, n_pages=3, chunk_size=5, concurrency=1, escalate_before_flagging=False)
    for p in (1, 2, 3):
        database.upsert_page(job_id, p, status="failed", text=task_queue.MANUAL_REVIEW_TEXT, error="boom")
    calls = {"n": 0}

    async def runner(pil_img, prompt, timeout, *rest):
        calls["n"] += 1
        if calls["n"] == 1:
            task_queue._paused_jobs.add(job_id)
        return "Clean text."

    try:
        await task_queue.retry_pages_bulk(job_id, "unlimited", None, runner, "document parsing.", False, 60, None, "failed")
    finally:
        task_queue._paused_jobs.discard(job_id)

    assert calls["n"] == 1, "pausing mid-loop must stop further pages from being claimed"
    assert database.get_job(job_id, include_pages=False)["status"] == "paused"
