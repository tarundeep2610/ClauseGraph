import glob
import json
from collections import Counter

latest = sorted(glob.glob('/app/data/eval_reports/eval_index_appeal-100p_*.json'))[-1]
print(f"Reading: {latest}")
data = json.load(open(latest))
d = data['results']['last_diff']
fps = d['false_positives']
fns = d['false_negatives']

print(f"Total FPs: {len(fps)}, Total FNs: {len(fns)}")

fp_pages = Counter([x['page'] for x in fps])
print("\nTop 15 FP Pages:")
for p, c in fp_pages.most_common(15):
    print(f"  Page {p}: {c} FPs")

fn_pages = Counter([x['page'] for x in fns])
print("\nTop 10 FN Pages:")
for p, c in fn_pages.most_common(10):
    print(f"  Page {p}: {c} FNs")

print("\nCheck Sentinel Gold Targets under Price Schedule (pages 86-88):")
sentinels = [
    'subdoc:payment-terms',
    'subdoc:stage-penalty',
    'subdoc:taxes-duties',
    'subdoc:contractor-s-liability',
    'subdoc:insurance',
    'subdoc:performance-security'
]
for s in sentinels:
    in_fn = any(x.get('key') == s for x in fns)
    print(f"  {s}: {'MISSING (in FNs)!' if in_fn else 'MATCHED (NOT in FNs)!'}")

print("\nPages 78-85 FPs:")
p78_85_fps = [x for x in fps if 78 <= x['page'] <= 85]
print(f"  Total FPs on pp. 78-85: {len(p78_85_fps)}")
for x in p78_85_fps[:15]:
    print(f"    p{x['page']} {x.get('key')} {x.get('label', '')[:60]}")

print("\nPages 90-95 FPs:")
p90_95_fps = [x for x in fps if 90 <= x['page'] <= 95]
print(f"  Total FPs on pp. 90-95: {len(p90_95_fps)}")
for x in p90_95_fps[:15]:
    print(f"    p{x['page']} {x.get('key')} {x.get('label', '')[:60]}")

print("\nPages with highest FPs sample labels:")
for p, _ in fp_pages.most_common(5):
    print(f"\n--- Page {p} ---")
    for x in [x for x in fps if x['page'] == p][:5]:
        print(f"  {x.get('key')}: {x.get('label', '')[:70]}")
