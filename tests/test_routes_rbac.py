import pytest

ADMIN_ONLY_ROUTES = [
    ("GET", "/api/settings", None),
    ("POST", "/api/settings", {}),
    ("POST", "/api/prompts/generic", {"text": "test prompt override"}),
    ("DELETE", "/api/prompts/generic", None),
    ("POST", "/api/jobs/stop-all", None),
    ("POST", "/api/shutdown", None),
    # Re-extraction is admin-only per the Studio redesign ("[⚡ Rerun [Admin]]") —
    # a `user` proofreads in place but never re-triggers GPU OCR, even on their own job.
    ("POST", "/api/jobs/nonexistent-job-id/pages/1/retry", None),
    ("POST", "/api/jobs/nonexistent-job-id/retry-pages?scope=failed", None),
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
    ("GET", "/api/jobs/nonexistent-job-id/review-pages", None),
    ("GET", "/api/review-queue", None),
    ("GET", "/api/jobs/nonexistent-job-id/pages/1/attempts", None),
    ("POST", "/api/jobs/nonexistent-job-id/pages/1/confirm-correct", None),
    ("PATCH", "/api/jobs/nonexistent-job-id/pages/1/text", {"text": "x"}),
    ("GET", "/api/jobs/nonexistent-job-id/export?format=md", None),
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


# ─── Document ownership RBAC: `admin` sees every job, `user` sees only their own ──

def _create_owned_job(tmp_path, owner_id):
    import uuid
    from PIL import Image
    import database

    job_id = str(uuid.uuid4())
    img_path = tmp_path / f"{job_id}.png"
    Image.new("RGB", (10, 10), color="white").save(img_path)
    database.create_job(
        job_id=job_id, filename="t.png", file_path=str(img_path), file_type="image",
        engine="unlimited", total_pages=1, owner_id=owner_id,
    )
    database.set_job_status(job_id, "completed")
    return job_id


NON_OWNER_DENIED_ROUTE_TEMPLATES = [
    ("GET", "/api/jobs/{job_id}", None),
    ("GET", "/api/jobs/{job_id}/file", None),
    ("GET", "/api/jobs/{job_id}/download-result", None),
    ("POST", "/api/jobs/{job_id}/pause", None),
    ("POST", "/api/jobs/{job_id}/retry", None),
    ("GET", "/api/jobs/{job_id}/review-pages", None),
    ("GET", "/api/jobs/{job_id}/scope-ranges", None),
    ("GET", "/api/jobs/{job_id}/references", None),
    ("GET", "/api/jobs/{job_id}/pages/1/attempts", None),
    ("POST", "/api/jobs/{job_id}/pages/1/confirm-correct", None),
    ("PATCH", "/api/jobs/{job_id}/pages/1/text", {"text": "x"}),
    ("GET", "/api/jobs/{job_id}/export?format=md", None),
    ("DELETE", "/api/jobs/{job_id}", None),
]


def test_owner_can_access_their_own_job(client, user_token, tmp_path):
    job_id = _create_owned_job(tmp_path, owner_id="dev-user-001")
    resp = client.get(f"/api/jobs/{job_id}", headers={"Authorization": f"Bearer {user_token}"})
    assert resp.status_code == 200


def test_admin_can_access_any_users_job(client, admin_token, tmp_path):
    job_id = _create_owned_job(tmp_path, owner_id="dev-user-001")
    resp = client.get(f"/api/jobs/{job_id}", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200


@pytest.mark.parametrize("method,path_template,body", NON_OWNER_DENIED_ROUTE_TEMPLATES)
def test_non_owner_user_cannot_access_someone_elses_job(
    client, other_user_token, method, path_template, body, tmp_path
):
    job_id = _create_owned_job(tmp_path, owner_id="dev-user-001")  # owned by dev-user-001, not dev-user-002
    path = path_template.format(job_id=job_id)
    resp = client.request(method, path, json=body, headers={"Authorization": f"Bearer {other_user_token}"})
    assert resp.status_code == 404, f"{method} {path} by non-owner should 404, got {resp.status_code}: {resp.text}"


def test_get_jobs_list_excludes_other_users_jobs_for_a_non_admin(client, user_token, tmp_path):
    owned_job = _create_owned_job(tmp_path, owner_id="dev-user-001")
    other_job = _create_owned_job(tmp_path, owner_id="dev-user-002")

    resp = client.get("/api/jobs", headers={"Authorization": f"Bearer {user_token}"})
    ids = {j["job_id"] for j in resp.json()}
    assert owned_job in ids
    assert other_job not in ids


def test_get_jobs_list_includes_every_users_jobs_for_admin(client, admin_token, tmp_path):
    job_a = _create_owned_job(tmp_path, owner_id="dev-user-001")
    job_b = _create_owned_job(tmp_path, owner_id="dev-user-002")

    resp = client.get("/api/jobs", headers={"Authorization": f"Bearer {admin_token}"})
    ids = {j["job_id"] for j in resp.json()}
    assert job_a in ids
    assert job_b in ids


def test_legacy_job_with_no_owner_is_admin_only(client, user_token, admin_token, tmp_path):
    job_id = _create_owned_job(tmp_path, owner_id=None)

    resp = client.get(f"/api/jobs/{job_id}", headers={"Authorization": f"Bearer {user_token}"})
    assert resp.status_code == 404

    resp = client.get(f"/api/jobs/{job_id}", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200


def test_new_job_created_via_api_is_stamped_with_uploaders_owner_id(client, user_token, tmp_path):
    import database
    from PIL import Image

    img_path = tmp_path / "upload.png"
    Image.new("RGB", (10, 10), color="white").save(img_path)
    with open(img_path, "rb") as f:
        resp = client.post(
            "/api/jobs",
            headers={"Authorization": f"Bearer {user_token}"},
            files={"file": ("upload.png", f, "image/png")},
        )
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]
    job = database.get_job(job_id, include_pages=False)
    assert job["owner_id"] == "dev-user-001"


def test_page_retry_endpoints_are_admin_only_even_for_the_jobs_owner(client, user_token, tmp_path):
    job_id = _create_owned_job(tmp_path, owner_id="dev-user-001")

    resp = client.post(f"/api/jobs/{job_id}/pages/1/retry", headers={"Authorization": f"Bearer {user_token}"})
    assert resp.status_code == 403

    resp = client.post(f"/api/jobs/{job_id}/retry-pages?scope=failed", headers={"Authorization": f"Bearer {user_token}"})
    assert resp.status_code == 403
