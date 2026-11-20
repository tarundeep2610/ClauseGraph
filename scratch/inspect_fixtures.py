import json

def inspect_index(path):
    with open(path, 'r', encoding='utf-8') as f:
        d = json.load(f)
    print(f"=== {path} ===")
    print(f"Targets ({len(d['targets'])}):")
    for t in d['targets']:
        print(f"  P{t.get('page')}: {t.get('key')} | {t.get('label')[:40] if t.get('label') else ''} | scope={t.get('scope_key')}")
    print(f"Mentions ({len(d.get('mentions', []))}):")
    for m in d.get('mentions', [])[:10]:
        print(f"  P{m.get('page')}: {m.get('key')} | raw='{m.get('raw_text')}' | res={m.get('resolution')}")

inspect_index('tests/fixtures/gold_index_de8764d2.json')
