import json
import sys
sys.path.insert(0, '.')

import reference_index
import ai_reference_index

# Let's inspect the actual targets in the database or rerun chunk 7..12
d = json.load(open('data/diff_replay_v4.json'))
# Let's see what was persisted in the actual run for pages 7-12
gold = json.load(open('tests/fixtures/gold_index_bb417471.json'))
g_p9_12 = [t for t in gold['targets'] if 7 <= t['page'] <= 12]
print("Gold targets 7-12:", len(g_p9_12))
for t in g_p9_12:
    print(f"  p{t['page']} key={t['key']} scope={t.get('scope_key')} label={repr(t.get('label'))[:50]}")
