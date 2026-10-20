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

# Root cause of the recurring 8+ minute hangs (confirmed via web research
# against Ollama's own issue tracker, e.g. ollama/ollama#9382, #11172): we
# never set `num_predict` (Ollama's generation-length cap), which defaults
# to unlimited. Once a generation fills num_ctx, Ollama's "context shift"
# lets it keep going indefinitely instead of stopping — and because this
# request uses "stream": false, nothing is sent until generation fully
# ends, so a runaway-but-alive generation is indistinguishable from a true
# hang (confirmed directly: the TCP connection stayed ESTABLISHED with
# zero bytes received for 15+ minutes, no error). `think: false` only
# removes the HIDDEN reasoning chain as a source of this — the model's
# VISIBLE answer (e.g. a repeated heading/JSON array entry) can still loop,
# which is why hangs persisted even with it set.
#
# num_ctx and num_predict are computed together, not as independent fixed
# constants, so prompt_tokens + num_predict can never exceed num_ctx
# regardless of future prompt-size changes (this already happened once,
# when chunk_chars_max was raised for a 64000-char experiment) — a fixed
# pair of constants would only be safe for today's observed prompt range.
MAX_CTX = 40960  # Qwen3-8B's native context ceiling
DESIRED_NUM_PREDICT = 14000  # comfortably above the largest legitimate response observed (10,468 tokens)
ATTEMPT_TIMEOUT_S = 300.0  # lowered now that num_predict bounds a legitimate response to ~prompt processing + num_predict/~90tok/s (~2-3 min)
MAX_ATTEMPTS = 2  # 1 initial + 1 retry, per the user's explicit "try once more, then skip" instruction
RETRY_TEMPERATURE_BUMP = 0.1  # a loop is sampling-dependent; an identical retry has no reason to behave differently


class OllamaNativeProvider(BaseProvider):
    name = "ollama_native"

    @classmethod
    def validate_config(cls, cfg: ProviderConfig) -> None:
        if not cfg.base_url:
            raise ValueError("ollama_native requires base_url (e.g. http://host:port)")
        if not cfg.model:
            raise ValueError("ollama_native requires model")

    async def _one_attempt(
        self, system_prompt: str, user_prompt: str, num_ctx: int, num_predict: int, attempt: int,
    ) -> CompletionResult:
        temperature = self.cfg.temperature + (RETRY_TEMPERATURE_BUMP if attempt > 1 else 0.0)
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
            "options": {"temperature": temperature, "num_ctx": num_ctx, "num_predict": num_predict},
        }
        url = f"{self.cfg.base_url.rstrip('/')}/api/chat"
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=ATTEMPT_TIMEOUT_S) as client:
                resp = await asyncio.wait_for(client.post(url, json=payload), timeout=ATTEMPT_TIMEOUT_S)
        except (httpx.TimeoutException, asyncio.TimeoutError) as e:
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            # No body was ever received for a genuine timeout, so there's no
            # eval_count/done_reason to log — log what we DO know instead,
            # so a timeout still leaves a diagnostic trace (previously only
            # a success logged anything at all).
            print(
                f"[ollama_native] TIMEOUT attempt={attempt} elapsed_ms={elapsed_ms} "
                f"num_ctx={num_ctx} num_predict={num_predict} approx_input_tokens={(len(system_prompt) + len(user_prompt)) // 4}",
                flush=True,
            )
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
        done_reason = data.get("done_reason")
        if done_reason == "length":
            # A loud, distinct outcome, not folded into a normal success
            # log line: the model was still generating when num_predict cut
            # it off — a runaway (capped, not hung) generation, instantly
            # distinguishable from a clean completion in the logs.
            print(
                f"[ollama_native] WARNING: generation capped by num_predict (done_reason=length) "
                f"num_predict={num_predict} eval_count={data.get('eval_count')} elapsed={elapsed_ms/1000:.1f}s",
                flush=True,
            )
        print(
            f"[ollama_native] num_ctx={num_ctx} num_predict={num_predict} elapsed={elapsed_ms/1000:.1f}s "
            f"prompt_eval_count={data.get('prompt_eval_count')} eval_count={data.get('eval_count')} "
            f"done_reason={done_reason} content_len={len(content)}",
            flush=True,
        )
        return CompletionResult(
            text=content,
            provider=self.name,
            model=self.cfg.model,
            duration_ms=elapsed_ms,
            finish_reason=done_reason,
        )

    async def _complete_once(self, system_prompt: str, user_prompt: str) -> CompletionResult:
        approx_input_tokens = (len(system_prompt) + len(user_prompt)) // 4
        # Co-dependent, not independent fixed constants: guarantees
        # approx_input_tokens + num_predict < num_ctx always, regardless of
        # how large a future prompt gets.
        num_ctx = min(MAX_CTX, max(32768, approx_input_tokens + DESIRED_NUM_PREDICT + 512))
        num_predict = min(DESIRED_NUM_PREDICT, num_ctx - approx_input_tokens - 256)
        last_exc: Exception = AITimeoutError("unreachable", provider=self.name)
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                return await self._one_attempt(system_prompt, user_prompt, num_ctx, num_predict, attempt)
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
        # script only, never production) so our own 2-attempt cap is the
        # real, total cap per chunk.
        last_exc.is_daily_quota = True
        raise last_exc
