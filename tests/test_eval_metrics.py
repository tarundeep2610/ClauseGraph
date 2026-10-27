"""Unit tests for evals.metrics subpackage (Test Suite 1).

Validates pure metric calculations, boundary conditions, and edge cases.
"""

import math
import pytest

from evals.metrics.classification import compute_precision_recall_f1, compute_set_overlap
from evals.metrics.structure import (
    score_target_slice,
    calculate_family_drift,
    calculate_drift_tax,
    score_content_dual_match,
    normalize_key_for_scoring,
    extract_family,
)
from evals.metrics.grounding import (
    normalize_quote,
    verify_quote_grounding,
    score_action_items,
)
from evals.metrics.ocr_error import character_error_rate, word_error_rate, table_cell_accuracy
from evals.metrics.calibration import (
    binary_brier_score,
    binary_roc_auc,
    binary_error_detection_summary,
    spearman_rank_correlation,
)


class TestClassificationMetrics:
    def test_precision_recall_edge_cases(self):
        # Both empty: perfect agreement
        both_empty = compute_precision_recall_f1(matched_count=0, actual_count=0, gold_count=0)
        assert both_empty["precision"] == 1.0
        assert both_empty["recall"] == 1.0
        assert both_empty["f1"] == 1.0

        # Silent prediction: actual=0, gold=10 -> Precision=0.0, Recall=0.0
        silent = compute_precision_recall_f1(matched_count=0, actual_count=0, gold_count=10)
        assert silent["precision"] == 0.0
        assert silent["recall"] == 0.0
        assert silent["f1"] == 0.0

        # Spurious prediction: actual=5, gold=0 -> Precision=0.0, Recall=1.0
        spurious = compute_precision_recall_f1(matched_count=0, actual_count=5, gold_count=0)
        assert spurious["precision"] == 0.0
        assert spurious["recall"] == 1.0
        assert spurious["f1"] == 0.0

        # Standard case
        normal = compute_precision_recall_f1(matched_count=8, actual_count=10, gold_count=10)
        assert pytest.approx(normal["precision"]) == 0.8
        assert pytest.approx(normal["recall"]) == 0.8
        assert pytest.approx(normal["f1"]) == 0.8

    def test_compute_set_overlap(self):
        actual = {"a", "b", "c"}
        gold = {"b", "c", "d"}
        res = compute_set_overlap(actual, gold)
        assert res["matched_count"] == 2
        assert res["actual_count"] == 3
        assert res["gold_count"] == 3
        assert pytest.approx(res["precision"]) == 2 / 3
        assert pytest.approx(res["recall"]) == 2 / 3
        assert res["false_positives_count"] == 1
        assert res["false_negatives_count"] == 1


class TestStructureMetrics:
    def test_normalize_key_and_extract_family(self):
        assert normalize_key_for_scoring("point:3") == "numheading:3"
        assert normalize_key_for_scoring("point:3.1") == "numheading:3.1"
        assert normalize_key_for_scoring("subdoc:judgment") == "subdoc:judgment"
        assert extract_family("subdoc:judgment") == "subdoc"
        assert extract_family("point:2") == "point"
        assert extract_family("bare_key") == "bare_key"

    def test_scope_accuracy_calculation(self):
        gold = [
            {"page": 1, "key": "subdoc:root", "scope_key": "", "occurrence_index": 0},
            {"page": 1, "key": "point:1", "scope_key": "subdoc:root", "occurrence_index": 0},
            {"page": 2, "key": "point:2", "scope_key": "subdoc:root", "occurrence_index": 0},
        ]
        # Actual has point:2 with incorrect scope
        actual = [
            {"page": 1, "key": "subdoc:root", "scope_key": "", "occurrence_index": 0},
            {"page": 1, "key": "point:1", "scope_key": "subdoc:root", "occurrence_index": 0},
            {"page": 2, "key": "point:2", "scope_key": "subdoc:wrong", "occurrence_index": 0},
        ]

        scores = score_target_slice(gold, actual)
        assert scores["matched_count"] == 3
        assert scores["precision"] == 1.0
        assert scores["recall"] == 1.0
        # 2 out of 3 matched targets have identical scope
        assert pytest.approx(scores["scope_accuracy"]) == 2 / 3
        assert scores["family_drift"] == 0

    def test_family_drift_identification(self):
        # Actual classifies 'FACTS' as subdoc:facts, but Gold has it as point:facts on same page
        gold = [
            {"page": 3, "key": "point:facts", "label": "FACTS", "occurrence_index": 0},
        ]
        actual = [
            {"page": 3, "key": "subdoc:facts", "label": "FACTS", "occurrence_index": 0},
        ]

        drift = calculate_family_drift(gold, actual, matched_ids=set())
        assert drift == 1

        # Different page should NOT count as family drift
        actual_diff_page = [
            {"page": 4, "key": "subdoc:facts", "label": "FACTS", "occurrence_index": 0},
        ]
        assert calculate_family_drift(gold, actual_diff_page, matched_ids=set()) == 0

    def test_ordinal_drift_tax(self):
        # Target content matches on page 4, but keys shifted: gold has point:2, actual has point:3
        gold = [
            {"page": 4, "key": "point:2", "label": "The Petitioner invited tenders", "occurrence_index": 0},
        ]
        actual = [
            {"page": 4, "key": "point:3", "label": "The Petitioner invited tenders", "occurrence_index": 0},
        ]

        dual = score_content_dual_match(gold, actual, matched_key_ids=set())
        assert dual["labeled_key_matched"] == 0
        assert dual["labeled_content_matched"] == 1
        assert dual["labeled_key_recall"] == 0.0
        assert dual["labeled_content_recall"] == 1.0
        assert dual["drift_tax"] == 1.0


