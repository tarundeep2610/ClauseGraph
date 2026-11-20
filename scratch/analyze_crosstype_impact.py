"""Retroactively estimate how much allowing obligation<->condition cross-type
matching would recover, using already-collected false_positive/false_negative
lists -- no new live calls. Mirrors analyze_gap1_impact.py's technique."""
import difflib
import html
import json
import os
import re

_FUZZY_CUTOFF = 0.85


def norm(q):
    s = html.unescape(str(q or ""))
    return re.sub(r"[\s\W]+", " ", s.casefold()).strip()


def quote_score(a, b):
    if not a or not b:
        return None
    if a == b:
        return 1.0
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if shorter in longer and len(shorter) / len(longer) >= 0.5:
        return max(_FUZZY_CUTOFF, len(shorter) / len(longer))
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    return ratio if ratio >= _FUZZY_CUTOFF else None


def cross_type_recover(fps, fns):
    """Only considers obligation<->condition pairs on the SAME page that
    the current (same-type-bucketed) scorer would never compare."""
    recovered = []
    used_fp, used_fn = set(), set()
    pairs = {"obligation": "condition", "condition": "obligation"}
    candidates = []
    for fi, fn in enumerate(fns):
        if fn.get("item_type") not in pairs:
            continue
        for ai, fp in enumerate(fps):
            if fp.get("item_type") != pairs[fn["item_type"]]:
                continue
            if fp.get("page") != fn.get("page"):
                continue
            score = quote_score(norm(fn.get("evidence_quote")), norm(fp.get("evidence_quote")))
            if score is not None:
                candidates.append((score, fi, ai))
    candidates.sort(key=lambda c: -c[0])
    for score, fi, ai in candidates:
        if fi in used_fn or ai in used_fp:
            continue
        used_fn.add(fi)
        used_fp.add(ai)
        recovered.append((score, fns[fi], fps[ai]))
    return recovered


def main():
    base = os.path.dirname(__file__)
    base = os.path.join(base, "baseline_post_fix_1")
    files = [
        "comp_tender_1-30_diff.json",
        "comp_tender_127-135_diff.json",
        "comp_dfccil_diff.json",
        "gemini_v5_midhani_diff.json",
        "gemini_v5_dfccil_diff.json",
        "gemini_v5_appeal_1-15_diff.json",
    ]
    for fname in files:
        path = os.path.join(base, fname)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        fps, fns = d.get("false_positives", []), d.get("false_negatives", [])
        recovered = cross_type_recover(fps, fns)
        print(f"=== {fname} ===  FP={len(fps)} FN={len(fns)}  cross-type recoverable: {len(recovered)}")
        for score, fn, fp in recovered:
            print(f"  [{score:.2f}] page={fn.get('page')} gold={fn.get('item_type')}/actual={fp.get('item_type')}")
            print(f"     gold: {fn.get('item_text','')[:80]}")
            print(f"     act:  {fp.get('item_text','')[:80]}")
        print()


if __name__ == "__main__":
    main()
