import pytest

ADMIN_ONLY_ROUTES = [
    ("GET", "/api/settings", None),
    ("POST", "/api/settings", {}),
    ("POST", "/api/prompts/generic", {"text": "test prompt override"}),
    ("DELETE", "/api/prompts/generic", None),
    ("POST", "/api/jobs/stop-all", None),
    ("POST", "/api/shutdown", None),
]

USER_OR_ADMIN_ROUTES = [
    ("GET", "/api/prompts", None),
    ("GET", "/api/jobs", None),
    ("GET", "/api/jobs/nonexistent-job-id", None),
    ("GET", "/api/jobs/nonexistent-job-id/file", None),
    ("GET", "/api/jobs/nonexistent-job-id/download-result", None),
    ("POST", "/api/jobs/nonexistent-job-id/pause", None),
    ("POST", "/api/jobs/nonexistent-job-id/retry", None),
    ("DELETE", "/api/jobs/nonexistent-job-id", None),
]

PUBLIC_ROUTES = [
    ("GET", "/health", None),
    ("GET", "/api/idle-status", None),
    ("GET", "/", None),
]


@pytest.mark.parametrize("method,path,body", ADMIN_ONLY_ROUTES)
def test_admin_only_route_authorization(
    client, method, path, body, admin_token, user_token, admin_api_key, user_api_key
):
    resp = client.request(method, path, json=body)
    assert resp.status_code == 401, f"{method} {path} with no creds should 401"

    resp = client.request(method, path, json=body, headers={"Authorization": f"Bearer {user_token}"})
    assert resp.status_code == 403, f"{method} {path} with user JWT should 403"

    resp = client.request(method, path, json=body, headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code not in (401, 403), f"{method} {path} with admin JWT should be authorized"

    user_key_raw, _ = user_api_key
    resp = client.request(method, path, json=body, headers={"X-API-Key": user_key_raw})
    assert resp.status_code == 403, f"{method} {path} with user-role API key should 403"

    admin_key_raw, _ = admin_api_key
    resp = client.request(method, path, json=body, headers={"X-API-Key": admin_key_raw})
    assert resp.status_code not in (401, 403), f"{method} {path} with admin-role API key should be authorized"


@pytest.mark.parametrize("method,path,body", USER_OR_ADMIN_ROUTES)
def test_user_or_admin_route_authorization(
    client, method, path, body, admin_token, user_token, admin_api_key, user_api_key
):
    resp = client.request(method, path, json=body)
    assert resp.status_code == 401, f"{method} {path} with no creds should 401"

    resp = client.request(method, path, json=body, headers={"Authorization": f"Bearer {user_token}"})
    assert resp.status_code not in (401, 403), f"{method} {path} with user JWT should be authorized"

    resp = client.request(method, path, json=body, headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code not in (401, 403), f"{method} {path} with admin JWT should be authorized"

    user_key_raw, _ = user_api_key
    resp = client.request(method, path, json=body, headers={"X-API-Key": user_key_raw})
    assert resp.status_code not in (401, 403), f"{method} {path} with user-role API key should be authorized"

    admin_key_raw, _ = admin_api_key
    resp = client.request(method, path, json=body, headers={"X-API-Key": admin_key_raw})
    assert resp.status_code not in (401, 403), f"{method} {path} with admin-role API key should be authorized"


@pytest.mark.parametrize("method,path,body", PUBLIC_ROUTES)
def test_public_routes_require_no_credentials(client, method, path, body):
    resp = client.request(method, path, json=body)
    assert resp.status_code not in (401, 403), f"{method} {path} should be public"


def test_revoked_api_key_is_rejected(client, admin_api_key):
    import database

    raw_key, key_id = admin_api_key
    database.revoke_api_key(key_id)
    resp = client.get("/api/jobs", headers={"X-API-Key": raw_key})
    assert resp.status_code == 401
