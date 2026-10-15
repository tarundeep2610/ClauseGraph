"""Scoring-harness-only provider: talks to Ollama's *native* /api/chat
endpoint (not the OpenAI-compatible shim), so we can set `num_ctx`
explicitly per request.

Why this exists instead of using OpenAICompatibleProvider: the OpenAI SDK
chat.completions.create() call that provider uses has no way to pass
Ollama's `num_ctx`. Ollama defaults num_ctx to a small window (commonly
2048) regardless of model capability, which silently truncates our
~15-30K-token prompts and returns an empty/useless completion
(`{"headings": [], "notes": ""}`) with no error — confirmed directly via
scratch/test_ollama_chunk1.py. Setting num_ctx explicitly, sized to the
actual prompt, fixes this.

Never registered in ai_indexing/providers.py's real _PROVIDERS dict —
only monkeypatched into it for the lifetime of this script's process, the
same pattern _resumable_rokkaai_provider.py uses for --resumable mode.
"""
import asyncio
import time

import httpx

from ai_indexing.errors import AIRemoteError, AITimeoutError, AIUnreachableError
from ai_indexing.providers import BaseProvider, CompletionResult, ProviderConfig

# Hard per-attempt cap, enforced via asyncio.wait_for rather than relying on
# httpx's own timeout: a slow-but-live Ollama generation left the TCP
# connection ESTABLISHED well past httpx's configured read timeout with no
# exception raised (observed directly — a chunk ran 15+ min silently).
# asyncio-level cancellation actually tears the call down.
ATTEMPT_TIMEOUT_S = 480.0  # 8 min — raised for the 64000-char/chunk_pages=999 run: a successful 45-page chunk already took 275s
MAX_ATTEMPTS = 2  # 1 initial + 1 retry, per the user's explicit "try once more, then skip" instruction


class OllamaNativeProvider(BaseProvider):
    name = "ollama_native"

    @classmethod
    def validate_config(cls, cfg: ProviderConfig) -> None:
        if not cfg.base_url:
            raise ValueError("ollama_native requires base_url (e.g. http://host:port)")
        if not cfg.model:
            raise ValueError("ollama_native requires model")

    async def _one_attempt(self, system_prompt: str, user_prompt: str, num_ctx: int) -> CompletionResult:
        payload = {
            "model": self.cfg.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "think": False,  # Ollama >=0.9's top-level hybrid-reasoning toggle (confirmed present: server is 0.34.3) —
            # skips Qwen3's internal <think> chain entirely. Its own RULER numbers score HIGHER in non-thinking mode
            # too, so this isn't a quality-for-speed tradeoff on this model.
            "options": {"temperature": self.cfg.temperature, "num_ctx": num_ctx},
        }
        url = f"{self.cfg.base_url.rstrip('/')}/api/chat"
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=ATTEMPT_TIMEOUT_S) as client:
                resp = await asyncio.wait_for(client.post(url, json=payload), timeout=ATTEMPT_TIMEOUT_S)
        except (httpx.TimeoutException, asyncio.TimeoutError) as e:
            raise AITimeoutError(
                f"ollama_native request exceeded the {ATTEMPT_TIMEOUT_S:.0f}s hard cap: {e}", provider=self.name
            ) from e
        except httpx.TransportError as e:
            raise AIUnreachableError(f"Cannot reach ollama_native at {url}: {e}", provider=self.name) from e
        if resp.status_code >= 400:
            raise AIRemoteError(f"ollama_native returned {resp.status_code}: {resp.text}", provider=self.name)
        data = resp.json()
        content = data.get("message", {}).get("content", "")
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        print(
            f"[ollama_native] num_ctx={num_ctx} elapsed={elapsed_ms/1000:.1f}s "
            f"prompt_eval_count={data.get('prompt_eval_count')} eval_count={data.get('eval_count')} "
            f"done_reason={data.get('done_reason')} content_len={len(content)}",
            flush=True,
        )
        return CompletionResult(
            text=content,
            provider=self.name,
            model=self.cfg.model,
            duration_ms=elapsed_ms,
            finish_reason=data.get("done_reason"),
        )

    async def _complete_once(self, system_prompt: str, user_prompt: str) -> CompletionResult:
        approx_input_tokens = (len(system_prompt) + len(user_prompt)) // 4
        num_ctx = max(32768, approx_input_tokens + 4096)
        last_exc: Exception = AITimeoutError("unreachable", provider=self.name)
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                return await self._one_attempt(system_prompt, user_prompt, num_ctx)
            except (AITimeoutError, AIUnreachableError, AIRemoteError) as e:
                last_exc = e
                print(
                    f"[ollama_native] attempt {attempt}/{MAX_ATTEMPTS} failed ({e.__class__.__name__}: {e}); "
                    + ("retrying once..." if attempt < MAX_ATTEMPTS else "giving up, falling back for this chunk."),
                    flush=True,
                )
        # BaseProvider.complete()'s own retry wrapper (ai_indexing/providers.py)
        # would otherwise retry a raised AITimeoutError/AIRemoteError up to
        # config.AI_INDEX_MAX_TRANSPORT_RETRIES more times, stacking another
        # N * MAX_ATTEMPTS * ATTEMPT_TIMEOUT_S on top. `is_daily_quota` is
        # read there via getattr(e, "is_daily_quota", False) as an unconditional
        # "stop retrying now" escape hatch — repurposed here (scoring-harness
        # script only, never production) so our own 2-attempt/5-min-each cap
        # is the real, total cap per chunk.
        last_exc.is_daily_quota = True
        raise last_exc
