"""Structural hierarchy and reference indexing metrics.

Evaluates:
- Exact target matches on (page, normalized_key, occurrence_index)
- Scope-path exact match accuracy
- Family drift (content captured on same page but assigned to wrong family prefix)
- Dual-match content scoring & Ordinal drift tax (renumbering vs omission)
"""

import re
from typing import Any, Dict, List, Optional, Set, Tuple

from evals.metrics.classification import compute_precision_recall_f1

_WS_RE = re.compile(r"\s+")
RULE_FAMILIES = {"page", "term", "docref"}


def normalize_key_for_scoring(key: Optional[str]) -> str:
    """Normalizes key prefixes so point:N and numheading:N align for historical compatibility."""
    if key and key.startswith("point:") and re.match(r"^point:\d+(\.|$)", key):
        return "numheading:" + key[len("point:"):]
    return key or ""


def target_identifier(target: Dict[str, Any]) -> Tuple[Any, str, int]:
    """Generates the primary identification tuple for a target: (page, normalized_key, occurrence_index)."""
    return (
        target.get("page"),
        normalize_key_for_scoring(target.get("key")),
        target.get("occurrence_index") or 0,
    )


def extract_family(key: Optional[str]) -> str:
    """Extracts the prefix family e.g. 'subdoc' from 'subdoc:judgment'."""
    if key and ":" in key:
        return key.split(":", 1)[0]
    return key or ""


def normalize_target_label(label: Any, max_len: int = 40) -> str:
    """Normalizes label text for fuzzy / dual-content comparison."""
    return _WS_RE.sub(" ", str(label or "")).strip().lower()[:max_len]


def calculate_family_drift(
    gold_targets: List[Dict[str, Any]],
    actual_targets: List[Dict[str, Any]],
    matched_ids: Set[Tuple[Any, str, int]],
) -> int:
    """Counts targets captured on the same page with the same label but classified

    under a different family prefix (e.g. leaf point vs container subdoc).
    """
    unmatched_gold = [t for t in gold_targets if target_identifier(t) not in matched_ids]
    unmatched_actual = [t for t in actual_targets if target_identifier(t) not in matched_ids]
    used_actual_indices = set()
    drift = 0

    for g in unmatched_gold:
        g_label = normalize_target_label(g.get("label"))
        if not g_label:
            continue
        for i, a in enumerate(unmatched_actual):
            if i in used_actual_indices or a.get("page") != g.get("page"):
                continue
            if (
                normalize_target_label(a.get("label")) == g_label
                and extract_family(a.get("key")) != extract_family(g.get("key"))
            ):
                drift += 1
                used_actual_indices.add(i)
                break

    return drift


def score_target_slice(
    gold_targets: List[Dict[str, Any]],
    actual_targets: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Scores a slice of targets (e.g. AI-only, Rule-only, or Merged) for

    precision, recall, scope-path accuracy, and family drift.
    """
    gold_ids = {target_identifier(t) for t in gold_targets}
    actual_ids = {target_identifier(t) for t in actual_targets}
    matched = gold_ids & actual_ids

    prf = compute_precision_recall_f1(
        matched_count=len(matched),
        actual_count=len(actual_ids),
        gold_count=len(gold_ids),
    )

    # Scope accuracy across matched targets
    gold_scope_map = {target_identifier(t): (t.get("scope_key") or "") for t in gold_targets}
    actual_scope_map = {target_identifier(t): (t.get("scope_key") or "") for t in actual_targets}

    scope_matches = sum(
        1 for tid in matched if gold_scope_map.get(tid) == actual_scope_map.get(tid)
    )
    scope_acc = (scope_matches / len(matched)) if matched else 1.0

    family_drift = calculate_family_drift(gold_targets, actual_targets, matched)

    return {
        "precision": prf["precision"],
        "recall": prf["recall"],
        "f1": prf["f1"],
        "scope_accuracy": scope_acc,
        "family_drift": family_drift,
        "matched_count": len(matched),
        "actual_count": len(actual_ids),
        "gold_count": len(gold_ids),
        "matched_ids": matched,
    }


def score_content_dual_match(
    gold_targets: List[Dict[str, Any]],
    actual_targets: List[Dict[str, Any]],
    matched_key_ids: Set[Tuple[Any, str, int]],
) -> Dict[str, Any]:
    """Scores targets on label content similarity on the same page,

    comparing content recall against exact key recall to compute ordinal drift tax.
    """
    labeled_gold = [g for g in gold_targets if normalize_target_label(g.get("label"))]
    used_actual_indices = set()
    content_matched = 0

    for g in labeled_gold:
        g_label = normalize_target_label(g.get("label"))
        for i, a in enumerate(actual_targets):
            if i in used_actual_indices or a.get("page") != g.get("page"):
                continue
            if normalize_target_label(a.get("label")) == g_label:
                content_matched += 1
                used_actual_indices.add(i)
                break

    labeled_key_matched = sum(1 for g in labeled_gold if target_identifier(g) in matched_key_ids)
    labeled_count = len(labeled_gold)
    key_r = (labeled_key_matched / labeled_count) if labeled_count > 0 else 1.0
    content_r = (content_matched / labeled_count) if labeled_count > 0 else 1.0
    drift_tax = content_r - key_r

    return {
        "labeled_gold_count": labeled_count,
        "labeled_key_matched": labeled_key_matched,
        "labeled_content_matched": content_matched,
        "labeled_key_recall": key_r,
        "labeled_content_recall": content_r,
        "drift_tax": drift_tax,
    }


def calculate_drift_tax(
    gold_targets: List[Dict[str, Any]],
    actual_targets: List[Dict[str, Any]],
    matched_key_ids: Set[Tuple[Any, str, int]],
) -> float:
    """Helper to return only the drift tax float value."""
    res = score_content_dual_match(gold_targets, actual_targets, matched_key_ids)
    return res["drift_tax"]
