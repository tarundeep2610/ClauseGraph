"""
unlimited_ocr_engine.py
-----------------------
Native Python inference wrapper for Baidu's Unlimited-OCR model.
Runs on CPU (no GPU required), using HuggingFace transformers with trust_remote_code=True.

The model's infer() method writes output to a directory, so we use tempfile to manage I/O.

Inference itself runs in a dedicated, persistent child process (see "Worker
process" below) rather than in-process - a timed-out call can then be killed
outright, which actually stops CUDA generation and frees GPU memory. A plain
thread can't be force-stopped in Python, so an earlier in-process/threaded
design left a timed-out call's generation running in the background
indefinitely, still holding the lock that serializes all inference, which
could make every later call queue behind it too (see the project plan).

NOTE: This is a large (~6-8 GB) model. Startup will take a while to download on
      first run. On CPU, inference takes 5-30+ minutes per page. This is expected.
"""

import contextlib
import glob
import io
import multiprocessing as _mp
import os
import queue as _queue
import re
import shutil
import sys
import tempfile
import threading
import time
import traceback
import uuid
from pathlib import Path
from PIL import Image

# Reconfigure stdout/stderr to UTF-8 (best-effort replacement for anything
# still unencodable) as early as possible, before any of this module's own
# emoji-laden log messages print. The worker process below is a freshly
# spawned interpreter (multiprocessing "spawn") - on Windows a bare python.exe
# subprocess defaults its console encoding to the legacy ANSI codepage (e.g.
# cp1252) rather than inheriting whatever UTF-8-capable terminal the parent
# happened to be running in, which raises UnicodeEncodeError and crashes the
# worker before it ever starts serving requests (discovered running this
# module's own tests on Windows - see the project plan).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────
MODEL_ID = "baidu/Unlimited-OCR"
# Pin to a specific commit once known-good, so an upstream change to the model
# repo can't silently swap in different trust_remote_code files. "main" is the
# safe-to-ship default until an operator pins an exact revision.
MODEL_REVISION = os.environ.get("UNLIMITED_OCR_MODEL_REVISION", "main")

# This GPU-side copy intentionally does NOT force CUDA_VISIBLE_DEVICES="" —
# unlike the original CPU-only script this was adapted from, we want CUDA
# visible here. The monkeypatch block below self-disables via the
# torch.cuda.is_available() check when a real GPU is present.

# ─────────────────────────────────────────────────────────────────────────────
# CPU Monkey-patch
# ─────────────────────────────────────────────────────────────────────────────
# The model's infer() method contains hard-coded .cuda() calls that we cannot
# edit (they live inside the downloaded trust_remote_code files).
# We intercept ALL .cuda() calls at the torch level and silently redirect them
# to CPU when no NVIDIA GPU is present.
import torch as _torch
import torch.nn as _nn

if not _torch.cuda.is_available():
    # 1. Patch .cuda()
    def _tensor_cpu_fallback(self, *args, **kwargs):
        return self.to("cpu")
    _torch.Tensor.cuda = _tensor_cpu_fallback

    def _module_cpu_fallback(self, *args, **kwargs):
        return self.to("cpu")
    _nn.Module.cuda = _module_cpu_fallback

    # 2. Patch .bfloat16() and .half() to prevent BFloat16/Float16 mismatches on CPU
    def _to_float32(self, *args, **kwargs):
        return self.to(_torch.float32)

    _torch.Tensor.bfloat16 = _to_float32
    _torch.Tensor.half = _to_float32
    _nn.Module.bfloat16 = _to_float32
    _nn.Module.half = _to_float32

    # 3. Patch .to(...) for Tensors and Modules to redirect device & dtype on CPU
    _orig_tensor_to = _torch.Tensor.to
    def _patched_tensor_to(self, *args, **kwargs):
        new_args = []
        for arg in args:
            if isinstance(arg, _torch.device) or (isinstance(arg, str) and "cuda" in str(arg)):
                arg = _torch.device("cpu")
            if arg in (_torch.bfloat16, _torch.float16):
                arg = _torch.float32
            new_args.append(arg)
        if "device" in kwargs and ("cuda" in str(kwargs.get("device")) or isinstance(kwargs.get("device"), _torch.device)):
            kwargs["device"] = "cpu"
        if "dtype" in kwargs and kwargs.get("dtype") in (_torch.bfloat16, _torch.float16):
            kwargs["dtype"] = _torch.float32
        return _orig_tensor_to(self, *new_args, **kwargs)

    _torch.Tensor.to = _patched_tensor_to

    _orig_module_to = _nn.Module.to
    def _patched_module_to(self, *args, **kwargs):
        new_args = []
        for arg in args:
            if isinstance(arg, _torch.device) or (isinstance(arg, str) and "cuda" in str(arg)):
                arg = _torch.device("cpu")
            if arg in (_torch.bfloat16, _torch.float16):
                arg = _torch.float32
            new_args.append(arg)
        if "device" in kwargs and ("cuda" in str(kwargs.get("device")) or isinstance(kwargs.get("device"), _torch.device)):
            kwargs["device"] = "cpu"
        if "dtype" in kwargs and kwargs.get("dtype") in (_torch.bfloat16, _torch.float16):
            kwargs["dtype"] = _torch.float32
        return _orig_module_to(self, *new_args, **kwargs)

    _nn.Module.to = _patched_module_to

    # 4. Patch autocast to no-op context manager
    class _DummyAutocast:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    if hasattr(_torch.cuda, "amp"):
        _torch.cuda.amp.autocast = _DummyAutocast
    if hasattr(_torch, "autocast"):
        _torch.autocast = _DummyAutocast

    print("[Unlimited-OCR] 🔧 No GPU detected — patched .cuda(), .bfloat16(), .half(), and autocast calls to CPU float32.")

