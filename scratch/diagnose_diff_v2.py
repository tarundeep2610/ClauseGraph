import json
from collections import Counter

with open("scratch/diff_89cfa529_two_pass_v2.json") as f:
    diff = json.load(f)

for page in [68, 7, 61, 30, 34, 35, 58, 59]:
    fps = [t for t in diff.get("false_positives", []) if t.get("page") == page]
    fns = [t for t in diff.get("false_negatives", []) if t.get("page") == page]
    print(f"\n=== PAGE {page}: {len(fps)} FPs, {len(fns)} FNs ===")
    print("  FPs:")
    for t in fps[:6]:
        lbl = (t.get("label") or "")[:50]
        print(f"    key={t.get('key')} fam={t.get('family')} lbl={lbl!r}")
    print("  FNs:")
    for t in fns[:6]:
        lbl = (t.get("label") or "")[:50]
        print(f"    key={t.get('key')} fam={t.get('family')} lbl={lbl!r}")
