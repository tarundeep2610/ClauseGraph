"""Tests for main.run_unlimited_ocr_for_image_with_raw()'s crop_mode
threading — the toggle that switches Unlimited-OCR into its "gundam"
config (image_size=640, per-region crops), confirmed via a direct test
against the model (see the project plan) to be what actually makes it
emit <|det|> layout tags. The prompt itself is never modified by this
toggle — an earlier version prepended a `<|grounding|>` token, but that
was confirmed to do nothing useful (and to degrade output quality) and
was removed."""

import pytest
from PIL import Image

import config
import main


class _CapturingEngine:
    """Fake unlimited_ocr_engine — records the exact prompt/crop_mode it
    was called with instead of running real inference."""

    def __init__(self):
        self.calls = []

    def run_ocr_with_raw(self, pil_image, prompt, base_size, timeout, crop_mode=False):
        self.calls.append((prompt, crop_mode))
        return ("cleaned", "raw")


@pytest.fixture
def capturing_engine(monkeypatch):
    engine = _CapturingEngine()
    monkeypatch.setattr(config, "OCR_LOCAL_ENGINE", True)
    monkeypatch.setattr(main, "_UNLIMITED_OCR_AVAILABLE", True)
    monkeypatch.setattr(main, "unlimited_ocr_engine", engine)
    return engine


@pytest.mark.asyncio
async def test_grounding_off_long_prompts_off_sends_the_short_label_with_crop_mode_off(capturing_engine):
    await main.run_unlimited_ocr_for_image_with_raw(
        Image.new("RGB", (10, 10)), "a" * 200, timeout=60,
        long_prompts_enabled=False, grounding_enabled=False,
    )
    assert capturing_engine.calls == [("document parsing.", False)]


@pytest.mark.asyncio
async def test_grounding_on_does_not_modify_the_prompt_only_sets_crop_mode(capturing_engine):
    await main.run_unlimited_ocr_for_image_with_raw(
        Image.new("RGB", (10, 10)), "a" * 200, timeout=60,
        long_prompts_enabled=False, grounding_enabled=True,
    )
    assert capturing_engine.calls == [("document parsing.", True)]


@pytest.mark.asyncio
async def test_grounding_off_long_prompts_on_sends_the_full_prompt_unmodified_with_crop_mode_off(capturing_engine):
    long_prompt = "a" * 200
    await main.run_unlimited_ocr_for_image_with_raw(
        Image.new("RGB", (10, 10)), long_prompt, timeout=60,
        long_prompts_enabled=True, grounding_enabled=False,
    )
    assert capturing_engine.calls == [(long_prompt, False)]


@pytest.mark.asyncio
async def test_grounding_and_long_prompts_both_on_are_independent(capturing_engine):
    long_prompt = "a" * 200
    await main.run_unlimited_ocr_for_image_with_raw(
        Image.new("RGB", (10, 10)), long_prompt, timeout=60,
        long_prompts_enabled=True, grounding_enabled=True,
    )
    assert capturing_engine.calls == [(long_prompt, True)]


@pytest.mark.asyncio
async def test_grounding_on_with_a_short_prompt_still_only_sets_crop_mode(capturing_engine):
    # A short prompt (<=80 chars) is sent unmodified regardless of
    # long_prompts_enabled — grounding must not touch the prompt text.
    await main.run_unlimited_ocr_for_image_with_raw(
        Image.new("RGB", (10, 10)), "short.", timeout=60,
        long_prompts_enabled=False, grounding_enabled=True,
    )
    assert capturing_engine.calls == [("short.", True)]


@pytest.mark.asyncio
async def test_run_unlimited_ocr_for_image_thin_wrapper_also_threads_crop_mode(capturing_engine):
    text = await main.run_unlimited_ocr_for_image(
        Image.new("RGB", (10, 10)), "a" * 200, timeout=60,
        long_prompts_enabled=False, grounding_enabled=True,
    )
    assert text == "cleaned"
    assert capturing_engine.calls == [("document parsing.", True)]