# This module is shared between the CPU (dev-fallback) and GPU deployments —
# everything below reads this flag instead of re-checking torch.cuda directly,
# so the GPU path activates automatically on a box with a real CUDA device.
_GPU_AVAILABLE = _torch.cuda.is_available()


# ─────────────────────────────────────────────────────────────────────────────
# Globals
# ─────────────────────────────────────────────────────────────────────────────
# _model/_tokenizer/_loaded/_load_error are only ever meaningful INSIDE the
# worker process (see "Worker process" below) - each worker is a freshly
# spawned interpreter with its own copy of this module's globals, so these
# are never touched by the supervisor code that runs in the main process.
_model = None
_tokenizer = None
_load_error: str | None = None
_loaded: bool = False
_load_lock = threading.Lock()

# Default OCR prompts that match what the GLM-OCR service uses
DEFAULT_PROMPT = (
    "Perform OCR on this document. Transcribe all visible text exactly as it appears, "
    "preserving structure. Format tables as Markdown tables using '|' borders. "
    "Handle both English and Hindi (Devanagari) text accurately."
)

DOCUMENT_PROMPT = (
    "document parsing."
)


# ─────────────────────────────────────────────────────────────────────────────
# Model loading (runs inside the worker process - see _worker_main below)
# ─────────────────────────────────────────────────────────────────────────────

def initialize(verbose: bool = True) -> bool:
    """
    Download (if needed) and load the Baidu Unlimited-OCR model into CPU memory.

    Returns True on success, False on failure.
    Call get_load_error() for the error message on failure.
    """
    global _model, _tokenizer, _load_error, _loaded

    if _loaded:
        return True

    with _load_lock:
        if _loaded:  # someone else finished loading while we were waiting on the lock
            return True

        # trust_remote_code=True executes Python code downloaded from HuggingFace at load time.
        # Require an explicit operator opt-in so this never runs silently.
        if not os.environ.get("UNLIMITED_OCR_ALLOW_REMOTE_CODE"):
            _load_error = (
                "Refused to load baidu/Unlimited-OCR: trust_remote_code=True would execute "
                "arbitrary model-provided Python code. Set UNLIMITED_OCR_ALLOW_REMOTE_CODE=1 "
                "in the environment to confirm you trust this model's remote code."
            )
            if verbose:
                print(f"[Unlimited-OCR] ❌ {_load_error}", file=sys.stderr)
            return False

        try:
            import torch
            from transformers import AutoModel, AutoTokenizer
        except ImportError as e:
            _load_error = (
                f"Missing required packages: {e}\n\n"
                "Please install dependencies:\n"
                "  pip install -r requirements_unlimited_ocr.txt"
            )
            if verbose:
                print(f"[Unlimited-OCR] ❌ Import error: {_load_error}", file=sys.stderr)
            return False

        target_device = "cuda" if _GPU_AVAILABLE else "cpu"
        model_dtype = torch.bfloat16 if _GPU_AVAILABLE else torch.float32

        if verbose:
            print("[Unlimited-OCR] 📥 Loading model from HuggingFace (this may take a while on first run)...")
            print(f"[Unlimited-OCR]    Model: {MODEL_ID} (revision={MODEL_REVISION})")
            print(f"[Unlimited-OCR]    Device: {'CUDA GPU' if _GPU_AVAILABLE else 'CPU (no CUDA GPU detected)'}")
            print(f"[Unlimited-OCR]    dtype: {model_dtype}")

        try:
            _tokenizer = AutoTokenizer.from_pretrained(
                MODEL_ID,
                revision=MODEL_REVISION,
                trust_remote_code=True,
            )

            # NOTE: deliberately NOT using device_map= here. Passing device_map
            # triggers HuggingFace/accelerate's memory-efficient loading path,
            # which initializes the model on a placeholder "meta" device before
            # materializing real weights. This model's remote code has at least
            # one buffer (model.vision_model.embeddings.position_ids) that isn't
            # present in the saved checkpoint, so accelerate has no data to
            # materialize it from — "Cannot copy out of meta tensor; no data!".
            # Loading normally then moving the whole model with .to() afterward
            # avoids the meta-tensor path entirely.
            _model = AutoModel.from_pretrained(
                MODEL_ID,
                revision=MODEL_REVISION,
                trust_remote_code=True,
                use_safetensors=True,
                dtype=model_dtype,
            ).to(target_device).eval()

            _loaded = True
            if verbose:
                print("[Unlimited-OCR] ✅ Model loaded and ready.")
            return True

        except Exception as e:
            _load_error = traceback.format_exc()
            if verbose:
                print(f"[Unlimited-OCR] ❌ Failed to load model:\n{_load_error}", file=sys.stderr)
            return False


