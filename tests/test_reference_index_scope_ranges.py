"""Regression tests for Part B's compute_scope_ranges() — deriving each
container's page span from the already-persisted per-page end_of_page_scope
stack, now that Part A's classification fixes make nested_stack trustworthy."""

import uuid

import pytest

import database
import reference_index
from reference_index import build_targets_for_page, compute_scope_ranges


def _make_job(total_pages):
    job_id = str(uuid.uuid4())
    database.create_job(
        job_id=job_id, filename="test.pdf", file_path="/tmp/does-not-exist.pdf", file_type="pdf",
        total_pages=total_pages,
    )
    return job_id


def _feed_pages(job_id, pages_text):
    """Runs build_targets_for_page page-by-page (seeding each from the
    previous page's own new_scope, exactly like _process_single_page does)
    and persists each page's resulting scope — the same data shape
    compute_scope_ranges reads."""
    seed_scope = None
    for page_num, text in enumerate(pages_text, start=1):
        _candidates, new_scope = build_targets_for_page(text, seed_scope, has_next_page=page_num < len(pages_text))
        database.set_end_of_page_scope(job_id, page_num, new_scope)
        database.upsert_page(job_id, page_num, status="success", text=text)
        seed_scope = new_scope


def _by_key(ranges, key):
    matches = [r for r in ranges if r["key"] == key]
    assert len(matches) == 1, f"expected exactly one range for {key!r}, got {len(matches)}: {matches}"
    return matches[0]


def test_annexure_spans_several_pages_including_pages_with_no_targets_of_its_own():
    job_id = _make_job(4)
    _feed_pages(job_id, [
        "Annexure A-2\n\nCONTRACT AGREEMENT BETWEEN THE PARTIES\n",
        "Just plain prose with no detected targets on this page at all.\n",
        "Clause 1 Definitions.\n",
        "Clause 2 Payment terms.\n",
    ])
    ranges = compute_scope_ranges(job_id, total_pages=4)
    annexure = _by_key(ranges, "annexure:a-2")
    assert annexure["start_page"] == 1
    assert annexure["end_page"] == 4
    assert annexure["depth"] == 0


def test_nested_article_range_is_narrower_than_its_annexure():
    job_id = _make_job(4)
    _feed_pages(job_id, [
        "Annexure A-1\n\nCover page.\n",
        "Article 2. Payment Terms\n\nClause 1 Text.\n",
        "Article 2. Payment Terms\n\nClause 2 More text.\n",
        # Deliberately avoids the word "annexure" (or any other
        # detect_subdoc_trigger keyword) within its first 15 chars — that
        # would fire the *generic* sub-document trigger on ordinary prose
        # mentioning the word, popping rank 0 for an unrelated reason.
        "Back to plain prose, the article has ended implicitly.\n",
    ])
    ranges = compute_scope_ranges(job_id, total_pages=4)
    annexure = _by_key(ranges, "annexure:a-1")
    article = _by_key(ranges, "article:2")
    # Neither ever closes just because the trailing page doesn't re-mention
    # it — an active container (at any depth) stays active until something
    # *else* actually replaces/pops it, not merely because a later page has
    # no fresh match at all. So both extend through the trailing prose page.
    # This test's real point is depth/scope_key nesting, not range width —
    # the disjoint-occurrence and unprocessed-tail tests above/below cover
    # range-narrowing specifically.
    assert annexure["start_page"] == 1
    assert annexure["end_page"] == 4
    assert article["start_page"] == 2
    assert article["end_page"] == 4
    assert article["depth"] == 1
    assert article["scope_key"] == "annexure:a-1"


def test_disjoint_occurrences_of_the_same_key_become_two_separate_ranges():
    job_id = _make_job(4)
    _feed_pages(job_id, [
        "Schedule 2: Pricing\n\nClause 1 First occurrence.\n",
        "Annexure A-9\n\nSomething else entirely closes Schedule 2.\n",
        "Clause 2 Still under Annexure A-9.\n",
        "Schedule 2: Pricing Again\n\nClause 3 Second, disjoint occurrence.\n",
    ])
    ranges = compute_scope_ranges(job_id, total_pages=4)
    schedule_ranges = [r for r in ranges if r["key"] == "schedule:2"]
    assert len(schedule_ranges) == 2
    schedule_ranges.sort(key=lambda r: r["start_page"])
    assert (schedule_ranges[0]["start_page"], schedule_ranges[0]["end_page"]) == (1, 1)
    assert (schedule_ranges[1]["start_page"], schedule_ranges[1]["end_page"]) == (4, 4)


