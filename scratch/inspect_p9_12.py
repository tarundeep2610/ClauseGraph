import json

gold = json.load(open('tests/fixtures/gold_index_bb417471.json'))
d = json.load(open('data/diff_replay_v4.json'))

# Check actual targets on pages 10, 11, 12 in the database or diff
fps = [x for x in d['false_positives'] if 9 <= x['page'] <= 12]
fns = [x for x in d['false_negatives'] if 9 <= x['page'] <= 12]

print(f"Pages 9-12 FPs ({len(fps)}):")
for x in fps:
    safe_lbl = x['label'].encode('ascii', errors='replace').decode('ascii') if x.get('label') else ''
    print(f"  p{x['page']} key={x['key']} scope={x.get('scope_key')} label={safe_lbl[:50]}")

print(f"\nPages 9-12 FNs ({len(fns)}):")
for x in fns:
    safe_lbl = x['label'].encode('ascii', errors='replace').decode('ascii') if x.get('label') else ''
    print(f"  p{x['page']} key={x['key']} scope={x.get('scope_key')} label={safe_lbl[:50]}")
