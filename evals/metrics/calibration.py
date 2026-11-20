"""Calibration metrics for confidence scoring.

Evaluates how well synthesized confidence scores (from confidence.py) correlate
with actual transcription accuracy and human-review necessity.

Two evaluation paradigms:
1. Binary Error Detection:
   - Does needs_review=True accurately flag pages whose CER > threshold?
   - ROC-AUC, Brier score, False Alarm Rate, Missed Error Rate.
2. Continuous Calibration:
   - Spearman rank correlation between confidence [0.0, 1.0] and (1.0 - CER).
"""

from typing import Dict, List, Sequence, Tuple


def binary_brier_score(y_true: Sequence[int], y_prob: Sequence[float]) -> float:
    """Computes Brier Score: mean squared error between binary outcomes y_true in {0, 1}

    and predicted probabilities y_prob in [0.0, 1.0].
    Lower is better (0.0 is perfect calibration).
    """
    if len(y_true) != len(y_prob):
        raise ValueError("y_true and y_prob must have identical lengths")
    if len(y_true) == 0:
        return 0.0

    total_sq_err = sum((p - y) ** 2 for y, p in zip(y_true, y_prob))
    return total_sq_err / len(y_true)


def binary_roc_auc(y_true: Sequence[int], y_score: Sequence[float]) -> float:
    """Computes Area Under the ROC Curve (ROC-AUC) for binary targets via Wilcoxon-Mann-Whitney.

    Returns float in [0.0, 1.0]. 0.5 is random guessing, 1.0 is perfect discrimination.
    """
    if len(y_true) != len(y_score):
        raise ValueError("y_true and y_score must have identical lengths")

    pos = [s for y, s in zip(y_true, y_score) if y == 1]
    neg = [s for y, s in zip(y_true, y_score) if y == 0]

    n_pos, n_neg = len(pos), len(neg)
    if n_pos == 0 or n_neg == 0:
        return 1.0  # Trivial single-class case

    # Count pairs where pos > neg (0.5 for ties)
    concordant = 0.0
    for p in pos:
        for n in neg:
            if p > n:
                concordant += 1.0
            elif p == n:
                concordant += 0.5

    return concordant / (n_pos * n_neg)


def binary_error_detection_summary(
    y_true: Sequence[int],
    y_pred: Sequence[int],
) -> Dict[str, float]:
    """Computes false alarm rate (type I) and missed error rate (type II) for review flags."""
    if len(y_true) != len(y_pred):
        raise ValueError("y_true and y_pred must have identical lengths")

    total = len(y_true)
    if total == 0:
        return {
            "false_alarm_rate": 0.0,
            "missed_error_rate": 0.0,
            "accuracy": 1.0,
        }

    tp = sum(1 for yt, yp in zip(y_true, y_pred) if yt == 1 and yp == 1)
    tn = sum(1 for yt, yp in zip(y_true, y_pred) if yt == 0 and yp == 0)
    fp = sum(1 for yt, yp in zip(y_true, y_pred) if yt == 0 and yp == 1)
    fn = sum(1 for yt, yp in zip(y_true, y_pred) if yt == 1 and yp == 0)

    actual_negatives = tn + fp
    actual_positives = tp + fn

    false_alarm_rate = (fp / actual_negatives) if actual_negatives > 0 else 0.0
    missed_error_rate = (fn / actual_positives) if actual_positives > 0 else 0.0
    accuracy = (tp + tn) / total

    return {
        "false_alarm_rate": false_alarm_rate,
        "missed_error_rate": missed_error_rate,
        "accuracy": accuracy,
        "true_positives": tp,
        "true_negatives": tn,
        "false_positives": fp,
        "false_negatives": fn,
    }


def _rankdata(seq: Sequence[float]) -> List[float]:
    """Assigns fractional ranks to data, resolving ties with average rank."""
    n = len(seq)
    sorted_indices = sorted(range(n), key=lambda i: seq[i])
    ranks = [0.0] * n

    i = 0
    while i < n:
        j = i
        while j < n - 1 and seq[sorted_indices[j]] == seq[sorted_indices[j + 1]]:
            j += 1
        avg_rank = 1.0 + (i + j) / 2.0
        for k in range(i, j + 1):
            ranks[sorted_indices[k]] = avg_rank
        i = j + 1

    return ranks


def spearman_rank_correlation(x: Sequence[float], y: Sequence[float]) -> float:
    """Computes Spearman's rank correlation coefficient between two continuous sequences.

    Returns float in [-1.0, 1.0].
    """
    if len(x) != len(y):
        raise ValueError("x and y must have identical lengths")
    n = len(x)
    if n < 2:
        return 1.0

    rank_x = _rankdata(x)
    rank_y = _rankdata(y)

    mean_x = sum(rank_x) / n
    mean_y = sum(rank_y) / n

    cov = sum((rx - mean_x) * (ry - mean_y) for rx, ry in zip(rank_x, rank_y))
    var_x = sum((rx - mean_x) ** 2 for rx in rank_x)
    var_y = sum((ry - mean_y) ** 2 for ry in rank_y)

    if var_x == 0 or var_y == 0:
        return 0.0

    return cov / ((var_x * var_y) ** 0.5)
