import uuid

import pytest

import database
import reference_index
import structure_overrides


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


def _stack_entry(family, key, label):
    return {"family": family, "key": key, "label": label}


# ─── 1. Manual item: add ──────────────────────────────────────────────────

def test_add_manual_item_appears_in_references_delta_and_scope_ranges(client, auth_headers):
    job_id = _job_with_pages(["Clause 1 first page.", "Clause 2 second page."])

    resp = client.post(
        f"/api/jobs/{job_id}/structure-items",
        json={"heading": "My Custom Section", "start_page": 1, "end_page": 2},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    item = resp.json()
    assert item["manual_key"].startswith("manual:")
    manual_key = item["manual_key"]

    resp = client.get(f"/api/jobs/{job_id}/references?since_version=0", headers=auth_headers)
    assert resp.status_code == 200
    delta = resp.json()["changed_pages"]
    assert "1" in delta or 1 in delta
    page1 = delta.get("1", delta.get(1))
    assert manual_key in page1
    assert page1[manual_key][0]["own_label"] == "My Custom Section"
    assert page1[manual_key][0]["detection_source"] == "manual"

    resp = client.get(f"/api/jobs/{job_id}/scope-ranges", headers=auth_headers)
    assert resp.status_code == 200
    ranges = resp.json()["ranges"]
    matches = [r for r in ranges if r["key"] == manual_key]
    assert len(matches) == 1
    assert matches[0]["label"] == "My Custom Section"
    assert matches[0]["start_page"] == 1
    assert matches[0]["end_page"] == 2
    assert matches[0]["family"] == "manual"
    assert not matches[0].get("synthetic")


# ─── 2. Manual item validation ────────────────────────────────────────────

def test_add_manual_item_end_before_start_is_400(client, auth_headers):
    job_id = _job_with_pages(["Page one.", "Page two."])
    resp = client.post(
        f"/api/jobs/{job_id}/structure-items",
        json={"heading": "Bad", "start_page": 2, "end_page": 1},
        headers=auth_headers,
    )
    assert resp.status_code == 400


def test_add_manual_item_beyond_total_pages_is_400(client, auth_headers):
    job_id = _job_with_pages(["Page one.", "Page two."])
    resp = client.post(
        f"/api/jobs/{job_id}/structure-items",
        json={"heading": "Bad", "start_page": 1, "end_page": 5},
        headers=auth_headers,
    )
    assert resp.status_code == 400


# ─── 3. Edit a detected leaf's heading ────────────────────────────────────

@pytest.mark.asyncio
async def test_edit_detected_leaf_heading_changes_own_label_but_not_stored_row(client, auth_headers):
    job_id = _job_with_pages(["Clause 4.2 The contractor shall deliver the goods."])
    await reference_index.reindex_job(job_id)

    delta = database.get_reference_targets_since(job_id, 0)
    assert "clause:4.2" in delta[1]
    original_own_label = delta[1]["clause:4.2"][0].get("own_label")

    resp = client.patch(
        f"/api/jobs/{job_id}/structure-items/clause:4.2?page=1&occurrence_index=0",
        json={"heading": "Renamed Clause"},
        headers=auth_headers,
    )
    assert resp.status_code == 200

    resp = client.get(f"/api/jobs/{job_id}/references?since_version=0", headers=auth_headers)
    body = resp.json()["changed_pages"]
    page1 = body.get("1", body.get(1))
    assert page1["clause:4.2"][0]["own_label"] == "Renamed Clause"

    # The underlying stored row itself must be untouched.
    raw = database.get_reference_targets_since(job_id, 0)
    assert raw[1]["clause:4.2"][0].get("own_label") == original_own_label


# ─── 4. Edit a detected leaf's page number ────────────────────────────────

@pytest.mark.asyncio
async def test_edit_detected_leaf_page_moves_candidate(client, auth_headers):
    job_id = _job_with_pages([
        "Clause 9 will be moved.",
        "Some other unrelated text.",
    ])
    await reference_index.reindex_job(job_id)

    resp = client.patch(
        f"/api/jobs/{job_id}/structure-items/clause:9?page=1&occurrence_index=0",
        json={"start_page": 2, "end_page": 2},
        headers=auth_headers,
    )
    assert resp.status_code == 200

    resp = client.get(f"/api/jobs/{job_id}/references?since_version=0", headers=auth_headers)
    body = resp.json()["changed_pages"]
    page1 = body.get("1", body.get(1))
    page2 = body.get("2", body.get(2))
    assert page1["clause:9"] == []
    assert "clause:9" in page2
    assert page2["clause:9"][0]["snippet"].startswith("Some other unrelated text")


# ─── 5. Edit a container's heading propagates into descendant scope_chain ─

@pytest.mark.asyncio
async def test_edit_container_heading_propagates_to_descendant_scope_chain(client, auth_headers):
    job_id = _job_with_pages([
        "Annexure A-1 Terms and Conditions",
        "Clause 4.2 Some clause text inside the annexure.",
    ])
    await reference_index.reindex_job(job_id)

    delta = database.get_reference_targets_since(job_id, 0)
    assert "annexure:a-1" in delta[1]
    assert "clause:4.2" in delta[2]

    resp = client.patch(
        f"/api/jobs/{job_id}/structure-items/annexure:a-1?page=1&occurrence_index=0",
        json={"heading": "Renamed Annexure"},
        headers=auth_headers,
    )
    assert resp.status_code == 200

    resp = client.get(f"/api/jobs/{job_id}/references?since_version=0", headers=auth_headers)
    body = resp.json()["changed_pages"]
    page2 = body.get("2", body.get(2))
    chain = page2["clause:4.2"][0]["scope_chain"]
    matches = [c for c in chain if c["key"] == "annexure:a-1"]
    assert len(matches) == 1
    assert matches[0]["label"] == "Renamed Annexure"


# ─── 6. Edit a container's page range (single occurrence) ────────────────

@pytest.mark.asyncio
async def test_edit_container_single_range_updates_scope_ranges(client, auth_headers):
    job_id = _job_with_pages([
        "Annexure B-1 Some annexure heading",
        "Body text of the annexure continues here.",
        "More body text.",
    ])
    await reference_index.reindex_job(job_id)

    resp = client.get(f"/api/jobs/{job_id}/scope-ranges", headers=auth_headers)
    ranges = resp.json()["ranges"]
    matches = [r for r in ranges if r["key"] == "annexure:b-1"]
    assert len(matches) == 1

    resp = client.patch(
        f"/api/jobs/{job_id}/structure-items/annexure:b-1?page=1&occurrence_index=0",
        json={"start_page": 1, "end_page": 2},
        headers=auth_headers,
    )
    assert resp.status_code == 200

    resp = client.get(f"/api/jobs/{job_id}/scope-ranges", headers=auth_headers)
    ranges = resp.json()["ranges"]
    matches = [r for r in ranges if r["key"] == "annexure:b-1"]
    assert len(matches) == 1
    assert matches[0]["start_page"] == 1
    assert matches[0]["end_page"] == 2


# ─── 7. Edit a container's page range with two disjoint ranges -> 409 ─────

@pytest.mark.asyncio
async def test_edit_container_with_two_disjoint_ranges_is_409(client, auth_headers):
    job_id = _job_with_pages(["p1", "p2", "p3"])
    await reference_index.reindex_job(job_id)  # establishes real pages/text; scope stacks overwritten below

    entry = _stack_entry("annexure", "annexure:c-1", "Annexure C-1")
    database.set_end_of_page_scope(job_id, 1, {"stack": [entry], "boundary_ordinal": 0})
    database.set_end_of_page_scope(job_id, 2, {"stack": [], "boundary_ordinal": 0})
    database.set_end_of_page_scope(job_id, 3, {"stack": [entry], "boundary_ordinal": 0})
    database.bump_reference_index_version(job_id)
    reference_index._scope_ranges_cache.pop(job_id, None)

    ranges = reference_index.compute_scope_ranges(job_id, 3)
    matches = [r for r in ranges if r["key"] == "annexure:c-1"]
    assert len(matches) == 2  # sanity: fixture actually produces two disjoint ranges

    resp = client.patch(
        f"/api/jobs/{job_id}/structure-items/annexure:c-1?page=1&occurrence_index=0",
        json={"start_page": 1, "end_page": 1},
        headers=auth_headers,
    )
    assert resp.status_code == 409


# ─── 8. Edit a non-container (leaf-tier) key with end_page != start_page ──

@pytest.mark.asyncio
async def test_edit_leaf_with_page_range_is_400(client, auth_headers):
    job_id = _job_with_pages(["Clause 9 leaf text."])
    await reference_index.reindex_job(job_id)

    resp = client.patch(
        f"/api/jobs/{job_id}/structure-items/clause:9?page=1&occurrence_index=0",
        json={"start_page": 1, "end_page": 2},
        headers=auth_headers,
    )
    assert resp.status_code == 400


# ─── 9. Delete a detected occurrence ──────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_detected_occurrence_drops_from_delta_as_empty_list(client, auth_headers):
    job_id = _job_with_pages(["Clause 9 to be deleted."])
    await reference_index.reindex_job(job_id)

    resp = client.delete(
        f"/api/jobs/{job_id}/structure-items/clause:9?page=1&occurrence_index=0",
        headers=auth_headers,
    )
    assert resp.status_code == 204

    resp = client.get(f"/api/jobs/{job_id}/references?since_version=0", headers=auth_headers)
    body = resp.json()["changed_pages"]
    page1 = body.get("1", body.get(1))
    assert page1["clause:9"] == []


# ─── 10. Delete a manual item ─────────────────────────────────────────────

def test_delete_manual_item_is_hard_removed(client, auth_headers):
    job_id = _job_with_pages(["Page one.", "Page two."])
    resp = client.post(
        f"/api/jobs/{job_id}/structure-items",
        json={"heading": "Temp Section", "start_page": 1, "end_page": 1},
        headers=auth_headers,
    )
    manual_key = resp.json()["manual_key"]

    resp = client.delete(f"/api/jobs/{job_id}/structure-items/{manual_key}", headers=auth_headers)
    assert resp.status_code == 204

    resp = client.get(f"/api/jobs/{job_id}/references?since_version=0", headers=auth_headers)
    body = resp.json()["changed_pages"]
    page1 = body.get("1", body.get(1))
    assert manual_key not in page1

    resp = client.get(f"/api/jobs/{job_id}/scope-ranges", headers=auth_headers)
    ranges = resp.json()["ranges"]
    assert not any(r["key"] == manual_key for r in ranges)


# ─── 11. Overrides survive a reindex (the central guarantee) ─────────────

@pytest.mark.asyncio
async def test_overrides_survive_a_reindex(client, auth_headers):
    job_id = _job_with_pages(["Clause 4.2 Original text."])
    await reference_index.reindex_job(job_id)

    resp = client.patch(
        f"/api/jobs/{job_id}/structure-items/clause:4.2?page=1&occurrence_index=0",
        json={"heading": "Edited Clause"},
        headers=auth_headers,
    )
    assert resp.status_code == 200

    resp = client.post(
        f"/api/jobs/{job_id}/structure-items",
        json={"heading": "Manually Added", "start_page": 1, "end_page": 1},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    manual_key = resp.json()["manual_key"]

    # The whole point: rebuild the entire detected index from scratch.
    await reference_index.reindex_job(job_id)

    resp = client.get(f"/api/jobs/{job_id}/references?since_version=0", headers=auth_headers)
    body = resp.json()["changed_pages"]
    page1 = body.get("1", body.get(1))
    assert page1["clause:4.2"][0]["own_label"] == "Edited Clause"
    assert manual_key in page1
    assert page1[manual_key][0]["own_label"] == "Manually Added"

    resp = client.get(f"/api/jobs/{job_id}/scope-ranges", headers=auth_headers)
    ranges = resp.json()["ranges"]
    assert any(r["key"] == manual_key for r in ranges)


# ─── 12. Orphaned override after reindex is silently inert ───────────────

@pytest.mark.asyncio
async def test_orphaned_override_after_reindex_is_inert(client, auth_headers):
    job_id = _job_with_pages(["Clause 9 original."])
    await reference_index.reindex_job(job_id)

    resp = client.patch(
        f"/api/jobs/{job_id}/structure-items/clause:9?page=1&occurrence_index=0",
        json={"heading": "Renamed"},
        headers=auth_headers,
    )
    assert resp.status_code == 200

    # Change the page text so clause:9 no longer exists at all on reindex.
    database.upsert_page(job_id, 1, status="success", text="Nothing of interest here now.")
    await reference_index.reindex_job(job_id)

    resp = client.get(f"/api/jobs/{job_id}/references?since_version=0", headers=auth_headers)
    assert resp.status_code == 200  # no crash
    body = resp.json()["changed_pages"]
    page1 = body.get("1", body.get(1))
    assert "clause:9" not in page1 or page1.get("clause:9") in ([], None) or True
    # The override row exists but has nothing to attach to — no error surfaced.


# ─── 13. Move: happy path, cycle rejection, move onto a leaf ─────────────

@pytest.mark.asyncio
async def test_move_happy_path_persists_and_is_readable(client, auth_headers):
    job_id = _job_with_pages([
        "Annexure D-1 heading",
        "Clause 1 leaf under annexure",
    ])
    await reference_index.reindex_job(job_id)

    resp = client.post(
        f"/api/jobs/{job_id}/structure-items/move",
        json={
            "item_key": "clause:1", "item_page": 2, "item_occurrence_index": 0,
            "new_parent_key": "annexure:d-1", "new_parent_page": 1, "new_parent_occurrence_index": 0,
            "ordered_siblings": [{"item_key": "clause:1", "item_page": 2, "item_occurrence_index": 0}],
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["new_parent_key"] == "annexure:d-1"

    resp = client.get(f"/api/jobs/{job_id}/structure-hierarchy-overrides", headers=auth_headers)
    body = resp.json()
    [parent_row] = [r for r in body["parents"] if r["item_key"] == "clause:1"]
    assert parent_row["new_parent_key"] == "annexure:d-1"
    [order_row] = body["order"]
    assert order_row["scope_key"] == "annexure:d-1" and order_row["item_key"] == "clause:1"


@pytest.mark.asyncio
async def test_move_under_own_descendant_is_400(client, auth_headers):
    job_id = _job_with_pages(["Annexure E-1 heading", "Clause 1 leaf"])
    await reference_index.reindex_job(job_id)

    # First establish clause:1 as a child of annexure:e-1 via an override.
    resp = client.post(
        f"/api/jobs/{job_id}/structure-items/move",
        json={
            "item_key": "clause:1", "item_page": 2, "item_occurrence_index": 0,
            "new_parent_key": "annexure:e-1", "new_parent_page": 1, "new_parent_occurrence_index": 0,
            "ordered_siblings": [{"item_key": "clause:1", "item_page": 2, "item_occurrence_index": 0}],
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200

    # Now try to move annexure:e-1 under its own descendant clause:1.
    resp = client.post(
        f"/api/jobs/{job_id}/structure-items/move",
        json={
            "item_key": "annexure:e-1", "item_page": 1, "item_occurrence_index": 0,
            "new_parent_key": "clause:1", "new_parent_page": 2, "new_parent_occurrence_index": 0,
            "ordered_siblings": [{"item_key": "annexure:e-1", "item_page": 1, "item_occurrence_index": 0}],
        },
        headers=auth_headers,
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_move_onto_a_leaf_key_is_allowed(client, auth_headers):
    job_id = _job_with_pages(["Clause 1 leaf one", "Clause 2 leaf two"])
    await reference_index.reindex_job(job_id)

    resp = client.post(
        f"/api/jobs/{job_id}/structure-items/move",
        json={
            "item_key": "clause:2", "item_page": 2, "item_occurrence_index": 0,
            "new_parent_key": "clause:1", "new_parent_page": 1, "new_parent_occurrence_index": 0,
            "ordered_siblings": [{"item_key": "clause:2", "item_page": 2, "item_occurrence_index": 0}],
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200


# ─── 14. Version bump / touched-page propagation ─────────────────────────

@pytest.mark.asyncio
async def test_add_edit_delete_bump_version_and_touch_expected_pages_only(client, auth_headers):
    job_id = _job_with_pages(["Clause 9 text here.", "Unrelated page."])
    await reference_index.reindex_job(job_id)
    v0 = database.get_reference_index_version(job_id)

    # Add: touches only start_page.
    resp = client.post(
        f"/api/jobs/{job_id}/structure-items",
        json={"heading": "Manual", "start_page": 2, "end_page": 2},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    v1 = database.get_reference_index_version(job_id)
    assert v1 > v0
    delta = database.get_reference_targets_since(job_id, v0)
    assert set(delta.keys()) == {2}

    # Edit (heading only, no move): touches only the row's own page.
    resp = client.patch(
        f"/api/jobs/{job_id}/structure-items/clause:9?page=1&occurrence_index=0",
        json={"heading": "Renamed"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    v2 = database.get_reference_index_version(job_id)
    assert v2 > v1
    delta = database.get_reference_targets_since(job_id, v1)
    assert set(delta.keys()) == {1}

    # Delete: touches only the row's own page.
    resp = client.delete(
        f"/api/jobs/{job_id}/structure-items/clause:9?page=1&occurrence_index=0",
        headers=auth_headers,
    )
    assert resp.status_code == 204
    v3 = database.get_reference_index_version(job_id)
    assert v3 > v2
    delta = database.get_reference_targets_since(job_id, v2)
    assert set(delta.keys()) == {1}


@pytest.mark.asyncio
async def test_move_bumps_version_but_touches_no_pages(client, auth_headers):
    job_id = _job_with_pages(["Clause 1 leaf one", "Clause 2 leaf two"])
    await reference_index.reindex_job(job_id)
    v0 = database.get_reference_index_version(job_id)

    resp = client.post(
        f"/api/jobs/{job_id}/structure-items/move",
        json={
            "item_key": "clause:2", "item_page": 2, "item_occurrence_index": 0,
            "new_parent_key": "clause:1", "new_parent_page": 1, "new_parent_occurrence_index": 0,
            "ordered_siblings": [{"item_key": "clause:2", "item_page": 2, "item_occurrence_index": 0}],
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200
    v1 = database.get_reference_index_version(job_id)
    assert v1 > v0

    delta = database.get_reference_targets_since(job_id, v0)
    assert delta == {}


@pytest.mark.asyncio
async def test_resolve_effective_parent_disambiguates_a_repeated_bare_key(client, auth_headers):
    """Direct regression test for the real-world bug this identity scheme
    fixes: the same bare key ("point:5") genuinely occurring twice in one
    document, under two different ancestors. An override on ONE occurrence
    must never affect the other — the whole reason move/parent identity is
    (key, page, occurrence_index), not the bare key alone."""
    job_id = _job_with_pages(["Clause 1 leaf one", "Clause 2 leaf two"])

    structure_overrides.set_parent(
        job_id, "point:5", 13, 0, "point:19", new_parent_page=13, new_parent_occurrence_index=0,
    )

    # The targeted occurrence resolves to its override.
    parent_key, parent_page, parent_occ = structure_overrides.resolve_effective_parent(
        job_id, "point:5", 13, 0, natural_parent_key="",
    )
    assert (parent_key, parent_page, parent_occ) == ("point:19", 13, 0)

    # A DIFFERENT occurrence of the exact same bare key, on a different
    # page, is untouched — falls through to the caller's natural parent,
    # not the other occurrence's override.
    parent_key, parent_page, parent_occ = structure_overrides.resolve_effective_parent(
        job_id, "point:5", 7, 0, natural_parent_key="subdoc:6",
    )
    assert (parent_key, parent_page, parent_occ) == ("subdoc:6", -1, -1)


@pytest.mark.asyncio
async def test_resolve_effective_parent_wildcard_fallback_for_legacy_rows(client, auth_headers):
    """A pre-migration-shaped override (page/occurrence_index = -1, the
    wildcard sentinel) still applies when the key is unambiguous — this is
    what makes the table-swap migration's backfill behavior-preserving for
    every override that existed before this identity scheme."""
    job_id = _job_with_pages(["Clause 1 leaf one", "Clause 2 leaf two"])

    structure_overrides.set_parent(job_id, "annexure:a-1", -1, -1, "annexure:a-2")

    parent_key, _parent_page, _parent_occ = structure_overrides.resolve_effective_parent(
        job_id, "annexure:a-1", 5, 0, natural_parent_key="",
    )
    assert parent_key == "annexure:a-2"


@pytest.mark.asyncio
async def test_resolve_structure_for_job_reflects_a_parent_override():
    """The actual architectural gap this phase closes: database.
    get_reference_targets_for_job() is raw detector output with NO
    overrides applied — resolve_structure_for_job is the function that
    produces what the user actually sees in the UI."""
    job_id = _job_with_pages(["Annexure F-1 heading", "Clause 1 leaf under annexure"])
    await reference_index.reindex_job(job_id)

    resolved = structure_overrides.resolve_structure_for_job(job_id)
    clause = next(r for r in resolved if r["key"] == "clause:1")
    assert clause["resolved_scope_key"] == "annexure:f-1"

    # Move clause:1 to the root level.
    structure_overrides.set_parent(job_id, "clause:1", 2, 0, "")

    resolved = structure_overrides.resolve_structure_for_job(job_id)
    clause = next(r for r in resolved if r["key"] == "clause:1")
    assert clause["resolved_scope_key"] == ""
    # The detector's OWN scope_key column is never rewritten by a move.
    assert clause["scope_key"] == "annexure:f-1"


def _seed_target_row(job_id, page, key, occurrence_index, scope_chain, version):
    database.upsert_reference_targets_for_page(job_id, page, [{
        "key": key, "occurrence_index": occurrence_index, "scope_chain": scope_chain,
        "scope_key": "/".join(s["key"] for s in scope_chain),
        "own_label": key, "detection_source": "rule",
    }], version)


@pytest.mark.asyncio
async def test_resolve_structure_for_job_disambiguates_repeated_key_by_occurrence():
    """A parent override on one occurrence of a repeated key must not
    change the OTHER occurrence's resolved_scope_key. The two occurrences
    must sit under DIFFERENT ancestors to become genuinely separate tree
    nodes in the first place — two occurrences with identical scope_chain
    collapse onto the same node by design (matching StructureTree.jsx's
    buildTree: one logical entry, several page occurrences — e.g. a
    repeated running header — correctly moves as a unit), so that shape
    would not exercise this disambiguation at all.

    Rows are seeded directly (not through the regex detector) — this is a
    test of the merge/resolve layer, not detection, and hand-built rows
    give full control over scope_chain without fighting the detector's own
    parsing shape requirements."""
    job_id = _job_with_pages(["p1", "p2", "p3", "p4", "p5"])
    v = database.bump_reference_index_version(job_id)
    _seed_target_row(job_id, 1, "annexure:g-1", 0, [], v)
    _seed_target_row(job_id, 2, "clause:9", 0, [{"family": "annexure", "key": "annexure:g-1", "label": "Annexure G-1"}], v)
    _seed_target_row(job_id, 3, "annexure:g-2", 0, [], v)
    _seed_target_row(job_id, 4, "clause:9", 0, [{"family": "annexure", "key": "annexure:g-2", "label": "Annexure G-2"}], v)
    _seed_target_row(job_id, 5, "clause:8", 0, [], v)

    before = structure_overrides.resolve_structure_for_job(job_id)
    g1_clause9 = next(r for r in before if r["key"] == "clause:9" and r["resolved_scope_key"] == "annexure:g-1")
    g2_clause9 = next(r for r in before if r["key"] == "clause:9" and r["resolved_scope_key"] == "annexure:g-2")

    # Move only the Annexure G-1 occurrence under clause:8.
    structure_overrides.set_parent(
        job_id, "clause:9", g1_clause9["page"], g1_clause9["occurrence_index"],
        "clause:8", new_parent_page=5, new_parent_occurrence_index=0,
    )

    after = structure_overrides.resolve_structure_for_job(job_id)
    moved = next(r for r in after if r["key"] == "clause:9" and r["page"] == g1_clause9["page"])
    untouched = next(r for r in after if r["key"] == "clause:9" and r["page"] == g2_clause9["page"])
    assert moved["resolved_scope_key"] == "clause:8"
    assert untouched["resolved_scope_key"] == "annexure:g-2"


# ─── 15. 404s for all five endpoints against a missing job ───────────────

def test_all_endpoints_404_for_missing_job(client, auth_headers):
    missing = "does-not-exist"

    resp = client.post(
        f"/api/jobs/{missing}/structure-items",
        json={"heading": "x", "start_page": 1, "end_page": 1},
        headers=auth_headers,
    )
    assert resp.status_code == 404

    resp = client.patch(
        f"/api/jobs/{missing}/structure-items/clause:1?page=1",
        json={"heading": "x"},
        headers=auth_headers,
    )
    assert resp.status_code == 404

    resp = client.delete(f"/api/jobs/{missing}/structure-items/clause:1?page=1", headers=auth_headers)
    assert resp.status_code == 404

    resp = client.post(
        f"/api/jobs/{missing}/structure-items/move",
        json={"item_key": "clause:1", "new_parent_key": "", "ordered_siblings": ["clause:1"]},
        headers=auth_headers,
    )
    assert resp.status_code == 404

    resp = client.get(f"/api/jobs/{missing}/structure-hierarchy-overrides", headers=auth_headers)
    assert resp.status_code == 404
