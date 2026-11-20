"""Text grounding and quote normalization metrics.

Used for verifying that extracted entities (such as action items or defined terms)
are anchored in verbatim source document text rather than hallucinated.
"""

import difflib
import html
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from evals.metrics.classification import compute_precision_recall_f1

_FUZZY_CUTOFF = 0.85
_COVERAGE_FLOOR = 0.5


def normalize_quote(quote: Any) -> str:
    """Normalizes an evidence quote for matching: HTML-unescapes entities first
    (e.g. `&quot;`/`&amp;` turn up in roughly a third of table-embedded
    clauses in real gold fixtures), then collapses whitespace and any other
    non-word character run (quotes -- curly or straight --, stray
    punctuation) to a single space, and casefolds. This mirrors
    `ai_action_items._norm_for_action_item_match`'s tolerance exactly, so
    eval scoring and the live pre-persistence grounding gate agree on what
    counts as "the same quote" -- without this, an entity on one side and
    the literal character it represents on the other normalize to
    different strings even though they're the same content, double-
    penalizing a genuinely correct extraction as both a false positive and
    a false negative.
    """
    s = str(quote or "")
    s = html.unescape(s)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"[\s\W]+", " ", s.casefold()).strip()


def action_item_identifier(item: Dict[str, Any]) -> Tuple[Any, str, str]:
    """Identity tuple for an action item: (page, item_type, normalized evidence_quote).

    Paraphrased item_text is excluded as identity because LLM phrasing varies,
    but verbatim grounded evidence quotes are deterministic. Used for exact
    matching and for the diagnostic `--dump-diff` identity, not for the
    fuzzy bipartite matching `score_action_items` itself does below.
    """
    return (
        item.get("page"),
        item.get("item_type") or "",
        normalize_quote(item.get("evidence_quote")),
    )


def verify_quote_grounding(quote: str, source_text: str) -> bool:
    """Checks whether an evidence quote is grounded in the source page text,
    using the same exact/substring/fuzzy tolerance
    `ai_action_items.locate_action_item_block` already applies live before
    persisting an item -- never a stricter check, since every item this is
    run against has already cleared that live gate once; re-checking with a
    stricter function here would misreport already-validated fuzzy matches
    as "ungrounded"."""
    norm_q = normalize_quote(quote)
    if not norm_q:
        return False
    norm_src = normalize_quote(source_text)
    if norm_q in norm_src:
        return True
    # difflib against the whole page is expensive and noisy for a long page;
    # a grounded quote should appear as a reasonably-sized contiguous
    # window, so this is a best-effort fallback for the same kind of minor
    # OCR drift locate_action_item_block's per-block fuzzy pass handles.
    if len(norm_q) < 8:
        return False
    ratio = difflib.SequenceMatcher(None, norm_q, norm_src).quick_ratio()
    return ratio >= _FUZZY_CUTOFF


def _quote_match_score(norm_gold: str, norm_actual: str) -> Optional[float]:
    """Returns a match score in (0, 1] if two normalized quotes should be
    considered the same real-world item, or None if they're too dissimilar.

    Exact equality scores 1.0. A substring relationship (one quote fully
    contained in the other) only counts when the shorter side covers at
    least half the longer side's length -- this guards against a short,
    weak fragment (e.g. "15 days") spuriously claiming a long, unrelated
    gold quote just because it happens to appear verbatim somewhere inside
    it. A naive `target in candidate` check, reused unguarded from
    `ai_action_items.locate_action_item_block` (designed for "a short
    quote inside one large OCR block", not "two similarly-sized quotes
    compared to each other"), would allow exactly that. Otherwise falls
    back to a difflib ratio, gated at the same 0.85 cutoff the live
    grounding path already uses, so "the same quote" means the same thing
    in both places."""
    if not norm_gold or not norm_actual:
        return None
    if norm_gold == norm_actual:
        return 1.0
    shorter, longer = (
        (norm_gold, norm_actual) if len(norm_gold) <= len(norm_actual) else (norm_actual, norm_gold)
    )
    if shorter in longer:
        coverage = len(shorter) / len(longer)
        if coverage >= _COVERAGE_FLOOR:
            return max(_FUZZY_CUTOFF, coverage)
    ratio = difflib.SequenceMatcher(None, norm_gold, norm_actual).ratio()
    if ratio >= _FUZZY_CUTOFF:
        return ratio

    # Token overlap fallback: prevents difflib's greedy alignment failure
    # when repeated tokens near sentence starts cause difflib to misalign
    # large identical text blocks.
    words_g = set(norm_gold.split())
    words_a = set(norm_actual.split())
    if words_g and words_a:
        intersect = words_g & words_a
        jaccard = len(intersect) / len(words_g | words_a)
        overlap_shorter = len(intersect) / min(len(words_g), len(words_a))
        if jaccard >= _FUZZY_CUTOFF or (overlap_shorter >= 0.75 and len(intersect) >= 6):
            return max(jaccard, _FUZZY_CUTOFF)

    return None


