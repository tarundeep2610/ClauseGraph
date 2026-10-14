import json
import sys
sys.path.insert(0, '.')

import ai_indexing.parsing as parsing
import reference_index
import ai_reference_index
from ai_indexing.schema import AIHeading, AIStructureChunk, normalize_chunk, _GENERIC_LEGAL_NOISE_RE

gold = json.load(open('tests/fixtures/gold_index_bb417471.json'))
gold_targets = gold['targets']

print(f"Total gold targets: {len(gold_targets)}")

# Check recording
rec_path = 'data/ai_recordings/bb417471-561c-4029-bb21-3bdcf74e7dcb_full_trial1_1791546500.json'
rec = json.load(open(rec_path))

print(f"Recorded chunks: {len(rec.get('chunks', {}))}")

# Let's inspect headings on pages 40, 41, 46, 61
for chunk_key, chunk_val in rec['chunks'].items():
    raw = chunk_val['raw_text']
    try:
        data = parsing.parse_json_object(raw)
        headings = data.get('headings', [])
        for h in headings:
            p = h.get('start_page')
            txt = h.get('heading_text', '')
            if p in (13, 40, 41, 46, 61):
                print(f"  Chunk {chunk_key} p{p}: {txt!r}")
    except Exception as e:
        print(f"  Chunk {chunk_key} error: {e}")
