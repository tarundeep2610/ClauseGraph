"""Record and replay harness for AI indexing passes.

Allows capturing raw model completion text per chunk during a live run and
replaying it deterministically offline to evaluate downstream structural
transformations (grounding, stack machine, key minting, overrides) with zero
model variance and zero API cost.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple


class ReplayFidelityError(Exception):
    """Raised when replaying against mismatched run parameters."""


class RecordSession:
    """Captures successful chunk completions and run metadata for offline replay."""

    def __init__(
        self,
        output_path: str,
        fingerprint: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.output_path = output_path
        self.fingerprint = dict(fingerprint or {})
        self.chunks: Dict[str, Dict[str, Any]] = {}
        self.failures: List[Dict[str, Any]] = []
        self.has_rule_fallback: bool = False
        self.created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    @staticmethod
    def _chunk_key(page_nums: List[int] | Tuple[int, ...]) -> str:
        return "_".join(str(p) for p in sorted(page_nums))

    def record_chunk(
        self,
        page_nums: List[int] | Tuple[int, ...],
        raw_text: str,
        duration_ms: int = 0,
        attempt: int = 1,
        system_prompt: Optional[str] = None,
    ) -> None:
        key = self._chunk_key(page_nums)
        if system_prompt and "prompt_hash" not in self.fingerprint:
            self.fingerprint["prompt_hash"] = hashlib.sha256(
                system_prompt.encode("utf-8")
            ).hexdigest()

        self.chunks[key] = {
            "page_nums": list(page_nums),
            "raw_text": raw_text,
            "duration_ms": duration_ms,
            "attempt": attempt,
        }

    def record_failure(
        self,
        page_nums: List[int] | Tuple[int, ...],
        error: str,
    ) -> None:
        self.has_rule_fallback = True
        self.failures.append({
            "page_nums": list(page_nums),
            "error": error,
        })

    def save(self) -> str:
        data = {
            "fingerprint": self.fingerprint,
            "created_at": self.created_at,
            "total_chunks": len(self.chunks),
            "has_rule_fallback": self.has_rule_fallback,
            "failures": self.failures,
            "chunks": self.chunks,
        }
        os.makedirs(os.path.dirname(os.path.abspath(self.output_path)), exist_ok=True)
        with open(self.output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return self.output_path


class ReplaySession:
    """Replays recorded AI completions keyed by chunk page numbers."""

    def __init__(self, source: str | Dict[str, Any]) -> None:
        if isinstance(source, str):
            self.file_path = source
            with open(source, "r", encoding="utf-8") as f:
                self.data = json.load(f)
        else:
            self.file_path = "<dict>"
            self.data = source

        self.fingerprint: Dict[str, Any] = self.data.get("fingerprint", {})
        self.chunks: Dict[str, Dict[str, Any]] = self.data.get("chunks", {})
        self.has_rule_fallback: bool = bool(self.data.get("has_rule_fallback", False))

    @staticmethod
    def _chunk_key(page_nums: List[int] | Tuple[int, ...]) -> str:
        return "_".join(str(p) for p in sorted(page_nums))

    def validate_fingerprint(
        self,
        expected: Dict[str, Any],
        strict: bool = True,
    ) -> None:
        """Validates static input parameters against the stored fingerprint.
        Dynamic prompt context (carry-in) is deliberately NOT part of this check."""
        if self.has_rule_fallback:
            raise ReplayFidelityError(
                f"Recording {self.file_path!r} contains rule-fallback chunks and cannot be replayed as pure AI output."
            )

        mismatches = []
        for k, exp_val in expected.items():
            if exp_val is None:
                continue
            rec_val = self.fingerprint.get(k)
            if rec_val != exp_val:
                mismatches.append(f"{k}: recorded={rec_val!r} != expected={exp_val!r}")

        if mismatches:
            msg = f"Replay fidelity guard failed for {self.file_path!r}:\n  " + "\n  ".join(mismatches)
            if strict:
                raise ReplayFidelityError(msg)
            else:
                print(f"[WARNING] {msg}")

    def get_chunk(self, page_nums: List[int] | Tuple[int, ...]) -> Dict[str, Any]:
        key = self._chunk_key(page_nums)
        if key not in self.chunks:
            available = list(self.chunks.keys())[:5]
            raise KeyError(
                f"No recorded AI completion found for pages {list(page_nums)} (key: {key!r}) in {self.file_path!r}. Available sample keys: {available}"
            )
        return self.chunks[key]


# Async-safe context variables
_active_record_session: contextvars.ContextVar[Optional[RecordSession]] = contextvars.ContextVar(
    "active_record_session", default=None
)
_active_replay_session: contextvars.ContextVar[Optional[ReplaySession]] = contextvars.ContextVar(
    "active_replay_session", default=None
)


def get_active_record_session() -> Optional[RecordSession]:
    return _active_record_session.get()


def set_active_record_session(session: Optional[RecordSession]) -> contextvars.Token:
    return _active_record_session.set(session)


def get_active_replay_session() -> Optional[ReplaySession]:
    return _active_replay_session.get()


def set_active_replay_session(session: Optional[ReplaySession]) -> contextvars.Token:
    return _active_replay_session.set(session)


class record_scope:
    """Context manager for recording chunk completions."""

    def __init__(
        self,
        output_path: str,
        fingerprint: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.session = RecordSession(output_path=output_path, fingerprint=fingerprint)
        self._token: Optional[contextvars.Token] = None

    def __enter__(self) -> RecordSession:
        self._token = set_active_record_session(self.session)
        return self.session

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._token:
            _active_record_session.reset(self._token)
        if exc_type is None and not self.session.has_rule_fallback:
            self.session.save()


class replay_scope:
    """Context manager for replaying recorded chunk completions."""

    def __init__(
        self,
        source: str | Dict[str, Any],
        expected_fingerprint: Optional[Dict[str, Any]] = None,
        strict_fidelity: bool = True,
    ) -> None:
        self.session = ReplaySession(source)
        if expected_fingerprint:
            self.session.validate_fingerprint(expected_fingerprint, strict=strict_fidelity)
        self._token: Optional[contextvars.Token] = None

    def __enter__(self) -> ReplaySession:
        self._token = set_active_replay_session(self.session)
        return self.session

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._token:
            _active_replay_session.reset(self._token)