def _bipartite_match_by_quote(
    gold_items: List[Dict[str, Any]],
    actual_items: List[Dict[str, Any]],
) -> Tuple[Set[int], Set[int], List[Tuple[int, int]]]:
    """Two-phase, (page, item_type)-bucketed greedy bipartite match: exact
    normalized-quote equality claims first, then every remaining gold x
    actual pair in the same bucket is scored and claimed in descending
    score order (not first-come order) -- processing best-score pairs
    first is what prevents a weak fragment from stealing a gold item away
    from the real near-exact match that appears later in an arbitrary
    iteration order. A third phase then allows obligation<->condition
    cross-type matches (by page only) for whatever's still unmatched --
    see the phase-3 comment below for why. Returns (matched_gold_indices,
    matched_actual_indices, matched_pairs) -- matched_pairs is the explicit
    (gold_idx, actual_idx) list the per-type breakdown in score_action_items
    needs once a gold item's own type and its matched actual item's type can
    legitimately differ."""
    gold_buckets: Dict[Tuple[Any, str], List[int]] = {}
    actual_buckets: Dict[Tuple[Any, str], List[int]] = {}
    for i, g in enumerate(gold_items):
        gold_buckets.setdefault((g.get("page"), g.get("item_type") or ""), []).append(i)
    for i, a in enumerate(actual_items):
        actual_buckets.setdefault((a.get("page"), a.get("item_type") or ""), []).append(i)

    gold_norm = [normalize_quote(g.get("evidence_quote")) for g in gold_items]
    actual_norm = [normalize_quote(a.get("evidence_quote")) for a in actual_items]

    matched_gold: Set[int] = set()
    matched_actual: Set[int] = set()
    matched_pairs: List[Tuple[int, int]] = []

    for key, g_idxs in gold_buckets.items():
        a_idxs = actual_buckets.get(key)
        if not a_idxs:
            continue

        # Phase 1: exact normalized-quote equality.
        for gi in g_idxs:
            if gi in matched_gold:
                continue
            for ai in a_idxs:
                if ai in matched_actual:
                    continue
                if gold_norm[gi] and gold_norm[gi] == actual_norm[ai]:
                    matched_gold.add(gi)
                    matched_actual.add(ai)
                    matched_pairs.append((gi, ai))
                    break

        # Phase 2: score-ranked fuzzy/substring, best pairs claimed first.
        candidates: List[Tuple[float, int, int]] = []
        for gi in g_idxs:
            if gi in matched_gold:
                continue
            for ai in a_idxs:
                if ai in matched_actual:
                    continue
                score = _quote_match_score(gold_norm[gi], actual_norm[ai])
                if score is not None:
                    candidates.append((score, gi, ai))
        candidates.sort(key=lambda c: -c[0])
        for _score, gi, ai in candidates:
            if gi in matched_gold or ai in matched_actual:
                continue
            matched_gold.add(gi)
            matched_actual.add(ai)
            matched_pairs.append((gi, ai))

    # Phase 3: Cross-type matching on the SAME page for whatever's still unmatched.
    # Scoped to obligation<->condition only -- a deadline or other type must not cross-match.
    cross_candidates: List[Tuple[float, int, int]] = []
    for gi, g in enumerate(gold_items):
        if gi in matched_gold:
            continue
        g_type = g.get("item_type")
        if g_type not in ("obligation", "condition"):
            continue
        for ai, a in enumerate(actual_items):
            if ai in matched_actual:
                continue
            a_type = a.get("item_type")
            if a_type not in ("obligation", "condition"):
                continue
            if a.get("page") != g.get("page"):
                continue
            score = _quote_match_score(gold_norm[gi], actual_norm[ai])
            if score is not None:
                cross_candidates.append((score, gi, ai))
    cross_candidates.sort(key=lambda c: -c[0])
    for _score, gi, ai in cross_candidates:
        if gi in matched_gold or ai in matched_actual:
            continue
        matched_gold.add(gi)
        matched_actual.add(ai)
        matched_pairs.append((gi, ai))
    return matched_gold, matched_actual, matched_pairs


