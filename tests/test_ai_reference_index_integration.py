"""End-to-end integration test for ai_reference_index.reindex_job_with_ai
against a real (temp) DB, with a stubbed StructureIndexer so no network
call happens. Covers the plan's "fallback-matrix" verification: one chunk
succeeds via AI, the next fails and falls back to rules, and a container
opened in the AI chunk that keeps going into the fallback chunk still gets
one continuous range — the core cross-chunk-boundary correctness property.
"""

import uuid

import pytest

import ai_indexing
import ai_reference_index
import config
import database
import reference_index
import task_queue
from ai_indexing.client import ChunkOutcome, ChunkStructureResult
from ai_indexing.schema import AIHeading


@pytest.fixture(autouse=True)
def _ai_env_configured(monkeypatch):
    # Env-only design: no DB credential row to create — just point config at
    # a fake key. The real provider call is stubbed per-test regardless.
    monkeypatch.setattr(config, "AI_INDEX_API_KEY", "AIzaFakeTestKey1234")
    monkeypatch.setattr(config, "AI_INDEX_PROVIDER", "gemini")
    monkeypatch.setattr(config, "AI_INDEX_MODEL", "gemini-2.5-flash")
    yield


def _job_with_ai_pages(pages_text, chunk_pages=2, pleading_points_via_ai=None):
    job_id = str(uuid.uuid4())
    engine_params = {"ai_indexing_enabled": True, "ai_index_chunk_pages": chunk_pages}
    if pleading_points_via_ai is not None:
        engine_params["ai_index_pleading_points_via_ai"] = pleading_points_via_ai
    database.create_job(
        job_id=job_id, filename="t.pdf", file_path="/tmp/x.pdf", file_type="pdf",
        engine="unlimited", total_pages=len(pages_text),
        engine_params=engine_params,
    )
    for i, text in enumerate(pages_text, start=1):
        database.upsert_page(job_id, i, status="success", text=text, confidence=0.95)
    database.set_job_status(job_id, "completed")
    return job_id


@pytest.mark.asyncio
async def test_fallback_matrix_ai_then_rule_with_continuous_range(monkeypatch):
    # Chunk 1 (pages 1-2): AI succeeds, opens Annexure A-1 > 3.0 CONDITIONS.
    # Chunk 2 (pages 3-4): AI fails -> falls back to rules; plain body text,
    # no new heading, so 3.0 CONDITIONS must stay open straight across the
    # chunk boundary with no fragmentation.
    monkeypatch.setattr(config, "AI_INDEX_CHUNK_OVERLAP_PAGES", 0)
    job_id = _job_with_ai_pages([
        "ANNEXURE A-1\n\nsome intro text",
        "3.0 CONDITIONS AND COVENANTS\n\nfirst body paragraph",
        "more body text continuing the same section",
        "even more body text, same section, no new heading",
    ], chunk_pages=2)

    call_count = {"n": 0}

    async def _fake_extract(self, chunk, *, carry_context=None, document_hint="", cancel_check=None,
                             include_pleading_points=False):
        call_count["n"] += 1
        if chunk.page_nums == [1, 2]:
            headings = [
                AIHeading(heading_text="ANNEXURE A-1", start_page=1, level=1, end_page=None,
                          still_open_at_chunk_end=True),
                AIHeading(heading_text="3.0 CONDITIONS AND COVENANTS", start_page=2, level=2,
                          parent_ref="ANNEXURE A-1", end_page=None, still_open_at_chunk_end=True),
            ]
            return ChunkStructureResult(outcome=ChunkOutcome.OK, page_nums=chunk.page_nums,
                                         headings=headings, provider="gemini", model="gemini-2.5-flash")
        # Chunk 2: simulate a hard failure -> caller must fall back to rules.
        return ChunkStructureResult(outcome=ChunkOutcome.FAILED, page_nums=chunk.page_nums,
                                     error_kind="remote", error_message="simulated provider outage")

    monkeypatch.setattr(ai_indexing.StructureIndexer, "extract_structure_for_chunk", _fake_extract)

    result = await ai_reference_index.reindex_job_with_ai(job_id)
    assert result["status"] == "done"
    assert call_count["n"] == 2

    delta = database.get_reference_targets_since(job_id, 0)
    assert delta[1]["annexure:a-1"][0]["detection_source"] == "ai"
    assert delta[2]["numheading:3.0"][0]["detection_source"] == "ai"
    # Pages 3-4 had no headings at all (rule-based, no match) -> no rows,
    # but they must still have persisted a carried-forward scope (checked
    # via compute_scope_ranges below, not via ocr_reference_targets rows).

    report = database.get_ai_index_report(job_id)
    assert report["status"] == "done"
    assert len(report["chunks"]) == 2
    assert report["chunks"][0]["status"] == "ok"
    assert report["chunks"][1]["status"] == "fallback"

    ranges = reference_index.compute_scope_ranges(job_id, total_pages=4)
    conditions_range = next(r for r in ranges if r["key"] == "numheading:3.0")
    assert conditions_range["start_page"] == 2
    assert conditions_range["end_page"] == 4, (
        "3.0 CONDITIONS must stay open across the AI->rule fallback chunk "
        "boundary with no fragmentation — the core cross-chunk-boundary "
        "correctness property."
    )
    annexure_range = next(r for r in ranges if r["key"] == "annexure:a-1")
    assert annexure_range["start_page"] == 1
    assert annexure_range["end_page"] == 4


