"""OCR transcription and table structure evaluation runner."""

import json
import os
from typing import Any, Dict, List, Optional

from evals.datasets.schema import GoldOCRFixture, GoldOCRPage
from evals.metrics.ocr_error import character_error_rate, table_cell_accuracy, word_error_rate
from evals.runners.base import BaseEvalRunner


def evaluate_ocr_page(
    gold_page: GoldOCRPage,
    hypothesis_text: str,
    hypothesis_tables: Optional[List[List[List[str]]]] = None,
) -> Dict[str, Any]:
    """Scores a single OCR-extracted page against its ground truth text and tables."""
    cer = character_error_rate(gold_page.ground_truth_text, hypothesis_text)
    wer = word_error_rate(gold_page.ground_truth_text, hypothesis_text)

    table_acc: Optional[float] = None
    if gold_page.ground_truth_tables and hypothesis_tables:
        table_accs = [
            table_cell_accuracy(ref, hyp)
            for ref, hyp in zip(gold_page.ground_truth_tables, hypothesis_tables)
        ]
        table_acc = sum(table_accs) / len(table_accs) if table_accs else 1.0
    elif gold_page.ground_truth_tables:
        table_acc = 0.0

    return {
        "page": gold_page.page,
        "source_job_id": gold_page.source_job_id,
        "category": gold_page.category,
        "cer": cer,
        "wer": wer,
        "table_accuracy": table_acc,
    }


def evaluate_ocr_fixture(
    fixture: GoldOCRFixture,
    hypothesis_pages: Dict[int, str],
) -> Dict[str, Any]:
    """Scores a collection of hypothesis page texts against the OCR gold fixture."""
    page_results = []
    by_category: Dict[str, List[float]] = {}

    for gold_page in fixture.pages:
        hyp_text = hypothesis_pages.get(gold_page.page, "")
        res = evaluate_ocr_page(gold_page, hyp_text)
        page_results.append(res)
        by_category.setdefault(gold_page.category, []).append(res["cer"])

    all_cer = [r["cer"] for r in page_results]
    all_wer = [r["wer"] for r in page_results]

    cat_means = {cat: (sum(cers) / len(cers)) for cat, cers in by_category.items()}

    return {
        "page_count": len(page_results),
        "mean_cer": (sum(all_cer) / len(all_cer)) if all_cer else 0.0,
        "mean_wer": (sum(all_wer) / len(all_wer)) if all_wer else 0.0,
        "cer_by_category": cat_means,
        "pages": page_results,
    }


class OCREvalRunner(BaseEvalRunner):
    """Runner for evaluating OCR engine output on ground truth pages."""

    async def run(self, dataset_id: str = "gold_ocr_pages", **kwargs: Any) -> Dict[str, Any]:
        fixture_path = kwargs.get("fixture_path")
        if not fixture_path:
            repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            fixture_path = os.path.join(repo_root, "tests", "fixtures", "gold_ocr_pages.json")

        with open(fixture_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        fixture = GoldOCRFixture.model_validate(data)
        hypothesis_pages = kwargs.get("hypothesis_pages", {})
        return evaluate_ocr_fixture(fixture, hypothesis_pages)
