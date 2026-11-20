import pytest
import uuid
from unittest.mock import AsyncMock, patch, MagicMock

import database
import ai_indexing
from ai_indexing.prompt import build_docref_review_prompt
from ai_indexing.parsing import parse_docref_review_response
from ai_indexing.schema import AIDocrefVerdict, AIDocrefReview
from ai_reference_index import review_docrefs_for_job


def test_docref_review_prompt_and_schema_round_trip():
    # Prompt building
    occs = {
        "docref:1": [
            {
                "occurrence_id": "occ_p20_0",
                "page": 20,
                "occurrence_index": 0,
                "raw_text": "claim Nos 1, 21, 22, 24",
                "snippet": "...has partially allowed claim Nos 1, 21, 22, 24...",
            },
            {
                "occurrence_id": "occ_p913_0",
                "page": 913,
                "occurrence_index": 0,
                "raw_text": "Claim No. 1",
                "snippet": "Claim No. 1 — Levy of stage penalty/liquidated damages on account of following delays...",
            },
        ]
    }
    sys_prompt, user_prompt = build_docref_review_prompt(occs)
    assert "definition_occurrence_id" in sys_prompt
    assert "occ_p20_0" in user_prompt
    assert "occ_p913_0" in user_prompt
    assert "docref:1" in user_prompt

    # Model JSON response parsing round-trip
    model_output = """```json
    {
      "reviews": [
        {
          "key": "docref:1",
          "definition_occurrence_id": "occ_p913_0",
          "evidence_quote": "Claim No. 1 — Levy of stage penalty"
        }
      ]
    }
    ```"""
    verdicts = parse_docref_review_response(model_output)
    assert len(verdicts) == 1
    assert verdicts[0].key == "docref:1"
    assert verdicts[0].definition_occurrence_id == "occ_p913_0"
    assert "Claim No. 1" in verdicts[0].evidence_quote


def test_docref_review_none_keeps_baseline():
    model_output = """{
      "reviews": [
        {
          "key": "docref:rec064",
          "definition_occurrence_id": "none",
          "evidence_quote": ""
        }
      ]
    }"""
    verdicts = parse_docref_review_response(model_output)
    assert len(verdicts) == 1
    assert verdicts[0].key == "docref:rec064"
    assert verdicts[0].definition_occurrence_id == "none"


@pytest.mark.asyncio
async def test_promotion_moves_target_restores_old_occurrence_stamps_ai():
    # Modeled on the real Claim No. 1 case:
    # Phase 1 baseline placed the target on page 20 (earliest cite).
    # Page 913 has the substantive definition.
    job_id = str(uuid.uuid4())
    database.create_job(
        job_id=job_id, filename="arbitration.pdf", file_path="/tmp/arb.pdf",
        file_type="pdf", engine="unlimited", total_pages=955,
    )
    database.upsert_page(job_id, 20, status="success", text="partial award claim Nos 1, 21")
    database.upsert_page(job_id, 913, status="success", text="Claim No. 1 — Levy of stage penalty")

    # Seed initial target on p20 (rule-detected baseline)
    init_target = {
        "key": "docref:1",
        "page": 20,
        "occurrence_index": 0,
        "own_label": "claim Nos 1",
        "snippet": "partial award claim Nos 1",
        "detection_source": "rule",
    }
    database.upsert_docref_targets_for_job(job_id, [init_target], version=1)

    # Seed mention on p913
    p913_mention = {
        "occurrence_index": 0,
        "key": "docref:1",
        "raw_text": "Claim No. 1",
        "resolution": "exact",
        "suppressed_reason": None,
        "detection_source": "rule",
    }
    database.upsert_reference_mentions_for_page(job_id, 913, [p913_mention], version=1)

    # Verify initial baseline
    targets_before = database.get_reference_targets_for_job(job_id)
    assert len(targets_before) == 1
    assert targets_before[0]["page"] == 20
    assert targets_before[0]["detection_source"] == "rule"

    mock_verdict = """```json
    {
      "reviews": [
        {
          "key": "docref:1",
          "definition_occurrence_id": "occ_p913_0",
          "evidence_quote": "Claim No. 1 — Levy of stage penalty"
        }
      ]
    }
    ```"""

    mock_provider = AsyncMock()
    mock_provider.complete.return_value = MagicMock(text=mock_verdict)

    with patch("ai_indexing.resolve_active_config") as mock_resolve, \
         patch("ai_indexing.get_provider", return_value=mock_provider):
        mock_config = MagicMock()
        mock_config.provider_config.model = "gemini-2.5-pro"
        mock_resolve.return_value = mock_config

        await review_docrefs_for_job(job_id, engine_params={"ai_index_override_docrefs": True})

    # Verify target was moved to page 913 and stamped with detection_source="ai"
    targets_after = database.get_reference_targets_for_job(job_id)
    assert len(targets_after) == 1
    assert targets_after[0]["key"] == "docref:1"
    assert targets_after[0]["page"] == 913
    assert targets_after[0]["detection_source"] == "ai"

    # Verify page 913's mention was removed (no self-mention on target page)
    mentions_p913 = database.get_reference_mentions_since(job_id, -1).get(913, [])
    assert not any(m["key"] == "docref:1" for m in mentions_p913)

    # Verify page 20's old occurrence was restored as an ordinary mention
    mentions_p20 = database.get_reference_mentions_since(job_id, -1).get(20, [])
    p20_docref = [m for m in mentions_p20 if m["key"] == "docref:1"]
    assert len(p20_docref) == 1
    assert p20_docref[0]["resolution"] == "exact"
    assert p20_docref[0]["detection_source"] == "ai"
