import os

# ─── AuthNexus SSO ────────────────────────────────────────────────────────────
AUTH_AUTHORITY = os.environ.get("AUTH_AUTHORITY", "").rstrip("/")
PROJECT_ID = os.environ.get("PROJECT_ID", "")
ORG_ID = os.environ.get("ORG_ID", "")
CLIENT_ID = os.environ.get("CLIENT_ID", "")

DEV_MODE = os.environ.get("DEV_MODE", "false").strip().lower() == "true"
DEV_SECRET = os.environ.get("DEV_SECRET", "")
if DEV_MODE and not DEV_SECRET:
    raise RuntimeError(
        "DEV_SECRET must be set (non-empty) when DEV_MODE=true. "
        "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
    )

# ─── GPU inference service (server-to-server) ────────────────────────────────
GPU_OCR_BASE_URL = os.environ.get("GPU_OCR_BASE_URL", "").rstrip("/")
GPU_OCR_SHARED_SECRET = os.environ.get("GPU_OCR_SHARED_SECRET", "")
GPU_OCR_CONNECT_TIMEOUT_SECONDS = float(os.environ.get("GPU_OCR_CONNECT_TIMEOUT_SECONDS", "10"))
GPU_OCR_POLL_INTERVAL_SECONDS = float(os.environ.get("GPU_OCR_POLL_INTERVAL_SECONDS", "3"))
# How long the CPU-side client waits for one page's GPU job before giving up,
# independent of the per-job `timeout` (OCR_TIMEOUT_SECONDS) sent to the GPU
# service. The GPU service already enforces `timeout` itself (killing and
# respawning its worker rather than ever hanging forever - see
# unlimited_ocr_engine.py), including for calls queued behind another one, so
# this is deliberately NOT derived from `timeout` (a max(timeout, ...) here
# previously raced the server's own enforcement and could abandon a call that
# was still legitimately queued, orphaning it and cascading into a backlog -
# see the project plan). This is purely a liveness safety net for a truly
# dead/unreachable GPU service; it should never fire during normal operation.
GPU_OCR_CLIENT_SAFETY_TIMEOUT_SECONDS = float(os.environ.get("GPU_OCR_CLIENT_SAFETY_TIMEOUT_SECONDS", "1800"))
OCR_LOCAL_ENGINE = os.environ.get("OCR_LOCAL_ENGINE", "false").strip().lower() == "true"

# ─── Existing OCR service config (relocated from main.py) ────────────────────
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
DEFAULT_RENDER_SCALE = float(os.environ.get("RENDER_SCALE", "1.5"))
OCR_TIMEOUT = int(os.environ.get("OCR_TIMEOUT_SECONDS", "600"))
RATE_LIMIT_MAX = int(os.environ.get("JOB_RATE_LIMIT", "10"))
RATE_LIMIT_WINDOW = int(os.environ.get("JOB_RATE_LIMIT_WINDOW_SECONDS", "60"))

# ─── Chunking / concurrency / batch mode ──────────────────────────────────────
OCR_CHUNK_SIZE = int(os.environ.get("OCR_CHUNK_SIZE", "5"))
OCR_MAX_CHUNK_SIZE = int(os.environ.get("OCR_MAX_CHUNK_SIZE", "200"))
if OCR_CHUNK_SIZE < 2:
    raise RuntimeError("OCR_CHUNK_SIZE must be >= 2 (a stride of 0 would loop forever).")
OCR_JOB_CONCURRENCY = int(os.environ.get("OCR_JOB_CONCURRENCY", "2"))  # matches GPU_OCR_MAX_CONCURRENT_JOBS default
OCR_ESCALATION_CONCURRENCY_SHARE = int(os.environ.get("OCR_ESCALATION_CONCURRENCY_SHARE", "1"))

# processing_mode ships defaulting to "single" (today's proven behavior) until S-5
# validates infer_multi()'s batch-size/quality/failure-semantics on real pages —
# then the default flips to "batch". Users can still opt into "batch" explicitly.
OCR_PROCESSING_MODE_DEFAULT = os.environ.get("OCR_PROCESSING_MODE_DEFAULT", "single")
OCR_BATCH_SIZE_DEFAULT = int(os.environ.get("OCR_BATCH_SIZE_DEFAULT", "5"))
OCR_MAX_BATCH_SIZE = int(os.environ.get("OCR_MAX_BATCH_SIZE", "20"))

# ─── Rolling context primer (best-effort quality bonus, not the continuity guarantee) ─
OCR_CONTEXT_PRIMER_ENABLED = os.environ.get("OCR_CONTEXT_PRIMER_ENABLED", "false").strip().lower() == "true"
OCR_CONTEXT_TAIL_CHARS = int(os.environ.get("OCR_CONTEXT_TAIL_CHARS", "300"))
OCR_CONTEXT_MAX_GAP = int(os.environ.get("OCR_CONTEXT_MAX_GAP", "1"))

