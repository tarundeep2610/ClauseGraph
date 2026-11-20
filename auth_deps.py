import enum
from typing import Callable

from fastapi import Depends, HTTPException, Header, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

import database
from auth_core import Principal, verify_token, verify_api_key

_bearer_scheme = HTTPBearer(auto_error=False)


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    USER = "user"


def _primary_role(roles: list[str]) -> str:
    roles_lower = {r.lower() for r in roles}
    if UserRole.ADMIN.value in roles_lower:
        return UserRole.ADMIN.value
    return UserRole.USER.value


async def get_current_principal(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    x_api_key: str | None = Header(None, alias="X-API-Key"),
) -> Principal:
    """
    Combined dependency: accepts EITHER a valid AuthNexus JWT (Authorization: Bearer)
    OR a valid API key (X-API-Key). If X-API-Key is present it takes precedence and
    is validated on its own — an invalid API key is a 401 even if a valid bearer
    token is also present, so the two paths never silently fall through into each other.
    """
    if x_api_key is not None:
        principal = await verify_api_key(x_api_key)
        if principal is None:
            raise HTTPException(status_code=401, detail="Invalid API key")
        return principal

    if creds is not None:
        claims = await verify_token(creds.credentials)
        return Principal(
            kind="jwt",
            subject=claims["user_id"],
            role=_primary_role(claims.get("roles", [])),
            rate_limit_key=f"jwt:{claims['user_id']}",
            claims=claims,
        )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing credentials: supply 'Authorization: Bearer <token>' or 'X-API-Key: <key>'",
    )


async def require_admin_jwt_only(principal: Principal = Depends(get_current_principal)) -> Principal:
    """
    Admin-only AND JWT-only: used for API-key management endpoints so a
    compromised API key (even an admin-role one) can never mint or revoke keys.
    """
    if principal.kind != "jwt" or principal.role != UserRole.ADMIN.value:
        raise HTTPException(status_code=403, detail="Admin login required for this action.")
    return principal


async def require_job_access(job_id: str, principal: Principal = Depends(get_current_principal)) -> Principal:
    """Per-job route guard: `admin` may access any job; `user` may access
    only a job they own (`owner_id == principal.subject`). A job with no
    owner_id (uploaded before this column existed) is treated as admin-only —
    never exposed to a `user` by accident. Returns 404 rather than 403 for an
    owned-by-someone-else job, so a `user` can't distinguish "not yours" from
    "doesn't exist" by probing job ids."""
    if principal.role.lower() not in (UserRole.ADMIN.value, UserRole.USER.value):
        raise HTTPException(
            status_code=403,
            detail=f"Access restricted. Required roles: {sorted({UserRole.ADMIN.value, UserRole.USER.value})}",
        )
    if principal.role.lower() == UserRole.ADMIN.value:
        return principal
    job = database.get_job(job_id, include_pages=False)
    if job is None or job.get("owner_id") != principal.subject:
        raise HTTPException(status_code=404, detail="Job not found")
    return principal


def require_role(allowed_roles: list[UserRole]) -> Callable:
    allowed_values = {(r.value if hasattr(r, "value") else r).lower() for r in allowed_roles}

    async def role_checker(principal: Principal = Depends(get_current_principal)) -> Principal:
        if principal.role.lower() not in allowed_values:
            raise HTTPException(
                status_code=403,
                detail=f"Access restricted. Required roles: {sorted(allowed_values)}",
            )
        return principal

    return role_checker
