"""Retroactively estimate how much of the measured precision/recall gap is
explained by scoring strictness (Gap 1) alone, using the already-captured
false_positives/false_negatives lists from the 4 existing (Oct 23)
eval_action-items_*.json reports -- no new live LLM calls needed.

Logic: the proposed two-phase matcher (exact, then score-ranked fuzzy within
(page, item_type) buckets) is strictly more lenient than the current
exact-set-intersection matcher. Anything that matched before still matches
(Phase 1 is still exact match) -- so re-running the new matcher over just the
existing FP list vs FN list recovers exactly the *additional* matches the
relaxed scoring would find, without re-deriving the full actual/gold sets.

new_matched = old_matched + newly_claimed_pairs(old_FP, old_FN)
new_precision = new_matched / actual_count   (actual_count unchanged)
new_recall    = new_matched / gold_count     (gold_count unchanged)
"""
import difflib
import html
import json
import os
import re
import sys

_WS_RE = re.compile(r"\s+")


def norm_lenient(q):
    """html.unescape -> strip non-word chars -> casefold (mirrors the live
    pipeline's _norm_for_action_item_match tolerance, the Step 2 proposal)."""
    s = str(q or "")
    s = html.unescape(s)
    return re.sub(r"[\s\W]+", " ", s.casefold()).strip()


def coverage_ratio(a, b):
    if not a or not b:
        return 0.0
    return min(len(a), len(b)) / max(len(a), len(b))


def match_fp_fn(fps, fns, fuzzy_cutoff=0.85, coverage_floor=0.5):
    """Buckets by (page, item_type), two-phase match: exact first, then
    score-ranked fuzzy/substring with a coverage floor to avoid short
    fragments stealing a long quote's match."""
    # bucket
    buckets = {}
    for i, fp in enumerate(fps):
        key = (fp.get("page"), fp.get("item_type") or "")
        buckets.setdefault(key, {"fp": [], "fn": []})["fp"].append(i)
    for i, fn in enumerate(fns):
        key = (fn.get("page"), fn.get("item_type") or "")
        buckets.setdefault(key, {"fp": [], "fn": []})["fn"].append(i)

    claimed_fp = set()
    claimed_fn = set()
    pairs = []  # (fp_idx, fn_idx, kind, score)

    for key, b in buckets.items():
        fp_idxs, fn_idxs = b["fp"], b["fn"]
        if not fp_idxs or not fn_idxs:
            continue
        norm_fp = {i: norm_lenient(fps[i].get("evidence_quote")) for i in fp_idxs}
        norm_fn = {i: norm_lenient(fns[i].get("evidence_quote")) for i in fn_idxs}

        # Phase 1: exact
        for fi in fn_idxs:
            if fi in claimed_fn:
                continue
            for ai in fp_idxs:
                if ai in claimed_fp:
                    continue
                if norm_fn[fi] and norm_fn[fi] == norm_fp[ai]:
                    claimed_fp.add(ai)
                    claimed_fn.add(fi)
                    pairs.append((ai, fi, "exact", 1.0))
                    break

        # Phase 2: score-ranked fuzzy/substring with coverage floor
        candidates = []
        for fi in fn_idxs:
            if fi in claimed_fn:
                continue
            for ai in fp_idxs:
                if ai in claimed_fp:
                    continue
                g, a = norm_fn[fi], norm_fp[ai]
                if not g or not a:
                    continue
                cov = coverage_ratio(g, a)
                is_sub = (g in a) or (a in g)
                ratio = difflib.SequenceMatcher(None, g, a).ratio()
                if is_sub and cov >= coverage_floor:
                    score = max(ratio, 0.86)  # substring-with-coverage counts as a strong match
                    kind = "substring+coverage"
                elif ratio >= fuzzy_cutoff:
                    score = ratio
                    kind = "fuzzy"
                else:
                    continue
                candidates.append((score, ai, fi, kind))

        candidates.sort(key=lambda c: -c[0])
        for score, ai, fi, kind in candidates:
            if ai in claimed_fp or fi in claimed_fn:
                continue
            claimed_fp.add(ai)
            claimed_fn.add(fi)
            pairs.append((ai, fi, kind, score))

    return pairs


def main():
    base = os.path.join(os.path.dirname(__file__), "existing_eval_reports")
    files = [
        "eval_action-items_gem-bid-dfccil-10p_1792760019.json",
        "eval_action-items_gem-bid-midhani-8p_1792760150.json",
        "eval_action-items_appeal-100p_1792761337.json",
        "eval_action-items_tender-135p_1792762295.json",
    ]
    print(f"{'dataset':<26} {'old P':>7} {'old R':>7} {'new P':>7} {'new R':>7} {'recovered':>10}")
    for fname in files:
        with open(os.path.join(base, fname), encoding="utf-8") as f:
            d = json.load(f)
        t = d["results"]["trials"][0]
        dataset_id = d["dataset_id"]
        gold_count = t["gold_count"]
        actual_count = t["actual_count"]
        old_matched = t["matched_count"]
        fps = t.get("false_positives", [])
        fns = t.get("false_negatives", [])

        pairs = match_fp_fn(fps, fns)
        recovered = len(pairs)
        new_matched = old_matched + recovered

        old_p = old_matched / actual_count if actual_count else 0.0
        old_r = old_matched / gold_count if gold_count else 0.0
        new_p = new_matched / actual_count if actual_count else 0.0
        new_r = new_matched / gold_count if gold_count else 0.0

        print(f"{dataset_id:<26} {old_p:7.3f} {old_r:7.3f} {new_p:7.3f} {new_r:7.3f} {recovered:10d}")

        if pairs:
            print(f"  sample recovered pairs ({min(5, len(pairs))} of {len(pairs)}):")
            for ai, fi, kind, score in pairs[:5]:
                fp, fn = fps[ai], fns[fi]
                print(f"    [{kind} {score:.2f}] page={fp.get('page')} type={fp.get('item_type')}")
                print(f"      FP quote: {(fp.get('evidence_quote') or '')[:100]!r}")
                print(f"      FN quote: {(fn.get('evidence_quote') or '')[:100]!r}")
        print()


if __name__ == "__main__":
    main()
