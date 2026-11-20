import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

import database
import auth_deps


def _creds(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


class TestGetCurrentPrincipal:
    @pytest.mark.asyncio
    async def test_bearer_only_resolves_jwt_principal(self, admin_token):
        principal = await auth_deps.get_current_principal(creds=_creds(admin_token), x_api_key=None)
        assert principal.kind == "jwt"
        assert principal.role == "admin"

    @pytest.mark.asyncio
    async def test_api_key_only_resolves_api_key_principal(self):
        created = database.create_api_key(name="dep-test-key", role="user")
        principal = await auth_deps.get_current_principal(creds=None, x_api_key=created["raw_key"])
        assert principal.kind == "api_key"
        assert principal.role == "user"

    @pytest.mark.asyncio
    async def test_no_credentials_raises_401(self):
        with pytest.raises(HTTPException) as exc_info:
            await auth_deps.get_current_principal(creds=None, x_api_key=None)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_invalid_jwt_and_no_api_key_raises_401(self):
        with pytest.raises(HTTPException) as exc_info:
            await auth_deps.get_current_principal(creds=_creds("not-a-real-jwt"), x_api_key=None)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_api_key_takes_precedence_and_invalid_one_still_401s(self, admin_token):
        """A wrong API key must 401 even when a valid bearer token is also present."""
        with pytest.raises(HTTPException) as exc_info:
            await auth_deps.get_current_principal(creds=_creds(admin_token), x_api_key="wrong-key")
        assert exc_info.value.status_code == 401


class TestRequireRole:
    @pytest.mark.asyncio
    async def test_admin_only_passes_for_admin_jwt(self, admin_token):
        checker = auth_deps.require_role([auth_deps.UserRole.ADMIN])
        principal = await auth_deps.get_current_principal(creds=_creds(admin_token), x_api_key=None)
        result = await checker(principal)
        assert result.role == "admin"

    @pytest.mark.asyncio
    async def test_admin_only_rejects_user_jwt(self, user_token):
        checker = auth_deps.require_role([auth_deps.UserRole.ADMIN])
        principal = await auth_deps.get_current_principal(creds=_creds(user_token), x_api_key=None)
        with pytest.raises(HTTPException) as exc_info:
            await checker(principal)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_admin_only_passes_for_admin_role_api_key(self):
        created = database.create_api_key(name="dep-admin-key", role="admin")
        checker = auth_deps.require_role([auth_deps.UserRole.ADMIN])
        principal = await auth_deps.get_current_principal(creds=None, x_api_key=created["raw_key"])
        result = await checker(principal)
        assert result.role == "admin"

    @pytest.mark.asyncio
    async def test_admin_only_rejects_user_role_api_key(self):
        created = database.create_api_key(name="dep-user-key", role="user")
        checker = auth_deps.require_role([auth_deps.UserRole.ADMIN])
        principal = await auth_deps.get_current_principal(creds=None, x_api_key=created["raw_key"])
        with pytest.raises(HTTPException) as exc_info:
            await checker(principal)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_admin_or_user_passes_for_either_role(self, admin_token, user_token):
        checker = auth_deps.require_role([auth_deps.UserRole.ADMIN, auth_deps.UserRole.USER])
        admin_principal = await auth_deps.get_current_principal(creds=_creds(admin_token), x_api_key=None)
        user_principal = await auth_deps.get_current_principal(creds=_creds(user_token), x_api_key=None)
        assert (await checker(admin_principal)).role == "admin"
        assert (await checker(user_principal)).role == "user"


class TestRequireAdminJwtOnly:
    @pytest.mark.asyncio
    async def test_admin_jwt_passes(self, admin_token):
        principal = await auth_deps.get_current_principal(creds=_creds(admin_token), x_api_key=None)
        result = await auth_deps.require_admin_jwt_only(principal)
        assert result.kind == "jwt"

    @pytest.mark.asyncio
    async def test_admin_role_api_key_is_rejected(self):
        """Anti-self-escalation: an admin-role API key must NOT pass this check."""
        created = database.create_api_key(name="dep-admin-key-2", role="admin")
        principal = await auth_deps.get_current_principal(creds=None, x_api_key=created["raw_key"])
        with pytest.raises(HTTPException) as exc_info:
            await auth_deps.require_admin_jwt_only(principal)
        assert exc_info.value.status_code == 403
