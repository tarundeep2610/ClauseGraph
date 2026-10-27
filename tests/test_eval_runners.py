"""Runner invariants, self-scoring, and complete database cleanup tests (Test Suite 3)."""

import os
import sqlite3
import sys
import uuid
import pytest

import database
import structure_overrides
from evals.runners.base import cleanup_duplicate_job
from evals.runners.index_runner import evaluate_job_structure
from evals.runners.action_items_runner import evaluate_job_action_items


def _create_seeded_job(pages_text):
    job_id = str(uuid.uuid4())
    database.create_job(
        job_id=job_id,
        filename="eval_test.pdf",
        file_path="/tmp/eval_test.pdf",
        file_type="pdf",
        engine="unlimited",
        total_pages=len(pages_text),
    )
    for i, text in enumerate(pages_text, start=1):
        database.upsert_page(job_id, i, status="success", text=text, confidence=0.95)
    database.set_job_status(job_id, "completed")
    return job_id


def _seed_target(job_id, page, key, occurrence_index, scope_chain, version, own_label=None):
    database.upsert_reference_targets_for_page(
        job_id,
        page,
        [
            {
                "key": key,
                "occurrence_index": occurrence_index,
                "scope_chain": scope_chain,
                "scope_key": "/".join(s["key"] for s in scope_chain),
                "own_label": own_label or key,
                "detection_source": "rule",
            }
        ],
        version,
    )


