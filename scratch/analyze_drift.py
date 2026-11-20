import json
import re
from collections import Counter

d = json.load(open('data/diff_full_trial1.json'))
fps = d['false_positives']
fns = d['false_negatives']

gold = json.load(open('tests/fixtures/gold_index_bb417471.json'))['targets']
RULE_FAMILIES = {"page", "term", "docref"}
gold_ai = [t for t in gold if t.get('key', '').split(':')[0] not in RULE_FAMILIES]

_WS_RE = re.compile(r"\s+")
def norm_label(l):
    return _WS_RE.sub(" ", str(l or "")).strip().lower()[:40]

# Map page -> list of fns
fns_by_page = {}
for fn in fns:
    fns_by_page.setdefault(fn['page'], []).append(fn)

fps_by_page = {}
for fp in fps:
    fps_by_page.setdefault(fp['page'], []).append(fp)

# Find content matches that failed key match
content_drift_pairs = []
for p in sorted(fns_by_page.keys()):
    if p not in fps_by_page:
        continue
    page_fns = fns_by_page[p]
    page_fps = fps_by_page[p]
    for fn in page_fns:
        fn_lbl = norm_label(fn.get('label'))
        if not fn_lbl:
            continue
        for fp in page_fps:
            fp_lbl = norm_label(fp.get('label'))
            if fn_lbl == fp_lbl:
                content_drift_pairs.append({
                    "page": p,
                    "label": fn.get('label'),
                    "gold_key": fn.get('key'),
                    "gold_scope": fn.get('scope_key'),
                    "act_key": fp.get('key'),
                    "act_scope": fp.get('scope_key'),
                })
                break

print(f"Total Content-matched but Key-mismatched targets: {len(content_drift_pairs)}")
print("\n--- Breakdown by Gold Family -> Actual Family ---")
family_transitions = Counter([(p['gold_key'].split(':')[0], p['act_key'].split(':')[0]) for p in content_drift_pairs])
for (g_fam, a_fam), cnt in family_transitions.most_common():
    print(f"  {g_fam} -> {a_fam}: {cnt}")

print("\n--- Sample pairs where families are SAME but ordinal index drifted ---")
same_fam = [p for p in content_drift_pairs if p['gold_key'].split(':')[0] == p['act_key'].split(':')[0]]
for s in same_fam[:15]:
    print(f"  P{s['page']}: gold={s['gold_key']} act={s['act_key']} | {s['label'][:50]}")

print("\n--- Sample pairs where families DIFFERED ---")
diff_fam = [p for p in content_drift_pairs if p['gold_key'].split(':')[0] != p['act_key'].split(':')[0]]
for s in diff_fam[:15]:
    print(f"  P{s['page']}: gold={s['gold_key']} act={s['act_key']} | {s['label'][:50]}")
