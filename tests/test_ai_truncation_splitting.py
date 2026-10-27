import asyncio
from unittest.mock import AsyncMock
import uuid
import pytest

import ai_indexing
import ai_reference_index
import config
import database
from ai_indexing.client import ChunkOutcome, ChunkStructureResult, StructureIndexer
from ai_indexing.credentials import ResolvedAIConfig
from ai_indexing.prompt import ChunkPlan, PageInput
from ai_indexing.providers import CompletionResult, ProviderConfig
from ai_indexing.redact import SecretStr
from ai_indexing.schema import AIHeading


@pytest.fixture(autouse=True)
def _ai_env_configured(monkeypatch):
    monkeypatch.setattr(config, "AI_INDEX_API_KEY", "AIzaFakeTestKey1234")
    monkeypatch.setattr(config, "AI_INDEX_PROVIDER", "gemini")
    monkeypatch.setattr(config, "AI_INDEX_MODEL", "gemini-2.5-flash")
    yield


def _job_with_ai_pages(pages_text, chunk_pages=2, overlap_pages=0):
    job_id = str(uuid.uuid4())
    engine_params = {
        "ai_indexing_enabled": True,
        "ai_index_chunk_pages": chunk_pages,
        "ai_index_chunk_overlap_pages": overlap_pages,
    }
    database.create_job(
        job_id=job_id, filename="test_doc.pdf", file_path="/tmp/test_doc.pdf", file_type="pdf",
        engine="unlimited", total_pages=len(pages_text),
        engine_params=engine_params,
    )
    for i, text in enumerate(pages_text, start=1):
        database.upsert_page(job_id, i, status="success", text=text, confidence=0.95)
    database.set_job_status(job_id, "completed")
    return job_id


@pytest.mark.asyncio
async def test_client_truncation_detection_on_success(monkeypatch):
    """Verify StructureIndexer sets truncated=True on ChunkStructureResult when finish_reason='length'."""
    fake_cfg = ProviderConfig(
        provider="ollama_native", model="qwen3:8b", api_key=SecretStr("fake")
    )
    resolved = ResolvedAIConfig(provider_config=fake_cfg, chunk_pages=2)
    indexer = StructureIndexer(resolved)

    fake_provider = AsyncMock()
    fake_provider.name = "ollama_native"
    fake_provider.complete = AsyncMock(return_value=CompletionResult(
        text='{"headings": [{"heading_text": "SECTION 1", "level": 1, "start_page": 1}]}',
        provider="ollama_native",
        model="qwen3:8b",
        duration_ms=150,
        finish_reason="length",
    ))
    monkeypatch.setattr(ai_indexing.client, "get_provider", AsyncMock(return_value=fake_provider))

    chunk = ChunkPlan(
        pages=[PageInput(page_num=1, text="SECTION 1\n\nBody text")],
        truncated_pages=[],
        total_chars=20,
    )
    result = await indexer.extract_structure_for_chunk(chunk)
    assert result.outcome == ChunkOutcome.OK
    assert result.truncated is True
    assert len(result.headings) == 1
    assert result.headings[0].heading_text == "SECTION 1"


@pytest.mark.asyncio
async def test_client_truncation_short_circuits_invalid_output_retry(monkeypatch):
    """When finish_reason='length' and JSON parsing fails, do NOT retry attempt 2 and don't trip circuit breaker."""
    fake_cfg = ProviderConfig(
        provider="ollama_native", model="qwen3:8b", api_key=SecretStr("fake")
    )
    resolved = ResolvedAIConfig(provider_config=fake_cfg, chunk_pages=2)
    indexer = StructureIndexer(resolved, failure_limit=3)

    fake_provider = AsyncMock()
    fake_provider.name = "ollama_native"
    # Plain text without JSON cut off by token cap
    fake_provider.complete = AsyncMock(return_value=CompletionResult(
        text='Here is the document analysis but it was cut off before any JSON',
        provider="ollama_native",
        model="qwen3:8b",
        duration_ms=150,
        finish_reason="length",
    ))
    monkeypatch.setattr(ai_indexing.client, "get_provider", AsyncMock(return_value=fake_provider))

    chunk = ChunkPlan(
        pages=[PageInput(page_num=1, text="SECTION 1\n\nBody text")],
        truncated_pages=[],
        total_chars=20,
    )
    result = await indexer.extract_structure_for_chunk(chunk)
    assert result.outcome == ChunkOutcome.FAILED
    assert result.truncated is True
    # Exactly one call — attempt 2 was short-circuited
    assert fake_provider.complete.call_count == 1
    # Circuit breaker counter was NOT incremented
    assert indexer._consecutive_failures == 0