@pytest.mark.asyncio
async def test_same_page_duplicate_heading_report_is_deduped_to_one_event(monkeypatch):
    """Confirmed real bug (job bb417471, page 28): the model occasionally
    reports the same real heading twice in one chunk response for the same
    page, the two entries differing only in trailing truncation. Without
    dedup, both survive as two separate "occurrences" of the same key on
    that page instead of being recognized as one real heading."""
    job_id = _job_with_ai_pages([
        "ANNEXURE A-1\n\nintro",
        "3.0 CONDITIONS AND COVENANTS\n\nFirst body paragraph text here for grounding purposes.",
    ], chunk_pages=2)

    async def _fake_extract(self, chunk, *, carry_context=None, document_hint="", cancel_check=None,
                             include_pleading_points=False):
        headings = [
            AIHeading(heading_text="ANNEXURE A-1", start_page=1, level=1, still_open_at_chunk_end=True),
            AIHeading(heading_text="3.0 CONDITIONS AND COVENANTS", start_page=2, level=2,
                      parent_ref="ANNEXURE A-1", still_open_at_chunk_end=True),
            AIHeading(heading_text="3.0 CONDITIONS AND COVENANTS", start_page=2, level=2,
                      parent_ref="ANNEXURE A-1", still_open_at_chunk_end=True),
        ]
        return ChunkStructureResult(outcome=ChunkOutcome.OK, page_nums=chunk.page_nums,
                                     headings=headings, provider="gemini", model="gemini-2.5-flash")

    monkeypatch.setattr(ai_indexing.StructureIndexer, "extract_structure_for_chunk", _fake_extract)

    result = await ai_reference_index.reindex_job_with_ai(job_id)
    assert result["status"] == "done"

    delta = database.get_reference_targets_since(job_id, 0)
    assert len(delta[2]["numheading:3.0"]) == 1


@pytest.mark.asyncio
async def test_two_different_headings_on_one_page_are_never_wrongly_deduped(monkeypatch):
    """Regression guard for the dedup fix above — two genuinely DIFFERENT
    headings reported on the same page (not a repeat, not a truncated
    echo) must both survive."""
    job_id = _job_with_ai_pages([
        "ANNEXURE A-1\n\nintro",
        "3.0 CONDITIONS AND COVENANTS\n\nfirst body.\n\n4.0 SETTLEMENT OF DISPUTES\n\nsecond body.",
    ], chunk_pages=2)

    async def _fake_extract(self, chunk, *, carry_context=None, document_hint="", cancel_check=None,
                             include_pleading_points=False):
        headings = [
            AIHeading(heading_text="ANNEXURE A-1", start_page=1, level=1, still_open_at_chunk_end=True),
            AIHeading(heading_text="3.0 CONDITIONS AND COVENANTS", start_page=2, level=2,
                      parent_ref="ANNEXURE A-1", still_open_at_chunk_end=True),
            AIHeading(heading_text="4.0 SETTLEMENT OF DISPUTES", start_page=2, level=2,
                      parent_ref="ANNEXURE A-1", still_open_at_chunk_end=True),
        ]
        return ChunkStructureResult(outcome=ChunkOutcome.OK, page_nums=chunk.page_nums,
                                     headings=headings, provider="gemini", model="gemini-2.5-flash")

    monkeypatch.setattr(ai_indexing.StructureIndexer, "extract_structure_for_chunk", _fake_extract)

    result = await ai_reference_index.reindex_job_with_ai(job_id)
    assert result["status"] == "done"

    delta = database.get_reference_targets_since(job_id, 0)
    assert "numheading:3.0" in delta[2]
    assert "numheading:4.0" in delta[2]


@pytest.mark.asyncio
async def test_chunk_pages_override_wins_over_the_jobs_frozen_engine_params(monkeypatch):
    """Regression: a manual "Rebuild Cross-References" call (main.py) passes
    chunk_pages_override so it always uses the CURRENT settings value, not
    whatever ai_index_chunk_pages was frozen into this job's engine_params
    at creation time — confirmed bug where a settings-panel chunk-size
    change had zero effect on an already-existing job's manual rebuilds."""
    monkeypatch.setattr(config, "AI_INDEX_CHUNK_OVERLAP_PAGES", 0)  # keeps the chunk count predictable
    job_id = _job_with_ai_pages([
        "page one text", "page two text", "page three text", "page four text",
    ], chunk_pages=10)  # frozen engine_params says 10 — override must beat this

    async def _fake_extract(self, chunk, *, carry_context=None, document_hint="", cancel_check=None,
                             include_pleading_points=False):
        return ChunkStructureResult(outcome=ChunkOutcome.EMPTY, page_nums=chunk.page_nums,
                                     headings=[], provider="gemini", model="gemini-2.5-flash")

    monkeypatch.setattr(ai_indexing.StructureIndexer, "extract_structure_for_chunk", _fake_extract)

    result = await ai_reference_index.reindex_job_with_ai(job_id, chunk_pages_override=2)
    assert result["status"] == "done"

    report = database.get_ai_index_report(job_id)
    assert report["chunk_pages"] == 2
    assert len(report["chunks"]) == 2, "4 pages at chunk_pages=2 must produce 2 chunks, not 1 (frozen value)"


