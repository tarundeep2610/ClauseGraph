"""Exception taxonomy for ai_indexing. Mirrors gpu_ocr_client.py's
GPUServiceError/Unreachable/AuthError/Timeout/JobFailed shape, split into two
families the calling (stitching) layer treats very differently:

- AIConfigError and its subclasses are TERMINAL: stop the whole AI pass for
  this job, surface a warning, never retried.
- AIProviderError/AIInvalidOutputError are SOFT: only this one chunk falls
  back to the rule-based detector; the rest of the document is unaffected.

client.StructureIndexer never raises the soft kind itself — it catches them
and returns a ChunkOutcome.FAILED result instead, so the stitching layer's
branch is a single `try/except AIConfigError` plus an `if outcome is FAILED`
check. See classify()/is_fallbackable() below for that dispatch.
"""

from typing import Literal, Optional


class AIIndexError(Exception):
    """Base for everything ai_indexing raises."""


# ─── Terminal: stop the whole AI pass, surface to the user ───────────────────

class AIConfigError(AIIndexError):
    """Misconfiguration — never retried, never silently swallowed per
    chunk. Raised for: AI_INDEX_API_KEY not set; an unknown provider name;
    a required base_url that's empty; a non-positive chunk_pages."""


class AIAuthError(AIConfigError):
    """401/403 from the provider — a bad/revoked/expired key. Subclass of
    AIConfigError deliberately: silently retrying or falling back would hide
    a key the user must go rotate."""


class AIIndexingDisabledError(AIConfigError):
    """Circuit breaker: config.AI_INDEX_CONSECUTIVE_FAILURE_LIMIT consecutive
    chunk failures. Stops a 900-page document from making dozens of doomed
    calls after the first few already failed for a structural reason."""


# ─── Soft: this one chunk falls back to the rule-based detector ──────────────

class AIProviderError(AIIndexError):
    """Base for remote-side failures that are per-call, not per-config."""

    def __init__(self, message: str, *, status_code: Optional[int] = None,
                 provider: str = "", retry_after: Optional[float] = None):
        super().__init__(message)
        self.status_code = status_code
        self.provider = provider
        self.retry_after = retry_after


class AIUnreachableError(AIProviderError):
    """DNS/connect/TLS/network-reset failure reaching the provider."""


class AITimeoutError(AIProviderError):
    """A read timeout, or a RokkaAI poll that hit its deadline."""


class AIRateLimitError(AIProviderError):
    """HTTP 429. `retry_after` is populated when the provider's response
    body/headers give a usable retry delay."""


class AIRemoteError(AIProviderError):
    """5xx, or a RokkaAI job whose own status became "failed"/"error"."""


class AICancelledError(AIProviderError):
    """The job was paused/cancelled while this chunk was in flight (checked
    mid-poll for providers whose call can run long, e.g. RokkaAI). NOT a
    provider failure — must not count against the circuit breaker and must
    not trigger a rule-based fallback for this chunk; the caller should just
    stop cleanly, exactly as pausing OCR already does."""


class AIInvalidOutputError(AIIndexError):
    """The provider answered, but the answer is not a schema-valid structure
    object after fence-strip + brace-extract + repair + json_repair +
    Pydantic-validate, even after one full re-call. Carries a redacted,
    truncated excerpt of the raw text for the job's audit log."""

    def __init__(self, message: str, *, raw_excerpt: str = "", validation_error: str = ""):
        super().__init__(message)
        self.raw_excerpt = raw_excerpt[:2000]
        self.validation_error = validation_error


ErrorKind = Literal[
    "config", "auth", "unreachable", "timeout", "rate_limit", "remote",
    "cancelled", "invalid_output", "unknown",
]


def classify(exc: BaseException) -> ErrorKind:
    if isinstance(exc, AIAuthError):
        return "auth"
    if isinstance(exc, AIConfigError):
        return "config"
    if isinstance(exc, AICancelledError):
        return "cancelled"
    if isinstance(exc, AIUnreachableError):
        return "unreachable"
    if isinstance(exc, AITimeoutError):
        return "timeout"
    if isinstance(exc, AIRateLimitError):
        return "rate_limit"
    if isinstance(exc, AIRemoteError):
        return "remote"
    if isinstance(exc, AIInvalidOutputError):
        return "invalid_output"
    return "unknown"


def is_fallbackable(exc: BaseException) -> bool:
    """True -> the stitching layer should run the rule-based detector for
    this chunk. False -> stop the AI pass and surface the error (a config
    problem, or a user-requested cancellation, neither of which a rule-based
    retry would fix or should override)."""
    if isinstance(exc, AICancelledError):
        return False
    return isinstance(exc, (AIProviderError, AIInvalidOutputError))