@pytest.mark.asyncio
async def test_macro_skeleton_split_and_requeue():
    """_extract_macro_skeleton splits truncated multi-page chunk, requeues halves, and collects all headings."""
    pages = [
        PageInput(page_num=1, text="SECTION I: INVITATION\n\nIntro"),
        PageInput(page_num=2, text="More section I text"),
        PageInput(page_num=3, text="SECTION II: BID DATA\n\nDetails"),
        PageInput(page_num=4, text="More section II text"),
    ]

    mock_indexer = AsyncMock()
    called_ranges = []

    async def _mock_extract(chunk, **kwargs):
        pnums = chunk.page_nums
        called_ranges.append(list(pnums))
        if pnums == [1, 2, 3, 4]:
            # Initial 4-page chunk truncates!
            return ChunkStructureResult(
                outcome=ChunkOutcome.OK,
                page_nums=pnums,
                headings=[],
                truncated=True,
            )
        elif pnums == [1, 2]:
            return ChunkStructureResult(
                outcome=ChunkOutcome.OK,
                page_nums=pnums,
                headings=[AIHeading(heading_text="SECTION I: INVITATION", start_page=1, level=1)],
                truncated=False,
            )
        elif pnums == [3, 4]:
            return ChunkStructureResult(
                outcome=ChunkOutcome.OK,
                page_nums=pnums,
                headings=[AIHeading(heading_text="SECTION II: BID DATA", start_page=3, level=1)],
                truncated=False,
            )
        raise ValueError(f"Unexpected chunk: {pnums}")

    mock_indexer.extract_structure_for_chunk = _mock_extract

    skeleton = await ai_reference_index._extract_macro_skeleton(
        pages, mock_indexer, coarse_chunk_pages=4
    )

    # Assert processing order: [1,2,3,4] truncated -> split to [1,2] then [3,4]
    assert called_ranges == [[1, 2, 3, 4], [1, 2], [3, 4]]
    assert len(skeleton) == 2
    assert skeleton[0]["heading_text"] == "SECTION I: INVITATION"
    assert skeleton[0]["start_page"] == 1
    assert skeleton[0]["end_page"] == 2
    assert skeleton[1]["heading_text"] == "SECTION II: BID DATA"
    assert skeleton[1]["start_page"] == 3
    assert skeleton[1]["end_page"] == 4


@pytest.mark.asyncio
async def test_macro_skeleton_single_page_floor():
    """A 1-page macro chunk that truncates cannot split further and terminates safely."""
    pages = [PageInput(page_num=1, text="SECTION I: INVITATION\n\nIntro")]

    mock_indexer = AsyncMock()
    mock_indexer.extract_structure_for_chunk = AsyncMock(return_value=ChunkStructureResult(
        outcome=ChunkOutcome.OK,
        page_nums=[1],
        headings=[AIHeading(heading_text="SECTION I: INVITATION", start_page=1, level=1)],
        truncated=True,
    ))

    skeleton = await ai_reference_index._extract_macro_skeleton(
        pages, mock_indexer, coarse_chunk_pages=1
    )
    assert len(skeleton) == 1
    assert skeleton[0]["heading_text"] == "SECTION I: INVITATION"
    assert mock_indexer.extract_structure_for_chunk.call_count == 1


