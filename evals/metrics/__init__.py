"""Metric formulation subpackage for evals.

Pure, stateless math functions for:
- Classification (Precision, Recall, F1)
- Structural hierarchy (Scope accuracy, Family drift, Ordinal tax, Dual match)
- Grounding (Quote normalization, substring verification)
- OCR error (CER, WER, Table similarity)
- Calibration (Binary error ROC-AUC / Brier, Continuous rank correlation)
"""

from evals.metrics.classification import compute_precision_recall_f1
from evals.metrics.structure import (
    score_target_slice,
    calculate_family_drift,
    calculate_drift_tax,
    score_content_dual_match,
)
from evals.metrics.grounding import normalize_quote, verify_quote_grounding
from evals.metrics.ocr_error import character_error_rate, word_error_rate
from evals.metrics.calibration import (
    binary_brier_score,
    binary_roc_auc,
    spearman_rank_correlation,
)

__all__ = [
    "compute_precision_recall_f1",
    "score_target_slice",
    "calculate_family_drift",
    "calculate_drift_tax",
    "score_content_dual_match",
    "normalize_quote",
    "verify_quote_grounding",
    "character_error_rate",
    "word_error_rate",
    "binary_brier_score",
    "binary_roc_auc",
    "spearman_rank_correlation",
]
