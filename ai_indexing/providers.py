"""Multi-provider AI client abstraction — Gemini (via its OpenAI-compatible
endpoint), a generic OpenAI-compatible endpoint (self-hosted/other
providers), and RokkaAI (a custom async submit-then-poll job queue).

Deliberately NOT tender_ai's pattern: no hand-rolled `_is_gemini`/
`_is_rokkaai` string-sniffing duplicated per pipeline. Provider is an
explicit, stored field (ProviderConfig.provider); sniffing is confined to
one place (sniff_provider(), a UI-convenience helper only — never on the
call path).

Also deliberately NOT tender_ai's `messages: list` / multimodal contract:
input here is always already-OCR'd plain text, so a plain
`(system_prompt, user_prompt) -> str` contract is sufficient and is the only
shape RokkaAI can honor natively, which removes the lossy
messages-to-RokkaAI translation tender_ai needs.
"""

import abc
import asyncio
import hashlib
import re
import time
from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, Optional

import httpx

import config
from ai_indexing.errors import (
    AICancelledError,
    AIAuthError,
    AIConfigError,
    AIRateLimitError,
    AIRemoteError,
    AITimeoutError,
    AIUnreachableError,
)
from ai_indexing.redact import SecretStr


@dataclass(frozen=True)
class ProviderConfig:
    provider: str                       # "gemini" | "openai_compatible" | "rokkaai" — explicit, never sniffed
    model: str
    api_key: SecretStr
    base_url: str = ""
    temperature: float = 0.0
    max_output_tokens: int = 8192
    request_timeout_s: float = 180.0
    json_mode: str = "auto"              # "auto" | "json_object" | "off"
    poll_interval_s: float = 3.0
    poll_timeout_s: float = 600.0
    extra: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.api_key, SecretStr):
            object.__setattr__(self, "api_key", SecretStr(str(self.api_key)))

    @property
    def key_fingerprint(self) -> str:
        return hashlib.sha256(self.api_key.get_secret_value().encode("utf-8")).hexdigest()[:12]

    def __repr__(self) -> str:
        return (f"ProviderConfig(provider={self.provider!r}, model={self.model!r}, "
                f"base_url={self.base_url!r}, api_key={self.api_key!r})")

    __str__ = __repr__


@dataclass
class CompletionResult:
    text: str
    provider: str
    model: str
    duration_ms: int
    finish_reason: Optional[str] = None
    provider_job_id: Optional[str] = None


def _require_https_or_local(base_url: str) -> None:
    if not base_url:
        return
    try:
        parsed = httpx.URL(base_url)
    except Exception as e:
        raise AIConfigError(f"base_url is not a valid URL: {base_url!r}") from e
    if parsed.scheme == "https":
        return
    if parsed.scheme == "http" and parsed.host in ("localhost", "127.0.0.1", "::1"):
        return
    raise AIConfigError(
        f"base_url must be https, or plain http only for localhost/127.0.0.1 — got {base_url!r}."
    )


class BaseProvider(abc.ABC):
    name: ClassVar[str] = ""

    def __init__(self, cfg: ProviderConfig) -> None:
        self.__class__.validate_config(cfg)
        self.cfg = cfg
        self._cancel_check = None  # optional callable[[], bool], set by the caller per call

    @classmethod
    @abc.abstractmethod
    def validate_config(cls, cfg: ProviderConfig) -> None:
        """Raises AIConfigError. No network I/O."""

    @abc.abstractmethod
    async def _complete_once(self, system_prompt: str, user_prompt: str) -> CompletionResult:
        ...

    async def complete(self, *, system_prompt: str, user_prompt: str,
                        cancel_check=None) -> CompletionResult:
        """Shared transport-retry wrapper: retries rate-limit/timeout/
        unreachable/5xx up to config.AI_INDEX_MAX_TRANSPORT_RETRIES with
        exponential backoff (honouring retry_after when present). Never
        retries AIConfigError/AIAuthError. `cancel_check`, if given, is a
        zero-arg callable returning True when the caller wants this call
        abandoned (pause/cancel) — checked before each attempt and, for
        providers whose own call can run long, inside their poll loop too."""
        self._cancel_check = cancel_check
        last_exc = None
        for attempt in range(config.AI_INDEX_MAX_TRANSPORT_RETRIES + 1):
            if cancel_check and cancel_check():
                raise AICancelledError("Cancelled before dispatch.", provider=self.name)
            start = time.monotonic()
            try:
                result = await self._complete_once(system_prompt, user_prompt)
                result.duration_ms = int((time.monotonic() - start) * 1000)
                return result
            except (AIRateLimitError, AITimeoutError, AIUnreachableError, AIRemoteError) as e:
                last_exc = e
                if attempt >= config.AI_INDEX_MAX_TRANSPORT_RETRIES:
                    raise
                delay = e.retry_after if getattr(e, "retry_after", None) else min(2 ** attempt, 30)
                await asyncio.sleep(delay)
        raise last_exc  # pragma: no cover — loop always returns or raises above

    async def health(self) -> Dict[str, Any]:
        start = time.monotonic()
        try:
            await self._complete_once(
                "You are a health check. Reply with exactly: OK",
                "Reply with exactly: OK",
            )
            return {"ok": True, "detail": "", "latency_ms": int((time.monotonic() - start) * 1000)}
        except AIConfigError as e:
            raise e
        except Exception as e:  # noqa: BLE001 — health check reports, never raises soft errors
            return {"ok": False, "detail": str(e), "latency_ms": int((time.monotonic() - start) * 1000)}

    async def aclose(self) -> None:
        pass


