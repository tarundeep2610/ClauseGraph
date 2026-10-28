"""Truncation-detection and adaptive split/requeue for the action-items pass --
mirrors tests/test_ai_truncation_splitting.py's coverage of the structure
pass's equivalent mechanism (ai_indexing/client.py's finish_reason check,
ai_reference_index.py's deque-based chunk requeue loop)."""
import uuid
from unittest.mock import AsyncMock

import pytest

import ai_action_items
import ai_indexing
import config
import database
from ai_indexing.client import ActionItemIndexer, ChunkActionItemResult, ChunkOutcome
from ai_indexing.credentials import ResolvedAIConfig
from ai_indexing.prompt import ChunkPlan, PageInput
from ai_indexing.providers import CompletionResult, ProviderConfig
from ai_indexing.redact import SecretStr


@pytest.fixture(autouse=True)
def _ai_env_configured(monkeypatch):
    monkeypatch.setattr(config, "AI_INDEX_API_KEY", "AIzaFakeTestKey1234")
    monkeypatch.setattr(config, "AI_INDEX_PROVIDER", "gemini")
    monkeypatch.setattr(config, "AI_INDEX_MODEL", "gemini-2.5-flash")
    yield


def _job_with_pages(pages_text):
    job_id = str(uuid.uuid4())
    database.create_job(
        job_id=job_id, filename="test_doc.pdf", file_path="/tmp/test_doc.pdf", file_type="pdf",
        engine="unlimited", total_pages=len(pages_text), engine_params={},
    )
    for i, text in enumerate(pages_text, start=1):
        database.upsert_page(job_id, i, status="success", text=text, confidence=0.95)
    database.set_job_status(job_id, "completed")
    return job_id


@pytest.mark.asyncio
async def test_client_truncation_detection_on_success(monkeypatch):
    """ActionItemIndexer sets truncated=True on ChunkActionItemResult when finish_reason='length'."""
    fake_cfg = ProviderConfig(provider="ollama_native", model="qwen3:8b", api_key=SecretStr("fake"))
    resolved = ResolvedAIConfig(provider_config=fake_cfg, chunk_pages=2)
    indexer = ActionItemIndexer(resolved)

    fake_provider = AsyncMock()
    fake_provider.name = "ollama_native"
    fake_provider.complete = AsyncMock(return_value=CompletionResult(
        text='{"items": [{"item_type": "deadline", "item_text": "Submit by due date", '
             '"page": 1, "evidence_quote": "Submission must be made by the due date"}], "notes": ""}',
        provider="ollama_native", model="qwen3:8b", duration_ms=150, finish_reason="length",
    ))
    monkeypatch.setattr(ai_indexing.client, "get_provider", AsyncMock(return_value=fake_provider))

    chunk = ChunkPlan(
        pages=[PageInput(page_num=1, text="Submission must be made by the due date")],
        truncated_pages=[], total_chars=40,
    )
    result = await indexer.extract_action_items_for_chunk(chunk)
    assert result.outcome == ChunkOutcome.OK
    assert result.truncated is True
    assert len(result.items) == 1
    assert result.items[0].item_type == "deadline"


@pytest.mark.asyncio
async def test_client_truncation_short_circuits_invalid_output_retry(monkeypatch):
    """When finish_reason='length' and JSON parsing fails, do NOT retry attempt 2 and don't trip circuit breaker."""
    fake_cfg = ProviderConfig(provider="ollama_native", model="qwen3:8b", api_key=SecretStr("fake"))
    resolved = ResolvedAIConfig(provider_config=fake_cfg, chunk_pages=2)
    indexer = ActionItemIndexer(resolved, failure_limit=3)

    fake_provider = AsyncMock()
    fake_provider.name = "ollama_native"
    fake_provider.complete = AsyncMock(return_value=CompletionResult(
        text='Here is the action item analysis but it was cut off before any JSON',
        provider="ollama_native", model="qwen3:8b", duration_ms=150, finish_reason="length",
    ))
    monkeypatch.setattr(ai_indexing.client, "get_provider", AsyncMock(return_value=fake_provider))

    chunk = ChunkPlan(
        pages=[PageInput(page_num=1, text="Submission must be made by the due date")],
        truncated_pages=[], total_chars=40,
    )
    result = await indexer.extract_action_items_for_chunk(chunk)
    assert result.outcome == ChunkOutcome.FAILED
    assert result.truncated is True
    assert fake_provider.complete.call_count == 1
    assert indexer._consecutive_failures == 0


@pytest.mark.asyncio
async def test_action_item_split_and_requeue(monkeypatch):
    """reindex_action_items_for_job splits a truncated chunk, updates total_chunks, and continues across halves."""
    job_id = _job_with_pages([
        "Page 1 body text about deadlines",
        "Page 2 body text",
        "Page 3 body text about obligations",
        "Page 4 body text",
    ])

    called_chunks = []

    async def _fake_extract(self, chunk, **kwargs):
        pnums = chunk.page_nums
        called_chunks.append(list(pnums))
        if pnums == [1, 2, 3, 4]:
            return ChunkActionItemResult(outcome=ChunkOutcome.OK, page_nums=pnums, items=[], truncated=True)
        return ChunkActionItemResult(outcome=ChunkOutcome.EMPTY, page_nums=pnums, items=[], truncated=False)

    monkeypatch.setattr(ActionItemIndexer, "extract_action_items_for_chunk", _fake_extract)

    result = await ai_action_items.reindex_action_items_for_job(job_id, chunk_pages_override=4)
    assert result["status"] == "done"
    # Originally planned 1 chunk of 4 pages; after split, total_chunks became 2
    assert result["total_chunks"] == 2
    assert called_chunks == [[1, 2, 3, 4], [1, 2], [3, 4]]

    report = database.get_action_item_report(job_id)
    assert report["current_chunk"] == 2
    assert report["total_chunks"] == 2
    assert len(report["chunks"]) == 2
    assert report["chunks"][0]["first_page"] == 1
    assert report["chunks"][0]["last_page"] == 2
    assert report["chunks"][1]["first_page"] == 3
    assert report["chunks"][1]["last_page"] == 4
    assert result["pages_done"] == 4


@pytest.mark.asyncio
async def test_action_item_single_page_truncated_accepted_as_is(monkeypatch):
    """A 1-page chunk that truncates cannot split further and is accepted as-is (its items still persisted)."""
    job_id = _job_with_pages(["Page 1 body text about deadlines"])

    called_chunks = []

    async def _fake_extract(self, chunk, **kwargs):
        pnums = chunk.page_nums
        called_chunks.append(list(pnums))
        from ai_indexing.schema import AIActionItem
        return ChunkActionItemResult(
            outcome=ChunkOutcome.OK, page_nums=pnums,
            items=[AIActionItem(
                item_type="deadline", item_text="Submit by due date", page=1,
                evidence_quote="Page 1 body text about deadlines",
            )],
            truncated=True,
        )

    monkeypatch.setattr(ActionItemIndexer, "extract_action_items_for_chunk", _fake_extract)

    result = await ai_action_items.reindex_action_items_for_job(job_id, chunk_pages_override=4)
    assert result["status"] == "done"
    assert result["total_chunks"] == 1
    assert called_chunks == [[1]]
    items = database.get_all_action_items_for_job(job_id)
    assert len(items) == 1