@pytest.mark.asyncio
async def test_paused_run_stops_between_chunks_and_resumes_without_reprocessing(monkeypatch):
    job_id = _job_with_ai_pages([
        "ANNEXURE A-1\n\nintro",
        "body page two",
        "3.0 CONDITIONS AND COVENANTS\n\nbody page three",
        "body page four",
    ], chunk_pages=1)  # 4 chunks of 1 page each, so we can pause mid-run

    processed_chunks = []

    async def _fake_extract(self, chunk, *, carry_context=None, document_hint="", cancel_check=None,
                             include_pleading_points=False):
        processed_chunks.append(chunk.page_nums[0])
        if chunk.page_nums[0] == 3:
            task_queue._paused_jobs.add(job_id)  # simulate a pause request arriving mid-run
        headings = []
        if chunk.page_nums[0] == 1:
            headings = [AIHeading(heading_text="ANNEXURE A-1", start_page=1, level=1, still_open_at_chunk_end=True)]
        return ChunkStructureResult(outcome=ChunkOutcome.OK if headings else ChunkOutcome.EMPTY,
                                     page_nums=chunk.page_nums, headings=headings,
                                     provider="gemini", model="gemini-2.5-flash")

    monkeypatch.setattr(ai_indexing.StructureIndexer, "extract_structure_for_chunk", _fake_extract)

    try:
        result = await ai_reference_index.reindex_job_with_ai(job_id)
        assert result["status"] == "paused"
        assert processed_chunks == [1, 2, 3]  # stopped before starting chunk 4

        report = database.get_ai_index_report(job_id)
        assert report["status"] == "paused"
        assert report["ai_last_persisted_page"] == 3

        # Resume: un-pause and re-invoke with no start_page — must pick up
        # from page 4, NOT reprocess 1-3.
        task_queue._paused_jobs.discard(job_id)
        result2 = await ai_reference_index.reindex_job_with_ai(job_id)
        assert result2["status"] == "done"
        assert processed_chunks == [1, 2, 3, 4], "resume must not reprocess already-completed chunks"
    finally:
        task_queue._paused_jobs.discard(job_id)


@pytest.mark.asyncio
async def test_single_page_retry_stops_early_when_scope_reconverges(monkeypatch):
    """The bounded-reprocessing fix from review: a targeted retry of ONE
    page must not walk to the end of the document when its re-detected
    scope matches what was already persisted."""
    job_id = _job_with_ai_pages([
        "ANNEXURE A-1\n\nintro",
        "body page two, unchanged",
        "body page three, unchanged",
    ], chunk_pages=1)

    async def _fake_extract(self, chunk, *, carry_context=None, document_hint="", cancel_check=None,
                             include_pleading_points=False):
        headings = []
        if chunk.page_nums[0] == 1:
            headings = [AIHeading(heading_text="ANNEXURE A-1", start_page=1, level=1, still_open_at_chunk_end=True)]
        return ChunkStructureResult(outcome=ChunkOutcome.OK if headings else ChunkOutcome.EMPTY,
                                     page_nums=chunk.page_nums, headings=headings,
                                     provider="gemini", model="gemini-2.5-flash")

    monkeypatch.setattr(ai_indexing.StructureIndexer, "extract_structure_for_chunk", _fake_extract)
    await ai_reference_index.reindex_job_with_ai(job_id)

    processed = []
    orig = ai_indexing.StructureIndexer.extract_structure_for_chunk

    async def _tracking_extract(self, chunk, **kw):
        processed.append(chunk.page_nums[0])
        return await _fake_extract(self, chunk, **kw)

    monkeypatch.setattr(ai_indexing.StructureIndexer, "extract_structure_for_chunk", _tracking_extract)

    # Retry page 2 — its text/scope outcome is unchanged, so this must stop
    # immediately rather than walking through page 3 as well.
    result = await ai_reference_index.reindex_job_with_ai(job_id, start_page=2)
    assert processed == [2]


@pytest.mark.asyncio
async def test_overlap_union_merge_recall_gain(monkeypatch):
    """Chunk 1 detects an incomplete set on its last page; chunk 2 detects a
    different incomplete set. Final persisted targets contain the union of both."""
    monkeypatch.setattr(config, "AI_INDEX_CHUNK_OVERLAP_PAGES", 1)
    job_id = _job_with_ai_pages([
        "ANNEXURE A-1\n\nintro",
        "3.0 SCOPE OF WORK\n\nscope text\n\n4.0 SETTLEMENT OF DISPUTES\n\ndisputes text",
        "more closing text",
    ], chunk_pages=2)

    async def _fake_extract(self, chunk, *, carry_context=None, document_hint="", cancel_check=None,
                             include_pleading_points=False):
        if chunk.page_nums == [1, 2]:
            # Chunk 1 catches 3.0 SCOPE but misses 4.0 DISPUTES
            headings = [
                AIHeading(heading_text="ANNEXURE A-1", start_page=1, level=1, still_open_at_chunk_end=True),
                AIHeading(heading_text="3.0 SCOPE OF WORK", start_page=2, level=2,
                          parent_ref="ANNEXURE A-1", still_open_at_chunk_end=True),
            ]
        elif chunk.page_nums == [2, 3]:
            # Chunk 2 catches 4.0 DISPUTES on page 2 but misses 3.0 SCOPE
            headings = [
                AIHeading(heading_text="4.0 SETTLEMENT OF DISPUTES", start_page=2, level=2,
                          parent_ref="c1", still_open_at_chunk_end=True),
            ]
        else:
            headings = []
        return ChunkStructureResult(outcome=ChunkOutcome.OK, page_nums=chunk.page_nums,
                                     headings=headings, provider="gemini", model="gemini-2.5-flash")

    monkeypatch.setattr(ai_indexing.StructureIndexer, "extract_structure_for_chunk", _fake_extract)

    result = await ai_reference_index.reindex_job_with_ai(job_id)
    assert result["status"] == "done"

    delta = database.get_reference_targets_since(job_id, 0)
    page_2_targets = delta[2]
    assert "numheading:3.0" in page_2_targets
    assert "numheading:4.0" in page_2_targets


