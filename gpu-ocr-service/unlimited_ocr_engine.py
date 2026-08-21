"""
unlimited_ocr_engine.py
-----------------------
Native Python inference wrapper for Baidu's Unlimited-OCR model.
Runs on CPU (no GPU required), using HuggingFace transformers with trust_remote_code=True.

The model's infer() method writes output to a directory, so we use tempfile to manage I/O.

NOTE: This is a large (~6-8 GB) model. Startup will take a while to download on first run.
      On CPU, inference takes 5-30+ minutes per page. This is expected.
"""

import io
import os
import sys
import glob
import tempfile
import textwrap
import traceback
from pathlib import Path
from PIL import Image

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
_model = None
_tokenizer = None
_load_error: str | None = None
_loaded: bool = False

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
# Model loading
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

    device_map = "cuda" if _GPU_AVAILABLE else "cpu"
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

        _model = AutoModel.from_pretrained(
            MODEL_ID,
            revision=MODEL_REVISION,
            trust_remote_code=True,
            use_safetensors=True,
            dtype=model_dtype,
            device_map=device_map,
        ).eval()

        _loaded = True
        if verbose:
            print("[Unlimited-OCR] ✅ Model loaded and ready.")
        return True

    except Exception as e:
        _load_error = traceback.format_exc()
        if verbose:
            print(f"[Unlimited-OCR] ❌ Failed to load model:\n{_load_error}", file=sys.stderr)
        return False


def is_loaded() -> bool:
    """Return True if the model has been successfully loaded."""
    return _loaded


def get_load_error() -> str | None:
    """Return the error message if model loading failed, else None."""
    return _load_error


# ─────────────────────────────────────────────────────────────────────────────
# Inference
# ─────────────────────────────────────────────────────────────────────────────

def run_ocr(pil_image: Image.Image, prompt: str | None = None, base_size: int = 1024, timeout: int | None = None) -> str:
    """
    Run Baidu Unlimited-OCR on a PIL Image and return the extracted text as a string.

    Args:
        pil_image: A PIL Image (RGB mode preferred).
        prompt:    The prompt sent to the model. Defaults to DOCUMENT_PROMPT.
        base_size: Resolution fed to the model (512/768/1024/2048). Higher = more detail, slower.
        timeout:   Seconds to wait for inference before raising TimeoutError (None = no limit).
                   The inference thread cannot be killed; timeout only raises in the caller.

    Returns:
        Extracted text string, or an error message prefixed with "### ❌".
    """
    if not _loaded:
        print("[Unlimited-OCR] 📥 First OCR request — loading model now (this may take a while)...")
        initialize(verbose=True)
    if not _loaded:
        error = _load_error or "Model failed to load."
        return f"### ❌ Unlimited-OCR not available\n{error}"

    prompt = prompt or DOCUMENT_PROMPT

    # The model's infer() API writes output to files in an output directory.
    # We use a temp directory and read back the result file afterward.
    with tempfile.TemporaryDirectory(prefix="unlimited_ocr_") as tmp_dir:
        # Save the PIL image to a temporary file that the model can read
        img_path = os.path.join(tmp_dir, "input_page.jpg")
        if pil_image.mode != "RGB":
            pil_image = pil_image.convert("RGB")
        pil_image.save(img_path, format="JPEG", quality=85)

        out_dir = os.path.join(tmp_dir, "output")
        os.makedirs(out_dir, exist_ok=True)

        stdout_capture = io.StringIO()
        import time
        import contextlib
        import concurrent.futures
        start_time = time.time()

        def _run_infer():
            _model.infer(
                _tokenizer,
                prompt=f"<image>{prompt}",
                image_file=img_path,
                output_path=out_dir,
                base_size=base_size,
                image_size=base_size,
                crop_mode=False,   # preserve document structure (no cropping)
            )

        try:
            with contextlib.redirect_stdout(stdout_capture):
                if timeout is not None:
                    _ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
                    _fut = _ex.submit(_run_infer)
                    _ex.shutdown(wait=False)
                    try:
                        _fut.result(timeout=timeout)
                    except concurrent.futures.TimeoutError:
                        raise TimeoutError(f"Unlimited-OCR inference timed out after {timeout}s.")
                else:
                    _run_infer()
        except Exception as e:
            tb = traceback.format_exc()
            return f"### ❌ Unlimited-OCR inference failed\n```\n{tb}\n```"

        elapsed_sec = time.time() - start_time
        mins = int(elapsed_sec // 60)
        secs = elapsed_sec % 60
        time_str = f"{mins}m {secs:.1f}s" if mins > 0 else f"{secs:.1f}s"

        # Read back output files
        result_text = _collect_output(out_dir)

        # If file output was empty or warning, use captured stdout tokens!
        captured_str = stdout_capture.getvalue().strip()
        if ("unexpected output files" in result_text or not result_text.strip()) and captured_str:
            clean_lines = [
                line for line in captured_str.splitlines()
                if not line.startswith("The attention mask") 
                and not line.startswith("Setting `pad_token_id`")
                and not line.startswith("A new version")
            ]
            result_text = "\n".join(clean_lines).strip()

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
                    f_raw.write(result_text)
            except Exception:
                pass

    return final_output


def clean_detection_tags(text: str) -> str:
    """Strips raw Baidu Unlimited-OCR layout detection tags <|det|>...<|/det|> for clean text output."""
    import re
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


# Model is loaded lazily on first OCR request via run_ocr(), not at import time.
# This lets the server start and serve the UI immediately, even before the
# ~6-8 GB weights finish downloading from HuggingFace.
