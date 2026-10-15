import uuid

import database


def _job_with_pages(tmp_path):
    job_id = str(uuid.uuid4())
    img_path = tmp_path / f"{job_id}.png"
    from PIL import Image
    Image.new("RGB", (10, 10), color="white").save(img_path)
    database.create_job(
        job_id=job_id, filename="t.png", file_path=str(img_path), file_type="image",
        engine="unlimited", total_pages=3,
    )
    database.upsert_page(job_id, 1, status="success", text="clean text", confidence=0.95, needs_review=0)
    database.upsert_page(job_id, 2, status="review", text="garbled", confidence=0.4, needs_review=1, review_reason="repetition_loop")
    database.upsert_page(job_id, 3, status="review", text="fine page", confidence=0.92, needs_review=1, review_reason="audit_sample")
    database.set_job_status(job_id, "completed")
    return job_id


def test_get_job_includes_confidence_pct_and_tier(client, auth_headers, tmp_path):
    job_id = _job_with_pages(tmp_path)
    resp = client.get(f"/api/jobs/{job_id}", headers=auth_headers)
    assert resp.status_code == 200
    pages = {p["page_num"]: p for p in resp.json()["pages_data"]}
    assert pages[1]["confidence_pct"] == 95
    assert pages[1]["confidence_tier"] == "highly_confident"
    assert pages[2]["confidence_tier"] == "needs_review"


