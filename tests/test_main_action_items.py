"""Tests for POST /api/jobs/{job_id}/reindex-action-items and
GET /api/jobs/{job_id}/action-items — mirroring test_main_scope_ranges.py's
shape. Deliberately never exercises the real AI extraction pass (no live
model calls in tests) — these test the endpoint's guards and the page-delta
contract against data inserted directly via database helpers, the same way
a completed extraction would have written it."""

import uuid

import database
import ai_action_items
import reference_index


def _job(total_pages=1, status="completed"):
    job_id = str(uuid.uuid4())
    database.create_job(
        job_id=job_id, filename="t.pdf", file_path="/tmp/x.pdf", file_type="pdf",
        engine="unlimited", total_pages=total_pages,
    )
    for i in range(1, total_pages + 1):
        database.upsert_page(job_id, i, status="success", text=f"page {i} text", confidence=0.9)
    database.set_job_status(job_id, status)
    return job_id


def test_reindex_action_items_404s_for_missing_job(client, auth_headers):
    resp = client.post("/api/jobs/does-not-exist/reindex-action-items", headers=auth_headers)
    assert resp.status_code == 404


def test_reindex_action_items_409s_while_job_still_processing(client, auth_headers):
    job_id = _job(status="processing")
    resp = client.post(f"/api/jobs/{job_id}/reindex-action-items", headers=auth_headers)
    assert resp.status_code == 409


def test_reindex_action_items_409s_when_already_active(client, auth_headers, monkeypatch):
    job_id = _job()
    ai_action_items._active_action_item_jobs.add(job_id)
    try:
        resp = client.post(f"/api/jobs/{job_id}/reindex-action-items", headers=auth_headers)
        assert resp.status_code == 409
    finally:
        ai_action_items._active_action_item_jobs.discard(job_id)


def test_reindex_action_items_409s_when_reference_index_rebuild_is_running(client, auth_headers):
    # The two passes share the same LLM provider — serialize them rather
    # than doubling concurrent load for no benefit.
    job_id = _job()
    reference_index._active_reindex_jobs.add(job_id)
    try:
        resp = client.post(f"/api/jobs/{job_id}/reindex-action-items", headers=auth_headers)
        assert resp.status_code == 409
    finally:
        reference_index._active_reindex_jobs.discard(job_id)


def test_get_job_reports_action_item_reindexing_flag(client, auth_headers):
    job_id = _job()
    resp = client.get(f"/api/jobs/{job_id}", headers=auth_headers)
    assert resp.json()["action_item_reindexing"] is False

    ai_action_items._active_action_item_jobs.add(job_id)
    try:
        resp = client.get(f"/api/jobs/{job_id}", headers=auth_headers)
        assert resp.json()["action_item_reindexing"] is True
    finally:
        ai_action_items._active_action_item_jobs.discard(job_id)


def test_action_items_endpoint_404s_for_missing_job(client, auth_headers):
    resp = client.get("/api/jobs/does-not-exist/action-items", headers=auth_headers)
    assert resp.status_code == 404


def test_action_items_endpoint_returns_version_and_changed_pages(client, auth_headers):
    job_id = _job(total_pages=2)
    version = database.bump_action_item_index_version(job_id)
    database.upsert_action_items_for_page(job_id, 1, [{
        "occurrence_index": 0, "item_type": "deadline", "item_text": "EMD deadline",
        "date_text": "15.03.2025", "date_iso": "2025-03-15", "anchor_id": "page-block-1",
        "evidence_quote": "EMD shall be submitted", "responsible_party": "Bidder",
        "confidence": "high", "detection_model": "test-model",
    }], version)

    resp = client.get(f"/api/jobs/{job_id}/action-items?since_version=0", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["action_item_index_version"] == version
    assert "1" in body["changed_pages"]
    assert body["changed_pages"]["1"][0]["item_text"] == "EMD deadline"
    assert "2" not in body["changed_pages"]


def test_action_items_endpoint_delta_respects_since_version(client, auth_headers):
    job_id = _job()
    v1 = database.bump_action_item_index_version(job_id)
    database.upsert_action_items_for_page(job_id, 1, [{
        "occurrence_index": 0, "item_type": "obligation", "item_text": "x",
        "evidence_quote": "x", "confidence": "medium",
    }], v1)

    resp = client.get(f"/api/jobs/{job_id}/action-items?since_version={v1}", headers=auth_headers)
    body = resp.json()
    assert body["changed_pages"] == {}
