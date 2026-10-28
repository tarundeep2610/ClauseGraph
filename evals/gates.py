"""Profile-aware regression gating engine.

Enforces minimum precision/recall thresholds, maximum error rates, and zero-regression
invariants on rule-based control baselines.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List

from evals.settings import ACTION_ITEMS_GATES, INDEX_GATES, OCR_GATES


class GateBreachError(Exception):
    """Raised when an evaluation run breaches fatal regression thresholds."""

    pass


@dataclass
class GateEvaluationResult:
    """Outcome of evaluating run metrics against quality gates."""

    passed: bool
    violations: List[str] = field(default_factory=list)
    profile_name: str = ""
    metrics: Dict[str, Any] = field(default_factory=dict)


def evaluate_index_gates(
    metrics: Dict[str, Any],
) -> GateEvaluationResult:
    """Evaluates indexing metrics against universal quality gates (P>=0.85, R>=0.95)."""
    violations: List[str] = []

    # Check Merged Precision
    merged_p = metrics.get("target_precision", 0.0)
    if merged_p < INDEX_GATES["min_merged_precision"]:
        violations.append(
            f"Merged Precision {merged_p:.3f} fell below minimum {INDEX_GATES['min_merged_precision']:.3f}"
        )

    # Check Merged Recall
    merged_r = metrics.get("target_recall", 0.0)
    if merged_r < INDEX_GATES["min_merged_recall"]:
        violations.append(
            f"Merged Recall {merged_r:.3f} fell below minimum {INDEX_GATES['min_merged_recall']:.3f}"
        )

    # Invariant: Rule-only control recall must NEVER regress
    rule_r = metrics.get("rule_recall", 1.0)
    if rule_r < INDEX_GATES["min_rule_recall"]:
        violations.append(
            f"FATAL INVARIANT BREACH: Rule-only recall {rule_r:.3f} fell below 1.000 deterministic invariance"
        )

    # Mention Precision / Recall
    mention_p = metrics.get("mention_precision", 1.0)
    if mention_p < INDEX_GATES["min_mention_precision"]:
        violations.append(
            f"Mention Precision {mention_p:.3f} fell below minimum {INDEX_GATES['min_mention_precision']:.3f}"
        )

    mention_r = metrics.get("mention_recall", 1.0)
    if mention_r < INDEX_GATES["min_mention_recall"]:
        violations.append(
            f"Mention Recall {mention_r:.3f} fell below minimum {INDEX_GATES['min_mention_recall']:.3f}"
        )

    # Family drift
    drift = metrics.get("family_drift_count", 0)
    if drift > INDEX_GATES["max_family_drift"]:
        violations.append(
            f"Family drift count {drift} exceeded maximum {INDEX_GATES['max_family_drift']}"
        )

    return GateEvaluationResult(
        passed=len(violations) == 0,
        violations=violations,
        profile_name="Index Gates",
        metrics=metrics,
    )


def evaluate_action_items_gates(metrics: Dict[str, Any]) -> GateEvaluationResult:
    """Evaluates action item metrics against quality thresholds."""
    violations: List[str] = []

    p = metrics.get("precision", 0.0)
    if p < ACTION_ITEMS_GATES["min_precision"]:
        violations.append(f"Precision {p:.3f} fell below minimum {ACTION_ITEMS_GATES['min_precision']:.3f}")

    r = metrics.get("recall", 0.0)
    if r < ACTION_ITEMS_GATES["min_recall"]:
        violations.append(f"Recall {r:.3f} fell below minimum {ACTION_ITEMS_GATES['min_recall']:.3f}")

    min_gr = ACTION_ITEMS_GATES.get("min_grounding_rate")
    if min_gr is not None:
        gr = metrics.get("grounding_rate", 1.0)
        if gr < min_gr:
            violations.append(f"Grounding rate {gr:.3f} fell below minimum {min_gr:.3f}")

    return GateEvaluationResult(
        passed=len(violations) == 0,
        violations=violations,
        profile_name="Action Items Default",
        metrics=metrics,
    )


def evaluate_ocr_gates(metrics: Dict[str, Any]) -> GateEvaluationResult:
    """Evaluates OCR transcription error rates against quality gates."""
    violations: List[str] = []

    mean_cer = metrics.get("mean_cer", 0.0)
    cat_cer = metrics.get("cer_by_category", {})
    clean_cer = cat_cer.get("clean_legal", mean_cer)

    if clean_cer > OCR_GATES["max_cer_clean"]:
        violations.append(
            f"Clean text CER {clean_cer:.4f} exceeded maximum threshold {OCR_GATES['max_cer_clean']:.4f}"
        )

    # Degraded/garbled scans get a looser ceiling than clean text (max_cer_degraded)
    # rather than max_cer_clean's tighter one — this was previously defined in
    # OCR_GATES but never actually checked against anything, so a degraded-scan
    # page's CER could never fail this gate no matter how bad it got.
    if "degraded_scan" in cat_cer:
        degraded_cer = cat_cer["degraded_scan"]
        if degraded_cer > OCR_GATES["max_cer_degraded"]:
            violations.append(
                f"Degraded-scan CER {degraded_cer:.4f} exceeded maximum threshold {OCR_GATES['max_cer_degraded']:.4f}"
            )

    mean_wer = metrics.get("mean_wer", 0.0)
    if mean_wer > OCR_GATES["max_wer"]:
        violations.append(
            f"Mean WER {mean_wer:.4f} exceeded maximum threshold {OCR_GATES['max_wer']:.4f}"
        )

    return GateEvaluationResult(
        passed=len(violations) == 0,
        violations=violations,
        profile_name="OCR Error Gates",
        metrics=metrics,
    )


def enforce_gates(result: GateEvaluationResult) -> None:
    """Raises GateBreachError if any violation occurred."""
    if not result.passed:
        violations_str = "\n  - " + "\n  - ".join(result.violations)
        raise GateBreachError(
            f"Evaluation gates breached for profile '{result.profile_name}':{violations_str}"
        )
