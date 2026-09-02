import sqlite3
import uuid

import database


def _job_id():
    job_id = str(uuid.uuid4())
    database.create_job(
        job_id=job_id, filename="t.pdf", file_path="/tmp/t.pdf", file_type="pdf",
        total_pages=5,
    )
    return job_id


def test_init_pages_for_job_is_idempotent():
    job_id = _job_id()
    database.init_pages_for_job(job_id, 5)
    database.init_pages_for_job(job_id, 5)  # second call must not duplicate or raise
    pages = database.get_pages(job_id)
    assert len(pages) == 5
    assert [p["page_num"] for p in pages] == [1, 2, 3, 4, 5]
    assert all(p["status"] == "pending" for p in pages)


def test_upsert_page_insert_then_update():
    job_id = _job_id()
    database.upsert_page(job_id, 1, status="success", text="hello", confidence=0.92)
    page = database.get_page(job_id, 1)
    assert page["status"] == "success"
    assert page["text"] == "hello"
    assert page["confidence"] == 0.92

    database.upsert_page(job_id, 1, status="review", confidence=0.5)
    page = database.get_page(job_id, 1)
    assert page["status"] == "review"
    assert page["confidence"] == 0.5
    assert page["text"] == "hello"  # untouched field preserved


def test_claim_page_for_processing_prevents_double_claim():
    job_id = _job_id()
    database.upsert_page(job_id, 1, status="pending")
    first = database.claim_page_for_processing(job_id, 1)
    second = database.claim_page_for_processing(job_id, 1)
    assert first is True
    assert second is False, "a page already 'processing' must not be claimable again"


def test_claim_page_for_processing_on_completed_job_still_works():
    job_id = _job_id()
    database.upsert_page(job_id, 3, status="review", confidence=0.5)
    database.set_job_status(job_id, "completed")
    assert database.claim_page_for_processing(job_id, 3) is True
    job = database.get_job(job_id, include_pages=False)
    assert job["status"] == "completed", "retrying one page must not revert job status"


def test_get_last_completed_page_before_respects_max_gap():
    job_id = _job_id()
    database.upsert_page(job_id, 2, status="success", text="page two text")
    # Immediate predecessor (gap=1) is available.
    prev = database.get_last_completed_page_before(job_id, 3, max_gap=1)
    assert prev is not None
    assert prev["page_num"] == 2

    # Page 6 is 4 pages ahead of the only completed page (2) — exceeds max_gap=1.
    prev = database.get_last_completed_page_before(job_id, 6, max_gap=1)
    assert prev is None


def test_get_last_completed_page_before_skips_empty_text():
    job_id = _job_id()
    database.upsert_page(job_id, 2, status="success", text="")
    prev = database.get_last_completed_page_before(job_id, 3, max_gap=1)
    assert prev is None, "a page with empty text can't be a primer source"


def test_count_pages_by_status_and_refresh_job_page_counts():
    job_id = _job_id()
    database.upsert_page(job_id, 1, status="success")
    database.upsert_page(job_id, 2, status="review", needs_review=1)
    database.upsert_page(job_id, 3, status="failed")
    database.upsert_page(job_id, 4, status="pending")
    counts = database.count_pages_by_status(job_id)
    assert counts.get("success") == 1
    assert counts.get("review") == 1
    assert counts.get("failed") == 1
    assert counts.get("pending") == 1

    database.refresh_job_page_counts(job_id)
    job = database.get_job(job_id, include_pages=False)
    assert job["succeeded_pages"] == 1
    assert job["review_pages"] == 1
    assert job["failed_pages"] == 1
    assert job["processed_pages"] == 3  # success + review + failed, not pending


def test_list_review_pages_orders_by_confidence_ascending():
    job_id = _job_id()
    database.upsert_page(job_id, 1, status="review", needs_review=1, confidence=0.65)
    database.upsert_page(job_id, 2, status="review", needs_review=1, confidence=0.40)
    database.upsert_page(job_id, 3, status="success", needs_review=0, confidence=0.95)
    reviews = database.list_review_pages(job_id)
    assert [p["page_num"] for p in reviews] == [2, 1]


def test_reset_stuck_processing_pages():
    job_id = _job_id()
    database.upsert_page(job_id, 1, status="processing")
    database.upsert_page(job_id, 2, status="success")
    database.reset_stuck_processing_pages(job_id)
    assert database.get_page(job_id, 1)["status"] == "pending"
    assert database.get_page(job_id, 2)["status"] == "success"


def test_record_page_attempt_and_get_page_attempts():
    job_id = _job_id()
    database.record_page_attempt(job_id, 1, attempt_no=1, escalation_level="base", engine="unlimited", confidence=0.4, outcome="success")
    database.record_page_attempt(job_id, 1, attempt_no=2, escalation_level="combined_recovery", engine="unlimited", confidence=0.9, outcome="success")
    attempts = database.get_page_attempts(job_id, 1)
    assert len(attempts) == 2
    assert attempts[0]["attempt_no"] == 1
    assert attempts[1]["escalation_level"] == "combined_recovery"


def test_delete_job_cascades_to_pages_and_attempts():
    job_id = _job_id()
    database.upsert_page(job_id, 1, status="success")
    database.record_page_attempt(job_id, 1, attempt_no=1, outcome="success")
    database.delete_job(job_id)
    assert database.get_pages(job_id) == []
    assert database.get_page_attempts(job_id, 1) == []


def test_get_job_falls_back_to_legacy_pages_data_when_no_ocr_pages_rows():
    job_id = _job_id()
    database.update_job_progress(job_id, 1, [{"page": 1, "status": "success", "text": "legacy", "error": ""}], status="processing")
    job = database.get_job(job_id, include_pages=True)
    assert job["pages_data"][0]["text"] == "legacy"


def test_migration_backfills_legacy_pages_data_idempotently():
    job_id = _job_id()
    legacy = [
        {"page": 1, "status": "success", "text": "hello", "error": ""},
        {"page": 2, "status": "failed", "text": "### boom", "error": "oops"},
    ]
    database.update_job_progress(job_id, 1, legacy, status="processing")

    conn = sqlite3.connect(database.DB_PATH)
    conn.execute("UPDATE schema_meta SET value = 'pending' WHERE key = 'pages_migrated_v1'")
    conn.execute("DELETE FROM ocr_pages WHERE job_id = ?", (job_id,))
    conn.commit()
    conn.close()

    database.init_db()  # first migration pass
    pages = database.get_pages(job_id)
    assert len(pages) == 2
    failed_page = next(p for p in pages if p["page_num"] == 2)
    assert failed_page["needs_review"] == 1
    assert failed_page["review_reason"] == "legacy_failed"
    assert failed_page["confidence"] is None

    database.init_db()  # second pass must not duplicate or raise
    pages_again = database.get_pages(job_id)
    assert len(pages_again) == 2
