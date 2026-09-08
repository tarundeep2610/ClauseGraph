"""Regression tests for:

1. The model-load race found in production logs: concurrent requests before
   the model was loaded each independently ran the full load sequence. Fixed
   by _load_lock in unlimited_ocr_engine.py. initialize() runs the same way
   before and after Phase 2 (it's only ever called from inside the worker
   process), so these tests are unaffected by the worker-process split below.

2. The worker-process supervisor added to fix the "one runaway page blocks
   every later page" incident (see the project plan): inference now runs in
   a dedicated child process instead of an in-process thread, specifically so
   a timed-out call can be killed outright - a thread's generation call can't
   be force-stopped in Python, so the earlier in-process design left a timed-
   out call running in the background indefinitely, still holding the lock
   that serialized all inference, which could make a later, otherwise-healthy
   page queue behind it too.

Real torch/transformers are installed in this environment, so the load-race
tests monkeypatch AutoModel.from_pretrained/AutoTokenizer.from_pretrained to
fast, call-counted, concurrency-tracking fakes instead of hitting the network
or a real GPU. The worker-process tests spawn REAL child processes (via the
real multiprocessing.Process/Queue machinery being tested), but point them at
lightweight fakes defined in unlimited_ocr_engine.py itself (never invoked in
production) instead of the real _worker_main, so no torch/transformers model
is ever loaded in a child - see _test_fake_worker_* in that module for why
they live there rather than in this test file.
"""
import os
import shutil
import signal
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


@pytest.fixture(autouse=True)
def _reset_worker_state():
    """Every test starts with no worker process running, and any worker left
    running by the test is force-killed afterward so nothing leaks between
    tests (or past the test session)."""
    engine._worker_proc = None
    engine._req_q = None
    engine._resp_q = None
    engine._worker_loaded_flag.value = 0
    engine._worker_load_error = None
    yield
    if engine._worker_proc is not None:
        try:
            engine._worker_proc.kill()
            engine._worker_proc.join(timeout=5)
        except Exception:
            pass
    engine._worker_proc = None
    engine._req_q = None
    engine._resp_q = None
    engine._worker_loaded_flag.value = 0
    engine._worker_load_error = None


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
    assert engine._loaded is True


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
    assert engine._loaded is False
    assert engine._load_error is not None

    assert engine.initialize(verbose=False) is True
    assert engine._loaded is True


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
    assert engine._loaded is False


# ─── Worker-process supervisor: normal calls run in a real child process ─────

def _make_image():
    return Image.new("RGB", (10, 10), color="white")


def test_run_ocr_result_comes_from_a_real_child_process(monkeypatch):
    monkeypatch.setattr(engine, "_worker_main", engine._test_fake_worker_echo)

    result = engine.run_ocr(_make_image(), prompt="p", base_size=512, timeout=10)

    assert "FAKE RESULT for infer" in result
    assert f"pid {os.getpid()}" not in result, (
        "the result must come from a different OS process, not be produced in-process"
    )
    assert "pid " in result, "sanity check: the fake worker is expected to report its own pid"
    assert engine.is_loaded() is True, "a successful response must mark the worker as loaded"


def test_run_ocr_multi_result_comes_from_a_real_child_process(monkeypatch):
    monkeypatch.setattr(engine, "_worker_main", engine._test_fake_worker_echo)

    result = engine.run_ocr_multi([_make_image(), _make_image()], prompt="p", timeout=10)

    assert "FAKE RESULT for infer_multi" in result


def test_worker_process_is_reused_across_calls(monkeypatch):
    """The worker should stay warm across successive successful calls, not be
    respawned every time (that would pay a full model-reload cost per page)."""
    monkeypatch.setattr(engine, "_worker_main", engine._test_fake_worker_echo)

    engine.run_ocr(_make_image(), prompt="p", timeout=10)
    proc_after_first = engine._worker_proc
    assert proc_after_first is not None and proc_after_first.is_alive()

    engine.run_ocr(_make_image(), prompt="p", timeout=10)
    assert engine._worker_proc is proc_after_first, "a healthy worker must be reused, not respawned"


