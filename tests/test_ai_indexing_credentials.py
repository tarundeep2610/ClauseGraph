"""Regression tests for ai_indexing.credentials — the env-only design
(no DB storage, no encryption at rest: the API key lives only in
config.AI_INDEX_API_KEY, read live on every call, same as every other
secret in this project)."""

import pytest

import ai_indexing
import config
from ai_indexing.errors import AIConfigError
from ai_indexing.redact import SecretStr


@pytest.fixture(autouse=True)
def _clean_ai_env(monkeypatch):
    monkeypatch.setattr(config, "AI_INDEX_API_KEY", "")
    monkeypatch.setattr(config, "AI_INDEX_PROVIDER", "gemini")
    monkeypatch.setattr(config, "AI_INDEX_MODEL", "gemini-2.5-flash")
    monkeypatch.setattr(config, "AI_INDEX_BASE_URL", "")
    yield


def test_resolve_raises_config_error_when_no_key_configured():
    with pytest.raises(AIConfigError):
        ai_indexing.resolve_active_config()


def test_describe_active_config_is_none_when_no_key_configured():
    assert ai_indexing.describe_active_config() is None


def test_resolve_reads_provider_config_straight_from_env(monkeypatch):
    monkeypatch.setattr(config, "AI_INDEX_API_KEY", "AIzaTestKey1234")
    monkeypatch.setattr(config, "AI_INDEX_PROVIDER", "gemini")
    monkeypatch.setattr(config, "AI_INDEX_MODEL", "gemini-2.5-flash")

    resolved = ai_indexing.resolve_active_config()
    assert resolved.provider_config.provider == "gemini"
    assert resolved.provider_config.model == "gemini-2.5-flash"
    assert resolved.provider_config.api_key.get_secret_value() == "AIzaTestKey1234"


def test_describe_active_config_never_returns_the_plaintext_key(monkeypatch):
    monkeypatch.setattr(config, "AI_INDEX_API_KEY", "AIzaTestKey1234")
    summary = ai_indexing.describe_active_config()
    assert summary["configured"] is True
    assert summary["key_hint"] == "1234"
    assert "AIzaTestKey1234" not in str(summary.values())


def test_no_cred_id_concept_left_anywhere_in_the_resolved_config(monkeypatch):
    """The whole point of this design: there is nothing to store, activate,
    or delete — resolve_active_config() takes no id at all."""
    monkeypatch.setattr(config, "AI_INDEX_API_KEY", "AIzaTestKey1234")
    resolved = ai_indexing.resolve_active_config()
    assert not hasattr(resolved, "cred_id")


def test_secret_never_leaks_via_repr_or_str():
    secret = SecretStr("AIzaVerySecretValue123456")
    assert "AIzaVerySecretValue123456" not in repr(secret)
    assert "AIzaVerySecretValue123456" not in str(secret)


def test_provider_config_repr_never_leaks_the_key(monkeypatch):
    monkeypatch.setattr(config, "AI_INDEX_API_KEY", "AIzaTopSecret999")
    resolved_cfg = ai_indexing.ProviderConfig(
        provider="gemini", model="gemini-2.5-flash", api_key=SecretStr("AIzaTopSecret999"),
    )
    assert "AIzaTopSecret999" not in repr(resolved_cfg)
    assert "AIzaTopSecret999" not in str(resolved_cfg)


def test_ai_credentials_db_table_is_gone_from_database_module():
    """Confirms the encrypted-credential-store code was actually removed,
    not just bypassed — this project deliberately never stores the key."""
    import database
    for name in (
        "create_ai_credential", "list_ai_credentials", "get_ai_credential",
        "get_active_ai_credential", "activate_ai_credential", "update_ai_credential",
        "delete_ai_credential", "touch_ai_credential_used",
    ):
        assert not hasattr(database, name), f"database.{name} should not exist in the env-only design"