@pytest.mark.asyncio
async def test_micro_pass_split_and_requeue(monkeypatch):
    """reindex_job_with_ai splits truncated chunk, updates total_chunks, and continues across halves."""
    job_id = _job_with_ai_pages([
        "SECTION 1: GENERAL\n\nPage 1 body",
        "Page 2 body text",
        "SECTION 2: SPECIAL\n\nPage 3 body",
        "Page 4 body text",
    ], chunk_pages=4)

    called_chunks = []

    async def _fake_extract(self, chunk, **kwargs):
        pnums = chunk.page_nums
        called_chunks.append(list(pnums))
        if pnums == [1, 2, 3, 4]:
            return ChunkStructureResult(
                outcome=ChunkOutcome.OK,
                page_nums=pnums,
                headings=[],
                truncated=True,
            )
        elif pnums == [1, 2]:
            return ChunkStructureResult(
                outcome=ChunkOutcome.OK,
                page_nums=pnums,
                headings=[AIHeading(heading_text="SECTION 1: GENERAL", start_page=1, level=1, still_open_at_chunk_end=True)],
                truncated=False,
            )
        elif pnums == [3, 4]:
            return ChunkStructureResult(
                outcome=ChunkOutcome.OK,
                page_nums=pnums,
                headings=[AIHeading(heading_text="SECTION 2: SPECIAL", start_page=3, level=1, still_open_at_chunk_end=True)],
                truncated=False,
            )
        raise ValueError(f"Unexpected chunk: {pnums}")

    monkeypatch.setattr(ai_indexing.StructureIndexer, "extract_structure_for_chunk", _fake_extract)

    result = await ai_reference_index.reindex_job_with_ai(job_id)
    assert result["status"] == "done"
    # Originally planned 1 chunk of 4 pages; after split, total_chunks became 2
    assert result["total_chunks"] == 2
    assert called_chunks == [[1, 2, 3, 4], [1, 2], [3, 4]]

    report = database.get_ai_index_report(job_id)
    assert report["current_chunk"] == 2
    assert report["total_chunks"] == 2
    assert len(report["chunks"]) == 2
    assert report["chunks"][0]["first_page"] == 1
    assert report["chunks"][0]["last_page"] == 2
    assert report["chunks"][1]["first_page"] == 3
    assert report["chunks"][1]["last_page"] == 4


@pytest.mark.asyncio
async def test_micro_pass_split_with_dynamic_overlap(monkeypatch):
    """Verify that dynamic overlap correctly processes all pages when a chunk splits under overlap > 0."""
    monkeypatch.setattr(config, "AI_INDEX_CHUNK_OVERLAP_PAGES", 1)
    job_id = _job_with_ai_pages([
        "Page 1: Title\n\nIntro",
        "Page 2: Terms\n\nDetails",
        "Page 3: Continuation\n\nMore",
        "Page 4: Signatures\n\nEnd",
    ], chunk_pages=2, overlap_pages=1)

    # Initial plan: chunk1=[1,2], chunk2=[2,3], chunk3=[3,4]
    # Suppose chunk2=[2,3] truncates and splits to [2] and [3]
    called_chunks = []

    async def _fake_extract(self, chunk, **kwargs):
        pnums = chunk.page_nums
        called_chunks.append(list(pnums))
        if pnums == [2, 3]:
            # Truncates!
            return ChunkStructureResult(
                outcome=ChunkOutcome.OK,
                page_nums=pnums,
                headings=[],
                truncated=True,
            )
        return ChunkStructureResult(
            outcome=ChunkOutcome.OK,
            page_nums=pnums,
            headings=[AIHeading(heading_text=f"Page {pnums[0]} Heading", start_page=pnums[0], level=1)],
            truncated=False,
        )

    monkeypatch.setattr(ai_indexing.StructureIndexer, "extract_structure_for_chunk", _fake_extract)

    result = await ai_reference_index.reindex_job_with_ai(job_id)
    assert result["status"] == "done"
    # All 4 pages were processed without skipping
    assert result["pages_done"] == 4
    report = database.get_ai_index_report(job_id)
    assert report["ai_last_persisted_page"] == 4