def score_action_items(
    gold_items: List[Dict[str, Any]],
    actual_items: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Scores extracted action items against gold annotations, with overall and per-type breakdowns.

    Matching is a fuzzy bipartite match within (page, item_type) buckets,
    plus an obligation<->condition cross-type pass (see
    `_bipartite_match_by_quote`), not exact-set-intersection -- a model item
    that's genuinely correct and grounds on the right page, but whose quote
    has different span boundaries, an undecoded HTML entity, or minor OCR
    drift relative to gold's recorded quote, now counts as a match instead
    of simultaneously penalizing both precision and recall for the same
    real hit.
    """
    matched_gold, matched_actual, _matched_pairs = _bipartite_match_by_quote(gold_items, actual_items)
    matched_count = len(matched_gold)

    prf = compute_precision_recall_f1(
        matched_count=matched_count,
        actual_count=len(actual_items),
        gold_count=len(gold_items),
    )

    # Per-type breakdown. Computed from each side's OWN matched-set
    # membership, not from one shared `m_t` -- the obligation<->condition
    # cross-type phase means a gold item's own type and its matched actual
    # item's type can now legitimately differ, so "gold items of type t that
    # got matched" (the recall numerator) and "actual items of type t that
    # got matched" (the precision numerator) are no longer always equal.
    # Reusing one combined matched count (as a single call to
    # compute_precision_recall_f1 requires) only worked when matching was
    # same-type-only, where the two numerators always coincided. The
    # formulas below reduce to byte-identical results to the old
    # single-m_t approach whenever that coincidence still holds (verified:
    # recall_t and precision_t's zero-denominator branches exactly mirror
    # compute_precision_recall_f1's own "both empty"/"silent prediction"/
    # "spurious prediction" edge cases), and generalize correctly when it
    # doesn't.
    by_type: Dict[str, Dict[str, Any]] = {}
    for t in ("deadline", "obligation", "condition", "other"):
        g_idx_t = [i for i, g in enumerate(gold_items) if (g.get("item_type") or "") == t]
        a_idx_t = [i for i, a in enumerate(actual_items) if (a.get("item_type") or "") == t]
        g_count, a_count = len(g_idx_t), len(a_idx_t)
        m_recall_t = sum(1 for i in g_idx_t if i in matched_gold)
        m_precision_t = sum(1 for i in a_idx_t if i in matched_actual)

        recall_t = 1.0 if g_count == 0 else m_recall_t / g_count
        if a_count == 0:
            precision_t = 1.0 if g_count == 0 else 0.0
        else:
            precision_t = m_precision_t / a_count
        f1_t = (2.0 * precision_t * recall_t / (precision_t + recall_t)) if (precision_t + recall_t) > 0 else 0.0

        by_type[t] = {
            "precision": precision_t,
            "recall": recall_t,
            "f1": f1_t,
            "gold": g_count,
            "actual": a_count,
            "matched": m_recall_t,
        }

    return {
        "precision": prf["precision"],
        "recall": prf["recall"],
        "f1": prf["f1"],
        "matched_count": matched_count,
        "actual_count": len(actual_items),
        "gold_count": len(gold_items),
        "by_type": by_type,
        "false_positives": [a for i, a in enumerate(actual_items) if i not in matched_actual],
        "false_negatives": [g for i, g in enumerate(gold_items) if i not in matched_gold],
    }
