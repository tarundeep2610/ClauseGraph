"""Regression tests for the model-load race and the shared-inference race found in
production logs (see the project plan): concurrent requests before the model was
loaded each independently ran the full load sequence, and concurrent inference
calls on the shared `_model` object could cross-contaminate each other's output
(one page's result containing another page's text). Both are fixed by threading
locks (_load_lock, _inference_lock) in unlimited_ocr_engine.py.

Real torch/transformers are installed in this environment, so these tests
monkeypatch AutoModel.from_pretrained/AutoTokenizer.from_pretrained (and, for the
inference tests, the loaded _model's own infer()/infer_multi()) to fast, call-
counted, concurrency-tracking fakes instead of hitting the network or a real GPU.
"""
import os
import sys
import threading
import time

import pytest
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("UNLIMITED_OCR_ALLOW_REMOTE_CODE", "1")

import unlimited_ocr_engine as engine


@pytest.fixture(autouse=True)
def _reset_engine_load_state():
    """Every test starts from a clean, unloaded state and leaves one behind."""
    engine._loaded = False
    engine._model = None
    engine._tokenizer = None
    engine._load_error = None
    yield
    engine._loaded = False
    engine._model = None
    engine._tokenizer = None
    engine._load_error = None


class _FakeTokenizer:
    @classmethod
    def from_pretrained(cls, *args, **kwargs):
        return cls()


def _fake_model_cls(call_counter, in_progress_counter, max_in_progress, delay=0.05, fail_until=None):
    """Builds a fake AutoModel with a from_pretrained that's call-counted and
    asserts it's never entered concurrently."""
    lock = threading.Lock()

    class _FakeModel:
        _attempt = [0]

        @classmethod
        def from_pretrained(cls, *args, **kwargs):
            with lock:
                call_counter["n"] += 1
                in_progress_counter["n"] += 1
                max_in_progress["n"] = max(max_in_progress["n"], in_progress_counter["n"])
            try:
                if fail_until is not None:
                    cls._attempt[0] += 1
                    if cls._attempt[0] < fail_until:
                        raise RuntimeError("simulated transient load failure")
                time.sleep(delay)
                return cls()
            finally:
                with lock:
                    in_progress_counter["n"] -= 1

        def to(self, device):
            return self

        def eval(self):
            return self

    return _FakeModel


