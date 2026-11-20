import pytest
from fastapi import HTTPException

import config
import database
import main as main_module


def test_create_key_returns_raw_key_once_and_list_never_exposes_it(client, auth_headers):
    resp = client.post("/api/auth/api-keys", json={"name": "integration-a", "role": "user"}, headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert "raw_key" in body and body["raw_key"]

    resp2 = client.get("/api/auth/api-keys", headers=auth_headers)
    assert resp2.status_code == 200
    for row in resp2.json():
        assert "raw_key" not in row
        assert "key_hash" not in row


def test_non_admin_cannot_manage_keys(client, user_token, admin_api_key):
    resp = client.post(
        "/api/auth/api-keys",
        json={"name": "x", "role": "user"},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert resp.status_code == 403

    admin_key_raw, _ = admin_api_key
    resp2 = client.post(
        "/api/auth/api-keys",
        json={"name": "y", "role": "user"},
        headers={"X-API-Key": admin_key_raw},
    )
    assert resp2.status_code == 403, "admin-role API key must not be able to mint new keys"


def test_revoke_key_blocks_future_use(client, auth_headers):
    created = database.create_api_key(name="to-revoke", role="user")
    resp = client.delete(f"/api/auth/api-keys/{created['key_id']}", headers=auth_headers)
    assert resp.status_code == 200

    resp2 = client.get("/api/jobs", headers={"X-API-Key": created["raw_key"]})
    assert resp2.status_code == 401


def test_revoke_unknown_key_404(client, auth_headers):
    resp = client.delete("/api/auth/api-keys/does-not-exist", headers=auth_headers)
    assert resp.status_code == 404


def test_rate_limit_buckets_are_independent_per_caller():
    main_module._rate_buckets.clear()
    for _ in range(config.RATE_LIMIT_MAX):
        main_module._check_rate_limit("apikey:key-A")

    with pytest.raises(HTTPException) as exc_info:
        main_module._check_rate_limit("apikey:key-A")
    assert exc_info.value.status_code == 429

    # A different caller's bucket must be unaffected by key-A's limit.
    main_module._check_rate_limit("apikey:key-B")
