"""compute_scope_ranges-level regression tests for the three bugs found on a
real 100-page job (Round 7): containers closing one page early when a
same-depth successor appears on a later page; a heading whose own AI-
reported end_page equals its own start_page vanishing entirely from ranges;
and interior same-page siblings being invisible except for the last one.
All three stem from compute_scope_ranges only ever seeing an end-of-page
snapshot — `closed_this_page` (apply_heading_events_to_scope) is what makes
same-page supersession visible to it at all.

These tests drive compute_scope_ranges directly off hand-built
end_of_page_scope rows (bypassing the AI/rule detectors entirely) so each
bug's exact shape can be reproduced in isolation.
"""

import uuid

import database
import reference_index


def _job(total_pages):
    job_id = str(uuid.uuid4())
    database.create_job(
        job_id=job_id, filename="t.pdf", file_path="/tmp/x.pdf", file_type="pdf",
        engine="unlimited", total_pages=total_pages,
    )
    for i in range(1, total_pages + 1):
        database.upsert_page(job_id, i, status="success", text=f"page {i}", confidence=0.95)
    database.set_job_status(job_id, "completed")
    return job_id


def _scope(stack, closed_this_page=None):
    return {
        "stack": stack, "boundary_ordinal": 0, "label_occurrence_counts": {},
        "last_bare_integer": None, "last_letter_ordinal": None,
        "closed_this_page": closed_this_page or [],
    }


def _entry(family, key, label):
    return {"family": family, "key": key, "label": label}


def test_container_superseded_on_a_later_page_gets_credit_for_that_page():
    # FACTS opens page 1, survives unchanged through page 2, then on page 3
    # a NEW depth-0 sibling appears — with the closure record present, FACTS
    # must be credited with page 3 too (41-42 in the real job), not stop at
    # page 2 (the old last_processed_page-based off-by-one).
    job_id = _job(3)
    database.set_end_of_page_scope(job_id, 1, _scope([_entry("subdoc", "subdoc:1", "FACTS")]))
    database.set_end_of_page_scope(job_id, 2, _scope([_entry("subdoc", "subdoc:1", "FACTS")]))
    database.set_end_of_page_scope(job_id, 3, _scope(
        [_entry("subdoc", "subdoc:2", "(A) something")],
        closed_this_page=[{
            "family": "subdoc", "key": "subdoc:1", "label": "FACTS",
            "depth": 0, "scope_key": "", "start_page": 3, "end_page": 3,
        }],
    ))

    ranges = reference_index.compute_scope_ranges(job_id, total_pages=3)
    facts = next(r for r in ranges if r["key"] == "subdoc:1")
    assert (facts["start_page"], facts["end_page"]) == (1, 3)
    successor = next(r for r in ranges if r["key"] == "subdoc:2")
    assert successor["start_page"] == 3


def test_self_closing_single_page_heading_gets_its_own_range_instead_of_vanishing():
    # Ground (XVI) is the only heading on page 5, self-declared as closing
    # on that same page — it never appears in ANY persisted "stack" snapshot
    # (before or after), so without closed_this_page it would be invisible
    # to compute_scope_ranges entirely, even though it's a real heading.
    job_id = _job(6)
    database.set_end_of_page_scope(job_id, 4, _scope([_entry("subdoc", "subdoc:1", "GROUNDS OF APPEAL")]))
    database.set_end_of_page_scope(job_id, 5, _scope(
        [_entry("subdoc", "subdoc:1", "GROUNDS OF APPEAL")],  # (XVI) never survives into this page's own stack
        closed_this_page=[{
            "family": "subdoc", "key": "subdoc:2", "label": "(XVI) a ground",
            "depth": 1, "scope_key": "subdoc:1", "start_page": 5, "end_page": 5,
        }],
    ))
    database.set_end_of_page_scope(job_id, 6, _scope([_entry("subdoc", "subdoc:1", "GROUNDS OF APPEAL")]))

    ranges = reference_index.compute_scope_ranges(job_id, total_pages=6)
    ground_16 = next((r for r in ranges if r["key"] == "subdoc:2"), None)
    assert ground_16 is not None, "self-closing single-page heading must not vanish from ranges"
    assert (ground_16["start_page"], ground_16["end_page"]) == (5, 5)
    assert ground_16["depth"] == 1
    assert ground_16["scope_key"] == "subdoc:1"


def test_interior_same_page_siblings_each_get_their_own_page_level_range():
    # (A), (B), (C) all land on page 7 — only (C) survives into page 7's own
    # persisted stack; (A) and (B) must each still get their own 7-7 range,
    # not just disappear because they were overwritten within the same page.
    job_id = _job(8)
    database.set_end_of_page_scope(job_id, 6, _scope([_entry("subdoc", "subdoc:1", "FACTS")]))
    database.set_end_of_page_scope(job_id, 7, _scope(
        [_entry("subdoc", "subdoc:4", "(C) third")],
        closed_this_page=[
            {"family": "subdoc", "key": "subdoc:1", "label": "FACTS",
             "depth": 0, "scope_key": "", "start_page": 7, "end_page": 7},
            {"family": "subdoc", "key": "subdoc:2", "label": "(A) first",
             "depth": 0, "scope_key": "", "start_page": 7, "end_page": 7},
            {"family": "subdoc", "key": "subdoc:3", "label": "(B) second",
             "depth": 0, "scope_key": "", "start_page": 7, "end_page": 7},
        ],
    ))
    database.set_end_of_page_scope(job_id, 8, _scope([_entry("subdoc", "subdoc:4", "(C) third")]))

    ranges = reference_index.compute_scope_ranges(job_id, total_pages=8)
    by_key = {r["key"]: r for r in ranges}
    assert (by_key["subdoc:1"]["start_page"], by_key["subdoc:1"]["end_page"]) == (6, 7), (
        "FACTS, carried in from an earlier page, must close AT page 7 (not one page early)"
    )
    assert (by_key["subdoc:2"]["start_page"], by_key["subdoc:2"]["end_page"]) == (7, 7)
    assert (by_key["subdoc:3"]["start_page"], by_key["subdoc:3"]["end_page"]) == (7, 7)
    assert by_key["subdoc:4"]["start_page"] == 7  # the survivor, continuing forward normally