# ─────────────────────────────────────────────────────────────────────────────
# Worker process
#
# Inference always runs in a dedicated child process, started lazily on the
# first call and kept warm across later calls (the model stays loaded there,
# so normal calls pay no extra cost). If a call doesn't get a response within
# its timeout - or the worker process has died - the supervisor kills that
# process outright and starts a fresh one for the next call. Killing a
# process (unlike giving up on a thread) genuinely stops CUDA generation and
# frees GPU memory, so a runaway page can no longer make a later page queue
# behind it indefinitely; the worst case is now "the next call pays one
# model-reload's worth of extra latency", not "the next call waits on
# whatever the abandoned generation happens to still be doing".
#
# spawn (not the Linux default fork) is required: this module already touches
# CUDA driver state at import time (_torch.cuda.is_available() above), and a
# fork inherits that state into the child, which is the textbook cause of
# "Cannot re-initialize CUDA in forked subprocess". spawn starts a genuinely
# fresh interpreter in the child instead.
# ─────────────────────────────────────────────────────────────────────────────
_mp_ctx = _mp.get_context("spawn")

_worker_proc = None
_req_q = None
_resp_q = None
# Serializes access to the worker from the main process - multiple concurrent
# callers (task_queue.py's per-job concurrency, or several jobs at once) must
# still only ever have one request in flight against the single worker at a
# time, same effective behavior as the pre-Phase-2 in-process lock.
_worker_lock = threading.Lock()
# Bumped by cancel_current_work(). A call that was already queued waiting for
# _worker_lock when a cancel happened checks this right after finally
# acquiring the lock, so it can bail out instead of dispatching brand new
# work to the freshly-respawned worker on behalf of a caller who's already
# been told to stop.
_cancel_epoch = 0

# Shared live flag: True once the CURRENT worker has a model loaded. Set
# directly by the worker itself the moment initialize() succeeds - not
# inferred from a request/response cycle, which is why this needs to be
# shared memory rather than a plain main-process variable: a call can take
# minutes (or sit queued behind another call) between submitting a request
# and getting its response, and is_loaded()/get_load_error() must reflect
# reality throughout that whole window, not just once it finally completes.
# Deliberately never actively pings/spawns a worker just to answer a health
# check - mirrors the original behavior of not forcing a model load for that.
_worker_loaded_flag = _mp_ctx.Value("b", 0)
_worker_load_error: str | None = None


def is_loaded() -> bool:
    """Return True if the current worker process has a model loaded."""
    return bool(_worker_loaded_flag.value)


def get_load_error() -> str | None:
    """Return the worker's load error message, if the last known load attempt failed."""
    return _worker_load_error


