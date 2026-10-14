import json

rec = json.load(open('data/ai_recordings/bb417471-561c-4029-bb21-3bdcf74e7dcb_full_trial1_1791546500.json'))
c = rec['chunks'].get('85_86_87')
if c:
    raw = json.loads(c['raw_text'])
    for h in raw['headings']:
        if any(h['heading_text'].startswith(x) for x in ('a)', 'b)', 'iii)', 'v)', 'i)')):
            print("HEADING:", repr(h['heading_text'][:60]), "parent_ref:", repr(h.get('parent_ref')), "family_hint:", repr(h.get('family_hint')))
