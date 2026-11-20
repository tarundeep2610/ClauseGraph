import json
import sys

gold = json.load(open('tests/fixtures/gold_index_bb417471.json'))
d = json.load(open('data/diff_replay_v4.json'))

gold_subdocs = [t for t in gold['targets'] if t.get('key','').startswith('subdoc:') and t['page'] < 88]
print(f"Gold subdocs before page 88: {len(gold_subdocs)}")
for t in gold_subdocs:
    safe_lbl = t['label'].encode('ascii', errors='replace').decode('ascii') if t.get('label') else ''
    print(f"  Gold Page {t['page']}: key={t['key']} label={safe_lbl[:60]}")

fps_subdocs = [x for x in d['false_positives'] if x.get('key','').startswith('subdoc:') and x['page'] < 88]
print(f"\nFP subdocs before page 88: {len(fps_subdocs)}")
for x in fps_subdocs:
    safe_lbl = x['label'].encode('ascii', errors='replace').decode('ascii') if x.get('label') else ''
    print(f"  FP Page {x['page']}: key={x['key']} label={safe_lbl[:60]}")