# ─── Confidence scoring ────────────────────────────────────────────────────────
OCR_CONFIDENCE_THRESHOLD = float(os.environ.get("OCR_CONFIDENCE_THRESHOLD", "0.70"))
OCR_ESCALATION_THRESHOLD = float(os.environ.get("OCR_ESCALATION_THRESHOLD", "0.85"))
OCR_MIN_TEXT_CHARS = int(os.environ.get("OCR_MIN_TEXT_CHARS", "40"))
OCR_BLANK_INK_RATIO = float(os.environ.get("OCR_BLANK_INK_RATIO", "0.005"))
OCR_MAX_CHAR_RUN = int(os.environ.get("OCR_MAX_CHAR_RUN", "25"))
OCR_MAX_LINE_DUP_RATIO = float(os.environ.get("OCR_MAX_LINE_DUP_RATIO", "0.50"))
OCR_MAX_FOREIGN_SCRIPT_RATIO = float(os.environ.get("OCR_MAX_FOREIGN_SCRIPT_RATIO", "0.02"))
OCR_ENGINE_AGREEMENT_MIN = float(os.environ.get("OCR_ENGINE_AGREEMENT_MIN", "0.60"))
OCR_IMAGE_BLUR_THRESHOLD = float(os.environ.get("OCR_IMAGE_BLUR_THRESHOLD", "50.0"))
OCR_IMAGE_CONTRAST_THRESHOLD = float(os.environ.get("OCR_IMAGE_CONTRAST_THRESHOLD", "15.0"))

# Token-level confidence (GLM only, via Ollama's logprobs) — gated on S-6 confirming
# it actually works for a vision/multimodal generate call on the deployed Ollama version.
OCR_TOKEN_CONFIDENCE_ENABLED = os.environ.get("OCR_TOKEN_CONFIDENCE_ENABLED", "false").strip().lower() == "true"
OCR_TOKEN_CONFIDENCE_MIN_TOP1_PROB = float(os.environ.get("OCR_TOKEN_CONFIDENCE_MIN_TOP1_PROB", "0.3"))

# ─── Escalation ────────────────────────────────────────────────────────────────
OCR_ESCALATE_BEFORE_FLAGGING_DEFAULT = os.environ.get("OCR_ESCALATE_BEFORE_FLAGGING_DEFAULT", "true").strip().lower() == "true"
OCR_ESCALATION_SCALE_FACTOR = float(os.environ.get("OCR_ESCALATION_SCALE_FACTOR", "2.0"))
OCR_MAX_RENDER_SCALE = float(os.environ.get("OCR_MAX_RENDER_SCALE", "4.0"))

# ─── Audit sampling (calibration check for the "confidence heuristic can't catch
# fluent hallucination" gap) ───────────────────────────────────────────────────
OCR_AUDIT_SAMPLE_RATE = float(os.environ.get("OCR_AUDIT_SAMPLE_RATE", "0.05"))
OCR_AUDIT_HIGH_CONFIDENCE_MIN = float(os.environ.get("OCR_AUDIT_HIGH_CONFIDENCE_MIN", "0.90"))
OCR_AUDIT_SAMPLE_CAP_PER_JOB = int(os.environ.get("OCR_AUDIT_SAMPLE_CAP_PER_JOB", "10"))
OCR_AUDIT_CROSS_ENGINE_ENABLED = os.environ.get("OCR_AUDIT_CROSS_ENGINE_ENABLED", "true").strip().lower() == "true"

# ─── Unlimited-OCR long-prompt gate — runtime-overridable via app_settings; see
# main.py's settings mechanism. This constant is only the fallback default. ────
ENABLE_UNLIMITED_LONG_PROMPTS = os.environ.get("ENABLE_UNLIMITED_LONG_PROMPTS", "false").strip().lower() == "true"

# ─── DB durability/performance ─────────────────────────────────────────────────
OCR_DB_BUSY_TIMEOUT_MS = int(os.environ.get("OCR_DB_BUSY_TIMEOUT_MS", "5000"))
OCR_WAL_CHECKPOINT_EVERY_N_PAGES = int(os.environ.get("OCR_WAL_CHECKPOINT_EVERY_N_PAGES", "50"))

# ─── Pause drain ───────────────────────────────────────────────────────────────
OCR_PAUSE_DRAIN_TIMEOUT_SECONDS = int(os.environ.get("OCR_PAUSE_DRAIN_TIMEOUT_SECONDS", "900"))
