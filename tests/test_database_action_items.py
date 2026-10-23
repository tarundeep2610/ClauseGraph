"""Round-trip tests for the ocr_action_items table and its helpers —
mirroring test_database_reference_targets.py's shape for the separate
actionable-items pass (its own independent version counter, no key/
scope_chain concept since this is a flat per-page list, not a hierarchy)."""

import uuid

import database


def _job(total_pages=1):
    job_id = str(uuid.uuid4())
    database.create_job(
        job_id=job_id, filename="t.pdf", file_path="/tmp/x.pdf", file_type="pdf",
        engine="unlimited", total_pages=total_pages,
    )
    for i in range(1, total_pages + 1):
        database.upsert_page(job_id, i, status="success", text=f"page {i} text", confidence=0.9)
    return job_id


def _item(**kwargs):
    defaults = dict(
        occurrence_index=0, item_type="deadline", item_text="EMD submission deadline",
        date_text="15.03.2025", date_iso="2025-03-15", anchor_event=None, cadence=None,
        end_page=None, anchor_id="page-block-1", evidence_quote="EMD shall be submitted",
        responsible_party="Bidder", confidence="high", detection_model="gemini-2.5-flash",
    )
    defaults.update(kwargs)
    return defaults


def test_version_starts_at_zero_and_bumps_monotonically():
    job_id = _job()
    assert database.get_action_item_index_version(job_id) == 0
    v1 = database.bump_action_item_index_version(job_id)
    v2 = database.bump_action_item_index_version(job_id)
    assert v2 == v1 + 1
    assert database.get_action_item_index_version(job_id) == v2


def test_upsert_and_fetch_round_trips_all_fields():
    job_id = _job()
    version = database.bump_action_item_index_version(job_id)
    item = _item(anchor_event="Effective Date of Contract")
    database.upsert_action_items_for_page(job_id, 1, [item], version)

    delta = database.get_action_items_since(job_id, 0)
    assert 1 in delta
    row = delta[1][0]
    assert row["item_type"] == "deadline"
    assert row["date_iso"] == "2025-03-15"
    assert row["anchor_event"] == "Effective Date of Contract"
    assert row["responsible_party"] == "Bidder"


def test_reference_index_version_is_independent_of_action_item_version():
    # The two passes must never share a counter — bumping one must not
    # move the other.
    job_id = _job()
    database.bump_reference_index_version(job_id)
    database.bump_reference_index_version(job_id)
    assert database.get_action_item_index_version(job_id) == 0
    database.bump_action_item_index_version(job_id)
    assert database.get_reference_index_version(job_id) == 2


def test_upsert_with_empty_list_still_touches_the_page():
    # Confirmed necessary: a page whose item count drops to zero must still
    # show up in a delta fetch (same reasoning as
    # upsert_reference_targets_for_page's own docstring), or the client
    # never learns that page's stale items should be cleared.
    job_id = _job()
    v1 = database.bump_action_item_index_version(job_id)
    database.upsert_action_items_for_page(job_id, 1, [_item()], v1)

    v2 = database.bump_action_item_index_version(job_id)
    database.upsert_action_items_for_page(job_id, 1, [], v2)

    delta = database.get_action_items_since(job_id, v1)
    assert delta[1] == []


def test_delta_fetch_only_returns_pages_changed_since_version():
    job_id = _job(total_pages=2)
    v1 = database.bump_action_item_index_version(job_id)
    database.upsert_action_items_for_page(job_id, 1, [_item()], v1)
    v2 = database.bump_action_item_index_version(job_id)
    database.upsert_action_items_for_page(job_id, 2, [_item(item_text="Another item")], v2)

    delta_since_v1 = database.get_action_items_since(job_id, v1)
    assert 1 not in delta_since_v1
    assert 2 in delta_since_v1

    delta_since_zero = database.get_action_items_since(job_id, 0)
    assert 1 in delta_since_zero
    assert 2 in delta_since_zero


def test_upsert_is_delete_then_reinsert_not_additive():
    job_id = _job()
    v1 = database.bump_action_item_index_version(job_id)
    database.upsert_action_items_for_page(job_id, 1, [_item(occurrence_index=0)], v1)
    v2 = database.bump_action_item_index_version(job_id)
    database.upsert_action_items_for_page(job_id, 1, [_item(occurrence_index=0, item_text="Replaced item")], v2)

    rows = database.get_all_action_items_for_job(job_id)
    assert len(rows) == 1
    assert rows[0]["item_text"] == "Replaced item"


def test_mark_action_items_built_stamps_timestamp():
    job_id = _job()
    database.mark_action_items_built(job_id)
    job = database.get_job(job_id, include_pages=False)
    assert job["action_items_built_at"] is not None


def test_action_item_report_round_trips_as_json():
    job_id = _job()
    database.update_action_item_report(job_id, status="running", current_chunk=1, total_chunks=3)
    report = database.get_action_item_report(job_id)
    assert report["status"] == "running"
    assert report["current_chunk"] == 1

    job = database.get_job(job_id, include_pages=False)
    assert job["action_item_report"]["status"] == "running"


def test_missing_confidence_defaults_to_medium_on_fetch():
    job_id = _job()
    version = database.bump_action_item_index_version(job_id)
    item = _item()
    del item["confidence"]
    database.upsert_action_items_for_page(job_id, 1, [item], version)
    delta = database.get_action_items_since(job_id, 0)
    assert delta[1][0]["confidence"] == "medium"
