"""Categorize false positives across all post-fix slice runs by item_type,
and dump a sample of each type's worst offenders for manual inspection."""
import json
import os
import collections

BASE = os.path.join(os.path.dirname(__file__), "baseline_post_fix_1")
FILES = [
    "postfix1_tender_1-30_diff.json",
    "postfix1_tender_84-126_off_diff.json",
    "postfix1_tender_84-126_on_diff.json",
    "postfix1_tender_127-135_diff.json",
    "postfix1_appeal_1-15_diff.json",
    "postfix1_appeal_55-70_diff.json",
    "postfix1_appeal_80-90_diff.json",
    "postfix1_appeal_16-36_diff.json",
    "postfix1_dfccil_diff.json",
    "postfix1_midhani_diff.json",
]

by_type_count = collections.Counter()
by_type_samples = collections.defaultdict(list)

for fname in FILES:
    path = os.path.join(BASE, fname)
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    for fp in d.get("false_positives", []):
        t = fp.get("item_type") or "?"
        by_type_count[t] += 1
        by_type_samples[t].append((fname, fp))

print("False positive counts by item_type (across all 10 runs):")
for t, c in by_type_count.most_common():
    print(f"  {t:<12} {c}")
print()

for t in ("obligation", "condition", "other", "deadline"):
    samples = by_type_samples.get(t, [])
    print(f"=== {t} ({len(samples)} total FPs) — 8 samples ===")
    for fname, fp in samples[:8]:
        print(f"  [{fname}] page={fp.get('page')}")
        print(f"    item_text: {(fp.get('item_text') or '')[:110]}")
        print(f"    quote: {(fp.get('evidence_quote') or '')[:130]}")
    print()
