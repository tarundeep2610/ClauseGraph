import json

gold = json.load(open('tests/fixtures/gold_index_bb417471.json'))
d = json.load(open('data/diff_replay_v4.json'))

# Let's inspect chunk 7_8_9 and 10_11_12 in the recording
rec = json.load(open('data/ai_recordings/bb417471-561c-4029-bb21-3bdcf74e7dcb_full_trial1_1791546500.json'))

for chk in ['7_8_9', '10_11_12']:
    print(f"\n=== Recording Chunk {chk} ===")
    txt = rec['chunks'][chk]['raw_text']
    try:
        comp = json.loads(txt)
        for h in comp.get('headings', []):
            safe_txt = h.get('heading_text','').encode('ascii', errors='replace').decode('ascii')
            print(f"  page={h.get('start_page')} level={h.get('level')} family={h.get('family_hint')} num={h.get('number_hint')} text={safe_txt[:60]}")
    except Exception as e:
        print("Error parsing json:", e)