def _worker_main(request_queue, response_queue, loaded_flag):
    """Entry point for the persistent worker process. Loads the model lazily
    on its first request (same lazy-load behavior as before Phase 2) and then
    serves infer()/infer_multi() requests one at a time for as long as this
    process lives. loaded_flag is shared with the main process so it can see
    the model's real load state without waiting for a full request/response
    round trip (see _worker_loaded_flag above)."""
    while True:
        req = request_queue.get()
        if req is None:  # sentinel for a clean stop - not currently sent, but supported
            return
        req_id = req.get("id")
        kind = req.get("kind")
        try:
            if kind == "ping":
                response_queue.put({"id": req_id, "ok": True, "loaded": _loaded, "load_error": _load_error})
                continue

            if not _loaded:
                initialize(verbose=True)
                if _loaded:
                    loaded_flag.value = 1
            if not _loaded:
                response_queue.put({
                    "id": req_id, "ok": False, "loaded": False,
                    "error": _load_error or "Model failed to load.",
                    "load_error": _load_error,
                })
                continue

            stdout_capture = io.StringIO()
            with contextlib.redirect_stdout(stdout_capture):
                if kind == "infer":
                    # crop_mode=True switches to the model's official "gundam"
                    # config (image_size=640, per-region crops) — confirmed
                    # directly (see the project plan) to be what actually
                    # makes the model emit <|det|> layout tags; crop_mode=False
                    # (image_size=base_size) never produces per-region output
                    # at all, since nothing splits the page into regions.
                    _crop_mode = bool(req.get("crop_mode"))
                    _model.infer(
                        _tokenizer,
                        prompt=f"<image>{req['prompt']}",
                        image_file=req["image_path"],
                        output_path=req["output_path"],
                        base_size=req["base_size"],
                        image_size=640 if _crop_mode else req["base_size"],
                        crop_mode=_crop_mode,
                        # Without this a pathological/blank/atypical page can run
                        # away repeating itself all the way to max_length.
                        no_repeat_ngram_size=35,
                        ngram_window=1024,
                        save_results=True,  # required for the model's own cleaned result.md
                    )
                elif kind == "infer_multi":
                    _model.infer_multi(
                        _tokenizer,
                        prompt=f"<image>{req['prompt']}",
                        image_files=req["image_paths"],
                        output_path=req["output_path"],
                        image_size=1024,  # multi-page mode only supports base resolution
                        max_length=32768,
                        no_repeat_ngram_size=35,
                        ngram_window=1024,
                        save_results=True,  # required to get the model's own <PAGE>-delimited result.md
                    )
                else:
                    raise ValueError(f"Unknown worker request kind: {kind!r}")

            response_queue.put({
                "id": req_id, "ok": True, "loaded": True,
                "captured_stdout": stdout_capture.getvalue(),
            })
        except Exception:
            response_queue.put({"id": req_id, "ok": False, "loaded": _loaded, "error": traceback.format_exc()})


# ─── Test-only worker fakes ──────────────────────────────────────────────────
# Never invoked in production (_worker_main is always the real target). Tests
# monkeypatch this module's _worker_main name to one of these instead of
# defining fakes in the test module itself, so multiprocessing.Process(target=)
# always pickles a reference into this same, reliably-importable module rather
# than depending on the test module also being importable from a spawned
# child's fresh interpreter.

def _test_fake_worker_echo(request_queue, response_queue, loaded_flag):
    """Instantly answers every request as a success, honoring an optional
    req['_test_sleep'] delay first and writing req['_test_result'] (or a
    default) into output_path so callers' normal file-reading path works.
    Sets loaded_flag before the delay, same as the real worker sets it right
    after initialize() succeeds and before the (potentially slow) inference
    call - so tests can verify is_loaded() goes live before a slow call
    returns, not only once it does."""
    while True:
        req = request_queue.get()
        if req is None:
            return
        loaded_flag.value = 1
        if req.get("_test_sleep"):
            time.sleep(req["_test_sleep"])
        out_dir = req.get("output_path")
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
            text = req.get("_test_result", f"FAKE RESULT for {req.get('kind')} from pid {os.getpid()}")
            with open(os.path.join(out_dir, "result.md"), "w", encoding="utf-8") as f:
                f.write(text)
        response_queue.put({"id": req.get("id"), "ok": True, "loaded": True, "captured_stdout": ""})


def _test_fake_worker_hang(request_queue, response_queue, loaded_flag):
    """Never responds to any request - simulates a runaway generation that
    only a real process kill can stop."""
    while True:
        req = request_queue.get()
        if req is None:
            return
        time.sleep(3600)


def _test_fake_worker_crash(request_queue, response_queue, loaded_flag):
    """Exits immediately without responding - simulates a hard crash (a plain
    non-zero exit, NOT a signal death - contrast with
    _test_fake_worker_sigkill below, which is what a real OOM-kill looks
    like from the parent's point of view)."""
    os._exit(1)


def _test_fake_worker_sigkill(request_queue, response_queue, loaded_flag):
    """Exits immediately via SIGKILL - simulates a real OOM-kill (the Linux
    OOM killer always uses SIGKILL), which multiprocessing reports as a
    negative exitcode (-9), unlike an ordinary os._exit() (a non-negative
    exitcode)."""
    import signal
    os.kill(os.getpid(), signal.SIGKILL)