def test_unprocessed_trailing_pages_close_the_range_early_not_at_total_pages():
    """A job that's only partway through processing (or a total_pages that
    outruns what's actually been OCR'd) must not report a range as if it
    extends all the way to total_pages — only to the last page actually
    known to still have it active."""
    job_id = _make_job(5)
    _feed_pages(job_id, [
        "Annexure A-1\n\nCover.\n",
        "Clause 1 Under the annexure.\n",
        "Clause 2 Still under the annexure.\n",
        # pages 4-5 deliberately never processed (no end_of_page_scope row)
    ])
    ranges = compute_scope_ranges(job_id, total_pages=5)
    annexure = _by_key(ranges, "annexure:a-1")
    assert annexure["end_page"] == 3


def test_gap_before_the_first_detected_container_is_a_synthetic_unclassified_range():
    job_id = _make_job(4)
    _feed_pages(job_id, [
        "Front matter with no detected structure at all.\n",
        "More front matter, still nothing detected.\n",
        "Annexure A-1\n\nCover page.\n",
        "Clause 1 Under the annexure.\n",
    ])
    ranges = compute_scope_ranges(job_id, total_pages=4)
    unclassified = [r for r in ranges if r["key"] == "unclassified"]
    assert len(unclassified) == 1
    assert unclassified[0]["synthetic"] is True
    assert unclassified[0]["family"] == "unclassified"
    assert (unclassified[0]["start_page"], unclassified[0]["end_page"]) == (1, 2)


def test_low_confidence_match_does_not_reopen_an_unclassified_gap():
    """Regression test for the corrected, fully stack-neutral design (§11):
    a low-confidence container match (chained_citation) must neither pop
    nor push — confirms it does NOT create a second "unclassified" gap
    after a real container, since the real container legitimately stays
    active (the flagged match is noise *within* it, not proof it ended).
    A second, later gap genuinely cannot occur under this design once any
    rank-0 container has ever been pushed — only a real replacement
    container can end one, and that's never "nothing," so "before the
    first container" is the only gap shape that can occur — see the
    single-gap test above."""
    job_id = _make_job(4)
    _feed_pages(job_id, [
        "Front matter before any container.\n",
        "Annexure A-1\n\nCover.\n",
        "Annexure 5 and Schedule 9 apply here.\n",  # chained_citation — flagged, but neither pops nor pushes
        "Back matter, still logically under Annexure A-1.\n",
    ])
    ranges = compute_scope_ranges(job_id, total_pages=4)
    unclassified = [r for r in ranges if r["key"] == "unclassified"]
    assert len(unclassified) == 1
    assert (unclassified[0]["start_page"], unclassified[0]["end_page"]) == (1, 1)
    annexure = _by_key(ranges, "annexure:a-1")
    assert (annexure["start_page"], annexure["end_page"]) == (2, 4)
    assert not any(r["key"] == "annexure:5" for r in ranges), \
        "a low-confidence match must never itself appear as a scope-range ancestor"


def test_scope_ranges_are_cached_until_the_reference_index_version_changes():
    job_id = _make_job(2)
    _feed_pages(job_id, ["Annexure A-1\n\nCover.\n", "Clause 1 Text.\n"])
    first = compute_scope_ranges(job_id, total_pages=2)

    # Mutate the underlying data directly, WITHOUT bumping
    # reference_index_version — a cached call must not see this change.
    database.set_end_of_page_scope(job_id, 2, {"stack": [], "boundary_ordinal": 0, "label_occurrence_counts": {}})
    cached = compute_scope_ranges(job_id, total_pages=2)
    assert cached == first, "must return the cached result when reference_index_version hasn't changed"

    database.bump_reference_index_version(job_id)
    recomputed = compute_scope_ranges(job_id, total_pages=2)
    assert recomputed != first, "must recompute once reference_index_version changes"


def test_get_end_of_page_scope_bulk_matches_the_per_page_lookup():
    job_id = _make_job(3)
    _feed_pages(job_id, ["Annexure A-1\n\nCover.\n", "Clause 1 Text.\n", "Clause 2 More text.\n"])
    bulk = database.get_end_of_page_scope_bulk(job_id)
    for page_num in (1, 2, 3):
        assert bulk[page_num] == database.get_end_of_page_scope(job_id, page_num)


@pytest.fixture(autouse=True)
def _reset_scope_ranges_cache():
    reference_index._scope_ranges_cache = {}
    yield
    reference_index._scope_ranges_cache = {}
