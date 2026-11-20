import sys, os, json
sys.path.insert(0, os.path.abspath('.'))
import database

pages = database.get_pages('0a42f322-858a-45c3-90af-30438476884c')
print(f"Total pages: {len(pages)}")

with open('tests/fixtures/gold_index_0a42f322.json', 'r', encoding='utf-8') as f:
    g_idx = json.load(f)

with open('tests/fixtures/gold_action_items_0a42f322.json', 'r', encoding='utf-8') as f:
    g_act = json.load(f)

print(f"Current gold targets ({len(g_idx['targets'])}):")
for t in g_idx['targets']:
    print(f"  P{t['page']}: {t['key']} | {t.get('label', '')[:50]}")

print(f"\nCurrent gold action items ({len(g_act['action_items'])}):")
for a in g_act['action_items']:
    print(f"  P{a['page']}: [{a['item_type']}] {a['item_text'][:60]}")
