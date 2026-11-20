"""Round-trip tests for the scope_chain column added to ocr_reference_targets
— the structured-ancestor-chain facet that sits alongside the existing
flattened scope_key/scope_label strings. subdoc_label/subdoc_index also
exist as columns (kept for lower-risk migration rather than dropped) but are
no longer populated by any real candidate — that information now lives as
scope_chain[0] instead, once the sub-document tier was unified into the same
ancestor stack as article/section."""

import uuid

import database


def _job(total_pages=1):
    job_id = str(uuid.uuid4())
    database.create_job(
        job_id=job_id, filename="t.pdf", file_path="/tmp/x.pdf", file_type="pdf",
        engine="unlimited", total_pages=total_pages,
    )
    database.upsert_page(job_id, 1, status="success", text="Clause 4.2 text", confidence=0.9)
    return job_id


def test_scope_chain_round_trips_through_upsert_and_fetch():
    job_id = _job()
    target = {
        "key": "clause:4.2",
        "occurrence_index": 0,
        "anchor_id": "xref-target-clause-4-2-p1-0",
        "snippet": "Clause 4.2 text",
        "truncated": False,
        "continues_on_page": None,
        "scope_key": "0:article:2/section:3",
        "scope_label": "Article 2 › Section 3",
        "scope_provisional": False,
        "collapsed_key": "clause:42",
        "fingerprint": "Clause 4.2 text",
        "scope_chain": [
            {"family": "article", "key": "article:2", "label": "Article 2"},
            {"family": "section", "key": "section:3", "label": "Section 3"},
        ],
    }
    version = database.bump_reference_index_version(job_id)
    database.upsert_reference_targets_for_page(job_id, 1, [target], version)

    delta = database.get_reference_targets_since(job_id, 0)
    row = delta[1]["clause:4.2"][0]
    assert row["scope_chain"] == target["scope_chain"]


def test_own_label_round_trips_through_upsert_and_fetch():
    # own_label is the numbered heading's own captured display text (e.g.
    # "2.0 CONTRACT DOCUMENTS", or just the bare number "2.1" for a
    # headerless numbered clause) — never sent for any other family.
    job_id = _job()
    target = {
        "key": "numheading:2.0",
        "occurrence_index": 0,
        "anchor_id": "xref-target-numheading-2-0-p1-0",
        "snippet": "2.0 CONTRACT DOCUMENTS",
        "truncated": False,
        "continues_on_page": None,
        "scope_key": "annexure:a-1",
        "scope_label": "Annexure A-1",
        "scope_provisional": False,
        "collapsed_key": "numheading:20",
        "fingerprint": "2.0 CONTRACT DOCUMENTS",
        "scope_chain": [{"family": "annexure", "key": "annexure:a-1", "label": "Annexure A-1"}],
        "own_label": "2.0 CONTRACT DOCUMENTS",
    }
    version = database.bump_reference_index_version(job_id)
    database.upsert_reference_targets_for_page(job_id, 1, [target], version)

    delta = database.get_reference_targets_since(job_id, 0)
    row = delta[1]["numheading:2.0"][0]
    assert row["own_label"] == "2.0 CONTRACT DOCUMENTS"


def test_candidate_missing_the_new_fields_defaults_to_empty_chain_and_none():
    # This is now the NORMAL shape for every real candidate — reference_index.py
    # no longer produces subdoc_label/subdoc_index at all (that information is
    # scope_chain[0] now); upsert's own `.get()` defaults must still produce a
    # clean, consistent shape for these two now-unused columns, not a crash.
    job_id = _job()
    target = {
        "key": "page:5",
        "occurrence_index": 0,
        "anchor_id": "page-block-1",
        "snippet": "",
        "truncated": False,
        "continues_on_page": None,
        "scope_key": None,
        "scope_label": "Printed page 5",
        "scope_provisional": False,
        "collapsed_key": "page:5",
        "fingerprint": "",
    }
    version = database.bump_reference_index_version(job_id)
    database.upsert_reference_targets_for_page(job_id, 1, [target], version)

    delta = database.get_reference_targets_since(job_id, 0)
    row = delta[1]["page:5"][0]
    assert row["scope_chain"] == []
    assert row["subdoc_label"] is None
    assert row["subdoc_index"] is None
    assert row["own_label"] is None


def test_genuinely_null_scope_chain_in_db_deserializes_safely_not_a_crash():
    # A row written before this migration ships has scope_chain/subdoc_label/
    # subdoc_index as a real SQL NULL (ALTER TABLE ADD COLUMN backfills NULL,
    # not '[]') — bypass upsert_reference_targets_for_page's own `.get() or []`
    # default to simulate that directly, matching get_end_of_page_scope's
    # existing precedent for this same nullable-JSON-column situation.
    job_id = _job()
    version = database.bump_reference_index_version(job_id)
    database.upsert_reference_targets_for_page(job_id, 1, [], version)  # sets up touched_version, clears page 1

    conn = database.get_db_connection()
    conn.execute("""
        INSERT INTO ocr_reference_targets (
            job_id, key, page, occurrence_index, anchor_id, snippet, truncated,
            continues_on_page, scope_key, scope_label, scope_provisional,
            collapsed_key, fingerprint, written_at_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (job_id, "clause:1", 1, 0, "anchor-1", "snippet", 0, None, "0:", "", 0, "clause:1", "", version))
    conn.commit()
    conn.close()

    delta = database.get_reference_targets_since(job_id, 0)
    row = delta[1]["clause:1"][0]
    assert row["scope_chain"] == []
