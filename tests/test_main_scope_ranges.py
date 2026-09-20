"""Tests for GET /api/jobs/{job_id}/scope-ranges (Part B)."""

import uuid

import pytest

import database
import reference_index


def _job_with_pages(pages_text):
    job_id = str(uuid.uuid4())
    database.create_job(
        job_id=job_id, filename="t.pdf", file_path="/tmp/x.pdf", file_type="pdf",
        engine="unlimited", total_pages=len(pages_text),
    )
    for i, text in enumerate(pages_text, start=1):
        database.upsert_page(job_id, i, status="success", text=text, confidence=0.95)
    database.set_job_status(job_id, "completed")
    return job_id


def test_scope_ranges_endpoint_404s_for_missing_job(client, auth_headers):
    resp = client.get("/api/jobs/does-not-exist/scope-ranges", headers=auth_headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_scope_ranges_endpoint_returns_version_and_ranges_after_reindex(client, auth_headers):
    job_id = _job_with_pages([
        "Annexure A-1\n\nCover.\n",
        "Clause 1 Under the annexure.\n",
    ])
    await reference_index.reindex_job(job_id)

    resp = client.get(f"/api/jobs/{job_id}/scope-ranges", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["reference_index_version"] == database.get_reference_index_version(job_id)
    ranges = body["ranges"]
    annexure = next(r for r in ranges if r["key"] == "annexure:a-1")
    assert annexure["start_page"] == 1
    assert annexure["end_page"] == 2
    assert annexure["label"] == "Annexure A-1"


@pytest.mark.asyncio
async def test_scope_ranges_endpoint_reflects_a_fresh_reindex_after_algorithm_relevant_changes(client, auth_headers):
    job_id = _job_with_pages(["Clause 1 first.\n"])
    await reference_index.reindex_job(job_id)
    first_resp = client.get(f"/api/jobs/{job_id}/scope-ranges", headers=auth_headers).json()
    # No real container at all — the whole page gap-fills as "unclassified",
    # not an empty list (gap-filling working as designed).
    assert [r["key"] for r in first_resp["ranges"]] == ["unclassified"]

    database.upsert_page(job_id, 1, status="success", text="Annexure A-9\n\nNow a real container.\n")
    await reference_index.reindex_job(job_id)
    second_resp = client.get(f"/api/jobs/{job_id}/scope-ranges", headers=auth_headers).json()
    assert second_resp["reference_index_version"] > first_resp["reference_index_version"]
    assert any(r["key"] == "annexure:a-9" for r in second_resp["ranges"])
