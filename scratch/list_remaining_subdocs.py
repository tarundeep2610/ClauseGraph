import json
from collections import Counter

d = json.load(open('data/diff_replay_v3.json'))
fps = d['false_positives']
gold = json.load(open('tests/fixtures/gold_index_bb417471.json'))['targets']

gold_subdocs = [t for t in gold if t.get('key', '').startswith('subdoc:')]
fp_subdocs = [t for t in fps if t.get('key', '').startswith('subdoc:') and t.get('page', 0) < 88]

print(f"Total FP subdocs before page 88: {len(fp_subdocs)}")
for fp in fp_subdocs:
    print(f"  P{fp['page']} key={fp['key']}: {repr(fp.get('label'))}")