def _ensure_worker_locked():
    """Must be called while holding _worker_lock. Starts the persistent
    worker process if one isn't already running."""
    global _worker_proc, _req_q, _resp_q
    if _worker_proc is not None and _worker_proc.is_alive():
        return
    _worker_loaded_flag.value = 0  # a fresh worker always starts unloaded
    _req_q = _mp_ctx.Queue()
    _resp_q = _mp_ctx.Queue()
    _worker_proc = _mp_ctx.Process(target=_worker_main, args=(_req_q, _resp_q, _worker_loaded_flag), daemon=True)
    _worker_proc.start()


def _confirm_worker_dead(proc, context: str):
    """Waits for `proc` to actually exit after being killed, logging loudly if
    it's taking unusually long instead of silently forgetting it. This model
    is large enough (observed 14+ GB RSS) that a box running it can have
    almost no memory headroom - if a killed worker is ever slow to actually
    exit (e.g. SIGKILL delivery delayed under heavy native compute), starting
    a fresh worker for the next call before the old one is confirmed gone
    risks two instances resident at once, which is enough to trigger a real
    OOM-kill on a tight box. This can't fix that race outright (a process
    that's stuck in an uninterruptible state can't be forced to exit faster
    from here), but it gives the old process a fair, bounded chance to
    actually die, and makes it loud in the logs if it doesn't, rather than
    letting it fail silently the way an unconfirmed kill did before."""
    proc.join(timeout=10)
    if not proc.is_alive():
        return
    print(
        f"[Unlimited-OCR] ⚠️ Worker pid={proc.pid} still alive 10s after SIGKILL ({context}) — "
        "waiting up to 10s more before continuing.",
        file=sys.stderr,
    )
    proc.join(timeout=10)
    if proc.is_alive():
        print(
            f"[Unlimited-OCR] ❌ Worker pid={proc.pid} still alive 20s after SIGKILL ({context}) — "
            "proceeding anyway; a new worker may run alongside it and risk an OOM on this box.",
            file=sys.stderr,
        )


def _kill_worker_locked():
    """Must be called while holding _worker_lock. Forcibly terminates the
    current worker (this is what actually frees GPU memory - a thread's
    generation call can't be force-stopped the way a process can) and clears
    supervisor state so the next call starts a fresh one, which reloads the
    model lazily on its own first request."""
    global _worker_proc, _worker_load_error
    if _worker_proc is not None:
        try:
            _worker_proc.kill()
            _confirm_worker_dead(_worker_proc, context="timeout/crash kill")
        except Exception:
            pass
    _worker_proc = None
    _worker_loaded_flag.value = 0
    _worker_load_error = None


def cancel_current_work():
    """Immediately kills whatever the worker process is doing right now, for
    an explicit external 'stop everything' request (e.g. the frontend's Stop
    All button) rather than a timeout. Deliberately does NOT go through
    _worker_lock: a call already in flight holds that lock for its entire
    wait, so acquiring it here would just block until that call's own timeout
    elapses - defeating the whole point of an immediate cancel. _worker_proc
    is a plain module global, and simple name rebinding is atomic under the
    GIL, so this is safe to call concurrently with an in-flight _call_worker
    - worst case is a narrow, harmless race where this kills a replacement
    worker that _ensure_worker_locked() just spawned, which only costs the
    next call one extra respawn.

    A call still waiting inside _call_worker when this runs will notice
    within about one poll interval (~2s) via its own _worker_proc.is_alive()
    check and give up cleanly, rather than being force-unblocked here. A call
    that hasn't reached the worker yet - still waiting on _worker_lock itself
    - is handled separately: see _cancel_epoch."""
    global _worker_proc, _worker_load_error, _cancel_epoch
    _cancel_epoch += 1
    proc = _worker_proc
    _worker_proc = None
    _worker_loaded_flag.value = 0
    _worker_load_error = None
    if proc is not None:
        try:
            proc.kill()
            _confirm_worker_dead(proc, context="Stop All cancel")
        except Exception:
            pass


