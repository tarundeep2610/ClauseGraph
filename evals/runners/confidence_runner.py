"""Confidence scoring calibration evaluation runner.

Evaluates how well synthesized confidence scores and human-review flags from
confidence.py predict true transcription errors.
"""

from typing import Any, Dict, List, Sequence

from evals.metrics.calibration import (
    binary_brier_score,
    binary_error_detection_summary,
    binary_roc_auc,
    spearman_rank_correlation,
)
from evals.runners.base import BaseEvalRunner


def evaluate_confidence_calibration(
    page_records: List[Dict[str, Any]],
    cer_error_threshold: float = 0.05,
) -> Dict[str, Any]:
    """Evaluates confidence calibration across a dataset of scored pages.

    Each page record is expected to contain:
    - 'page': int
    - 'confidence': float (0.0 to 1.0)
    - 'needs_review': bool
    - 'cer': float (actual measured Character Error Rate)
    """
    if not page_records:
        return {
            "page_count": 0,
            "roc_auc": 1.0,
            "brier_score": 0.0,
            "spearman_correlation": 1.0,
            "error_detection": {},
        }

    # True binary error: CER > threshold
    y_true = [1 if r.get("cer", 0.0) > cer_error_threshold else 0 for r in page_records]

    # Inverted confidence probability of error: P(error) ~ 1.0 - confidence
    p_error = [max(0.0, min(1.0, 1.0 - r.get("confidence", 1.0))) for r in page_records]
    y_pred = [1 if r.get("needs_review", False) else 0 for r in page_records]

    roc_auc = binary_roc_auc(y_true, p_error)
    brier = binary_brier_score(y_true, p_error)
    detection_summary = binary_error_detection_summary(y_true, y_pred)

    confidences = [r.get("confidence", 1.0) for r in page_records]
    accuracies = [max(0.0, 1.0 - r.get("cer", 0.0)) for r in page_records]
    spearman = spearman_rank_correlation(confidences, accuracies)

    return {
        "page_count": len(page_records),
        "roc_auc": roc_auc,
        "brier_score": brier,
        "spearman_correlation": spearman,
        "error_detection": detection_summary,
    }


class ConfidenceEvalRunner(BaseEvalRunner):
    """Runner for confidence scorer calibration."""

    async def run(self, dataset_id: str = "confidence_eval", **kwargs: Any) -> Dict[str, Any]:
        page_records = kwargs.get("page_records", [])
        threshold = kwargs.get("cer_error_threshold", 0.05)
        return evaluate_confidence_calibration(page_records, cer_error_threshold=threshold)