# ─── is_loaded() must reflect live state, not a stale response-cycle cache ───
#
# Regression: is_loaded() used to just return a main-process cache that was
# only ever updated when a _call_worker call received its FINAL response -
# meaning it read False for the model's entire load-then-infer duration on
# a slow (or queued-behind-another-call) page, even though the worker had
# genuinely finished loading long before. Confirmed in production: GPU logs
# showed "Model loaded and ready" while /health still reported
# model_loaded: false for the full duration of that page's inference.

def test_is_loaded_becomes_true_while_a_call_is_still_in_flight(monkeypatch):
    monkeypatch.setattr(engine, "_worker_main", engine._test_fake_worker_echo)
    result_holder = {}

    def _call():
        result_holder["result"] = engine.run_ocr(_make_image(), prompt="p", timeout=10)

    assert engine.is_loaded() is False
    t = threading.Thread(target=_call)
    t.start()

    # _test_fake_worker_echo sets the loaded flag before responding, same as
    # the real worker sets it right after initialize() succeeds and before
    # the (potentially long) inference call - but process spawn + re-import
    # overhead alone can take a few seconds, so rather than guess a fixed
    # window, poll until the flag flips and check *at that exact moment*
    # whether the call is still running - proving the flag went true before
    # the response, not merely eventually.
    became_loaded_while_in_flight = False
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if engine.is_loaded():
            became_loaded_while_in_flight = t.is_alive()
            break
        if not t.is_alive():
            break  # call already finished without us ever observing the flag
        time.sleep(0.01)

    t.join(timeout=10)
    assert became_loaded_while_in_flight, "is_loaded() must go True before the call's response arrives, not only after"
    assert "FAKE RESULT for infer" in result_holder["result"]


def test_is_loaded_resets_to_false_immediately_after_cancel_current_work(monkeypatch):
    monkeypatch.setattr(engine, "_worker_main", engine._test_fake_worker_echo)
    engine.run_ocr(_make_image(), prompt="p", timeout=10)
    assert engine.is_loaded() is True

    engine.cancel_current_work()

    assert engine.is_loaded() is False, "a killed worker's load state must not be trusted"


def test_is_loaded_resets_to_false_for_a_freshly_spawned_worker_after_a_timeout(monkeypatch):
    monkeypatch.setattr(engine, "_worker_main", engine._test_fake_worker_hang)
    engine.run_ocr(_make_image(), prompt="p", timeout=0.3)
    assert engine.is_loaded() is False, "the killed worker's stale load state must not leak forward"

    monkeypatch.setattr(engine, "_worker_main", engine._test_fake_worker_echo)
    engine.run_ocr(_make_image(), prompt="p", timeout=10)
    assert engine.is_loaded() is True, "the fresh worker's own successful load must be reflected"


def test_concurrent_calls_are_serialized_against_the_single_worker(monkeypatch):
    monkeypatch.setattr(engine, "_worker_main", engine._test_fake_worker_echo)
    delay = 0.15
    n_calls = 4

    def _worker(i):
        engine.run_ocr(_make_image(), prompt=f"p{i}", timeout=10)

    start = time.monotonic()
    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(n_calls)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.monotonic() - start

    assert elapsed >= delay * n_calls * 0.8, (
        "calls must be serialized against the one worker, not run concurrently"
    )


# ─── A timed-out call kills its worker and a fresh one serves the next call ──
#
# Regression guard: the pre-Phase-2 in-process design used a ThreadPoolExecutor
# whose timeout only made the *caller* give up waiting - the submitted thread
# kept running _model.infer() in the background indefinitely, still holding
# the lock that serialized all inference, so a later call could queue behind
# it for an unbounded amount of time (see the project plan for the production
# incident: a poor-quality page timed out, and the very next page failed too
# with no useful error). Phase 2 fixes this by running inference in a real
# child process and killing it outright on timeout, which actually stops
# generation and frees GPU memory - the next call always gets a fresh worker
# instead of ever queueing behind an abandoned one.

