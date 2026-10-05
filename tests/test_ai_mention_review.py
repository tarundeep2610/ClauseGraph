import pytest
from unittest.mock import AsyncMock, patch, MagicMock

import ai_indexing
from ai_indexing.prompt import PageInput, build_mention_review_prompt
from ai_indexing.parsing import parse_mention_response
from ai_indexing.schema import AIMentionReview
from ai_reference_index import review_mentions_for_job


def test_parse_mention_response_valid():
    raw_json = """```json
    {
      "reviews": [
        {"mention_id": "M1", "action": "confirm", "evidence_quote": ""},
        {"mention_id": "M2", "action": "reject", "evidence_quote": "Arbitration and Conciliation Act, 1996"},
        {"mention_id": "M3", "action": "unsuppress", "evidence_quote": "Clause 11 of the agreement"}
      ]
    }
    ```"""
    reviews = parse_mention_response(raw_json)
    assert len(reviews) == 3
    assert reviews[0].mention_id == "M1"
    assert reviews[0].action == "confirm"
    assert reviews[1].mention_id == "M2"
    assert reviews[1].action == "reject"
    assert reviews[1].evidence_quote == "Arbitration and Conciliation Act, 1996"
    assert reviews[2].mention_id == "M3"
    assert reviews[2].action == "unsuppress"


def test_parse_mention_response_invalid_and_corrupt():
    corrupt = "{ broken json... "
    reviews = parse_mention_response(corrupt)
    assert reviews == []

    # Invalid action defaults to confirm
    raw_invalid_action = '{"reviews": [{"mention_id": "M1", "action": "unknown_action"}]}'
    reviews2 = parse_mention_response(raw_invalid_action)
    assert len(reviews2) == 1
    assert reviews2[0].action == "confirm"


def test_build_mention_review_prompt():
    pages = [PageInput(page_num=1, text="Page 1 text content with Section 34")]
    mentions = [
        {"mention_id": "M1", "occurrence_index": 0, "page": 1, "key": "section:34", "raw_text": "Section 34", "resolution": "suppressed", "suppressed_reason": "external_law"}
    ]
    sys_prompt, user_prompt = build_mention_review_prompt(pages, mentions)
    assert "confirm" in sys_prompt
    assert "reject" in sys_prompt
    assert "unsuppress" in sys_prompt
    assert "M1 | p1 | key=section:34" in user_prompt
    assert 'text="Section 34"' in user_prompt
    assert "status=suppressed (external_law)" in user_prompt


@pytest.mark.asyncio
async def test_review_mentions_for_job_actions():
    job_id = "test-job-mentions"
    all_mentions = [
        {
            "mention_id": "M1",
            "page": 1,
            "occurrence_index": 0,
            "key": "clause:11",
            "raw_text": "Clause 11",
            "resolution": "exact",
            "suppressed_reason": None,
            "detection_source": "rule",
        },
        {
            "mention_id": "M2",
            "page": 1,
            "occurrence_index": 1,
            "key": "section:34",
            "raw_text": "Section 34",
            "resolution": "suppressed",
            "suppressed_reason": "external_law",
            "detection_source": "rule",
        },
        {
            "mention_id": "M3",
            "page": 1,
            "occurrence_index": 2,
            "key": "annexure:a1",
            "raw_text": "Annexure A-1",
            "resolution": "suppressed",
            "suppressed_reason": "external_law",
            "detection_source": "rule",
        },
    ]
    pages = [{"page_num": 1, "text": "Clause 11 of the contract governs. Also Section 34 of the Arbitration Act, 1996 applies. See Annexure A-1 of this agreement."}]

    mock_llm_json = """{
      "reviews": [
        {"mention_id": "M1", "action": "confirm", "evidence_quote": ""},
        {"mention_id": "M2", "action": "reject", "evidence_quote": "Arbitration Act, 1996"},
        {"mention_id": "M3", "action": "unsuppress", "evidence_quote": "Annexure A-1 of this agreement"}
      ]
    }"""

    mock_provider = AsyncMock()
    mock_provider.complete.return_value = ai_indexing.CompletionResult(
        provider="mock",
        model="mock-model",
        text=mock_llm_json,
        duration_ms=100,
    )

    mock_config = MagicMock()
    mock_config.provider_config.provider = "mock"
    mock_config.provider_config.model = "mock-model"

    with patch("ai_indexing.resolve_active_config", return_value=mock_config), \
         patch("database.get_all_reference_mentions_for_job", return_value=all_mentions), \
         patch("database.get_pages", return_value=pages), \
         patch("database.get_reference_targets_for_job", return_value=[{"key": "annexure:a1"}]), \
         patch("database.bump_reference_index_version", return_value=2), \
         patch("database.upsert_reference_mentions_for_page") as mock_upsert, \
         patch("ai_indexing.get_provider", return_value=mock_provider):

        await review_mentions_for_job(job_id, engine_params={"ai_index_chunk_pages": 5})

        assert mock_upsert.called
        saved_mentions = mock_upsert.call_args[0][2]
        m1 = [m for m in saved_mentions if m["key"] == "clause:11"][0]
        assert m1["detection_source"] == "ai"

        m2 = [m for m in saved_mentions if m["key"] == "section:34"][0]
        assert m2["resolution"] == "suppressed"
        assert m2["suppressed_reason"] == "ai_rejected"
        assert m2["detection_source"] == "ai"

        m3 = [m for m in saved_mentions if m["key"] == "annexure:a1"][0]
        assert m3["resolution"] == "exact"
        assert m3["suppressed_reason"] is None
        assert m3["detection_source"] == "ai"