def test_concurrent_initialize_only_loads_once(monkeypatch):
    call_counter = {"n": 0}
    in_progress = {"n": 0}
    max_in_progress = {"n": 0}
    fake_model = _fake_model_cls(call_counter, in_progress, max_in_progress, delay=0.08)

    monkeypatch.setattr("transformers.AutoTokenizer", _FakeTokenizer)
    monkeypatch.setattr("transformers.AutoModel", fake_model)

    results = []
    barrier = threading.Barrier(8)

    def _worker():
        barrier.wait()
        results.append(engine.initialize(verbose=False))

    threads = [threading.Thread(target=_worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert call_counter["n"] == 1, "only one thread should ever perform the actual load"
    assert all(results), "every concurrent caller should still get True back"
    assert engine.is_loaded() is True


def test_concurrent_initialize_never_overlaps(monkeypatch):
    call_counter = {"n": 0}
    in_progress = {"n": 0}
    max_in_progress = {"n": 0}
    fake_model = _fake_model_cls(call_counter, in_progress, max_in_progress, delay=0.08)

    monkeypatch.setattr("transformers.AutoTokenizer", _FakeTokenizer)
    monkeypatch.setattr("transformers.AutoModel", fake_model)

    barrier = threading.Barrier(8)

    def _worker():
        barrier.wait()
        engine.initialize(verbose=False)

    threads = [threading.Thread(target=_worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert max_in_progress["n"] <= 1, "two threads must never be inside from_pretrained at the same time"


def test_failed_load_does_not_deadlock_and_allows_retry(monkeypatch):
    call_counter = {"n": 0}
    in_progress = {"n": 0}
    max_in_progress = {"n": 0}
    # Fails on the 1st call, succeeds on the 2nd.
    fake_model = _fake_model_cls(call_counter, in_progress, max_in_progress, delay=0.0, fail_until=2)

    monkeypatch.setattr("transformers.AutoTokenizer", _FakeTokenizer)
    monkeypatch.setattr("transformers.AutoModel", fake_model)

    assert engine.initialize(verbose=False) is False
    assert engine.is_loaded() is False
    assert engine.get_load_error() is not None

    assert engine.initialize(verbose=False) is True
    assert engine.is_loaded() is True


def test_concurrent_calls_during_a_failing_load_all_retry_safely(monkeypatch):
    call_counter = {"n": 0}
    in_progress = {"n": 0}
    max_in_progress = {"n": 0}
    # Always fails (fail_until far beyond any attempt count reached in this test).
    fake_model = _fake_model_cls(call_counter, in_progress, max_in_progress, delay=0.02, fail_until=10_000)

    monkeypatch.setattr("transformers.AutoTokenizer", _FakeTokenizer)
    monkeypatch.setattr("transformers.AutoModel", fake_model)

    results = []
    barrier = threading.Barrier(6)

    def _worker():
        barrier.wait()
        results.append(engine.initialize(verbose=False))

    threads = [threading.Thread(target=_worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert all(r is False for r in results), "a failing load must return False, never hang or raise"
    assert max_in_progress["n"] <= 1, "the failure path must still be fully serialized"
    assert engine.is_loaded() is False


# ─── Inference lock (_inference_lock) — the wrong/mixed-page-output regression ────

class _FakeLoadedModel:
    """Stands in for the already-loaded _model — infer()/infer_multi() write a
    distinguishable output file into their own output_path so a cross-call mixup
    would be directly observable, and track concurrent-entry like the load fakes."""

    def __init__(self):
        self.in_progress = {"n": 0}
        self.max_in_progress = {"n": 0}
        self.call_count = {"n": 0}
        self._lock = threading.Lock()

    def _enter(self):
        with self._lock:
            self.call_count["n"] += 1
            self.in_progress["n"] += 1
            self.max_in_progress["n"] = max(self.max_in_progress["n"], self.in_progress["n"])

    def _exit(self):
        with self._lock:
            self.in_progress["n"] -= 1

    def infer(self, tokenizer, prompt, image_file, output_path, **kwargs):
        self._enter()
        try:
            time.sleep(0.05)
            marker = os.path.basename(image_file)
            with open(os.path.join(output_path, "result.md"), "w", encoding="utf-8") as f:
                f.write(f"RESULT FOR {marker}")
        finally:
            self._exit()

    def infer_multi(self, tokenizer, prompt, image_files, output_path, **kwargs):
        self._enter()
        try:
            time.sleep(0.05)
            marker = ",".join(os.path.basename(p) for p in image_files)
            with open(os.path.join(output_path, "result.md"), "w", encoding="utf-8") as f:
                f.write(f"RESULT FOR {marker}")
        finally:
            self._exit()


def _make_image():
    return Image.new("RGB", (10, 10), color="white")


def test_concurrent_run_ocr_calls_never_overlap_on_the_shared_model():
    fake = _FakeLoadedModel()
    engine._model = fake
    engine._tokenizer = object()
    engine._loaded = True

    results = [None] * 6

    def _worker(i):
        results[i] = engine.run_ocr(_make_image(), prompt="p", base_size=512)

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert fake.call_count["n"] == 6
    assert fake.max_in_progress["n"] <= 1, "two threads must never be inside infer() at the same time"
    for r in results:
        assert r is not None and "RESULT FOR" in r, "every call must get its own result back, not an error/empty string"


def test_concurrent_run_ocr_multi_calls_never_overlap_on_the_shared_model():
    fake = _FakeLoadedModel()
    engine._model = fake
    engine._tokenizer = object()
    engine._loaded = True

    results = [None] * 4

    def _worker(i):
        results[i] = engine.run_ocr_multi([_make_image(), _make_image()], prompt="p")

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert fake.call_count["n"] == 4
    assert fake.max_in_progress["n"] <= 1, "two threads must never be inside infer_multi() at the same time"
    for r in results:
        assert r is not None and "RESULT FOR" in r