def test_timed_out_call_kills_the_worker_process(monkeypatch):
    monkeypatch.setattr(engine, "_worker_main", engine._test_fake_worker_hang)

    result = engine.run_ocr(_make_image(), prompt="p", timeout=0.3)

    assert "TimeoutError" in result or "timed out" in result
    assert engine._worker_proc is None, "the stalled worker must be killed, not left running"
    assert engine.is_loaded() is False, "the killed worker's load state must no longer be trusted"


def test_call_after_a_timeout_gets_a_fresh_worker_and_succeeds(monkeypatch):
    monkeypatch.setattr(engine, "_worker_main", engine._test_fake_worker_hang)
    timed_out_result = engine.run_ocr(_make_image(), prompt="p", timeout=0.3)
    assert "timed out" in timed_out_result or "TimeoutError" in timed_out_result
    proc_after_timeout = engine._worker_proc
    assert proc_after_timeout is None

    # A later, independent call must still make its own genuine attempt on a
    # brand new worker - never short-circuited and never left waiting on the
    # process that was just killed.
    monkeypatch.setattr(engine, "_worker_main", engine._test_fake_worker_echo)
    second_result = engine.run_ocr(_make_image(), prompt="p", timeout=10)

    assert "FAKE RESULT for infer" in second_result
    assert engine.is_loaded() is True


def test_crashed_worker_is_detected_and_reported_without_waiting_out_the_full_timeout(monkeypatch):
    monkeypatch.setattr(engine, "_worker_main", engine._test_fake_worker_crash)

    start = time.monotonic()
    result = engine.run_ocr(_make_image(), prompt="p", timeout=30)
    elapsed = time.monotonic() - start

    assert "crashed" in result.lower()
    assert elapsed < 15, "a crashed (not just hung) worker must be detected well before the full timeout"
    assert engine._worker_proc is None


# ─── C: a worker killed by SIGKILL (the real OOM-killer's signature) gets a ──
# distinct, diagnosable error instead of the generic crash message. Regression
# context: a production incident this session showed nothing but repeated
# "Loading model" cycles in the logs with zero exception text between them -
# the fingerprint of a silent SIGKILL, indistinguishable from any other crash
# under the old generic message, which meant confirming an OOM-kill required
# live `docker stats`/`free -h` digging instead of just reading the logs.

_HAS_SIGKILL = hasattr(signal, "SIGKILL")


@pytest.mark.skipif(not _HAS_SIGKILL, reason="SIGKILL is POSIX-only - this simulates a Linux OOM-kill signature")
def test_sigkilled_worker_error_names_the_signal_and_suggests_oom(monkeypatch):
    monkeypatch.setattr(engine, "_worker_main", engine._test_fake_worker_sigkill)

    result = engine.run_ocr(_make_image(), prompt="p", timeout=30)

    assert "signal 9" in result.lower() or "sigkill" in result.lower()
    assert "memory" in result.lower()
    assert engine._worker_proc is None


@pytest.mark.skipif(not _HAS_SIGKILL, reason="SIGKILL is POSIX-only - this simulates a Linux OOM-kill signature")
def test_sigkilled_worker_logs_the_raw_exit_code(monkeypatch, capsys):
    monkeypatch.setattr(engine, "_worker_main", engine._test_fake_worker_sigkill)

    engine.run_ocr(_make_image(), prompt="p", timeout=30)

    captured = capsys.readouterr()
    assert "exitcode=-9" in captured.err