def test_get_job_returns_live_processed_pages_count_mid_processing(client, auth_headers, tmp_path):
    # _job_with_pages seeds 3 terminal-status pages but never calls
    # refresh_job_page_counts - reproduces the reported bug where the
    # progress readout stays stuck at 0/N for the whole run.
    job_id = _job_with_pages(tmp_path)
    resp = client.get(f"/api/jobs/{job_id}", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["processed_pages"] == 3
    assert body["succeeded_pages"] == 1
    assert body["review_pages"] == 2
    assert body["failed_pages"] == 0


def test_review_pages_for_one_job(client, auth_headers, tmp_path):
    job_id = _job_with_pages(tmp_path)
    resp = client.get(f"/api/jobs/{job_id}/review-pages", headers=auth_headers)
    assert resp.status_code == 200
    pages = resp.json()["pages"]
    assert len(pages) == 2
    assert pages[0]["page_num"] == 2  # lower confidence first


def test_review_queue_blinds_audit_samples_from_genuine_flags(client, auth_headers, tmp_path):
    job_id = _job_with_pages(tmp_path)
    resp = client.get("/api/review-queue", headers=auth_headers)
    assert resp.status_code == 200
    pages = resp.json()["pages"]
    audit_page = next(p for p in pages if p["job_id"] == job_id and p["page_num"] == 3)
    genuine_page = next(p for p in pages if p["job_id"] == job_id and p["page_num"] == 2)
    # Both must be structurally identical in shape — no field reveals which is which.
    assert audit_page["review_reason"] == ""
    assert set(audit_page.keys()) == set(genuine_page.keys())


def test_confirm_correct_closes_out_a_flagged_page(client, auth_headers, tmp_path):
    job_id = _job_with_pages(tmp_path)
    resp = client.post(f"/api/jobs/{job_id}/pages/2/confirm-correct", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["needs_review"] == 0

    review_resp = client.get(f"/api/jobs/{job_id}/review-pages", headers=auth_headers)
    remaining = [p["page_num"] for p in review_resp.json()["pages"]]
    assert 2 not in remaining


def test_confirm_correct_missing_page_404s(client, auth_headers, tmp_path):
    job_id = _job_with_pages(tmp_path)
    resp = client.post(f"/api/jobs/{job_id}/pages/99/confirm-correct", headers=auth_headers)
    assert resp.status_code == 404


def test_page_attempts_endpoint(client, auth_headers, tmp_path):
    job_id = _job_with_pages(tmp_path)
    database.record_page_attempt(job_id, 2, attempt_no=1, escalation_level="base", outcome="review", confidence=0.4)
    resp = client.get(f"/api/jobs/{job_id}/pages/2/attempts", headers=auth_headers)
    assert resp.status_code == 200
    assert len(resp.json()["attempts"]) == 1


def test_download_result_include_review_returns_only_flagged_pages(client, auth_headers, tmp_path):
    job_id = _job_with_pages(tmp_path)
    resp = client.get(f"/api/jobs/{job_id}/download-result?include=review", headers=auth_headers)
    assert resp.status_code == 200
    text = resp.text
    assert "clean text" not in text
    assert "garbled" in text


def test_download_result_include_all_shows_review_banner(client, auth_headers, tmp_path):
    job_id = _job_with_pages(tmp_path)
    resp = client.get(f"/api/jobs/{job_id}/download-result?include=all", headers=auth_headers)
    assert resp.status_code == 200
    text = resp.text
    assert "NEEDS HUMAN REVIEW" in text
    assert "Review Summary" in text


def test_download_result_clean_excludes_flagged_pages(client, auth_headers, tmp_path):
    job_id = _job_with_pages(tmp_path)
    resp = client.get(f"/api/jobs/{job_id}/download-result?include=clean", headers=auth_headers)
    assert resp.status_code == 200
    text = resp.text
    assert "clean text" in text
    assert "garbled" not in text and "fine page" not in text


def _job_with_seam(tmp_path, processing_mode, enable_overlap=True):
    job_id = str(uuid.uuid4())
    img_path = tmp_path / f"{job_id}.png"
    from PIL import Image
    Image.new("RGB", (10, 10), color="white").save(img_path)
    database.create_job(
        job_id=job_id, filename="t.png", file_path=str(img_path), file_type="image",
        engine="unlimited", total_pages=2,
        processing_mode=processing_mode, enable_overlap=enable_overlap,
    )
    database.upsert_page(
        job_id, 1, status="success", confidence=0.95, needs_review=0,
        text="Intro.\n\n| Item | Qty |\n|---|---|\n| Pen | 2 |",
    )
    database.upsert_page(
        job_id, 2, status="success", confidence=0.95, needs_review=0,
        text="| Pencil | 5 |",  # continuation, no header repeated
    )
    database.set_job_status(job_id, "completed")
    return job_id


def test_download_result_stitches_batch_mode_with_overlap_enabled(client, auth_headers, tmp_path):
    job_id = _job_with_seam(tmp_path, processing_mode="batch", enable_overlap=True)
    resp = client.get(f"/api/jobs/{job_id}/download-result", headers=auth_headers)
    assert resp.status_code == 200
    assert "seam:" in resp.text


def test_download_result_skips_stitch_for_batch_mode_with_overlap_disabled(client, auth_headers, tmp_path):
    job_id = _job_with_seam(tmp_path, processing_mode="batch", enable_overlap=False)
    resp = client.get(f"/api/jobs/{job_id}/download-result", headers=auth_headers)
    assert resp.status_code == 200
    assert "seam:" not in resp.text


def test_download_result_never_stitches_single_page_mode(client, auth_headers, tmp_path):
    job_id = _job_with_seam(tmp_path, processing_mode="single", enable_overlap=True)
    resp = client.get(f"/api/jobs/{job_id}/download-result", headers=auth_headers)
    assert resp.status_code == 200
    assert "seam:" not in resp.text


def test_download_result_clean_tables_no_op_when_overlap_disabled(client, auth_headers, tmp_path):
    job_id = _job_with_seam(tmp_path, processing_mode="batch", enable_overlap=False)
    resp = client.get(f"/api/jobs/{job_id}/download-result?clean_tables=true", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.text.count("## Page") == 2, "clean_tables must not merge anything when stitching is disabled"


# ─── PATCH .../pages/{page_num}/text — WYSIWYG editor autosave ──────────────

def test_update_page_text_saves_to_both_text_and_formatted_text(client, auth_headers, tmp_path):
    job_id = _job_with_pages(tmp_path)
    resp = client.patch(
        f"/api/jobs/{job_id}/pages/1/text", json={"text": "# Edited Heading\n\nNew body text."}, headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["text"] == "# Edited Heading\n\nNew body text."
    assert body["formatted_text"] == "# Edited Heading\n\nNew body text."

    page = database.get_page(job_id, 1)
    assert page["text"] == "# Edited Heading\n\nNew body text."
    assert page["formatted_text"] == "# Edited Heading\n\nNew body text."


def test_update_page_text_bumps_reference_index_version(client, auth_headers, tmp_path):
    job_id = _job_with_pages(tmp_path)
    before = database.get_job(job_id, include_pages=False)["reference_index_version"]
    resp = client.patch(f"/api/jobs/{job_id}/pages/1/text", json={"text": "edited"}, headers=auth_headers)
    assert resp.status_code == 200
    after = database.get_job(job_id, include_pages=False)["reference_index_version"]
    assert after > before


def test_update_page_text_missing_page_404s(client, auth_headers, tmp_path):
    job_id = _job_with_pages(tmp_path)
    resp = client.patch(f"/api/jobs/{job_id}/pages/99/text", json={"text": "x"}, headers=auth_headers)
    assert resp.status_code == 404


def test_update_page_text_requires_text_field(client, auth_headers, tmp_path):
    job_id = _job_with_pages(tmp_path)
    resp = client.patch(f"/api/jobs/{job_id}/pages/1/text", json={}, headers=auth_headers)
    assert resp.status_code == 400


def test_update_page_text_allowed_for_non_admin_owner(client, user_token, tmp_path):
    import database as db
    job_id = _job_with_pages(tmp_path)
    # _job_with_pages doesn't set an owner — directly set it so the ownership
    # check (admin bypasses, user must own) actually exercises the "owner" path.
    conn = db.get_db_connection()
    conn.execute("UPDATE ocr_jobs SET owner_id = ? WHERE job_id = ?", ("dev-user-001", job_id))
    conn.commit()
    conn.close()

    resp = client.patch(
        f"/api/jobs/{job_id}/pages/1/text", json={"text": "proofread by a regular user"},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert resp.status_code == 200, "a non-admin must be able to save their own in-place edits — this is the general user's primary review action"
