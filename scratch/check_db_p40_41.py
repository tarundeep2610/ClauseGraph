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

# Let's inspect what happened on page 40 and 41
for p in [40, 41]:
    row = database.get_reference_index_page(job_id, p)
    print(f"=== DB Reference Index Page {p} ===")
    if row:
        print("Scope:", row.get('scope'))
        print("Candidates count:", len(row.get('candidates', [])))
        for c in row.get('candidates', []):
            print("  c:", c.get('key'), c.get('scope_key'), c.get('own_label'))
    else:
        print("No row in DB")