@pytest.mark.asyncio
async def test_overlap_merge_ordering_preserves_document_order_despite_misleading_hint(monkeypatch):
    """Merged-in heading physically precedes pass-2's heading on the page, despite
    a misleading start_line_hint. Unconstrained ranking ensures proper block grounding."""
    monkeypatch.setattr(config, "AI_INDEX_CHUNK_OVERLAP_PAGES", 1)
    page_2_text = "1.0 FIRST SECTION\n\nparagraph 1\n\n2.0 SECOND SECTION\n\nparagraph 2"
    job_id = _job_with_ai_pages([
        "ANNEXURE A-1\n\nintro",
        page_2_text,
        "closing text",
    ], chunk_pages=2)

    async def _fake_extract(self, chunk, *, carry_context=None, document_hint="", cancel_check=None,
                             include_pleading_points=False):
        if chunk.page_nums == [1, 2]:
            # Chunk 1 catches 1.0 FIRST SECTION (physically at block 0), but with misleading high line hint
            headings = [
                AIHeading(heading_text="ANNEXURE A-1", start_page=1, level=1, still_open_at_chunk_end=True),
                AIHeading(heading_text="1.0 FIRST SECTION", start_page=2, level=2,
                          start_line_hint=100, parent_ref="ANNEXURE A-1", still_open_at_chunk_end=True),
            ]
        elif chunk.page_nums == [2, 3]:
            # Chunk 2 catches 2.0 SECOND SECTION (physically at block 2), with lower line hint
            headings = [
                AIHeading(heading_text="2.0 SECOND SECTION", start_page=2, level=2,
                          start_line_hint=1, parent_ref="c1", still_open_at_chunk_end=True),
            ]
        else:
            headings = []
        return ChunkStructureResult(outcome=ChunkOutcome.OK, page_nums=chunk.page_nums,
                                     headings=headings, provider="gemini", model="gemini-2.5-flash")

    monkeypatch.setattr(ai_indexing.StructureIndexer, "extract_structure_for_chunk", _fake_extract)

    result = await ai_reference_index.reindex_job_with_ai(job_id)
    assert result["status"] == "done"

    delta = database.get_reference_targets_since(job_id, 0)
    page_2_targets = delta[2]
    first_target = page_2_targets["numheading:1.0"][0]
    second_target = page_2_targets["numheading:2.0"][0]
    assert "ai_unlocated" not in first_target["heading_confidence_reasons"]
    assert "ai_unlocated" not in second_target["heading_confidence_reasons"]
    assert "1.0 FIRST SECTION" in first_target["snippet"]
    assert "2.0 SECOND SECTION" in second_target["snippet"]


@pytest.mark.asyncio
async def test_overlap_union_merge_no_silent_loss(monkeypatch):
    """Chunk 2 redo of overlap page returns fewer headings (simulating a worse pass).
    Union merge ensures chunk 1's heading is preserved."""
    monkeypatch.setattr(config, "AI_INDEX_CHUNK_OVERLAP_PAGES", 1)
    job_id = _job_with_ai_pages([
        "ANNEXURE A-1\n\nintro",
        "3.0 SCOPE OF WORK\n\nbody text",
        "closing text",
    ], chunk_pages=2)

    async def _fake_extract(self, chunk, *, carry_context=None, document_hint="", cancel_check=None,
                             include_pleading_points=False):
        if chunk.page_nums == [1, 2]:
            headings = [
                AIHeading(heading_text="ANNEXURE A-1", start_page=1, level=1, still_open_at_chunk_end=True),
                AIHeading(heading_text="3.0 SCOPE OF WORK", start_page=2, level=2,
                          parent_ref="ANNEXURE A-1", still_open_at_chunk_end=True),
            ]
        else:
            # Chunk 2 misses 3.0 SCOPE on page 2 completely
            headings = []
        return ChunkStructureResult(outcome=ChunkOutcome.OK if headings else ChunkOutcome.EMPTY,
                                     page_nums=chunk.page_nums, headings=headings,
                                     provider="gemini", model="gemini-2.5-flash")

    monkeypatch.setattr(ai_indexing.StructureIndexer, "extract_structure_for_chunk", _fake_extract)

    result = await ai_reference_index.reindex_job_with_ai(job_id)
    assert result["status"] == "done"

    delta = database.get_reference_targets_since(job_id, 0)
    assert "numheading:3.0" in delta[2]


@pytest.mark.asyncio
async def test_rule_fallback_never_overwrites_ai_overlap_result(monkeypatch):
    """Chunk 1 succeeds via AI on pages 1-4; chunk 2 (overlap 3-4 + new 5-6) falls back
    to rules. Pages 3-4 are untouched by fallback, and scope continuity propagates to 5-6."""
    monkeypatch.setattr(config, "AI_INDEX_CHUNK_OVERLAP_PAGES", 2)
    job_id = _job_with_ai_pages([
        "page 1 intro",
        "page 2 body",
        "page 3 body",
        "ANNEXURE A-1\n\nheading on page 4",
        "page 5 continuation body text",
        "page 6 ending body text",
    ], chunk_pages=4)

    async def _fake_extract(self, chunk, *, carry_context=None, document_hint="", cancel_check=None,
                             include_pleading_points=False):
        if chunk.page_nums == [1, 2, 3, 4]:
            headings = [
                AIHeading(heading_text="ANNEXURE A-1", start_page=4, level=1, still_open_at_chunk_end=True),
            ]
            return ChunkStructureResult(outcome=ChunkOutcome.OK, page_nums=chunk.page_nums,
                                         headings=headings, provider="gemini", model="gemini-2.5-flash")
        # Chunk 2 fails and falls back to rules
        return ChunkStructureResult(outcome=ChunkOutcome.FAILED, page_nums=chunk.page_nums,
                                     error_kind="timeout", error_message="timeout")

    monkeypatch.setattr(ai_indexing.StructureIndexer, "extract_structure_for_chunk", _fake_extract)

    result = await ai_reference_index.reindex_job_with_ai(job_id)
    assert result["status"] == "done"

    delta = database.get_reference_targets_since(job_id, 0)
    # Page 4 AI heading is preserved with source "ai"
    assert delta[4]["annexure:a-1"][0]["detection_source"] == "ai"

    # Verify scope ranges continuity through pages 4-6
    ranges = reference_index.compute_scope_ranges(job_id, total_pages=6)
    annexure_range = next(r for r in ranges if r["key"] == "annexure:a-1")
    assert annexure_range["start_page"] == 4
    assert annexure_range["end_page"] == 6