def _call_worker(request: dict, timeout: float | None) -> dict:
    """Submit one request to the persistent worker process and wait for its
    response. Polls in short slices (rather than one blocking wait for the
    full timeout) so a crashed - not just hung - worker is detected quickly
    instead of only after the entire timeout elapses. On timeout or crash,
    kills/discards the worker so the next call always starts a fresh one
    rather than ever queueing behind a runaway generation."""
    global _worker_load_error
    req_id = uuid.uuid4().hex
    request = {**request, "id": req_id}
    epoch_at_start = _cancel_epoch

    with _worker_lock:
        if _cancel_epoch != epoch_at_start:
            # A cancel_current_work() happened while this call was still
            # waiting for _worker_lock (held by whatever was cancelled) - the
            # caller has already been told to stop, so don't dispatch fresh
            # work to the worker that respawned in the meantime.
            return {"ok": False, "cancelled": True}
        _ensure_worker_locked()
        _req_q.put(request)

        deadline = None if timeout is None else time.monotonic() + timeout
        poll_interval = 2.0
        while True:
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                _kill_worker_locked()
                return {"ok": False, "timed_out": True}
            wait_for = poll_interval if remaining is None else min(poll_interval, remaining)
            try:
                response = _resp_q.get(timeout=wait_for)
            except _queue.Empty:
                # _worker_proc can become None here even though this call still
                # holds _worker_lock - cancel_current_work() is deliberately
                # able to kill the worker from outside without waiting on this
                # lock (see its docstring), so a concurrent "stop all" request
                # can null it out mid-poll.
                if _worker_proc is None or not _worker_proc.is_alive():
                    exitcode = _worker_proc.exitcode if _worker_proc is not None else None
                    print(f"[Unlimited-OCR] Worker process died — exitcode={exitcode!r}.", file=sys.stderr)
                    _kill_worker_locked()
                    if exitcode is not None and exitcode < 0:
                        signum = -exitcode
                        if signum == 9:
                            error = (
                                f"OCR worker process was killed by signal 9 (SIGKILL, exit code {exitcode}) — "
                                "this usually means the OS ran out of memory and killed it."
                            )
                        else:
                            error = f"OCR worker process was killed by signal {signum} (exit code {exitcode})."
                    else:
                        error = "OCR worker process crashed or was cancelled."
                    return {"ok": False, "error": error}
                continue
            if response.get("id") != req_id:
                continue  # stale response from an earlier call - keep waiting for ours
            # "loaded" is no longer read here - the worker updates
            # _worker_loaded_flag directly the moment initialize() succeeds
            # (see _worker_main), so is_loaded() is already live without
            # waiting for this response.
            if "load_error" in response:
                _worker_load_error = response["load_error"]
            return response


# ─────────────────────────────────────────────────────────────────────────────
# Inference
# ─────────────────────────────────────────────────────────────────────────────

def run_ocr(
    pil_image: Image.Image, prompt: str | None = None, base_size: int = 1024, timeout: int | None = None,
    crop_mode: bool = False,
) -> str:
    """
    Run Baidu Unlimited-OCR on a PIL Image and return the extracted text as a string.

    Args:
        pil_image: A PIL Image (RGB mode preferred).
        prompt:    The prompt sent to the model. Defaults to DOCUMENT_PROMPT.
        base_size: Resolution fed to the model (512/768/1024/2048). Higher = more detail, slower.
        timeout:   Seconds to wait for inference before giving up (None = no limit).
                   Inference runs in a dedicated worker process; on timeout that
                   process is killed outright (actually freeing GPU memory,
                   unlike giving up on a thread) and a fresh one is started
                   for the next call.
        crop_mode: False (default) uses the model's "base" config — one
                   global view of the page, no per-region output. True uses
                   the "gundam" config (image_size=640, per-region crops) —
                   confirmed (see the project plan) to be what actually
                   makes the model emit <|det|> layout tags.

    Returns:
        Extracted text string, or an error message prefixed with "### ❌".
    """
    final_output, _raw_text = run_ocr_with_raw(pil_image, prompt, base_size, timeout, crop_mode=crop_mode)
    return final_output


