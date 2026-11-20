"""Tests for database.remap_subdoc_keys_for_job — the one-time-per-reindex
fix-up that keeps a job's structure overrides pointed at the right item once
reference_index.py's `subdoc` fallback family switched from a positional
counter (subdoc:7) to a content-addressed slug (subdoc:verification). See
remap_subdoc_keys_for_job's own docstring for why ocr_reference_targets
itself needs no fix-up here (the subsequent reindex regenerates it), while
the three override tables do (reindex never touches them by design)."""

import uuid

import database
import structure_overrides


def _job():
    job_id = str(uuid.uuid4())
    database.create_job(
        job_id=job_id, filename="t.pdf", file_path="/tmp/x.pdf", file_type="pdf",
        engine="unlimited", total_pages=10,
    )
    return job_id


def _seed_old_style_subdoc_target(job_id, page, key, own_label, version):
    database.upsert_page(job_id, page, status="success", text=own_label, confidence=0.9)
    database.upsert_reference_targets_for_page(job_id, page, [{
        "key": key, "occurrence_index": 0, "snippet": own_label,
        "scope_key": "", "scope_label": "", "scope_chain": [],
        "own_label": own_label,
    }], version)


def test_remap_rewrites_override_tables_but_leaves_reference_targets_alone():
    job_id = _job()
    version = database.bump_reference_index_version(job_id)
    # Old-style ordinal keys, each on a different page — "subdoc:3"'s
    # canonical row deliberately lives on a LATER page than the override
    # that references it, the exact shape a per-page remap would have raced.
    _seed_old_style_subdoc_target(job_id, 1, "subdoc:7", "Verification", version)
    _seed_old_style_subdoc_target(job_id, 10, "subdoc:3", "Annexure Details", version)

    # An edit override directly on the page-1 "subdoc:7" row.
    structure_overrides.upsert_edit(job_id, "subdoc:7", 1, 0, heading="Verification (edited)")
    # A parent override reparenting an unrelated item onto "subdoc:3" — whose
    # canonical row is on page 10, far from this override's own page 5 item.
    structure_overrides.set_parent(job_id, "clause:1", 5, 0, "subdoc:3")
    # A sibling-order override scoped under "subdoc:7".
    structure_overrides.set_sibling_order(job_id, "subdoc:7", 1, 0, [("clause:2", 1, 0)])

    database.remap_subdoc_keys_for_job(job_id)

    edits = structure_overrides.get_edit_overrides(job_id)
    assert ("subdoc:7", 1, 0) not in edits
    assert edits[("subdoc:verification", 1, 0)]["heading_override"] == "Verification (edited)"

    parents = {(p["item_key"], p["item_page"], p["item_occurrence_index"]): p
               for p in structure_overrides.get_parent_overrides(job_id)}
    assert parents[("clause:1", 5, 0)]["new_parent_key"] == "subdoc:annexure-details"

    sorts = structure_overrides.get_sort_overrides(job_id)
    assert any(s["scope_key"] == "subdoc:verification" for s in sorts)
    assert not any(s["scope_key"] == "subdoc:7" for s in sorts)

    # ocr_reference_targets itself is untouched by the migration — the
    # subsequent reindex (not exercised by this unit test) is what rewrites
    # it, using the new minting code directly.
    delta = database.get_reference_targets_since(job_id, 0)
    assert "subdoc:7" in delta[1]
    assert "subdoc:verification" not in delta[1]


def test_remap_is_idempotent_once_override_keys_are_already_current():
    job_id = _job()
    version = database.bump_reference_index_version(job_id)
    _seed_old_style_subdoc_target(job_id, 1, "subdoc:7", "Verification", version)
    structure_overrides.upsert_edit(job_id, "subdoc:7", 1, 0, heading="Verification (edited)")

    database.remap_subdoc_keys_for_job(job_id)
    database.remap_subdoc_keys_for_job(job_id)  # must not raise or double-remap

    edits = structure_overrides.get_edit_overrides(job_id)
    assert ("subdoc:verification", 1, 0) in edits
    assert ("subdoc:7", 1, 0) not in edits


def test_remap_is_a_no_op_when_no_subdoc_targets_exist_for_the_job():
    job_id = _job()
    structure_overrides.upsert_edit(job_id, "clause:1", 1, 0, heading="Clause one")
    database.remap_subdoc_keys_for_job(job_id)  # must not raise
    edits = structure_overrides.get_edit_overrides(job_id)
    assert ("clause:1", 1, 0) in edits