@pytest.mark.asyncio
async def test_overlap_frontier_stays_monotonic(monkeypatch):
    """Frontier never decreases during an overlap run."""
    monkeypatch.setattr(config, "AI_INDEX_CHUNK_OVERLAP_PAGES", 2)
    job_id = _job_with_ai_pages([
        f"page {i} text" for i in range(1, 11)
    ], chunk_pages=4)

    frontier_history = []
    orig_set = database.set_reference_index_frontier

    def _tracking_set(jid, page_num):
        frontier_history.append(page_num)
        return orig_set(jid, page_num)

    monkeypatch.setattr(database, "set_reference_index_frontier", _tracking_set)

    async def _fake_extract(self, chunk, *, carry_context=None, document_hint="", cancel_check=None,
                             include_pleading_points=False):
        return ChunkStructureResult(outcome=ChunkOutcome.EMPTY, page_nums=chunk.page_nums,
                                     headings=[], provider="gemini", model="gemini-2.5-flash")

    monkeypatch.setattr(ai_indexing.StructureIndexer, "extract_structure_for_chunk", _fake_extract)

    result = await ai_reference_index.reindex_job_with_ai(job_id)
    assert result["status"] == "done"

    # Verify non-decreasing sequence
    for a, b in zip(frontier_history, frontier_history[1:]):
        assert b >= a, f"Frontier decreased from {a} to {b}"


@pytest.mark.asyncio
async def test_multi_chunk_targeted_retry_does_not_stop_early_at_overlap_seam(monkeypatch):
    """A retry spanning multiple chunks does not falsely trigger early stop on revisited
    overlap pages and continues to genuinely new pages."""
    monkeypatch.setattr(config, "AI_INDEX_CHUNK_OVERLAP_PAGES", 1)
    job_id = _job_with_ai_pages([
        "ANNEXURE A-1\n\nintro",
        "3.0 SCOPE\n\nbody 2",
        "4.0 DISPUTES\n\nbody 3",
        "5.0 CLOSING\n\nbody 4",
    ], chunk_pages=2)

    # First run to populate initial state
    async def _fake_extract_initial(self, chunk, *, carry_context=None, document_hint="", cancel_check=None,
                                     include_pleading_points=False):
        headings = []
        if 1 in chunk.page_nums:
            headings.append(AIHeading(heading_text="ANNEXURE A-1", start_page=1, level=1, still_open_at_chunk_end=True))
        if 2 in chunk.page_nums:
            headings.append(AIHeading(heading_text="3.0 SCOPE", start_page=2, level=2, parent_ref="c1", still_open_at_chunk_end=True))
        if 3 in chunk.page_nums:
            headings.append(AIHeading(heading_text="4.0 DISPUTES", start_page=3, level=2, parent_ref="c1", still_open_at_chunk_end=True))
        if 4 in chunk.page_nums:
            headings.append(AIHeading(heading_text="5.0 CLOSING", start_page=4, level=2, parent_ref="c1", still_open_at_chunk_end=True))
        return ChunkStructureResult(outcome=ChunkOutcome.OK, page_nums=chunk.page_nums,
                                     headings=headings, provider="gemini", model="gemini-2.5-flash")

    monkeypatch.setattr(ai_indexing.StructureIndexer, "extract_structure_for_chunk", _fake_extract_initial)
    await ai_reference_index.reindex_job_with_ai(job_id)

    # Now simulate retry from page 2, where retry chunks are:
    # Chunk 0: pages [2, 3] (changes scope on 2 and 3)
    # Chunk 1: pages [3, 4] (overlap 3 + new 4)
    # Revisit of page 3 must NOT stop early; page 4 will reconverge.
    processed_pages = []

    async def _fake_extract_retry(self, chunk, *, carry_context=None, document_hint="", cancel_check=None,
                                   include_pleading_points=False):
        processed_pages.extend(chunk.page_nums)
        # Modify page 2 & 3 headings
        headings = [
            AIHeading(heading_text=f"MODIFIED {p}", start_page=p, level=2, parent_ref="c1", still_open_at_chunk_end=True)
            for p in chunk.page_nums if p in (2, 3)
        ]
        # Page 4 keeps 5.0 CLOSING
        if 4 in chunk.page_nums:
            headings.append(AIHeading(heading_text="5.0 CLOSING", start_page=4, level=2, parent_ref="c1", still_open_at_chunk_end=True))
        return ChunkStructureResult(outcome=ChunkOutcome.OK, page_nums=chunk.page_nums,
                                     headings=headings, provider="gemini", model="gemini-2.5-flash")

    monkeypatch.setattr(ai_indexing.StructureIndexer, "extract_structure_for_chunk", _fake_extract_retry)
    result = await ai_reference_index.reindex_job_with_ai(job_id, start_page=2)
    assert result["status"] == "done"
    # Chunk 0 processed [2, 3], chunk 1 processed [3, 4] -> page 4 was reached and processed
    assert 4 in processed_pages


