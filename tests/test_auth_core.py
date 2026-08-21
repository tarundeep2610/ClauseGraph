import time

import pytest
import respx
import httpx
from jose import jwt as jose_jwt
from fastapi import HTTPException

import config
import auth_core
from role_utils import extract_roles_from_payload


def _dev_token(sub="dev-user-001", roles=None, project_id=None, exp_delta=3600):
    now = int(time.time())
    payload = {
        "sub": sub,
        "roles": roles if roles is not None else ["user"],
        "project_id": project_id or config.PROJECT_ID,
        "iat": now,
        "exp": now + exp_delta,
    }
    return jose_jwt.encode(payload, config.DEV_SECRET, algorithm="HS256")


class TestDevTokenPath:
    @pytest.mark.asyncio
    async def test_valid_dev_token(self):
        token = _dev_token(sub="dev-admin-001", roles=["admin"])
        claims = await auth_core.verify_token(token)
        assert claims["user_id"] == "dev-admin-001"
        assert claims["roles"] == ["admin"]

    @pytest.mark.asyncio
    async def test_tampered_dev_token_rejected(self):
        token = _dev_token()
        tampered = token[:-2] + ("aa" if token[-2:] != "aa" else "bb")
        with pytest.raises(HTTPException) as exc_info:
            await auth_core.verify_token(tampered)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_expired_dev_token_rejected(self):
        token = _dev_token(exp_delta=-10)
        with pytest.raises(HTTPException) as exc_info:
            await auth_core.verify_token(token)
        assert exc_info.value.status_code == 401


class TestProductionJwtPath:
    @pytest.mark.asyncio
    async def test_dev_mode_disabled_falls_back_to_jwks_and_fails_without_mock(self, monkeypatch):
        monkeypatch.setattr(config, "DEV_MODE", False)
        auth_core._jwks_cache = None
        auth_core._jwks_fetched_at = None
        with respx.mock:
            respx.get(f"{config.AUTH_AUTHORITY}/api/v1/auth/jwks").mock(
                return_value=httpx.Response(500)
            )
            with pytest.raises(HTTPException) as exc_info:
                await auth_core.verify_token(_dev_token())
            assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_jwks_cache_is_reused_within_ttl(self, monkeypatch):
        monkeypatch.setattr(config, "DEV_MODE", False)
        auth_core._jwks_cache = None
        auth_core._jwks_fetched_at = None
        with respx.mock:
            route = respx.get(f"{config.AUTH_AUTHORITY}/api/v1/auth/jwks").mock(
                return_value=httpx.Response(200, json={"keys": []})
            )
            await auth_core.get_jwks()
            await auth_core.get_jwks()
            assert route.call_count == 1


class TestRoleExtraction:
    def test_flat_roles_claim(self):
        payload = {"roles": ["admin"]}
        assert extract_roles_from_payload(payload, "proj-1") == ["admin"]

    def test_nexus_projects_matching_project(self):
        payload = {"nexus_projects": [{"id": "proj-1", "roles": ["user"]}]}
        assert extract_roles_from_payload(payload, "proj-1") == ["user"]

    def test_nexus_projects_no_matching_project_returns_none(self):
        payload = {"nexus_projects": [{"id": "other-proj", "roles": ["admin"]}]}
        assert extract_roles_from_payload(payload, "proj-1") is None

    def test_nexus_projects_takes_priority_over_flat_roles(self):
        payload = {
            "nexus_projects": [{"id": "proj-1", "roles": ["admin"]}],
            "roles": ["user"],
        }
        assert extract_roles_from_payload(payload, "proj-1") == ["admin"]


class TestApiKeyVerification:
    @pytest.mark.asyncio
    async def test_correct_key_resolves_principal(self):
        import database

        created = database.create_api_key(name="k1", role="admin")
        principal = await auth_core.verify_api_key(created["raw_key"])
        assert principal is not None
        assert principal.role == "admin"
        assert principal.subject == created["key_id"]

    @pytest.mark.asyncio
    async def test_wrong_key_returns_none(self):
        import database

        database.create_api_key(name="k2", role="user")
        principal = await auth_core.verify_api_key("not-a-real-key")
        assert principal is None

    @pytest.mark.asyncio
    async def test_revoked_key_rejected(self):
        import database

        created = database.create_api_key(name="k3", role="user")
        database.revoke_api_key(created["key_id"])
        principal = await auth_core.verify_api_key(created["raw_key"])
        assert principal is None

    @pytest.mark.asyncio
    async def test_two_keys_resolve_to_distinct_ids(self):
        import database

        a = database.create_api_key(name="k4a", role="user")
        b = database.create_api_key(name="k4b", role="user")
        pa = await auth_core.verify_api_key(a["raw_key"])
        pb = await auth_core.verify_api_key(b["raw_key"])
        assert pa.subject != pb.subject

    @pytest.mark.asyncio
    async def test_last_used_at_updates_on_success(self):
        import database

        created = database.create_api_key(name="k5", role="user")
        before = database.find_active_api_key_by_hash(
            auth_core._hash_key(created["raw_key"])
        )
        await auth_core.verify_api_key(created["raw_key"])
        rows = database.list_api_keys()
        row = next(r for r in rows if r["key_id"] == created["key_id"])
        assert row["last_used_at"] is not None
