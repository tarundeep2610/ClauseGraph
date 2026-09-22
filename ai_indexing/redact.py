"""Scrubs secrets out of every log line / exception message / stored
last_error this package produces. Two mechanisms: a live registry of secrets
actually issued this process (register_secret), and a set of known API-key
SHAPE patterns (works even for a secret this process never saw, e.g. one
pasted into an error by a provider's own SDK).
"""

import re
import threading
from typing import Set

_lock = threading.Lock()
_live_secrets: Set[str] = set()

_SHAPE_PATTERNS = [
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),      # Google/Gemini API keys
    re.compile(r"sk-[A-Za-z0-9]{20,}"),           # OpenAI-style keys
    re.compile(r"rokkaai_\S+"),                   # RokkaAI keys
]

_MASK = "***REDACTED***"


def register_secret(value: str) -> None:
    """Interns a live secret so redact_secrets() can scrub it even from
    exception text produced deep inside a third-party SDK."""
    if not value:
        return
    with _lock:
        _live_secrets.add(value)


def redact_secrets(text: str) -> str:
    if not text:
        return text
    with _lock:
        secrets_snapshot = list(_live_secrets)
    for secret in secrets_snapshot:
        if secret and secret in text:
            text = text.replace(secret, _MASK)
    for pattern in _SHAPE_PATTERNS:
        text = pattern.sub(_MASK, text)
    return text


class SecretStr:
    """Wraps a live API key so an accidental print()/repr()/f-string, or a
    third-party SDK's own exception formatting, can't leak it verbatim.
    Equality/hash compare on the real value (so it can still be used as a
    dict/cache key); str()/repr() are always masked."""

    __slots__ = ("_value",)

    def __init__(self, value: str):
        self._value = value or ""
        register_secret(self._value)

    def get_secret_value(self) -> str:
        return self._value

    def __bool__(self) -> bool:
        return bool(self._value)

    def __len__(self) -> int:
        return len(self._value)

    def __eq__(self, other) -> bool:
        if isinstance(other, SecretStr):
            return self._value == other._value
        return self._value == other

    def __hash__(self) -> int:
        return hash(self._value)

    def __repr__(self) -> str:
        if not self._value:
            return "SecretStr('')"
        return f"SecretStr('***{self._value[-4:]}')"

    __str__ = __repr__
