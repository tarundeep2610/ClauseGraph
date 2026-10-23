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

# ─── Unlimited-OCR grounding gate — despite the name (kept for setting-key/
# DB compatibility with the setting this was first built as), this does NOT
# prepend a `<|grounding|>` prompt token. Confirmed directly against the
# model (see the project plan) that the token does nothing useful here —
# an independent, merged fix in the Docling project found the same plain
# prompt already produces identical `<|det|>` output, and that adding the
# token only degrades transcription quality (matching what was observed
# here too). What this flag actually controls: switching Unlimited-OCR
# from its "base" config (image_size=base_size, one global view, no
# per-region output) to its official "gundam" config (image_size=640,
# per-region crops) — confirmed, via a corrected direct test, to be what
# actually makes the model emit real `<|det|>type [bbox]<|/det|>content`
# layout tags (the exact shape reference_index.py's `_DET_TAG_RE`/
# `parse_layout_blocks()` already parse):
#   <|det|>header [612, 29, 905, 61]<|/det|>AmneXureA-1
#   <|det|>text [383, 223, 792, 243]<|/det|>CONTRACT AGREEMENT BETWEEN
# Getting this into `raw_ocr_text` also required a second, independent fix
# in unlimited_ocr_engine.py's run_ocr_with_raw() (both copies): the
# model's own save_results=True output file is already stripped of tags by
# the vendor's own infer() before it's written — the tags only ever exist
# in the raw generation stream, which is now what raw_text is sourced from.
# On by default: gated the same way as ENABLE_UNLIMITED_LONG_PROMPTS above —
# runtime-overridable via app_settings; this constant is only the fallback
# default. Originally opt-in-only (gundam mode changes image preprocessing
# for every page and was measured at 2-4x slower per page at the time) — a
# real-job measurement on the current GPU service (18.1s/page) found gundam
# mode far cheaper than that, so the per-job checkbox now defaults to on,
# with per-job opt-out still available for anyone who wants the faster
# single-pass mode for a specific job.
ENABLE_UNLIMITED_GROUNDING = os.environ.get("ENABLE_UNLIMITED_GROUNDING", "true").strip().lower() == "true"

# ─── DB durability/performance ─────────────────────────────────────────────────
OCR_DB_BUSY_TIMEOUT_MS = int(os.environ.get("OCR_DB_BUSY_TIMEOUT_MS", "5000"))
OCR_WAL_CHECKPOINT_EVERY_N_PAGES = int(os.environ.get("OCR_WAL_CHECKPOINT_EVERY_N_PAGES", "50"))

# ─── Pause drain ───────────────────────────────────────────────────────────────
OCR_PAUSE_DRAIN_TIMEOUT_SECONDS = int(os.environ.get("OCR_PAUSE_DRAIN_TIMEOUT_SECONDS", "900"))

# ─── AI-assisted document-structure indexing — runtime-overridable via
# app_settings (ai_indexing_enabled); this is only the fallback default, same
# pattern as ENABLE_UNLIMITED_GROUNDING above. See ai_indexing/ package and
# ai_reference_index.py.
#
# The provider API key is deliberately NEVER stored anywhere (no DB table,
# no encryption at rest) — read live from this env var only, same as every
# other secret in this project (GPU_OCR_SHARED_SECRET, TS_AUTHKEY, ...).
# AI_INDEX_PROVIDER/MODEL/BASE_URL are one shared config for the whole
# deployment, not a per-user/per-job credential store. ────────────────────────
AI_INDEXING_ENABLED = os.environ.get("AI_INDEXING_ENABLED", "false").strip().lower() == "true"
AI_INDEX_PROVIDER = os.environ.get("AI_INDEX_PROVIDER", "gemini")
AI_INDEX_MODEL = os.environ.get("AI_INDEX_MODEL", "gemini-2.5-flash")
AI_INDEX_BASE_URL = os.environ.get("AI_INDEX_BASE_URL", "")
AI_INDEX_API_KEY = os.environ.get("AI_INDEX_API_KEY", "")
# Optional extra HTTP headers for the openai_compatible provider's client,
# as a JSON object string — e.g. '{"x-gpu-ocr-key": "..."}' to reach an
# Ollama instance sitting behind this project's own gated Caddy /ollama/*
# route (see gpu_ocr_client.py's identical header for the GLM-OCR engine's
# own calls to the same box). Empty/unset for every deployment that talks
# to a provider directly — this changes nothing unless explicitly set.
AI_INDEX_EXTRA_HEADERS_JSON = os.environ.get("AI_INDEX_EXTRA_HEADERS_JSON", "")

