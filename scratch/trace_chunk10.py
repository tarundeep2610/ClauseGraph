import json
import re

rec = json.load(open('data/ai_recordings/bb417471-561c-4029-bb21-3bdcf74e7dcb_full_trial1_1791546500.json'))
txt = rec['chunks']['10_11_12']['raw_text']
comp = json.loads(txt)

import sys
sys.path.insert(0, '.')
from ai_indexing.schema import AIStructureChunk, _GENERIC_LEGAL_NOISE_RE

chunk_model = AIStructureChunk.model_validate(comp)
print("Before normalize:", len(chunk_model.headings))

from ai_indexing.schema import normalize_chunk
cleaned, dropped = normalize_chunk(chunk_model, [10, 11, 12])
print("After normalize:", len(cleaned.headings), "Dropped:", dropped)
for h in cleaned.headings:
    print(f"  page={h.start_page} level={h.level} text={h.heading_text[:60]}")
