import os
import sys

os.environ["GPU_OCR_SHARED_SECRET"] = "test-gpu-secret"
os.environ["GPU_OCR_MAX_CONCURRENT_JOBS"] = "1"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

import main as gpu_main


class _FakeUnlimitedEngine:
    """Stub swapped in for the real unlimited_ocr_engine (which needs torch) in tests."""

    def __init__(self):
        self.calls = []
        self.script = []  # list of results or Exceptions to return/raise, one per call

    def is_loaded(self):
        return True

    def run_ocr(self, pil_image, prompt, base_size, timeout):
        self.calls.append((prompt, base_size, timeout))
        effect = self.script.pop(0)
        if isinstance(effect, Exception):
            raise effect
        return effect


@pytest.fixture()
def fake_engine(monkeypatch):
    fake = _FakeUnlimitedEngine()
    monkeypatch.setattr(gpu_main, "_UNLIMITED_OCR_AVAILABLE", True)
    monkeypatch.setattr(gpu_main, "unlimited_ocr_engine", fake)
    return fake


@pytest.fixture()
def client():
    with TestClient(gpu_main.app) as c:
        yield c


@pytest.fixture()
def auth_headers():
    return {"x-gpu-ocr-key": "test-gpu-secret"}
