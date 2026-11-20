import json

with open("scratch/diff_89cfa529_two_pass_v3.json") as f:
    diff = json.load(f)

for page in [44, 3, 60, 61, 30, 34, 15]:
    fps = [t for t in diff.get("false_positives", []) if t.get("page") == page]
    fns = [t for t in diff.get("false_negatives", []) if t.get("page") == page]
    print(f"\n=== PAGE {page}: {len(fps)} FPs, {len(fns)} FNs ===")
    if fps:
        print("  FPs:")
        for t in fps[:6]:
            lbl = (t.get("label") or "")[:45]
            print(f"    key={t.get('key')} fam={t.get('family')} lbl={lbl!r}")
    if fns:
        print("  FNs:")
        for t in fns[:6]:
            lbl = (t.get("label") or "")[:45]
            print(f"    key={t.get('key')} fam={t.get('family')} lbl={lbl!r}")
