import time
import asyncio
import hashlib
import secrets
from dataclasses import dataclass, field

import httpx
from jose import jwt, JWTError
from fastapi import HTTPException, status

import config
import database
from role_utils import extract_roles_from_payload

ALGORITHM = "RS256"

_jwks_cache = None
_jwks_fetched_at = None
_jwks_lock = asyncio.Lock()


@dataclass
class Principal:
    kind: str  # "jwt" | "api_key"
    subject: str  # AuthNexus user_id, or the api key's key_id
    role: str  # "admin" | "user"
    rate_limit_key: str
    claims: dict = field(default_factory=dict)


async def get_jwks():
    """Fetch and cache AuthNexus's public keys, 1h TTL."""
    global _jwks_cache, _jwks_fetched_at

    async with _jwks_lock:
        now = time.time()
        if _jwks_fetched_at and (now - _jwks_fetched_at > 3600):
            _jwks_cache = None

        if _jwks_cache is None:
            if not config.AUTH_AUTHORITY:
                return None
            jwks_url = f"{config.AUTH_AUTHORITY}/api/v1/auth/jwks"
            try:
                async with httpx.AsyncClient(timeout=20.0) as client:
                    response = await client.get(jwks_url)
                    response.raise_for_status()
                    _jwks_cache = response.json()
                    _jwks_fetched_at = now
            except Exception:
                _jwks_cache = None
                _jwks_fetched_at = None
        return _jwks_cache


def _verify_dev_token(token: str) -> dict:
    """HS256 verification used only when DEV_MODE=True. Never reached in production."""
    try:
        payload = jwt.decode(
            token,
            config.DEV_SECRET,
            algorithms=["HS256"],
            options={"verify_aud": False, "verify_iss": False},
        )
        return {
            "user_id": payload.get("sub"),
            "roles": payload.get("roles", []),
            "project_id": payload.get("project_id"),
            "email": payload.get("email"),
            "name": payload.get("name"),
        }
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Dev token invalid: {str(e)}",
        )


async def verify_token(token: str) -> dict:
    """Validates the AuthNexus JWT and returns claims."""
    if config.DEV_MODE:
        return _verify_dev_token(token)

    jwks = await get_jwks()
    if not jwks:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="AuthNexus keys unavailable",
        )

    try:
        payload = jwt.decode(
            token,
            jwks,
            algorithms=[ALGORITHM],
            options={"verify_aud": False, "verify_iss": False, "leeway": 60},
        )

        token_iss = (payload.get("iss") or "").rstrip("/")
        if token_iss != config.AUTH_AUTHORITY:
            raise HTTPException(status_code=401, detail="Invalid issuer")

        project_id = payload.get("project_id")
        if config.PROJECT_ID and project_id != config.PROJECT_ID:
            raise HTTPException(status_code=403, detail="Not authorized for this project")

        roles = extract_roles_from_payload(payload, config.PROJECT_ID)
        if roles is None:
            raise HTTPException(status_code=403, detail="Not authorized for this project")

        return {
            "user_id": payload.get("sub"),
            "roles": roles,
            "project_id": project_id,
            "email": payload.get("email"),
            "name": payload.get("name"),
        }
    except JWTError as e:
        global _jwks_cache, _jwks_fetched_at
        _jwks_cache = None
        _jwks_fetched_at = None
        raise HTTPException(status_code=401, detail=f"Session invalid: {str(e)}")


def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


async def verify_api_key(raw_key: str) -> Principal | None:
    """Validates a static integration API key against database.api_keys."""
    candidate_hash = _hash_key(raw_key)
    row = database.find_active_api_key_by_hash(candidate_hash)
    if row is None:
        return None
    if not secrets.compare_digest(row["key_hash"], candidate_hash):
        return None
    database.touch_api_key_last_used(row["key_id"])
    return Principal(
        kind="api_key",
        subject=row["key_id"],
        role=row["role"],
        rate_limit_key=f"apikey:{row['key_id']}",
        claims={"name": row["name"]},
    )
