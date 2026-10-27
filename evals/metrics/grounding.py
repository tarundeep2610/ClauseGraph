"""Text grounding and quote normalization metrics.

Used for verifying that extracted entities (such as action items or defined terms)
are anchored in verbatim source document text rather than hallucinated.
"""

import re
from typing import Any, Dict, List, Optional, Set, Tuple

from evals.metrics.classification import compute_precision_recall_f1

_WS_RE = re.compile(r"\s+")
_CURLY_QUOTES = {
    "\u201c": '"',
    "\u201d": '"',
    "\u2018": "'",
    "\u2019": "'",
}


def normalize_quote(quote: Any) -> str:
    """Normalizes an evidence quote by collapsing whitespace, replacing curly quotes,

    and folding case for stable string matching.
    """
    s = str(quote or "")
    for curly, straight in _CURLY_QUOTES.items():
        s = s.replace(curly, straight)
    return _WS_RE.sub(" ", s).strip().casefold()


def action_item_identifier(item: Dict[str, Any]) -> Tuple[Any, str, str]:
    """Identity tuple for an action item: (page, item_type, normalized evidence_quote).

    Paraphrased item_text is excluded as identity because LLM phrasing varies,
    but verbatim grounded evidence quotes are deterministic.
    """
    return (
        item.get("page"),
        item.get("item_type") or "",
        normalize_quote(item.get("evidence_quote")),
    )


def verify_quote_grounding(quote: str, source_text: str) -> bool:
    """Checks whether an evidence quote exists verbatim in the source page text (normalized)."""
    norm_q = normalize_quote(quote)
    if not norm_q:
        return False
    norm_src = normalize_quote(source_text)
    return norm_q in norm_src


def score_action_items(
    gold_items: List[Dict[str, Any]],
    actual_items: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Scores extracted action items against gold annotations, with overall and per-type breakdowns."""
    gold_ids = {action_item_identifier(i) for i in gold_items}
    actual_ids = {action_item_identifier(i) for i in actual_items}
    matched = gold_ids & actual_ids

    prf = compute_precision_recall_f1(
        matched_count=len(matched),
        actual_count=len(actual_ids),
        gold_count=len(gold_ids),
    )

    # Per-type breakdown
    by_type: Dict[str, Dict[str, Any]] = {}
    for t in ("deadline", "obligation", "condition", "other"):
        g_t = {i for i in gold_ids if i[1] == t}
        a_t = {i for i in actual_ids if i[1] == t}
        m_t = g_t & a_t
        t_prf = compute_precision_recall_f1(
            matched_count=len(m_t),
            actual_count=len(a_t),
            gold_count=len(g_t),
        )
        by_type[t] = {
            **t_prf,
            "gold": len(g_t),
            "actual": len(a_t),
            "matched": len(m_t),
        }

    return {
        "precision": prf["precision"],
        "recall": prf["recall"],
        "f1": prf["f1"],
        "matched_count": len(matched),
        "actual_count": len(actual_ids),
        "gold_count": len(gold_ids),
        "by_type": by_type,
        "false_positives": [a for a in actual_items if action_item_identifier(a) not in matched],
        "false_negatives": [g for g in gold_items if action_item_identifier(g) not in matched],
    }
