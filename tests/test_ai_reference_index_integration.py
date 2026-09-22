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


def _job_with_ai_pages(pages_text, chunk_pages=2):
    job_id = str(uuid.uuid4())
    database.create_job(
        job_id=job_id, filename="t.pdf", file_path="/tmp/x.pdf", file_type="pdf",
        engine="unlimited", total_pages=len(pages_text),
        engine_params={
            "ai_indexing_enabled": True, "ai_index_chunk_pages": chunk_pages,
        },
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

    async def _fake_extract(self, chunk, *, carry_context=None, document_hint="", cancel_check=None):
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
async def test_paused_run_stops_between_chunks_and_resumes_without_reprocessing(monkeypatch):
    job_id = _job_with_ai_pages([
        "ANNEXURE A-1\n\nintro",
        "body page two",
        "3.0 CONDITIONS AND COVENANTS\n\nbody page three",
        "body page four",
    ], chunk_pages=1)  # 4 chunks of 1 page each, so we can pause mid-run

    processed_chunks = []

    async def _fake_extract(self, chunk, *, carry_context=None, document_hint="", cancel_check=None):
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

    async def _fake_extract(self, chunk, *, carry_context=None, document_hint="", cancel_check=None):
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

    async def _fake_extract(self, chunk, *, carry_context=None, document_hint="", cancel_check=None):
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

    async def _fake_extract(self, chunk, *, carry_context=None, document_hint="", cancel_check=None):
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

    async def _fake_extract(self, chunk, *, carry_context=None, document_hint="", cancel_check=None):
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

    async def _fake_extract(self, chunk, *, carry_context=None, document_hint="", cancel_check=None):
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

    async def _fake_extract(self, chunk, *, carry_context=None, document_hint="", cancel_check=None):
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
    async def _fake_extract_initial(self, chunk, *, carry_context=None, document_hint="", cancel_check=None):
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

    async def _fake_extract_retry(self, chunk, *, carry_context=None, document_hint="", cancel_check=None):
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

