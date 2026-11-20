import json
from collections import Counter

d = json.load(open('data/diff_full_trial1.json'))
fns = d['false_negatives']

print(f"Total FNs: {len(fns)}")
by_page = Counter([fn['page'] for fn in fns])
print("\nFNs by page:")
for p, c in by_page.most_common(15):
    print(f"  Page {p}: {c} FNs")
    sample = [(fn.get('key'), fn.get('label')[:40]) for fn in fns if fn['page'] == p][:5]
    print(f"    {sample}")

print("\nFNs by family:")
by_fam = Counter([fn.get('key', '').split(':')[0] for fn in fns])
for f, c in by_fam.most_common():
    print(f"  {f}: {c} FNs")
