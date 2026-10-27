"""Fast, deterministic evaluation smoke test for CI/CD (<3s execution, Test Suite 5).

Runs completely offline without external network, GPU, or Ollama dependencies.
Enforces that future refactoring of reference_index, database, or ai_indexing
does not break evaluation invariants.
"""

import json
import pytest

from evals.datasets.loader import load_gold_action_items_fixture, load_gold_index_fixture
from evals.datasets.registry import get_benchmark_dataset
from evals.datasets.schema import GoldOCRFixture
from evals.gates import evaluate_action_items_gates, evaluate_index_gates, evaluate_ocr_gates
from evals.metrics.grounding import score_action_items
from evals.metrics.ocr_error import character_error_rate, word_error_rate
from evals.metrics.structure import score_target_slice
from evals.runners.ocr_runner import evaluate_ocr_fixture


@pytest.mark.eval_smoke
class TestEvalSmoke:
    def test_indexing_structure_smoke_offline(self):
        """Validates that scoring a 17-page judgment fixture against itself

        produces perfect scores and passes profile gates.
        """
        bench = get_benchmark_dataset("judgment-17p")
        fixture = load_gold_index_fixture(bench.gold_index_path)
        gold_targets = [t.model_dump() for t in fixture.targets]

        # Self-score slice
        scores = score_target_slice(gold_targets, gold_targets)
        assert scores["precision"] == 1.0
        assert scores["recall"] == 1.0
        assert scores["scope_accuracy"] == 1.0
        assert scores["family_drift"] == 0

        # Pass through gating engine
        metrics = {
            "target_precision": scores["precision"],
            "target_recall": scores["recall"],
            "rule_recall": 1.000,
            "mention_precision": 1.000,
            "mention_recall": 1.000,
            "family_drift_count": scores["family_drift"],
        }
        gate_res = evaluate_index_gates(metrics)
        assert gate_res.passed is True

    def test_action_items_smoke_offline(self):
        """Validates that action items scoring runs offline and passes default action item gates."""
        bench = get_benchmark_dataset("tender-135p")
        assert bench.gold_action_items_path is not None
        fixture = load_gold_action_items_fixture(bench.gold_action_items_path)
        gold_items = [i.model_dump() for i in fixture.action_items]

        # Take first 10 items as smoke sample
        sample_items = gold_items[:10]
        scores = score_action_items(sample_items, sample_items)
        assert scores["precision"] == 1.0
        assert scores["recall"] == 1.0

        gate_res = evaluate_action_items_gates(scores)
        assert gate_res.passed is True

    def test_ocr_evaluation_smoke_offline(self):
        """Validates OCR evaluation against gold_ocr_pages.json using identical hypothesis strings."""
        import os
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        fixture_path = os.path.join(repo_root, "tests", "fixtures", "gold_ocr_pages.json")

        with open(fixture_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        fixture = GoldOCRFixture.model_validate(data)
        # Hypothesis matches ground truth text exactly
        hypothesis_pages = {p.page: p.ground_truth_text for p in fixture.pages}

        results = evaluate_ocr_fixture(fixture, hypothesis_pages)
        assert results["mean_cer"] == 0.0
        assert results["mean_wer"] == 0.0

        gate_res = evaluate_ocr_gates(results)
        assert gate_res.passed is True
