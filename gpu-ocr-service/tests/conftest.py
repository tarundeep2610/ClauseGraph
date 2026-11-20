import os
import sys

os.environ["GPU_OCR_SHARED_SECRET"] = "test-gpu-secret"
os.environ["GPU_OCR_MAX_CONCURRENT_JOBS"] = "1"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio

import pytest
from fastapi.testclient import TestClient

import config
import main as gpu_main


class _FakeUnlimitedEngine:
    """Stub swapped in for the real unlimited_ocr_engine (which needs torch) in tests."""

    def __init__(self):
        self.calls = []
        self.script = []  # list of results or Exceptions to return/raise, one per call
        self.cancel_calls = 0

    def is_loaded(self):
        return True

    def run_ocr(self, pil_image, prompt, base_size, timeout):
        self.calls.append((prompt, base_size, timeout))
        effect = self.script.pop(0)
        if isinstance(effect, Exception):
            raise effect
        return effect

    def run_ocr_with_raw(self, pil_image, prompt, base_size, timeout, crop_mode=False):
        """Additive counterpart the real module exposes alongside run_ocr()
        (see unlimited_ocr_engine.run_ocr_with_raw) — gpu-ocr-service/main.py
        calls this one for the single-page path. Routes through self.run_ocr
        so a test that overrides that method (e.g. to simulate slow/blocking
        inference) is honored here too; raw_result is None by default since
        no test currently asserts on it. crop_mode is accepted (matching the
        real signature) but not otherwise used by this fake."""
        text = self.run_ocr(pil_image, prompt, base_size, timeout)
        return text, None

    def cancel_current_work(self):
        self.cancel_calls += 1


@pytest.fixture()
def fake_engine(monkeypatch):
    fake = _FakeUnlimitedEngine()
    monkeypatch.setattr(gpu_main, "_UNLIMITED_OCR_AVAILABLE", True)
    monkeypatch.setattr(gpu_main, "unlimited_ocr_engine", fake)
    return fake


@pytest.fixture(autouse=True)
def _reset_job_state():
    """_jobs/_job_tasks/_inference_semaphore are plain module globals in
    main.py, not scoped to any one TestClient/event loop.

    Without this, jobs (and any straggler tasks) from an earlier test leak
    into the next one, e.g. inflating cancel-all's counted total. Worse,
    asyncio.Semaphore binds permanently to whichever event loop first awaits
    it (see asyncio.mixins._LoopBoundMixin) - each test's `client` fixture
    spins up a fresh TestClient/event loop, so reusing the one semaphore
    object across tests raises "bound to a different event loop" the moment
    a later test's job tries to acquire it. Rebuilding it fresh per test
    sidesteps this entirely; production never hits it since one process only
    ever has the one long-lived loop."""
    gpu_main._jobs.clear()
    gpu_main._job_tasks.clear()
    gpu_main._inference_semaphore = asyncio.Semaphore(config.GPU_OCR_MAX_CONCURRENT_JOBS)
    yield
    gpu_main._jobs.clear()
    gpu_main._job_tasks.clear()


@pytest.fixture()
def client():
    with TestClient(gpu_main.app) as c:
        yield c


@pytest.fixture()
def auth_headers():
    return {"x-gpu-ocr-key": "test-gpu-secret"}
