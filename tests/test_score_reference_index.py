"""Regression tests for scripts/score_reference_index.py's matching/scoring
logic — kept separate from scripts/export_gold_fixture.py's own correctness
(covered by tests/test_structure_overrides.py's resolve_structure_for_job
tests), this file is specifically about whether the SCORER itself computes
precision/recall/scope-accuracy/family-drift correctly.

Per the plan's own verification requirement: "score the exported gold
against itself — must report 100% precision, 100% recall, 100% scope-path
match and zero family drift. A scorer that can't score identical data
perfectly can't be trusted on a real run." Uses hand-seeded rows (not a
live reindex) so this runs as a fast, deterministic unit test."""

import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import database
import structure_overrides
from score_reference_index import evaluate_run


def _job_with_pages(pages_text):
    job_id = str(uuid.uuid4())
    database.create_job(
        job_id=job_id, filename="t.pdf", file_path="/tmp/x.pdf", file_type="pdf",
        engine="unlimited", total_pages=len(pages_text),
    )
    for i, text in enumerate(pages_text, start=1):
        database.upsert_page(job_id, i, status="success", text=text, confidence=0.95)
    database.set_job_status(job_id, "completed")
    return job_id


def _seed_target_row(job_id, page, key, occurrence_index, scope_chain, version, own_label=None):
    database.upsert_reference_targets_for_page(job_id, page, [{
        "key": key, "occurrence_index": occurrence_index, "scope_chain": scope_chain,
        "scope_key": "/".join(s["key"] for s in scope_chain),
        "own_label": own_label or key, "detection_source": "rule",
    }], version)


def _gold_from_resolved(resolved):
    return {
        "targets": [{
            "page": r["page"], "key": r["key"], "occurrence_index": r.get("occurrence_index", 0),
            "label": r.get("own_label"), "scope_key": r.get("resolved_scope_key") or "",
        } for r in resolved],
        "mentions": [],
    }


@pytest.fixture
def seeded_job():
    job_id = _job_with_pages(["p1", "p2", "p3"])
    v = database.bump_reference_index_version(job_id)
    _seed_target_row(job_id, 1, "annexure:a-1", 0, [], v, "Annexure A-1")
    _seed_target_row(job_id, 2, "clause:1", 0, [{"family": "annexure", "key": "annexure:a-1", "label": "Annexure A-1"}], v, "Clause 1")
    _seed_target_row(job_id, 3, "clause:2", 0, [], v, "Clause 2")
    return job_id


def test_scorer_self_test_identical_data_scores_perfectly(seeded_job):
    """The exact check the plan calls for: score a job's own resolved
    structure against a gold fixture built from that SAME structure."""
    resolved = structure_overrides.resolve_structure_for_job(seeded_job)
    gold = _gold_from_resolved(resolved)

    scores = evaluate_run(seeded_job, gold)

    assert scores["target_precision"] == 1.0
    assert scores["target_recall"] == 1.0
    assert scores["scope_accuracy"] == 1.0
    assert scores["family_drift_count"] == 0


def test_scorer_detects_a_missed_target(seeded_job):
    resolved = structure_overrides.resolve_structure_for_job(seeded_job)
    gold = _gold_from_resolved(resolved)
    # Gold expects one more target than the job actually has.
    gold["targets"].append({"page": 3, "key": "clause:3", "occurrence_index": 0, "label": "Clause 3", "scope_key": ""})

    scores = evaluate_run(seeded_job, gold)

    assert scores["target_recall"] < 1.0
    assert scores["target_precision"] == 1.0  # nothing extra was produced


def test_scorer_distinguishes_family_drift_from_a_genuine_miss(seeded_job):
    """A target whose content clearly matches gold (same page, same label)
    but was classified under the WRONG KEY FAMILY must count as
    family_drift, not just lower recall — this is the error class the
    qwen trials found (a ground/point leaf misclassified as a false
    subdoc root), and precision/recall alone can't tell it apart from the
    target simply never being found."""
    resolved = structure_overrides.resolve_structure_for_job(seeded_job)
    gold = _gold_from_resolved(resolved)
    # Gold says clause:2's heading should be "point:2" instead — same
    # label, different family, same page.
    for t in gold["targets"]:
        if t["key"] == "clause:2":
            t["key"] = "point:2"

    scores = evaluate_run(seeded_job, gold)

    assert scores["family_drift_count"] == 1
    assert scores["target_recall"] < 1.0


def test_scorer_disambiguates_repeated_key_by_occurrence(seeded_job):
    """Matching on (page, key, occurrence_index) — not the bare (page, key)
    pair — must not conflate two genuinely different occurrences of the
    same key, mirroring the real bug Phase A fixed end to end."""
    v = database.bump_reference_index_version(seeded_job)
    _seed_target_row(seeded_job, 3, "point:5", 0, [{"family": "annexure", "key": "annexure:a-1", "label": "Annexure A-1"}], v, "Point 5 under A-1")

    resolved = structure_overrides.resolve_structure_for_job(seeded_job)
    gold = _gold_from_resolved(resolved)
    # Gold has the SAME key on a different page/scope — must not spuriously
    # match the one just seeded.
    gold["targets"].append({"page": 99, "key": "point:5", "occurrence_index": 0, "label": "A different Point 5 entirely", "scope_key": "subdoc:1"})

    scores = evaluate_run(seeded_job, gold)

    # The real occurrence (page 3) matches; the fabricated gold occurrence
    # (page 99) does not exist in the actual job, so recall must be < 1.0 —
    # if (page, key) alone were used for matching, both "point:5" entries
    # would collide into one identity and this would incorrectly read 1.0.
    assert scores["target_recall"] < 1.0