def test_plain_crash_still_returns_the_generic_message_not_the_signal_one(monkeypatch):
    """Regression guard: os._exit(1) is a normal (non-negative) exit code, not
    a signal death - the new exit-code branching must not misclassify it."""
    monkeypatch.setattr(engine, "_worker_main", engine._test_fake_worker_crash)

    result = engine.run_ocr(_make_image(), prompt="p", timeout=30)

    assert "signal" not in result.lower()
    assert "crashed" in result.lower()


# ─── D: a killed worker's death is confirmed, not just assumed ──────────────
# _confirm_worker_dead is tested directly against a fake proc double, not a
# real spawned process - SIGKILL can't be trapped or delayed by a real
# process, so there's no way to make one genuinely linger for a test. The
# fake controls what is_alive() reports across successive calls instead.

class _FakeDyingProc:
    def __init__(self, alive_sequence):
        self.pid = 4242
        self._sequence = list(alive_sequence)
        self.join_call_count = 0

    def join(self, timeout=None):
        self.join_call_count += 1

    def is_alive(self):
        if self._sequence:
            return self._sequence.pop(0)
        return False


def test_confirm_worker_dead_logs_nothing_when_it_dies_immediately(capsys):
    proc = _FakeDyingProc(alive_sequence=[False])

    engine._confirm_worker_dead(proc, context="test-context")

    captured = capsys.readouterr()
    assert captured.err == ""
    assert proc.join_call_count == 1


def test_confirm_worker_dead_logs_one_warning_when_it_dies_after_first_wait(capsys):
    proc = _FakeDyingProc(alive_sequence=[True, False])

    engine._confirm_worker_dead(proc, context="test-context")

    captured = capsys.readouterr()
    assert "still alive 10s after SIGKILL (test-context)" in captured.err
    assert "still alive 20s" not in captured.err
    assert proc.join_call_count == 2


def test_confirm_worker_dead_logs_both_warnings_and_returns_when_it_never_dies(capsys):
    proc = _FakeDyingProc(alive_sequence=[True, True])

    engine._confirm_worker_dead(proc, context="test-context")

    captured = capsys.readouterr()
    assert "still alive 10s after SIGKILL (test-context)" in captured.err
    assert "still alive 20s after SIGKILL (test-context)" in captured.err
    assert proc.join_call_count == 2


# ─── cancel_current_work(): an explicit external "stop everything now" ───────
#
# Used by the frontend's Stop All action (via a new GPU-service endpoint) to
# actually free the GPU instead of just abandoning a CPU-side wait while the
# worker keeps running orphaned. Must work even while another call is
# currently blocked inside _call_worker holding _worker_lock for its whole
# wait - cancel_current_work() deliberately doesn't go through that lock (see
# its docstring), so this exercises that exact concurrent scenario with a
# real second thread, not just sequential calls.

def test_cancel_current_work_stops_a_call_blocked_on_a_hung_worker(monkeypatch):
    monkeypatch.setattr(engine, "_worker_main", engine._test_fake_worker_hang)
    result_holder = {}

    def _blocked_call():
        result_holder["result"] = engine.run_ocr(_make_image(), prompt="p", timeout=30)

    t = threading.Thread(target=_blocked_call)
    t.start()
    deadline = time.monotonic() + 5
    while engine._worker_proc is None and time.monotonic() < deadline:
        time.sleep(0.05)
    assert engine._worker_proc is not None and engine._worker_proc.is_alive(), (
        "the blocked call never got as far as starting a worker - test setup is broken"
    )

    start = time.monotonic()
    engine.cancel_current_work()
    t.join(timeout=10)
    elapsed = time.monotonic() - start

    assert not t.is_alive(), "the blocked call must return after cancellation, not hang for the full 30s timeout"
    assert elapsed < 8, "cancellation must free the blocked call within about one poll interval, not the full timeout"
    assert "crashed" in result_holder["result"].lower() or "cancelled" in result_holder["result"].lower()
    assert engine._worker_proc is None