class TestEvalRunners:
    def test_index_runner_self_score_is_perfect(self):
        """Scoring a job's own resolved structure against its exported gold fixture

        must yield 100% precision, 100% recall, 100% scope match, and zero drift.
        """
        job_id = _create_seeded_job(["page 1", "page 2", "page 3"])
        try:
            v = database.bump_reference_index_version(job_id)
            _seed_target(job_id, 1, "annexure:a-1", 0, [], v, "Annexure A-1")
            _seed_target(
                job_id,
                2,
                "clause:1",
                0,
                [{"family": "annexure", "key": "annexure:a-1", "label": "Annexure A-1"}],
                v,
                "Clause 1",
            )
            _seed_target(job_id, 3, "clause:2", 0, [], v, "Clause 2")

            resolved = structure_overrides.resolve_structure_for_job(job_id)
            gold = {
                "targets": [
                    {
                        "page": r["page"],
                        "key": r["key"],
                        "occurrence_index": r.get("occurrence_index", 0),
                        "label": r.get("own_label"),
                        "scope_key": r.get("resolved_scope_key") or "",
                    }
                    for r in resolved
                ],
                "mentions": [],
            }

            scores = evaluate_job_structure(job_id, gold)
            assert scores["target_precision"] == 1.0
            assert scores["target_recall"] == 1.0
            assert scores["scope_accuracy"] == 1.0
            assert scores["family_drift_count"] == 0
        finally:
            cleanup_duplicate_job(job_id)

    def test_duplicate_job_isolation_and_complete_cleanup(self):
        """Verifies that cleanup_duplicate_job purges all associated tables without leaving orphaned rows."""
        source_id = _create_seeded_job(["source page 1"])
        dup_id = _create_seeded_job(["dup page 1"])

        try:
            v = database.bump_reference_index_version(dup_id)
            _seed_target(dup_id, 1, "subdoc:test", 0, [], v, "Test Subdoc")

            # Seed an action item under dup_id
            conn = database.get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO ocr_action_items (job_id, page, occurrence_index, item_type, item_text, evidence_quote) "
                "VALUES (?, 1, 0, 'obligation', 'Contractor shall pay', 'Contractor shall pay')",
                (dup_id,),
            )
            conn.commit()

            # Verify rows exist before cleanup
            cursor.execute("SELECT count(*) FROM ocr_reference_targets WHERE job_id = ?", (dup_id,))
            assert cursor.fetchone()[0] > 0
            cursor.execute("SELECT count(*) FROM ocr_action_items WHERE job_id = ?", (dup_id,))
            assert cursor.fetchone()[0] > 0
            conn.close()

            # Execute cascading cleanup
            cleanup_duplicate_job(dup_id)

            # Verify all rows under dup_id are completely purged
            conn = database.get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT count(*) FROM ocr_jobs WHERE job_id = ?", (dup_id,))
            assert cursor.fetchone()[0] == 0
            cursor.execute("SELECT count(*) FROM ocr_pages WHERE job_id = ?", (dup_id,))
            assert cursor.fetchone()[0] == 0
            cursor.execute("SELECT count(*) FROM ocr_reference_targets WHERE job_id = ?", (dup_id,))
            assert cursor.fetchone()[0] == 0
            cursor.execute("SELECT count(*) FROM ocr_action_items WHERE job_id = ?", (dup_id,))
            assert cursor.fetchone()[0] == 0

            # Source job must remain untouched
            cursor.execute("SELECT count(*) FROM ocr_jobs WHERE job_id = ?", (source_id,))
            assert cursor.fetchone()[0] == 1
            conn.close()
        finally:
            cleanup_duplicate_job(source_id)

    def test_action_items_runner_self_score(self):
        job_id = _create_seeded_job(["p1"])
        try:
            conn = database.get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO ocr_action_items (job_id, page, occurrence_index, item_type, item_text, evidence_quote) "
                "VALUES (?, 1, 0, 'deadline', 'Complete within 30 days', 'complete within 30 days')",
                (job_id,),
            )
            conn.commit()
            conn.close()

            gold_items = [
                {
                    "page": 1,
                    "item_type": "deadline",
                    "evidence_quote": "complete within 30 days",
                }
            ]

            scores = evaluate_job_action_items(job_id, gold_items)
            assert scores["precision"] == 1.0
            assert scores["recall"] == 1.0
            assert scores["matched_count"] == 1
        finally:
            cleanup_duplicate_job(job_id)

    def test_cleanup_duplicate_job_removes_physical_file(self, tmp_path):
        """Verifies cleanup_duplicate_job removes the duplicated physical file from disk."""
        pdf_file = tmp_path / "dup_test.pdf"
        pdf_file.write_text("dummy pdf content")
        assert pdf_file.exists()

        job_id = str(uuid.uuid4())
        database.create_job(
            job_id=job_id,
            filename="dup_test.pdf",
            file_path=str(pdf_file),
            file_type="pdf",
            engine="unlimited",
            total_pages=1,
        )

        cleanup_duplicate_job(job_id)
        assert not pdf_file.exists(), "cleanup_duplicate_job failed to delete physical PDF file from disk"

    @pytest.mark.asyncio
    async def test_ocr_eval_runner_execution(self):
        """Directly exercises OCREvalRunner class."""
        import json
        from evals.runners.ocr_runner import OCREvalRunner

        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        fixture_path = os.path.join(repo_root, "tests", "fixtures", "gold_ocr_pages.json")
        with open(fixture_path, "r", encoding="utf-8") as f:
            gold_data = json.load(f)

        hypothesis = {p["page"]: p["ground_truth_text"] for p in gold_data["pages"]}
        runner = OCREvalRunner()
        results = await runner.run("gold_ocr_pages", hypothesis_pages=hypothesis)

        assert results["page_count"] == 10
        assert results["mean_cer"] == 0.0
        assert results["mean_wer"] == 0.0

    @pytest.mark.asyncio
    async def test_confidence_eval_runner_execution(self):
        """Directly exercises ConfidenceEvalRunner class."""
        from evals.runners.confidence_runner import ConfidenceEvalRunner

        records = [
            {"page": 1, "confidence": 0.98, "needs_review": False, "cer": 0.01},
            {"page": 2, "confidence": 0.40, "needs_review": True, "cer": 0.15},
            {"page": 3, "confidence": 0.92, "needs_review": False, "cer": 0.02},
        ]
        runner = ConfidenceEvalRunner()
        results = await runner.run("confidence_eval", page_records=records)

        assert results["page_count"] == 3
        assert results["roc_auc"] == 1.0
        assert results["brier_score"] < 0.20
        assert results["error_detection"]["accuracy"] == 1.0
