import json
from collections import Counter

d = json.load(open('data/diff_full_trial1.json'))
fps = d['false_positives']
fns = d['false_negatives']

print(f"Total FPs: {len(fps)}, Total FNs: {len(fns)}")

print("\n--- Sample FPs on Page 91 ---")
for fp in [x for x in fps if x['page'] == 91][:10]:
    print(f"key={fp.get('key')} scope={fp.get('scope_key')} label={fp.get('label', '')[:60]}")

print("\n--- Sample FPs on Page 76 ---")
for fp in [x for x in fps if x['page'] == 76][:10]:
    print(f"key={fp.get('key')} scope={fp.get('scope_key')} label={fp.get('label', '')[:60]}")

print("\n--- Sample FPs on Page 88 ---")
for fp in [x for x in fps if x['page'] == 88][:10]:
    print(f"key={fp.get('key')} scope={fp.get('scope_key')} label={fp.get('label', '')[:60]}")

print("\n--- Sample FNs on Page 88 ---")
for fn in [x for x in fns if x['page'] == 88][:10]:
    print(f"key={fn.get('key')} scope={fn.get('scope_key')} label={fn.get('label', '')[:60]}")

print("\n--- Family breakdown of FPs ---")
fp_families = Counter([x.get('key', '').split(':')[0] for x in fps])
for fam, count in fp_families.most_common():
    print(f"  {fam}: {count}")

print("\n--- Subdoc FPs by Page ---")
subdoc_fps_by_page = Counter([x['page'] for x in fps if x.get('key', '').startswith('subdoc:')])
for page, count in subdoc_fps_by_page.most_common(15):
    print(f"  Page {page}: {count} subdocs")
    sample_labels = [x.get('label') for x in fps if x['page'] == page and x.get('key', '').startswith('subdoc:')][:5]
    print(f"    Samples: {sample_labels}")

print("\n--- Family breakdown of FNs ---")
fn_families = Counter([x.get('key', '').split(':')[0] for x in fns])
for fam, count in fn_families.most_common():
    print(f"  {fam}: {count}")