@pytest.mark.asyncio
async def test_wrong_page_heading_gets_persisted_on_the_correct_neighboring_page_not_the_claimed_one(monkeypatch):
    """Direct repro of the confirmed real-job bug: the AI claims a heading
    is on page 1, but the real matching text is actually on page 2 (both in
    the same chunk/window) — it must be persisted on page 2, flagged
    ai_page_corrected, not silently anchored to the wrong page 1."""
    job_id = _job_with_ai_pages([
        "intro text with nothing structurally relevant on it",
        "3.4 QUALITY PLANS\n\nsome content about quality assurance",
    ], chunk_pages=2)

    async def _fake_extract(self, chunk, *, carry_context=None, document_hint="", cancel_check=None,
                             include_pleading_points=False):
        headings = [AIHeading(heading_text="3.4 QUALITY PLANS", start_page=1, level=1,
                               end_page=1, still_open_at_chunk_end=False)]
        return ChunkStructureResult(outcome=ChunkOutcome.OK, page_nums=chunk.page_nums,
                                     headings=headings, provider="gemini", model="gemini-2.5-flash")

    monkeypatch.setattr(ai_indexing.StructureIndexer, "extract_structure_for_chunk", _fake_extract)
    result = await ai_reference_index.reindex_job_with_ai(job_id)
    assert result["status"] == "done"

    delta = database.get_reference_targets_since(job_id, 0)
    assert "numheading:3.4" not in delta.get(1, {})
    row = delta[2]["numheading:3.4"][0]
    assert row["detection_source"] == "ai"
    assert "ai_page_corrected" in (row.get("heading_confidence_reasons") or [])


@pytest.mark.asyncio
async def test_pleading_points_are_persisted_alongside_ai_detected_headings_on_the_same_page(monkeypatch):
    """Tests the regex-augmentation code path directly (still available via
    an explicit per-job opt-out of the now-default AI handoff): the AI
    reports only the section heading (never the numbered pleading
    paragraphs under it) — detect_pleading_points_for_page must still add
    them as their own indexed point rows on the same page."""
    job_id = _job_with_ai_pages([
        "RESPECTFULLY SHOWETH:\n\n"
        "1. That the appellant is filing the accompanying appeal.\n\n"
        "2. That for proper and just decision of the appeal.",
    ], chunk_pages=1, pleading_points_via_ai=False)

    async def _fake_extract(self, chunk, *, carry_context=None, document_hint="", cancel_check=None,
                             include_pleading_points=False):
        headings = [AIHeading(heading_text="RESPECTFULLY SHOWETH:", start_page=1, level=1,
                               end_page=None, still_open_at_chunk_end=True)]
        return ChunkStructureResult(outcome=ChunkOutcome.OK, page_nums=chunk.page_nums,
                                     headings=headings, provider="gemini", model="gemini-2.5-flash")

    monkeypatch.setattr(ai_indexing.StructureIndexer, "extract_structure_for_chunk", _fake_extract)
    result = await ai_reference_index.reindex_job_with_ai(job_id)
    assert result["status"] == "done"

    delta = database.get_reference_targets_since(job_id, 0)
    page_keys = delta[1]
    assert "point:1" in page_keys and "point:2" in page_keys
    assert page_keys["point:1"][0]["own_label"] == "1. That the appellant is filing the accompanying appeal."


@pytest.mark.asyncio
async def test_pleading_points_own_decimal_ground_finding_propagates_to_the_next_page(monkeypatch):
    """Tests the regex-augmentation code path directly (opted out of the
    now-default AI handoff), for a job/prompt combination where the AI
    still doesn't report the decimal ground itself: a decimal ground (here,
    "10.") that the AI never reports — detect_pleading_points_for_page is
    the ONLY thing that finds it — must still be available as
    `last_decimal_ground` on the NEXT page, so a roman sub-ground the AI
    DOES report there correctly nests under it instead of landing flat.
    Without folding this function's finding back into new_scope, this
    cross-page case never worked even after the same-page nesting fix."""
    job_id = _job_with_ai_pages([
        "GROUNDS OF APPEAL\n\n"
        "9. That the ninth ground.\n\n"
        "10. That the tenth ground introduces the following sub-grounds.",
        "(XI) First sub-ground under ground 10.",
    ], chunk_pages=1, pleading_points_via_ai=False)

    async def _fake_extract(self, chunk, *, carry_context=None, document_hint="", cancel_check=None,
                             include_pleading_points=False):
        if chunk.page_nums == [1]:
            headings = [AIHeading(heading_text="GROUNDS OF APPEAL", start_page=1, level=1,
                                   still_open_at_chunk_end=True)]
        else:
            headings = [AIHeading(heading_text="(XI) First sub-ground under ground 10.", start_page=2,
                                   level=2, parent_ref="GROUNDS OF APPEAL", still_open_at_chunk_end=True)]
        return ChunkStructureResult(outcome=ChunkOutcome.OK, page_nums=chunk.page_nums,
                                     headings=headings, provider="gemini", model="gemini-2.5-flash")

    monkeypatch.setattr(ai_indexing.StructureIndexer, "extract_structure_for_chunk", _fake_extract)
    result = await ai_reference_index.reindex_job_with_ai(job_id)
    assert result["status"] == "done"

    delta = database.get_reference_targets_since(job_id, 0)
    assert delta[1]["ground:10"][0]["scope_key"] == "subdoc:1"
    assert delta[2]["ground:xi"][0]["scope_key"] == "subdoc:1/ground:10"