def test_call_after_cancel_current_work_gets_a_fresh_worker(monkeypatch):
    monkeypatch.setattr(engine, "_worker_main", engine._test_fake_worker_hang)
    result_holder = {}

    def _blocked_call():
        result_holder["result"] = engine.run_ocr(_make_image(), prompt="p", timeout=30)

    t = threading.Thread(target=_blocked_call)
    t.start()
    deadline = time.monotonic() + 5
    while engine._worker_proc is None and time.monotonic() < deadline:
        time.sleep(0.05)

    engine.cancel_current_work()
    t.join(timeout=10)

    monkeypatch.setattr(engine, "_worker_main", engine._test_fake_worker_echo)
    second_result = engine.run_ocr(_make_image(), prompt="p", timeout=10)

    assert "FAKE RESULT for infer" in second_result, "a later call must get a genuine fresh attempt, not stay stuck"
    assert engine.is_loaded() is True


def test_call_queued_behind_worker_lock_is_cancelled_without_starting_fresh_work(monkeypatch):
    """Regression: a call that's already past its own submission and blocked
    waiting for _worker_lock (held by whatever's currently using the worker)
    when cancel_current_work() fires must not, once it finally gets the lock,
    proceed to dispatch fresh work to the freshly-respawned worker - the
    caller has already been told to stop. Without the epoch check, this call
    would silently waste GPU compute on work nobody's waiting for anymore."""
    monkeypatch.setattr(engine, "_worker_main", engine._test_fake_worker_hang)
    result_a = {}
    result_b = {}

    def _call_a():
        result_a["result"] = engine.run_ocr(_make_image(), prompt="a", timeout=30)

    t_a = threading.Thread(target=_call_a)
    t_a.start()
    deadline = time.monotonic() + 5
    while engine._worker_proc is None and time.monotonic() < deadline:
        time.sleep(0.05)
    assert engine._worker_proc is not None and engine._worker_proc.is_alive(), (
        "call A never got as far as starting a worker - test setup is broken"
    )

    def _call_b():
        result_b["result"] = engine.run_ocr(_make_image(), prompt="b", timeout=30)

    t_b = threading.Thread(target=_call_b)
    t_b.start()
    # Give call B time to reach and block on _worker_lock (held by call A).
    time.sleep(0.3)
    assert engine._worker_lock.locked(), "call B never got as far as queueing behind _worker_lock"

    engine.cancel_current_work()
    t_a.join(timeout=10)
    t_b.join(timeout=10)

    assert not t_a.is_alive() and not t_b.is_alive()
    assert "cancelled" in result_b["result"].lower(), (
        f"call B must report it was cancelled while queued, not silently run fresh work: {result_b['result']!r}"
    )
    assert engine._worker_proc is None, (
        "call B must never have spawned a new worker on behalf of a caller who's already been told to stop"
    )


# ─── save_results=True must still reach infer()/infer_multi() ────────────────
#
# This is now exercised through the real worker process, so it also verifies
# the request payload (prompt, image path, output path, base_size) survives
# the trip through the multiprocessing queue unchanged.

def test_run_ocr_output_files_are_read_back_from_the_worker(monkeypatch):
    monkeypatch.setattr(engine, "_worker_main", engine._test_fake_worker_echo)

    seen = {}
    real_rmtree = shutil.rmtree

    def _spy_rmtree(path, *args, **kwargs):
        if os.path.basename(os.path.normpath(path)) == "output":
            seen["listing_before_cleanup"] = sorted(os.listdir(path))
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(engine.shutil, "rmtree", _spy_rmtree)

    result = engine.run_ocr(_make_image(), prompt="p", timeout=10)

    assert "FAKE RESULT for infer" in result
    assert "listing_before_cleanup" in seen, "run_ocr must explicitly clean up out_dir, not rely solely on the outer tempdir"
    assert "result.md" in seen["listing_before_cleanup"]
