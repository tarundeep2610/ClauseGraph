import json
import sys
sys.path.insert(0, '.')

import database
import reference_index
import ai_indexing
import ai_reference_index
from ai_indexing.schema import AIStructureChunk, normalize_chunk

rec = json.load(open('data/ai_recordings/bb417471-561c-4029-bb21-3bdcf74e7dcb_full_trial1_1791546500.json'))
txt = rec['chunks']['10_11_12']['raw_text']
comp = json.loads(txt)
chunk_model = AIStructureChunk.model_validate(comp)
cleaned, dropped = normalize_chunk(chunk_model, [10, 11, 12])

# Let's get page blocks for 10, 11, 12 from database or OCR text
# For job bb417471...
job_id = "bb417471-561c-4029-bb21-3bdcf74e7dcb"
pages = {p: database.get_page(job_id, p) for p in [10, 11, 12]}
page_texts = {p: (pages[p] or {}).get('text', '') for p in [10, 11, 12]}
page_blocks = {p: reference_index.parse_page_blocks(txt) for p, txt in page_texts.items()}

print("Page 10 blocks count:", len(page_blocks[10]))
for i, b in enumerate(page_blocks[10][:5]):
    print(f"  Block {i}: {repr(b)[:60]}")

for h in cleaned.headings:
    res = ai_reference_index._locate_heading_in_chunk(page_blocks, h.start_page, h.heading_text, {})
    print(f"Locate heading p{h.start_page} {repr(h.heading_text)[:40]} -> {res}")
