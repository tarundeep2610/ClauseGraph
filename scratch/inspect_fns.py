import json

d = json.load(open('data/diff_replay_v4.json'))
fns = d['false_negatives']

pages = [64, 12, 38, 57, 9, 10, 46, 63, 8, 11]
for p in pages:
    page_fns = [x for x in fns if x['page'] == p]
    print(f"\n=== Page {p} ({len(page_fns)} FNs) ===")
    for x in page_fns:
        safe_lbl = x['label'].encode('ascii', errors='replace').decode('ascii') if x.get('label') else ''
        print(f"  key={x['key']:<15} scope={x.get('scope_key',''):<30} label={safe_lbl[:60]}")