def run_ocr_with_raw(
    pil_image: Image.Image, prompt: str | None = None, base_size: int = 1024, timeout: int | None = None,
    crop_mode: bool = False,
) -> "tuple[str, str | None]":
    """Same contract as run_ocr(), but also returns the raw, pre-
    clean_detection_tags() text the model actually produced — the
    `<|det|>type [bbox]<|/det|>content` layout-tagged output, before this
    module strips it down to plain text. Used by reference_index.py's
    layout-tag heading-vs-reference confidence signal (see the project
    plan); `None` on any path that never reaches real inference (worker
    timeout/crash/cancel) — there is no raw text to return in those cases.
    run_ocr() itself is a thin wrapper around this and is unchanged for all
    of its existing callers.

    Returns:
        (cleaned_text_with_timing_footer, raw_text_or_None)
    """
    prompt = prompt or DOCUMENT_PROMPT

    # The model's infer() API writes output to files in an output directory.
    # We use a temp directory and read back the result file afterward.
    with tempfile.TemporaryDirectory(prefix="unlimited_ocr_") as tmp_dir:
        # Save the PIL image to a temporary file that the worker process can read.
        img_path = os.path.join(tmp_dir, "input_page.jpg")
        if pil_image.mode != "RGB":
            pil_image = pil_image.convert("RGB")
        pil_image.save(img_path, format="JPEG", quality=85)

        out_dir = os.path.join(tmp_dir, "output")
        os.makedirs(out_dir, exist_ok=True)

        start_time = time.time()
        response = _call_worker(
            {
                "kind": "infer", "image_path": img_path, "output_path": out_dir,
                "prompt": prompt, "base_size": base_size, "crop_mode": crop_mode,
            },
            timeout,
        )

        if not response.get("ok"):
            if response.get("timed_out"):
                tb = f"TimeoutError: Unlimited-OCR inference timed out after {timeout}s. The stalled worker process was terminated."
            elif response.get("cancelled"):
                tb = "Cancelled: an explicit Stop All request was received while this call was still queued."
            else:
                tb = response.get("error") or "Unknown worker error."
            return f"### ❌ Unlimited-OCR inference failed\n```\n{tb}\n```", None

        elapsed_sec = time.time() - start_time
        mins = int(elapsed_sec // 60)
        secs = elapsed_sec % 60
        time_str = f"{mins}m {secs:.1f}s" if mins > 0 else f"{secs:.1f}s"

        # Read back output files
        result_text = _collect_output(out_dir)
        # save_results (multi-page path) writes boxed-image artifacts alongside
        # the text we just read; free them now rather than waiting for this
        # whole tempdir to be torn down when the function returns.
        shutil.rmtree(out_dir, ignore_errors=True)

        # The worker's captured stdout includes the model's own live-streamed
        # generation trace - the ONLY place <|det|> layout tags ever appear.
        # save_results=True's own result.md is already stripped of them by
        # the model's own infer() before it's ever written to disk
        # (confirmed directly - see the project plan), so _collect_output()
        # above can never see them. Filtered to drop known noise (loader
        # warnings, tqdm progress bars, the "===...save results...==="
        # marker), the captured trace also still works as the existing
        # last-resort recovery of the cleaned text if the file read itself
        # failed or came back empty.
        captured_str = (response.get("captured_stdout") or "").strip()
        filtered_captured = ""
        if captured_str:
            clean_lines = [
                line for line in captured_str.splitlines()
                if not line.startswith("The attention mask")
                and not line.startswith("Setting `pad_token_id`")
                and not line.startswith("A new version")
                and "it/s]" not in line
                and not line.startswith("===")
            ]
            filtered_captured = "\n".join(clean_lines).strip()

        if ("unexpected output files" in result_text or not result_text.strip()) and filtered_captured:
            result_text = filtered_captured

        # raw_text (this function's second return value) always prefers the
        # captured generation trace when available - it's the only source
        # that can ever contain <|det|> tags, regardless of whether the
        # saved-file read above succeeded.
        raw_text = filtered_captured or result_text

        # Clean detection coordinate tags (<|det|>...<|/det|>) for clean readability
        cleaned_text = clean_detection_tags(result_text)

        # Append processing time footer
        timing_footer = f"\n\n---\n*⏱️ **Unlimited-OCR Execution Time:** {time_str} ({elapsed_sec:.1f}s)*"
        final_output = cleaned_text + timing_footer

        # Print to stdout and optionally save debug copies
        print(f"\n⏱️ [Unlimited-OCR Execution Time]: {time_str} ({elapsed_sec:.2f} seconds)", flush=True)
        print(f"\n==================== [Unlimited-OCR Clean Result] ====================\n{final_output}\n======================================================================\n", flush=True)
        if os.environ.get("UNLIMITED_OCR_SAVE_DEBUG_FILES"):
            try:
                with open("latest_ocr_result.md", "w", encoding="utf-8") as f_out:
                    f_out.write(final_output)
                with open("latest_ocr_result_raw.md", "w", encoding="utf-8") as f_raw:
                    f_raw.write(raw_text)
            except Exception:
                pass

    return final_output, raw_text


MULTI_PAGE_PROMPT = "Multi page parsing."


def run_ocr_multi(pil_images: list, prompt: str | None = None, timeout: int | None = None) -> str:
    """
    Batch-mode counterpart to run_ocr() — calls the model's documented
    `infer_multi()` API so several page images share one generation context.

    Calls `infer_multi(..., save_results=True)` so the model applies its own
    reference per-page cleanup and writes a `<PAGE>`-delimited `result.md`
    (see modeling_unlimitedocr.py's infer_multi(), save_results branch) —
    task_queue._split_batch_output() splits on that same literal `<PAGE>`
    marker. Multi-page mode also only supports "base" resolution per Baidu's
    documentation, not the higher-fidelity "gundam" cropping mode single-page
    run_ocr() can use — that resolution trade-off is exactly why the
    escalation ladder's combined-recovery step exists.

    Args:
        pil_images: list of PIL Images (RGB mode preferred), in page order.
        prompt:     defaults to MULTI_PAGE_PROMPT.
        timeout:    seconds to wait before giving up (None = no limit). See
                    run_ocr()'s docstring for what happens on timeout.

    Returns:
        The model's combined text output across all pages, or an error message
        prefixed with "### ❌" (same failure-mode contract as run_ocr()).
    """
    prompt = prompt or MULTI_PAGE_PROMPT

    with tempfile.TemporaryDirectory(prefix="unlimited_ocr_multi_") as tmp_dir:
        image_paths = []
        for i, pil_image in enumerate(pil_images):
            if pil_image.mode != "RGB":
                pil_image = pil_image.convert("RGB")
            img_path = os.path.join(tmp_dir, f"input_page_{i}.jpg")
            pil_image.save(img_path, format="JPEG", quality=85)
            image_paths.append(img_path)

        out_dir = os.path.join(tmp_dir, "output")
        os.makedirs(out_dir, exist_ok=True)

        start_time = time.time()
        response = _call_worker(
            {"kind": "infer_multi", "image_paths": image_paths, "output_path": out_dir, "prompt": prompt},
            timeout,
        )

        if not response.get("ok"):
            if response.get("timed_out"):
                tb = f"TimeoutError: Unlimited-OCR multi-page inference timed out after {timeout}s. The stalled worker process was terminated."
            elif response.get("cancelled"):
                tb = "Cancelled: an explicit Stop All request was received while this call was still queued."
            else:
                tb = response.get("error") or "Unknown worker error."
            return f"### ❌ Unlimited-OCR inference failed\n```\n{tb}\n```"

        elapsed_sec = time.time() - start_time
        result_text = _collect_output(out_dir)
        used_result_md = not ("unexpected output files" in result_text or not result_text.strip())
        # save_results writes result_with_boxes_*.jpg and images/ alongside
        # result.md; free them now rather than waiting for this whole tempdir
        # to be torn down when the function returns.
        shutil.rmtree(out_dir, ignore_errors=True)
        captured_str = (response.get("captured_stdout") or "").strip()
        if not used_result_md and captured_str:
            clean_lines = [
                line for line in captured_str.splitlines()
                if not line.startswith("The attention mask")
                and not line.startswith("Setting `pad_token_id`")
                and not line.startswith("A new version")
            ]
            result_text = "\n".join(clean_lines).strip()

        # Diagnostic for the shared 32768-token context ceiling (input images +
        # entire combined output, see config.json's max_position_embeddings):
        # if page_markers < len(pil_images), the model ran out of budget (or
        # was mis-prompted) before transcribing every page - not a parsing bug.
        page_markers = len(re.findall(r"<PAGE>", result_text, flags=re.IGNORECASE))

        cleaned_text = clean_detection_tags(result_text)
        timing_footer = f"\n\n---\n*⏱️ **Unlimited-OCR Multi-Page Execution Time:** {elapsed_sec:.1f}s ({len(pil_images)} pages)*"
        final_output = cleaned_text + timing_footer

        print(
            f"\n⏱️ [Unlimited-OCR Multi-Page Execution Time]: {elapsed_sec:.2f}s for {len(pil_images)} pages "
            f"(result.md used: {used_result_md}, <PAGE> markers found: {page_markers}, "
            f"raw output chars: {len(result_text)})",
            flush=True,
        )

    return final_output


def clean_detection_tags(text: str) -> str:
    """Strips raw Baidu Unlimited-OCR layout detection tags <|det|>...<|/det|> for clean text output."""
    cleaned = re.sub(r'<\|det\|>.*?<\|/det\|>', '', text)
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    return "\n\n".join(lines)


def _collect_output(out_dir: str) -> str:
    """
    Read and combine all text output files written by the model's infer() call.
    Supports .md, .txt, and .json output formats.
    """
    # Try markdown first
    for ext in ("*.md", "*.txt", "*.json"):
        files = sorted(glob.glob(os.path.join(out_dir, "**", ext), recursive=True))
        if files:
            parts = []
            for fpath in files:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read().strip()
                if content:
                    parts.append(content)
            if parts:
                return "\n\n---\n\n".join(parts)

    # Fallback: list what was written to help debug
    all_files = list(Path(out_dir).rglob("*"))
    if not all_files:
        return "### ⚠️ Unlimited-OCR ran but produced no output files."

    file_list = "\n".join(f"  - {f.name}" for f in all_files if f.is_file())
    return (
        f"### ⚠️ Unlimited-OCR wrote unexpected output files.\n"
        f"Files found in output dir:\n{file_list}\n\n"
        "Please check the output directory manually."
    )


# The worker process (and, inside it, the model) is started lazily on the
# first OCR request via run_ocr()/run_ocr_multi(), not at import time. This
# lets the server start and serve the UI immediately, even before the
# ~6-8 GB weights finish downloading from HuggingFace.
