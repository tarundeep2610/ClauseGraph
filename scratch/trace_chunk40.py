import json
import sys
sys.path.insert(0, '.')

import database
import reference_index
import ai_indexing
import ai_reference_index
from ai_indexing.schema import AIHeading, AIStructureChunk, normalize_chunk
import asyncio

rec = json.load(open('data/ai_recordings/bb417471-561c-4029-bb21-3bdcf74e7dcb_full_trial1_1791546500.json'))
job_id = "bb417471-561c-4029-bb21-3bdcf74e7dcb"

txt40 = rec['chunks']['40_41_42']['raw_text']
data40 = ai_indexing.parsing.parse_json_object(txt40)
model40 = AIStructureChunk(headings=[AIHeading(**h) for h in data40.get('headings', [])])
cleaned40, _ = normalize_chunk(model40, [40, 41, 42])

print("Chunk 40_41_42 headings after normalize_chunk:")
for h in cleaned40.headings:
    print(f"  p{h.start_page} lvl={h.level} parent={h.parent_ref!r} text={h.heading_text!r}")
