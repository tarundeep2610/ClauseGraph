"""Scoring-harness-only RokkaAI provider variant that survives the
container/laptop going away mid-chunk.

Why this exists: RokkaAI is a submit-then-poll job queue (`RokkaAIProvider`
in `ai_indexing/providers.py` — `POST /api/v1/query` -> job_id, `GET
/api/v1/jobs/{job_id}` -> status/answer). Today `_complete_once` submits and
polls back-to-back in one call; if our own process dies mid-poll (confirmed:
closing the laptop suspends Docker Desktop itself, not just the terminal),
the in-memory job_id is gone even though RokkaAI's GPU backend keeps
computing it regardless, on its own infrastructure. A restart has no way to
find that job_id again and must resubmit from scratch, wasting whatever was
already done or in flight.

The prompt sent to RokkaAI for a given chunk is built purely from page
content (OCR text + carry-context from already-persisted prior chunks),
never from our own job's UUID — so a job_id cache keyed by
hash(system_prompt + user_prompt) works even across a brand new duplicate
job after `score_reference_index.py` is simply re-run from scratch: the
fresh duplicate's chunk 1 produces byte-identical prompt text to the
abandoned duplicate's chunk 1, so this cache recognizes it and polls the
already-submitted RokkaAI job instead of waiting on a fresh GPU run.

Deliberately NOT wired into ai_indexing/providers.py's own _PROVIDERS
registry or used by any production code path — scope is the scoring harness
only (score_reference_index.py's own --resumable flag registers this class
process-locally). See the investigation's plan file for the full rationale.
"""

import hashlib
import json
import os
import time
from typing import Any, Dict

import httpx

from ai_indexing.providers import CompletionResult, RokkaAIProvider

STATE_FILE = os.path.join(os.environ.get("DATA_DIR", "/app/data"), "rokkaai_resume_state.json")


def _prompt_hash(system_prompt: str, user_prompt: str) -> str:
    return hashlib.sha256((system_prompt + "\0" + user_prompt).encode("utf-8")).hexdigest()


def _load_state() -> Dict[str, Any]:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_state(state: Dict[str, Any]) -> None:
    # Atomic write (temp file + rename) so a kill mid-write can never leave
    # the state file half-written/corrupted — the one write this whole
    # mechanism depends on being trustworthy.
    os.makedirs(os.path.dirname(STATE_FILE) or ".", exist_ok=True)
    tmp_path = f"{STATE_FILE}.tmp-{os.getpid()}"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp_path, STATE_FILE)


class ResumableRokkaAIProvider(RokkaAIProvider):
    """Drop-in RokkaAIProvider replacement for score_reference_index.py's
    --resumable flag. Only _complete_once differs — _submit/_poll are
    reused unchanged from the parent class."""

    async def _complete_once(self, system_prompt: str, user_prompt: str) -> CompletionResult:
        prompt_hash = _prompt_hash(system_prompt, user_prompt)
        state = _load_state()
        pending = state.get(prompt_hash)

        async with httpx.AsyncClient(timeout=self.cfg.request_timeout_s) as client:
            if pending:
                job_id = pending["job_id"]
            else:
                job_id = await self._submit(client, user_prompt, system_prompt)
                # Persisted BEFORE polling starts — this is the write that
                # must survive an abrupt kill; everything after it is just
                # re-derivable by polling the same job_id again.
                state[prompt_hash] = {"job_id": job_id, "submitted_at": time.time()}
                _save_state(state)

            try:
                text = await self._poll(client, job_id)
            except Exception:
                # Terminal failure (or cancellation) — don't leave the cache
                # pointing at a dead job forever; a genuine retry must be
                # able to submit fresh next time.
                state = _load_state()
                state.pop(prompt_hash, None)
                _save_state(state)
                raise

        # Resolved — no longer pending.
        state = _load_state()
        state.pop(prompt_hash, None)
        _save_state(state)

        return CompletionResult(
            text=text, provider=self.name, model=self.cfg.model, duration_ms=0,
            provider_job_id=job_id,
        )