class _OpenAIChatProvider(BaseProvider):
    """Shared /chat/completions implementation. Gemini and the generic
    catch-all are thin subclasses of this, not two separate copies."""

    default_base_url: ClassVar[str] = ""
    _json_mode_supported: ClassVar[bool] = True  # per-class memo; flips off on a 400 mentioning response_format

    def __init__(self, cfg: ProviderConfig) -> None:
        super().__init__(cfg)
        self._client = None

    def _get_client(self):
        if self._client is None:
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(
                api_key=self.cfg.api_key.get_secret_value(),
                base_url=self.cfg.base_url or self.default_base_url,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    def _use_json_mode(self) -> bool:
        return self.cfg.json_mode == "json_object" or (
            self.cfg.json_mode == "auto" and self.__class__._json_mode_supported
        )

    async def _complete_once(self, system_prompt: str, user_prompt: str) -> CompletionResult:
        import openai as openai_sdk

        kwargs: Dict[str, Any] = dict(
            model=self.cfg.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=self.cfg.temperature,
            max_tokens=self.cfg.max_output_tokens,
        )
        use_json_mode = self._use_json_mode()
        if use_json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        try:
            resp = await self._get_client().chat.completions.create(
                **kwargs, timeout=self.cfg.request_timeout_s,
            )
        except openai_sdk.AuthenticationError as e:
            raise AIAuthError(f"{self.name} rejected the API key: {e}", provider=self.name) from e
        except openai_sdk.RateLimitError as e:
            retry_after = _parse_retry_after(str(e))
            raise AIRateLimitError(f"{self.name} rate-limited this request: {e}",
                                    provider=self.name, retry_after=retry_after) from e
        except openai_sdk.APITimeoutError as e:
            raise AITimeoutError(f"{self.name} request timed out: {e}", provider=self.name) from e
        except openai_sdk.APIConnectionError as e:
            raise AIUnreachableError(f"Cannot reach {self.name}: {e}", provider=self.name) from e
        except openai_sdk.BadRequestError as e:
            if use_json_mode and "response_format" in str(e).lower():
                # Self-hosted/other endpoint doesn't implement JSON mode —
                # degrade instead of hard-failing, then retry once without it.
                self.__class__._json_mode_supported = False
                kwargs.pop("response_format", None)
                resp = await self._get_client().chat.completions.create(
                    **kwargs, timeout=self.cfg.request_timeout_s,
                )
            else:
                raise AIRemoteError(f"{self.name} rejected the request: {e}", provider=self.name) from e
        except openai_sdk.APIStatusError as e:
            status = getattr(e, "status_code", None)
            if status in (401, 403):
                raise AIAuthError(f"{self.name} rejected the API key: {e}", provider=self.name) from e
            raise AIRemoteError(f"{self.name} returned {status}: {e}",
                                 provider=self.name, status_code=status) from e

        choice = resp.choices[0]
        text = choice.message.content or ""
        return CompletionResult(
            text=text, provider=self.name, model=self.cfg.model, duration_ms=0,
            finish_reason=getattr(choice, "finish_reason", None),
        )


_RETRY_DELAY_RE = re.compile(r"retryDelay['\"]?\s*[:=]\s*['\"]?(\d+(?:\.\d+)?)s", re.IGNORECASE)


def _parse_retry_after(text: str) -> Optional[float]:
    m = _RETRY_DELAY_RE.search(text or "")
    return float(m.group(1)) if m else None


class GeminiProvider(_OpenAIChatProvider):
    name = "gemini"
    default_base_url = config.AI_GEMINI_BASE_URL

    @classmethod
    def validate_config(cls, cfg: ProviderConfig) -> None:
        if not cfg.model:
            raise AIConfigError("A model name is required for the Gemini provider.")
        if not cfg.api_key or not cfg.api_key.get_secret_value():
            raise AIConfigError("An API key is required for the Gemini provider.")


class OpenAICompatibleProvider(_OpenAIChatProvider):
    name = "openai_compatible"

    @classmethod
    def validate_config(cls, cfg: ProviderConfig) -> None:
        if not cfg.base_url:
            raise AIConfigError("base_url is required for the generic OpenAI-compatible provider.")
        _require_https_or_local(cfg.base_url)
        if not cfg.model:
            raise AIConfigError("A model name is required for the OpenAI-compatible provider.")


class RokkaAIProvider(BaseProvider):
    """Submit-then-poll job queue, mirroring gpu_ocr_client.py's own
    submit/_poll_until_done_full idiom for this exact API shape. Fixes two
    real bugs found in the sibling tender_ai project's rokkaai_client.py:
    (1) it read a module-global settings object instead of per-call config,
    so it couldn't serve two credentials; (2) a job whose own status became
    "failed" polled all the way to the timeout instead of raising
    immediately — this raises AIRemoteError the moment a terminal failure
    status is observed."""

    name = "rokkaai"

    @classmethod
    def validate_config(cls, cfg: ProviderConfig) -> None:
        if not cfg.base_url:
            raise AIConfigError("base_url is required for the RokkaAI provider.")
        _require_https_or_local(cfg.base_url)
        if not cfg.api_key or not cfg.api_key.get_secret_value():
            raise AIConfigError("An API key is required for the RokkaAI provider.")

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.cfg.api_key.get_secret_value()}"}

    async def _submit(self, client: httpx.AsyncClient, query: str, system_prompt: str) -> str:
        try:
            resp = await client.post(
                f"{self.cfg.base_url.rstrip('/')}/api/v1/query",
                json={"query": query, "data": [{"type": "text", "value": system_prompt}]},
                headers=self._headers(),
            )
        except httpx.RequestError as e:
            raise AIUnreachableError(f"Cannot reach RokkaAI: {e}", provider=self.name) from e
        if resp.status_code in (401, 403):
            raise AIAuthError("RokkaAI rejected the API key.", provider=self.name)
        if resp.status_code == 429:
            raise AIRateLimitError("RokkaAI rate-limited this request.", provider=self.name)
        if resp.status_code >= 400:
            raise AIRemoteError(f"RokkaAI returned {resp.status_code}: {resp.text}",
                                 provider=self.name, status_code=resp.status_code)
        return resp.json()["job_id"]

    async def _poll(self, client: httpx.AsyncClient, job_id: str) -> str:
        deadline = time.monotonic() + self.cfg.poll_timeout_s
        while True:
            if self._cancel_check and self._cancel_check():
                raise AICancelledError("Cancelled while polling RokkaAI.", provider=self.name)
            if time.monotonic() > deadline:
                raise AITimeoutError(f"RokkaAI job {job_id} did not complete within "
                                      f"{self.cfg.poll_timeout_s}s.", provider=self.name)
            try:
                resp = await client.get(
                    f"{self.cfg.base_url.rstrip('/')}/api/v1/jobs/{job_id}", headers=self._headers(),
                )
            except httpx.RequestError as e:
                raise AIUnreachableError(f"Cannot reach RokkaAI while polling: {e}", provider=self.name) from e
            if resp.status_code in (401, 403):
                raise AIAuthError("RokkaAI rejected the API key.", provider=self.name)
            data = resp.json()
            status = data.get("status")
            if status == "done":
                return data.get("result") or ""
            if status in ("failed", "error"):
                raise AIRemoteError(data.get("error") or "RokkaAI job failed with no error detail.",
                                     provider=self.name)
            # Poll immediately on first check, then sleep — deliberately not a
            # fixed floor before the first poll (a real bug in the sibling
            # project's own client, which slept before checking even once).
            await asyncio.sleep(self.cfg.poll_interval_s)

    async def _complete_once(self, system_prompt: str, user_prompt: str) -> CompletionResult:
        async with httpx.AsyncClient(timeout=self.cfg.request_timeout_s) as client:
            job_id = await self._submit(client, user_prompt, system_prompt)
            text = await self._poll(client, job_id)
        return CompletionResult(
            text=text, provider=self.name, model=self.cfg.model, duration_ms=0,
            provider_job_id=job_id,
        )


