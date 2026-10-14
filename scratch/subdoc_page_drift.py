import json
from collections import Counter

d = json.load(open('data/diff_full_trial1.json'))
fps = d['false_positives']
fns = d['false_negatives']
gold = json.load(open('tests/fixtures/gold_index_bb417471.json'))['targets']

gold_subdocs = [t for t in gold if t.get('key', '').startswith('subdoc:')]
fp_subdocs = [t for t in fps if t.get('key', '').startswith('subdoc:')]

print(f"Total Gold subdocs: {len(gold_subdocs)}")
print(f"Total FP subdocs: {len(fp_subdocs)}")

gold_by_page = Counter([t['page'] for t in gold_subdocs])
fp_by_page = Counter([t['page'] for t in fp_subdocs])

all_pages = sorted(set(list(gold_by_page.keys()) + list(fp_by_page.keys())))

cum_diff = 0
print(f"{'Page':<6} {'Gold Subdocs':<14} {'FP Subdocs':<12} {'Cum Extra Subdocs':<18} {'Sample FP Labels'}")
print("-" * 80)
for p in all_pages:
    if p > 88:
        continue
    g_cnt = gold_by_page.get(p, 0)
    fp_cnt = fp_by_page.get(p, 0)
    cum_diff += fp_cnt
    sample = [x.get('label', '')[:30] for x in fp_subdocs if x['page'] == p][:3]
    if fp_cnt > 0 or g_cnt > 0:
        print(f"{p:<6} {g_cnt:<14} {fp_cnt:<12} {cum_diff:<18} {sample}")
