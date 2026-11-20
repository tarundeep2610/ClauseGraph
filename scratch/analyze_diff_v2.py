import json
from collections import Counter

d = json.load(open('data/diff_replay_v2.json'))
fps = d['false_positives']
fns = d['false_negatives']

print(f"Total FPs: {len(fps)}, Total FNs: {len(fns)}")

print("\n--- Family breakdown of FPs ---")
fp_families = Counter([x.get('key', '').split(':')[0] for x in fps])
for fam, count in fp_families.most_common():
    print(f"  {fam}: {count}")

print("\n--- Top FP Pages ---")
fp_pages = Counter([x['page'] for x in fps])
for p, c in fp_pages.most_common(10):
    print(f"  Page {p}: {c} FPs")
    samples = [x.get('label')[:40] for x in fps if x['page'] == p][:3]
    print(f"    Samples: {samples}")

print("\n--- Top FN Pages ---")
fn_pages = Counter([x['page'] for x in fns])
for p, c in fn_pages.most_common(10):
    print(f"  Page {p}: {c} FNs")