@pytest.mark.asyncio
async def test_ai_reported_heading_preceded_by_a_reproduced_text_intro_never_becomes_an_ancestor(monkeypatch):
    """AI-path analogue of the rule engine's possible_reproduced_text fix:
    the model doesn't always apply its own "don't report quoted text"
    instruction correctly — confirmed real case (job bb417471): a clause
    ("9.0 Local/Site conditions") quoted verbatim mid-argument, introduced
    by "...which reads as under:", got reported by the AI as a real
    heading and pushed as a genuine container, corrupting everything that
    should have nested under the ACTUAL still-open ancestor instead."""
    job_id = _job_with_ai_pages([
        "GROUNDS OF APPEAL\n\n"
        "(XI) That the tribunal erred, as per clause 9.0 ITB, Vol-II, "
        "Contract, which reads as under:\n\n"
        "9.0 Local/ Site conditions\n\n"
        "9.1 It will be imperative on each Bidder to inform himself.\n\n"
        "3.0 CONDITIONS AND COVENANTS\n\nreal body text.",
    ], chunk_pages=1)

    async def _fake_extract(self, chunk, *, carry_context=None, document_hint="", cancel_check=None,
                             include_pleading_points=False):
        headings = [
            AIHeading(heading_text="GROUNDS OF APPEAL", start_page=1, level=1, still_open_at_chunk_end=True),
            AIHeading(heading_text="9.0 Local/ Site conditions", start_page=1, level=2,
                      parent_ref="GROUNDS OF APPEAL", still_open_at_chunk_end=True),
            AIHeading(heading_text="3.0 CONDITIONS AND COVENANTS", start_page=1, level=2,
                      parent_ref="GROUNDS OF APPEAL", still_open_at_chunk_end=True),
        ]
        return ChunkStructureResult(outcome=ChunkOutcome.OK, page_nums=chunk.page_nums,
                                     headings=headings, provider="gemini", model="gemini-2.5-flash")

    monkeypatch.setattr(ai_indexing.StructureIndexer, "extract_structure_for_chunk", _fake_extract)
    result = await ai_reference_index.reindex_job_with_ai(job_id)
    assert result["status"] == "done"

    delta = database.get_reference_targets_since(job_id, 0)
    quoted = delta[1]["numheading:9.0"][0]
    assert "possible_reproduced_text" in quoted["heading_confidence_reasons"]
    assert quoted["scope_key"] == "subdoc:1"  # never anchored
    # The real next heading is a sibling of the quote, not its child.
    assert delta[1]["numheading:3.0"][0]["scope_key"] == "subdoc:1"


@pytest.mark.asyncio
async def test_ai_reported_reproduced_text_continuation_line_also_suppressed(monkeypatch):
    """Tests the regex-augmentation code path directly (opted out of the
    now-default AI handoff, since this fake AI doesn't report the roman
    grounds itself — only detect_pleading_points_for_page does): confirmed
    real bug: the AI also reported the quote's OWN "9.1 ..." sub-clause as
    its own heading (not just "9.0") — since 9.1 isn't itself immediately
    preceded by the "reads as under:" intro, it was treated as genuine,
    pushed as a real ancestor, and reset last_decimal_ground — corrupting
    nesting for every roman ground after it. Both quote lines must be
    suppressed, and ground:10 must still be the nesting parent for the
    roman ground that follows."""
    job_id = _job_with_ai_pages([
        "GROUNDS OF APPEAL\n\n"
        "9. That the ninth ground.\n\n"
        "10. That the tenth ground introduces the following sub-grounds.\n\n"
        "(XI) That the tribunal erred, as per clause 9.0 ITB, Vol-II, "
        "Contract, which reads as under:\n\n"
        "9.0 Local/ Site conditions\n\n"
        "9.1 It will be imperative on each Bidder to inform himself.\n\n"
        "(XII) That furthermore, this finding is incorrect.",
    ], chunk_pages=1, pleading_points_via_ai=False)

    async def _fake_extract(self, chunk, *, carry_context=None, document_hint="", cancel_check=None,
                             include_pleading_points=False):
        headings = [
            AIHeading(heading_text="GROUNDS OF APPEAL", start_page=1, level=1, still_open_at_chunk_end=True),
            AIHeading(heading_text="9.0 Local/ Site conditions", start_page=1, level=2,
                      parent_ref="GROUNDS OF APPEAL", still_open_at_chunk_end=True),
            AIHeading(heading_text="9.1 It will be imperative on each Bidder to inform himself.", start_page=1,
                      level=2, parent_ref="GROUNDS OF APPEAL", still_open_at_chunk_end=True),
        ]
        return ChunkStructureResult(outcome=ChunkOutcome.OK, page_nums=chunk.page_nums,
                                     headings=headings, provider="gemini", model="gemini-2.5-flash")

    monkeypatch.setattr(ai_indexing.StructureIndexer, "extract_structure_for_chunk", _fake_extract)
    result = await ai_reference_index.reindex_job_with_ai(job_id)
    assert result["status"] == "done"

    delta = database.get_reference_targets_since(job_id, 0)
    assert "possible_reproduced_text" in delta[1]["numheading:9.1"][0]["heading_confidence_reasons"]
    assert delta[1]["numheading:9.1"][0]["scope_key"] == "subdoc:1"  # never anchored either
    assert delta[1]["ground:xi"][0]["scope_key"] == "subdoc:1/ground:10"
    assert delta[1]["ground:xii"][0]["scope_key"] == "subdoc:1/ground:10"


