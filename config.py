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
GPU_OCR_POLL_TIMEOUT_SECONDS = float(os.environ.get("GPU_OCR_POLL_TIMEOUT_SECONDS", "600"))
OCR_LOCAL_ENGINE = os.environ.get("OCR_LOCAL_ENGINE", "false").strip().lower() == "true"

# ─── Existing OCR service config (relocated from main.py) ────────────────────
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
DEFAULT_RENDER_SCALE = float(os.environ.get("RENDER_SCALE", "1.5"))
OCR_TIMEOUT = int(os.environ.get("OCR_TIMEOUT_SECONDS", "600"))
RATE_LIMIT_MAX = int(os.environ.get("JOB_RATE_LIMIT", "10"))
RATE_LIMIT_WINDOW = int(os.environ.get("JOB_RATE_LIMIT_WINDOW_SECONDS", "60"))
