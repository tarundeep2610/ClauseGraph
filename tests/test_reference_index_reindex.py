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


@pytest.mark.asyncio
async def test_reindex_job_builds_index_for_a_job_that_predates_the_feature():
    # Simulates a job OCR'd before reference indexing existed: real page text,
    # but zero rows in ocr_reference_targets and no reference_index_frontier progress.
    job_id = _job_with_pages([
        "Clause 4.2 The contractor shall deliver the goods within 30 days.",
        "Clause 4.3 Penalty for delay is per Clause 4.2 above.",
    ])
    assert database.get_reference_targets_since(job_id, 0) == {}

    await reference_index.reindex_job(job_id)

    delta = database.get_reference_targets_since(job_id, 0)
    assert set(delta.keys()) == {1, 2}
    assert "clause:4.2" in delta[1]
    assert "clause:4.3" in delta[2]


@pytest.mark.asyncio
async def test_reindex_is_a_noop_while_already_active_for_the_same_job():
    job_id = _job_with_pages(["Clause 1 some text."])
    assert reference_index.is_reindex_active(job_id) is False

    reference_index._active_reindex_jobs.add(job_id)
    try:
        await reference_index.reindex_job(job_id)  # should return immediately, not touch anything
        assert database.get_reference_targets_since(job_id, 0) == {}
    finally:
        reference_index._active_reindex_jobs.discard(job_id)

    assert reference_index.is_reindex_active(job_id) is False


@pytest.mark.asyncio
async def test_reindex_resets_the_frontier_so_it_always_takes_the_in_order_path():
    job_id = _job_with_pages(["Clause 1 first.", "Clause 2 second."])
    database.set_reference_index_frontier(job_id, 5)  # stale/bogus value from a prior run

    await reference_index.reindex_job(job_id)

    assert database.get_reference_index_frontier(job_id) == 2
    delta = database.get_reference_targets_since(job_id, 0)
    assert not any(c["scope_provisional"] for cands in delta.values() for arr in cands.values() for c in arr)


@pytest.mark.asyncio
async def test_a_key_that_disappears_on_reindex_still_shows_up_as_a_changed_page():
    # Regression test for the reference_index_touched_version fix: a page whose
    # candidate count drops to zero must still appear in the next delta fetch,
    # so an already-connected client actually clears its stale local copy.
    job_id = _job_with_pages(["Clause 9 will be removed."])
    await reference_index.reindex_job(job_id)
    first_delta = database.get_reference_targets_since(job_id, 0)
    assert "clause:9" in first_delta[1]
    version_after_first = database.get_reference_index_version(job_id)

    # Simulate the algorithm no longer detecting anything on this page.
    database.upsert_page(job_id, 1, status="success", text="Nothing of interest here now.")
    await reference_index.reindex_job(job_id)

    second_delta = database.get_reference_targets_since(job_id, version_after_first)
    assert 1 in second_delta
    assert second_delta[1] == {}


def test_reindex_endpoint_404s_for_missing_job(client, auth_headers):
    resp = client.post("/api/jobs/does-not-exist/reindex-references", headers=auth_headers)
    assert resp.status_code == 404


def test_reindex_endpoint_409s_while_job_is_processing(client, auth_headers):
    job_id = _job_with_pages(["Clause 1 text."])
    database.set_job_status(job_id, "processing")
    resp = client.post(f"/api/jobs/{job_id}/reindex-references", headers=auth_headers)
    assert resp.status_code == 409


def test_reindex_endpoint_409s_when_already_active(client, auth_headers):
    job_id = _job_with_pages(["Clause 1 text."])
    reference_index._active_reindex_jobs.add(job_id)
    try:
        resp = client.post(f"/api/jobs/{job_id}/reindex-references", headers=auth_headers)
        assert resp.status_code == 409
    finally:
        reference_index._active_reindex_jobs.discard(job_id)


def test_reindex_endpoint_starts_a_background_task_for_a_completed_job(client, auth_headers, monkeypatch):
    job_id = _job_with_pages(["Clause 1 text."])
    calls = []

    async def _fake_reindex(job_id_arg):
        calls.append(job_id_arg)

    monkeypatch.setattr(reference_index, "reindex_job", _fake_reindex)
    resp = client.post(f"/api/jobs/{job_id}/reindex-references", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "reindexing"


def test_job_details_response_surfaces_reindexing_flag(client, auth_headers):
    job_id = _job_with_pages(["Clause 1 text."])
    resp = client.get(f"/api/jobs/{job_id}", headers=auth_headers)
    assert resp.json()["reference_index_reindexing"] is False

    reference_index._active_reindex_jobs.add(job_id)
    try:
        resp = client.get(f"/api/jobs/{job_id}", headers=auth_headers)
        assert resp.json()["reference_index_reindexing"] is True
    finally:
        reference_index._active_reindex_jobs.discard(job_id)
