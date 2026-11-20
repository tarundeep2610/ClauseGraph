"""General classification metrics with explicit edge-case semantics."""

from typing import Any, Dict, Set, Tuple


def compute_precision_recall_f1(
    matched_count: int,
    actual_count: int,
    gold_count: int,
) -> Dict[str, float]:
    """Compute precision, recall, and F1 given match and population counts.

    Edge-case semantics:
    - Both empty (actual=0, gold=0): agreement is perfect -> P=1.0, R=1.0, F1=1.0.
    - Silent prediction (actual=0, gold>0): model produced nothing -> P=0.0, R=0.0, F1=0.0.
    - Spurious prediction (actual>0, gold=0): everything is false positive -> P=0.0, R=1.0, F1=0.0.
    """
    if actual_count == 0 and gold_count == 0:
        return {"precision": 1.0, "recall": 1.0, "f1": 1.0}

    if actual_count == 0 and gold_count > 0:
        return {"precision": 0.0, "recall": 0.0, "f1": 0.0}

    if actual_count > 0 and gold_count == 0:
        return {"precision": 0.0, "recall": 1.0, "f1": 0.0}

    precision = matched_count / max(1, actual_count)
    recall = matched_count / max(1, gold_count)

    if precision + recall > 0:
        f1 = (2.0 * precision * recall) / (precision + recall)
    else:
        f1 = 0.0

    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def compute_set_overlap(
    actual_set: Set[Any],
    gold_set: Set[Any],
) -> Dict[str, Any]:
    """Computes intersection and precision/recall/f1 for two discrete sets."""
    matched = actual_set & gold_set
    scores = compute_precision_recall_f1(
        matched_count=len(matched),
        actual_count=len(actual_set),
        gold_count=len(gold_set),
    )
    return {
        **scores,
        "matched_count": len(matched),
        "actual_count": len(actual_set),
        "gold_count": len(gold_set),
        "false_positives_count": len(actual_set - gold_set),
        "false_negatives_count": len(gold_set - actual_set),
    }
