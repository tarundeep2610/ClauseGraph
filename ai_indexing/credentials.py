"""AI provider configuration — read live from env vars only, on every call.
Deliberately NOT stored anywhere (no DB table, no encryption at rest): the
API key lives in config.AI_INDEX_API_KEY, same as every other secret in
this project (GPU_OCR_SHARED_SECRET, TS_AUTHKEY, ...), and is never written
to the database or returned to the frontend. This is one shared provider
config for the whole deployment, not a per-user/per-job credential store.
"""

import json
from dataclasses import dataclass
from typing import Any, Dict, Optional

import config
from ai_indexing.errors import AIConfigError
from ai_indexing.providers import ProviderConfig
from ai_indexing.redact import SecretStr


@dataclass(frozen=True)
class ResolvedAIConfig:
    provider_config: ProviderConfig
    chunk_pages: int


def resolve_active_config() -> ResolvedAIConfig:
    """Builds a usable provider config straight from the current process
    environment. Raises AIConfigError if no key is configured — the caller
    (ai_reference_index.reindex_job_with_ai) treats this as terminal for
    the whole AI pass, falling the document back to the rule-based
    detector entirely."""
    if not config.AI_INDEX_API_KEY:
        raise AIConfigError(
            "AI_INDEX_API_KEY is not set on the server — AI indexing has nothing to "
            "authenticate with. Set AI_INDEX_API_KEY (and AI_INDEX_PROVIDER/MODEL/"
            "BASE_URL as needed) in the server's environment and restart it."
        )
    extra: Dict[str, Any] = {}
    if config.AI_INDEX_EXTRA_HEADERS_JSON:
        try:
            headers = json.loads(config.AI_INDEX_EXTRA_HEADERS_JSON)
        except (ValueError, TypeError) as e:
            raise AIConfigError(
                f"AI_INDEX_EXTRA_HEADERS_JSON is not valid JSON: {e}"
            ) from e
        if not isinstance(headers, dict):
            raise AIConfigError("AI_INDEX_EXTRA_HEADERS_JSON must be a JSON object.")
        extra["extra_headers"] = {str(k): str(v) for k, v in headers.items()}

    provider_config = ProviderConfig(
        provider=config.AI_INDEX_PROVIDER,
        model=config.AI_INDEX_MODEL,
        api_key=SecretStr(config.AI_INDEX_API_KEY),
        base_url=config.AI_INDEX_BASE_URL,
        request_timeout_s=config.AI_INDEX_REQUEST_TIMEOUT_SECONDS,
        max_output_tokens=config.AI_INDEX_MAX_OUTPUT_TOKENS,
        temperature=config.AI_INDEX_TEMPERATURE,
        poll_interval_s=config.AI_ROKKAAI_POLL_INTERVAL_SECONDS,
        poll_timeout_s=config.AI_ROKKAAI_POLL_TIMEOUT_SECONDS,
        extra=extra,
    )
    return ResolvedAIConfig(provider_config=provider_config, chunk_pages=config.AI_INDEX_CHUNK_PAGES)


def describe_active_config() -> Optional[Dict[str, Any]]:
    """Redacted view for job creation / settings UI: no plaintext key, ever.
    None means "not configured" — the caller treats that as AI indexing
    being effectively off regardless of the enable_ai_indexing checkbox."""
    if not config.AI_INDEX_API_KEY:
        return None
    key = config.AI_INDEX_API_KEY
    return {
        "provider": config.AI_INDEX_PROVIDER, "model": config.AI_INDEX_MODEL,
        "base_url": config.AI_INDEX_BASE_URL,
        "key_hint": key[-4:] if len(key) >= 4 else key,
        "configured": True,
    }