class TestGroundingMetrics:
    def test_normalize_quote(self):
        raw = "  \u201cThe   Appellant \t May Kindly\nBe Permitted\u201d  "
        norm = normalize_quote(raw)
        assert norm == '"the appellant may kindly be permitted"'

    def test_verify_quote_grounding(self):
        page_text = "In view of the above, the appellant may kindly be permitted to place Annexure A-1 on record."
        good_quote = "the appellant may kindly be permitted"
        bad_quote = "the respondent must immediately pay"

        assert verify_quote_grounding(good_quote, page_text) is True
        assert verify_quote_grounding(bad_quote, page_text) is False
        assert verify_quote_grounding("", page_text) is False

    def test_score_action_items(self):
        gold = [
            {"page": 7, "item_type": "obligation", "evidence_quote": "contractor shall submit monthly invoice"},
            {"page": 10, "item_type": "deadline", "evidence_quote": "within 15 days of notice"},
        ]
        actual = [
            {"page": 7, "item_type": "obligation", "evidence_quote": "contractor SHALL submit monthly invoice"},
            {"page": 10, "item_type": "condition", "evidence_quote": "within 15 days of notice"},  # Wrong type
        ]

        scores = score_action_items(gold, actual)
        assert scores["matched_count"] == 1
        assert scores["precision"] == 0.5
        assert scores["recall"] == 0.5
        assert scores["by_type"]["obligation"]["precision"] == 1.0
        assert scores["by_type"]["deadline"]["recall"] == 0.0


class TestOCRErrorMetrics:
    def test_character_error_rate(self):
        assert character_error_rate("hello", "hello") == 0.0
        assert character_error_rate("", "") == 0.0
        assert character_error_rate("abc", "") == 1.0

        # 1 substitution in 4 chars: 'test' -> 'text' = 1/4 = 0.25
        assert character_error_rate("test", "text") == 0.25

        # 1 deletion in 5 chars: 'hello' -> 'helo' = 1/5 = 0.20
        assert character_error_rate("hello", "helo") == 0.20

        # 1 insertion in 4 chars: 'word' -> 'words' = 1/4 = 0.25
        assert character_error_rate("word", "words") == 0.25

    def test_word_error_rate(self):
        ref = "The quick brown fox jumps"
        hyp = "The fast brown fox jumps"
        # 1 substitution in 5 words = 0.20
        assert word_error_rate(ref, hyp) == 0.20

    def test_table_cell_accuracy(self):
        ref = [["Item", "Qty"], ["Wave Trap", "2"]]
        hyp = [["Item", "Qty"], ["Wave Trap", "2"]]
        assert table_cell_accuracy(ref, hyp) == 1.0

        hyp_wrong = [["Item", "Qty"], ["Wave Trap", "3"]]
        # 3 out of 4 cells match
        assert table_cell_accuracy(ref, hyp_wrong) == 0.75


class TestCalibrationMetrics:
    def test_binary_brier_score(self):
        # Perfect predictions
        assert binary_brier_score([1, 0, 1], [1.0, 0.0, 1.0]) == 0.0
        # Completely wrong
        assert binary_brier_score([1, 0], [0.0, 1.0]) == 1.0
        # Average probability
        assert binary_brier_score([1, 0], [0.5, 0.5]) == 0.25

    def test_binary_roc_auc(self):
        # Perfect discrimination
        assert binary_roc_auc([1, 1, 0, 0], [0.9, 0.8, 0.2, 0.1]) == 1.0
        # Inverted discrimination
        assert binary_roc_auc([1, 1, 0, 0], [0.1, 0.2, 0.8, 0.9]) == 0.0
        # Random / equal scores
        assert binary_roc_auc([1, 0], [0.5, 0.5]) == 0.5

    def test_binary_error_detection_summary(self):
        y_true = [1, 1, 0, 0]  # 1 means real error
        y_pred = [1, 0, 1, 0]  # TP=1, FN=1, FP=1, TN=1
        summary = binary_error_detection_summary(y_true, y_pred)
        assert summary["accuracy"] == 0.5
        assert summary["false_alarm_rate"] == 0.5
        assert summary["missed_error_rate"] == 0.5

    def test_spearman_rank_correlation(self):
        # Perfect monotonic increasing
        assert pytest.approx(spearman_rank_correlation([1, 2, 3, 4], [10, 20, 30, 40])) == 1.0
        # Perfect monotonic decreasing
        assert pytest.approx(spearman_rank_correlation([1, 2, 3, 4], [40, 30, 20, 10])) == -1.0