# Was 5. Measured (AI_INDEXING_CHUNK_SIZE_MEASUREMENT.md, qwen3:8b via
# rokkaai, 5 trials at chunk_pages=3 vs. 5 at chunk_pages=10 and 4 valid at
# chunk_pages=5, review_rule_prior=off, overlap=0, on the 17-page gold
# fixture): chunk_pages=3 had the best mean precision (0.781 vs 0.700/0.666)
# and recall (0.754 vs 0.696/0.710), and was the only size with zero
# family-drift incidents across every trial (chunk_pages=10 produced one
# FamilyDrift=8 episode). Trial-to-trial variance at every size tested is
# real and substantial (chunk3 alone ranged 0.651-0.837 precision across 5
# trials) — this is the known non-deterministic-recall problem, not unique
# to this setting. Costs ~3.3x more AI calls than chunk_pages=10 for the
# same document; accepted given the accuracy/stability difference.
# Re-measure before changing again, especially on a different model.
AI_INDEX_CHUNK_PAGES = int(os.environ.get("AI_INDEX_CHUNK_PAGES", "3"))
# Was 2. Measured (AI_INDEXING_REVIEW_OVERLAP_MEASUREMENT.md, qwen3:8b via
# rokkaai, 3 trials/arm on the 17-page gold fixture): overlap 0/1/2 scored
# within the same band on precision/recall/scope-accuracy — no measurable
# accuracy benefit from overlap — while overlap 2 costs ~28-39% more chunks
# than 0 on a larger document. Changed to 0 since there's no accuracy reason
# left to pay that cost. Re-measure before changing again.
AI_INDEX_CHUNK_OVERLAP_PAGES = int(os.environ.get("AI_INDEX_CHUNK_OVERLAP_PAGES", "0"))
AI_INDEX_CHUNK_CHARS_MAX = int(os.environ.get("AI_INDEX_CHUNK_CHARS_MAX", "60000"))
AI_INDEX_ADAPTIVE_CHUNKING = os.environ.get("AI_INDEX_ADAPTIVE_CHUNKING", "true").strip().lower() == "true"
AI_INDEX_TWO_PASS = os.environ.get("AI_INDEX_TWO_PASS", "false").strip().lower() == "true"
# How many neighboring pages (each direction, within the same chunk) to
# search when grounding an AI-reported heading whose claimed start_page
# doesn't actually contain matching text — see
# ai_reference_index._locate_heading_in_chunk.
AI_INDEX_GROUND_PAGE_WINDOW = int(os.environ.get("AI_INDEX_GROUND_PAGE_WINDOW", "3"))
if AI_INDEX_CHUNK_PAGES < 1:
    raise RuntimeError("AI_INDEX_CHUNK_PAGES must be >= 1.")

AI_INDEX_MAX_HEADINGS_PER_CHUNK = int(os.environ.get("AI_INDEX_MAX_HEADINGS_PER_CHUNK", "200"))
# Sibling cap for the actionable-items pass (ai_action_items.py) — a separate
# constant, not a reuse of the heading cap above, since a dense contract page
# can legitimately report many more discrete obligations than headings.
AI_ACTION_ITEM_MAX_PER_CHUNK = int(os.environ.get("AI_ACTION_ITEM_MAX_PER_CHUNK", "300"))
AI_INDEX_CONSECUTIVE_FAILURE_LIMIT = int(os.environ.get("AI_INDEX_CONSECUTIVE_FAILURE_LIMIT", "3"))

AI_INDEX_REQUEST_TIMEOUT_SECONDS = float(os.environ.get("AI_INDEX_REQUEST_TIMEOUT_SECONDS", "180"))
AI_INDEX_MAX_TRANSPORT_RETRIES = int(os.environ.get("AI_INDEX_MAX_TRANSPORT_RETRIES", "3"))
AI_INDEX_MAX_OUTPUT_TOKENS = int(os.environ.get("AI_INDEX_MAX_OUTPUT_TOKENS", "8192"))
AI_INDEX_TEMPERATURE = float(os.environ.get("AI_INDEX_TEMPERATURE", "0.0"))
# Proactive pacer between chunk dispatches, independent of the per-call
# transport retry above — avoids a predictable 429 cascade on a long
# sequential run against a rate-limited provider tier, rather than relying on
# retries alone to absorb it.
AI_INDEX_MIN_CHUNK_INTERVAL_SECONDS = float(os.environ.get("AI_INDEX_MIN_CHUNK_INTERVAL_SECONDS", "1.0"))

AI_GEMINI_BASE_URL = os.environ.get(
    "AI_GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/")
AI_ROKKAAI_POLL_INTERVAL_SECONDS = float(os.environ.get("AI_ROKKAAI_POLL_INTERVAL_SECONDS", "3"))
AI_ROKKAAI_POLL_TIMEOUT_SECONDS = float(os.environ.get("AI_ROKKAAI_POLL_TIMEOUT_SECONDS", "600"))

AI_INDEX_REVIEW_RULE_PRIOR = os.environ.get("AI_INDEX_REVIEW_RULE_PRIOR", "false").strip().lower() == "true"
AI_INDEX_REVIEW_PRIOR_CHARS_MAX = int(os.environ.get("AI_INDEX_REVIEW_PRIOR_CHARS_MAX", "10000"))
AI_INDEX_REVIEW_MAX_PRIOR_ROWS = int(os.environ.get("AI_INDEX_REVIEW_MAX_PRIOR_ROWS", "150"))