@pytest.mark.asyncio
async def test_pleading_points_via_ai_flag_skips_the_regex_augmentation_and_uses_the_ai_prompt(monkeypatch):
    """Round 10's full-AI-handoff, now the primary/default behavior
    (confirmed via a real A/B on job bb417471: perfect nesting across all
    22 grounds, at the cost of missing some points elsewhere without the
    regex safety net — an accepted trade per the user's decision). With
    engine_params.ai_index_pleading_points_via_ai=True (explicit here; see
    the companion test below for the same thing via the default), (a) the
    prompt sent to the model includes the new section, (b)
    detect_pleading_points_for_page contributes nothing (no duplicate rows
    for the same heading), and (c) the AI-reported grounds/points persist
    with correct nesting via apply_heading_events_to_scope's existing
    forced-family/last_decimal_ground handling."""
    job_id = str(uuid.uuid4())
    database.create_job(
        job_id=job_id, filename="t.pdf", file_path="/tmp/x.pdf", file_type="pdf",
        engine="unlimited", total_pages=1,
        engine_params={
            "ai_indexing_enabled": True, "ai_index_chunk_pages": 1,
            "ai_index_pleading_points_via_ai": True,
        },
    )
    database.upsert_page(job_id, 1, status="success", confidence=0.95, text=(
        "GROUNDS OF APPEAL\n\n"
        "10. That the tenth ground introduces the following sub-grounds.\n\n"
        "(XI) First sub-ground under ground 10.\n\n"
        "(XII) Second sub-ground under ground 10."
    ))
    database.set_job_status(job_id, "completed")

    seen_prompts = []

    async def _fake_extract(self, chunk, *, carry_context=None, document_hint="", cancel_check=None,
                             include_pleading_points=False):
        seen_prompts.append(include_pleading_points)
        headings = [
            AIHeading(heading_text="GROUNDS OF APPEAL", start_page=1, level=1, still_open_at_chunk_end=True),
            AIHeading(heading_text="10. That the tenth ground introduces the following sub-grounds.",
                      start_page=1, level=2, parent_ref="GROUNDS OF APPEAL", still_open_at_chunk_end=True),
            AIHeading(heading_text="(XI) First sub-ground under ground 10.", start_page=1, level=3,
                      parent_ref="10. That the tenth ground introduces the following sub-grounds.",
                      still_open_at_chunk_end=True),
            AIHeading(heading_text="(XII) Second sub-ground under ground 10.", start_page=1, level=3,
                      parent_ref="10. That the tenth ground introduces the following sub-grounds.",
                      still_open_at_chunk_end=True),
        ]
        return ChunkStructureResult(outcome=ChunkOutcome.OK, page_nums=chunk.page_nums,
                                     headings=headings, provider="gemini", model="gemini-2.5-flash")

    monkeypatch.setattr(ai_indexing.StructureIndexer, "extract_structure_for_chunk", _fake_extract)
    result = await ai_reference_index.reindex_job_with_ai(job_id)
    assert result["status"] == "done"
    assert seen_prompts == [True]  # confirms the flag actually reached the prompt-building call

    delta = database.get_reference_targets_since(job_id, 0)
    # No duplicate rows from detect_pleading_points_for_page — exactly one
    # occurrence of each ground key, sourced from the AI.
    assert len(delta[1]["ground:10"]) == 1
    assert delta[1]["ground:10"][0]["detection_source"] == "ai"
    assert len(delta[1]["ground:xi"]) == 1
    assert delta[1]["ground:xi"][0]["detection_source"] == "ai"
    # Nesting still correct even though the AI (not the regex augmentation)
    # is the sole source of these events.
    assert delta[1]["ground:10"][0]["scope_key"] == "subdoc:1"
    assert delta[1]["ground:xi"][0]["scope_key"] == "subdoc:1/ground:10"
    assert delta[1]["ground:xii"][0]["scope_key"] == "subdoc:1/ground:10"


@pytest.mark.asyncio
async def test_pleading_points_via_ai_is_the_default_when_engine_params_omits_it(monkeypatch):
    """A job with no ai_index_pleading_points_via_ai key at all (e.g. any
    job created before this setting existed) must still get the new
    default (True) — regression guard for the promotion from opt-in test
    to primary behavior."""
    job_id = _job_with_ai_pages([
        "GROUNDS OF APPEAL\n\n"
        "10. That the tenth ground introduces the following sub-grounds.\n\n"
        "(XI) First sub-ground under ground 10.",
    ], chunk_pages=1)  # no pleading_points_via_ai kwarg at all

    seen_prompts = []

    async def _fake_extract(self, chunk, *, carry_context=None, document_hint="", cancel_check=None,
                             include_pleading_points=False):
        seen_prompts.append(include_pleading_points)
        headings = [
            AIHeading(heading_text="GROUNDS OF APPEAL", start_page=1, level=1, still_open_at_chunk_end=True),
            AIHeading(heading_text="10. That the tenth ground introduces the following sub-grounds.",
                      start_page=1, level=2, parent_ref="GROUNDS OF APPEAL", still_open_at_chunk_end=True),
            AIHeading(heading_text="(XI) First sub-ground under ground 10.", start_page=1, level=3,
                      parent_ref="10. That the tenth ground introduces the following sub-grounds.",
                      still_open_at_chunk_end=True),
        ]
        return ChunkStructureResult(outcome=ChunkOutcome.OK, page_nums=chunk.page_nums,
                                     headings=headings, provider="gemini", model="gemini-2.5-flash")

    monkeypatch.setattr(ai_indexing.StructureIndexer, "extract_structure_for_chunk", _fake_extract)
    result = await ai_reference_index.reindex_job_with_ai(job_id)
    assert result["status"] == "done"
    assert seen_prompts == [True]

    delta = database.get_reference_targets_since(job_id, 0)
    assert delta[1]["ground:xi"][0]["scope_key"] == "subdoc:1/ground:10"