_PROVIDERS: Dict[str, type] = {
    GeminiProvider.name: GeminiProvider,
    OpenAICompatibleProvider.name: OpenAICompatibleProvider,
    RokkaAIProvider.name: RokkaAIProvider,
}


def list_providers():
    return sorted(_PROVIDERS)


_instances: Dict[tuple, BaseProvider] = {}
_instances_lock = asyncio.Lock()


async def get_provider(cfg: ProviderConfig) -> BaseProvider:
    """Returns a cached instance keyed by (provider, base_url, model,
    key_fingerprint) — never by the key itself — so the underlying HTTP
    connection pool is reused across a whole document's worth of chunk
    calls instead of reconnecting every time."""
    cls = _PROVIDERS.get(cfg.provider)
    if cls is None:
        raise AIConfigError(f"Unknown AI provider {cfg.provider!r}. Supported: {list_providers()}")
    key = (cfg.provider, cfg.base_url, cfg.model, cfg.key_fingerprint)
    async with _instances_lock:
        inst = _instances.get(key)
        if inst is None:
            inst = cls(cfg)
            _instances[key] = inst
        return inst


def sniff_provider(model: str, api_key: str) -> Optional[str]:
    """UX-only helper for prefilling the settings-UI provider dropdown from
    a pasted key/model — never called on the actual request path, where
    provider is always an explicit, stored field."""
    if api_key and api_key.startswith("rokkaai_"):
        return "rokkaai"
    if model and model.lower().startswith("gemini"):
        return "gemini"
    return None


async def aclose_all() -> None:
    async with _instances_lock:
        for inst in _instances.values():
            try:
                await inst.aclose()
            except Exception:  # noqa: BLE001 — best-effort shutdown
                pass
        _instances.clear()
