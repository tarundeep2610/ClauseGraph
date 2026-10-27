"""Unit tests for regression gating and profile enforcement (Test Suite 4)."""

import pytest

from evals.gates import (
    GateBreachError,
    enforce_gates,
    evaluate_action_items_gates,
    evaluate_index_gates,
    evaluate_ocr_gates,
)


class TestEvalGates:
    def test_gates_pass_on_compliant_tender_scores(self):
        metrics = {
            "target_precision": 0.890,
            "target_recall": 0.960,
            "rule_recall": 1.000,
            "mention_precision": 0.961,
            "mention_recall": 0.994,
            "family_drift_count": 5,
        }
        res = evaluate_index_gates(metrics)
        assert res.passed is True
        assert len(res.violations) == 0

    def test_gates_enforce_universal_precision_floor(self):
        """Universal threshold: precision < 0.85 fails unconditionally across all documents."""
        appeal_metrics = {
            "target_precision": 0.780,  # Below universal 0.85 floor
            "target_recall": 0.960,
            "rule_recall": 1.000,
            "mention_precision": 0.957,
            "mention_recall": 0.971,
            "family_drift_count": 8,
        }

        res = evaluate_index_gates(appeal_metrics)
        assert res.passed is False
        assert any("Precision 0.780 fell below minimum 0.850" in v for v in res.violations)

    def test_gates_catch_recall_drop(self):
        regressed_metrics = {
            "target_precision": 0.900,
            "target_recall": 0.780,  # Critical recall regression (below 0.950)
            "rule_recall": 1.000,
            "mention_precision": 0.950,
            "mention_recall": 0.950,
            "family_drift_count": 2,
        }
        res = evaluate_index_gates(regressed_metrics)
        assert res.passed is False
        assert any("Recall 0.780 fell below minimum 0.950" in v for v in res.violations)

    def test_rule_path_zero_regression_invariant(self):
        """Rule-only recall must never drop below 1.000."""
        broken_rule = {
            "target_precision": 0.900,
            "target_recall": 0.950,
            "rule_recall": 0.980,  # Broken invariant
            "mention_precision": 0.950,
            "mention_recall": 0.950,
            "family_drift_count": 0,
        }
        res = evaluate_index_gates(broken_rule)
        assert res.passed is False
        assert any("Rule-only recall 0.980 fell below 1.000" in v for v in res.violations)

    def test_enforce_gates_raises_exception(self):
        bad_metrics = {
            "target_precision": 0.500,
            "target_recall": 0.500,
            "rule_recall": 1.000,
        }
        res = evaluate_index_gates(bad_metrics)
        with pytest.raises(GateBreachError, match="Evaluation gates breached"):
            enforce_gates(res)

    def test_ocr_gates_enforcement(self):
        passing_ocr = {
            "mean_cer": 0.015,
            "mean_wer": 0.035,
            "cer_by_category": {"clean_legal": 0.012},
        }
        assert evaluate_ocr_gates(passing_ocr).passed is True

        failing_ocr = {
            "mean_cer": 0.055,
            "mean_wer": 0.080,
            "cer_by_category": {"clean_legal": 0.030},  # Exceeds 0.020
        }
        res = evaluate_ocr_gates(failing_ocr)
        assert res.passed is False
        assert len(res.violations) == 2
