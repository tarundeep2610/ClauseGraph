"""Unit tests for Phase 0 (record/replay harness) and Phase 1-3 scoring extensions."""

import os
import tempfile
import pytest

from ai_indexing.recording import (
    RecordSession,
    ReplaySession,
    ReplayFidelityError,
    record_scope,
    replay_scope,
    get_active_record_session,
    get_active_replay_session,
)
from scripts.score_reference_index import (
    _score_target_slice,
    _score_content_dual_match,
    _labeled_diff,
)


def test_record_and_replay_roundtrip():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "recording.json")
        fp = {
            "source_job_id": "job-123",
            "prompt_variant": "full",
            "chunk_pages": 3,
            "overlap_pages": 0,
            "model": "qwen3:8b",
            "provider": "rokkaai",
        }

        # 1. Record
        with record_scope(path, fingerprint=fp) as rec:
            rec.record_chunk([1, 2, 3], '{"headings": []}', duration_ms=100, attempt=1, system_prompt="sys prompt")
            rec.record_chunk([4, 5, 6], '{"headings": [{"heading_text": "Section 1"}]}', duration_ms=200, attempt=1)

        assert os.path.exists(path)

        # 2. Replay with matching fingerprint
        with replay_scope(path, expected_fingerprint=fp) as rep:
            c1 = rep.get_chunk([1, 2, 3])
            assert c1["raw_text"] == '{"headings": []}'
            assert c1["duration_ms"] == 100

            c2 = rep.get_chunk([4, 5, 6])
            assert "Section 1" in c2["raw_text"]

        # 3. Replay with mismatched fingerprint raises ReplayFidelityError
        mismatched_fp = dict(fp, prompt_variant="compact")
        with pytest.raises(ReplayFidelityError):
            with replay_scope(path, expected_fingerprint=mismatched_fp):
                pass


def test_replay_rejects_rule_fallback_recording():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "failed_rec.json")
        rec = RecordSession(path, fingerprint={"source_job_id": "job-123"})
        rec.record_chunk([1, 2, 3], '{"headings": []}')
        rec.record_failure([4, 5, 6], error="RokkaAI timed out")
        rec.save()

        with pytest.raises(ReplayFidelityError, match="rule-fallback"):
            rep = ReplaySession(path)
            rep.validate_fingerprint({"source_job_id": "job-123"})


def test_dual_match_and_drift_tax_calculation():
    # Gold targets
    gold = [
        {"page": 1, "key": "subdoc:1", "occurrence_index": 0, "label": "Introduction", "scope_key": ""},
        {"page": 2, "key": "subdoc:2", "occurrence_index": 0, "label": "Scope of Work", "scope_key": ""},
        {"page": 3, "key": "point:1", "occurrence_index": 0, "label": "", "scope_key": "subdoc:2"},  # label-less
    ]

    # Actual targets: subdoc:2 suffered ordinal drift on page 2 and became subdoc:3
    act = [
        {"page": 1, "key": "subdoc:1", "occurrence_index": 0, "label": "Introduction", "scope_key": ""},
        {"page": 2, "key": "subdoc:3", "occurrence_index": 0, "label": "Scope of Work", "scope_key": ""},
        {"page": 3, "key": "point:1", "occurrence_index": 0, "label": "", "scope_key": "subdoc:3"},
    ]

    # Key matching
    gold_ids = {(t["page"], t["key"], t["occurrence_index"]) for t in gold}
    act_ids = {(t["page"], t["key"], t["occurrence_index"]) for t in act}
    matched_key_ids = gold_ids & act_ids  # only subdoc:1 and point:1 match on key

    res = _score_content_dual_match(gold, act, matched_key_ids)

    # 2 labeled gold targets: Introduction and Scope of Work
    assert res["labeled_gold_count"] == 2
    # Only Introduction matched on key
    assert res["labeled_key_matched"] == 1
    assert res["labeled_key_recall"] == 0.5
    # Both matched on content (greedy same-page label match)
    assert res["labeled_content_matched"] == 2
    assert res["labeled_content_recall"] == 1.0
    # Drift tax is +0.50 (50% of labeled recall lost to ordinal drift!)
    assert res["drift_tax"] == 0.50


def test_labeled_diff_generation():
    gold = [
        {"page": 1, "key": "subdoc:1", "occurrence_index": 0, "label": "Intro", "scope_key": ""},
        {"page": 2, "key": "subdoc:2", "occurrence_index": 0, "label": "Scope", "scope_key": ""},
    ]
    act = [
        {"page": 1, "key": "subdoc:1", "occurrence_index": 0, "label": "Intro", "scope_key": ""},
        {"page": 2, "key": "subdoc:99", "occurrence_index": 0, "label": "Scope", "scope_key": "subdoc:1"},
    ]
    matched_ids = {(1, "subdoc:1", 0)}

    diff = _labeled_diff(gold, act, matched_ids)
    assert len(diff["false_positives"]) == 1
    assert diff["false_positives"][0]["key"] == "subdoc:99"
    assert diff["false_positives"][0]["level"] == 1

    assert len(diff["false_negatives"]) == 1
    assert diff["false_negatives"][0]["key"] == "subdoc:2"

    assert len(diff["grid"]) > 0
