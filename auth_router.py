import time

import httpx
from fastapi import APIRouter, Depends, Request, Response, HTTPException
from jose import jwt as jose_jwt
from pydantic import BaseModel

import config
import database
from auth_deps import require_admin_jwt_only

router = APIRouter(prefix="/api/auth")

DEV_USERS = [
    {"user_id": "dev-admin-001", "name": "Dev Admin", "email": "admin@dev.ocr.local", "roles": ["admin"]},
    {"user_id": "dev-user-001", "name": "Dev User", "email": "user@dev.ocr.local", "roles": ["user"]},
]


class DevLoginPayload(BaseModel):
    user_id: str


class ApiKeyCreatePayload(BaseModel):
    name: str
    role: str = "user"


@router.get("/dev/users")
async def get_dev_user_list():
    if not config.DEV_MODE:
        raise HTTPException(status_code=404, detail="Not found")
    return DEV_USERS


@router.post("/dev/login")
async def dev_login(payload: DevLoginPayload):
    if not config.DEV_MODE:
        raise HTTPException(status_code=404, detail="Not found")

    user = next((u for u in DEV_USERS if u["user_id"] == payload.user_id), None)
    if not user:
        raise HTTPException(status_code=400, detail=f"Unknown dev user: {payload.user_id}")

    now = int(time.time())
    token_payload = {
        "sub": user["user_id"],
        "roles": user["roles"],
        "project_id": "dev-project",
        "email": user["email"],
        "name": user["name"],
        "iat": now,
        "exp": now + 86400,
    }
    token = jose_jwt.encode(token_payload, config.DEV_SECRET, algorithm="HS256")
    return {
        "access_token": token,
        "token_type": "Bearer",
        "expires_in": 86400,
        "role": user["roles"][0],
        "name": user["name"],
    }


@router.post("/proxy/token")
async def proxy_token(request: Request):
    """Proxy OIDC token requests to AuthNexus — avoids browser CORS on the token endpoint."""
    body = await request.body()
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                f"{config.AUTH_AUTHORITY}/api/v1/auth/token",
                content=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30.0,
            )
            return Response(
                content=resp.content,
                status_code=resp.status_code,
                media_type=resp.headers.get("content-type", "application/json"),
            )
        except httpx.TimeoutException:
            raise HTTPException(status_code=504, detail="AuthNexus token endpoint timed out.")
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"Connection to AuthNexus failed: {str(e)}")


@router.get("/proxy/userinfo")
async def proxy_userinfo(request: Request):
    """Proxy OIDC userinfo requests to AuthNexus — avoids browser CORS."""
    auth_header = request.headers.get("Authorization")
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(
                f"{config.AUTH_AUTHORITY}/oidc/v1/userinfo",
                headers={"Authorization": auth_header} if auth_header else {},
                timeout=30.0,
            )
            return Response(
                content=resp.content,
                status_code=resp.status_code,
                media_type=resp.headers.get("content-type", "application/json"),
            )
        except httpx.TimeoutException:
            raise HTTPException(status_code=504, detail="AuthNexus userinfo endpoint timed out.")
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"Connection to AuthNexus failed: {str(e)}")


# ─── API-key management — admin JWT sessions only, never callable via API key ──

@router.post("/api-keys")
async def create_api_key(payload: ApiKeyCreatePayload, _admin=Depends(require_admin_jwt_only)):
    if payload.role not in ("admin", "user"):
        raise HTTPException(status_code=400, detail="role must be 'admin' or 'user'")
    result = database.create_api_key(name=payload.name, role=payload.role)
    return result


@router.get("/api-keys")
async def list_api_keys(_admin=Depends(require_admin_jwt_only)):
    return database.list_api_keys()


@router.delete("/api-keys/{key_id}")
async def revoke_api_key(key_id: str, _admin=Depends(require_admin_jwt_only)):
    ok = database.revoke_api_key(key_id)
    if not ok:
        raise HTTPException(status_code=404, detail="API key not found or already revoked")
    return {"message": f"API key {key_id} revoked."}
