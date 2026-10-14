"""Tests for scripts/_resumable_rokkaai_provider.py — the scoring-harness-
only RokkaAI provider variant that caches job_ids by prompt content so a
re-run after this process dies (e.g. the laptop sleeping, which suspends
Docker Desktop itself — confirmed, not just the terminal) polls an
already-submitted chunk instead of resubmitting it. Never used by any
production code path — see the module's own docstring for the full
rationale.

Mocks _submit/_poll entirely — no real RokkaAI calls."""

import json
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from ai_indexing.providers import ProviderConfig
from ai_indexing.redact import SecretStr
import _resumable_rokkaai_provider as rrp
from _resumable_rokkaai_provider import ResumableRokkaAIProvider, _prompt_hash


@pytest.fixture(autouse=True)
def _isolated_state_file(tmp_path, monkeypatch):
    # Never touch the real $DATA_DIR state file from a test run.
    state_file = str(tmp_path / f"rokkaai_resume_state_{uuid.uuid4().hex}.json")
    monkeypatch.setattr(rrp, "STATE_FILE", state_file)
    return state_file


def _provider():
    cfg = ProviderConfig(
        provider="rokkaai", model="qwen3:8b", api_key=SecretStr("test-key"),
        base_url="https://rokka.example.test",
    )
    return ResumableRokkaAIProvider(cfg)


def test_a_fresh_prompt_submits_and_persists_the_job_id_before_polling(monkeypatch):
    submit_calls = []
    poll_calls = []

    async def fake_submit(self, client, query, system_prompt):
        submit_calls.append((query, system_prompt))
        return "job_abc123"

    async def fake_poll(self, client, job_id):
        poll_calls.append(job_id)
        # At the moment polling starts, the job_id must already be durably
        # persisted — the ordering this whole mechanism depends on.
        state = rrp._load_state()
        assert state[_prompt_hash("sys", "usr")]["job_id"] == "job_abc123"
        return "the answer"

    monkeypatch.setattr(ResumableRokkaAIProvider, "_submit", fake_submit)
    monkeypatch.setattr(ResumableRokkaAIProvider, "_poll", fake_poll)

    provider = _provider()
    import asyncio
    result = asyncio.run(provider._complete_once("sys", "usr"))

    assert submit_calls == [("usr", "sys")]
    assert poll_calls == ["job_abc123"]
    assert result.text == "the answer"
    assert result.provider_job_id == "job_abc123"
    # Resolved on success — no longer pending.
    assert _prompt_hash("sys", "usr") not in rrp._load_state()


def test_a_pending_prompt_skips_submit_and_polls_the_existing_job_id(monkeypatch):
    rrp._save_state({_prompt_hash("sys", "usr"): {"job_id": "job_already_submitted", "submitted_at": 0}})

    submit_calls = []
    poll_calls = []

    async def fake_submit(self, client, query, system_prompt):
        submit_calls.append((query, system_prompt))
        return "job_should_not_be_called"

    async def fake_poll(self, client, job_id):
        poll_calls.append(job_id)
        return "resumed answer"

    monkeypatch.setattr(ResumableRokkaAIProvider, "_submit", fake_submit)
    monkeypatch.setattr(ResumableRokkaAIProvider, "_poll", fake_poll)

    provider = _provider()
    import asyncio
    result = asyncio.run(provider._complete_once("sys", "usr"))

    assert submit_calls == []  # never resubmitted
    assert poll_calls == ["job_already_submitted"]
    assert result.text == "resumed answer"


def test_a_terminal_failure_clears_the_pending_entry_so_a_real_retry_can_submit_fresh(monkeypatch):
    async def fake_submit(self, client, query, system_prompt):
        return "job_will_fail"

    async def fake_poll(self, client, job_id):
        raise RuntimeError("RokkaAI job failed")

    monkeypatch.setattr(ResumableRokkaAIProvider, "_submit", fake_submit)
    monkeypatch.setattr(ResumableRokkaAIProvider, "_poll", fake_poll)

    provider = _provider()
    import asyncio
    with pytest.raises(RuntimeError):
        asyncio.run(provider._complete_once("sys", "usr"))

    assert _prompt_hash("sys", "usr") not in rrp._load_state()


def test_missing_state_file_is_treated_as_empty_not_a_crash():
    assert rrp._load_state() == {}


def test_prompt_hash_is_sensitive_to_both_system_and_user_prompt():
    assert _prompt_hash("a", "b") != _prompt_hash("b", "a")
    assert _prompt_hash("a", "b") == _prompt_hash("a", "b")
