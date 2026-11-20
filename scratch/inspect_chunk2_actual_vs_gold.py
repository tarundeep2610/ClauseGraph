import os
import sys
sys.path.insert(0, "/app")
import json
import sqlite3
import database
import reference_index
import ai_reference_index
from ai_indexing.client import StructureIndexer
from ai_indexing.prompt import ChunkPlan, PageInput

FIXTURE_PATH = "/app/tests/fixtures/gold_index_89cfa529.json"
with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
    gold = json.load(f)

gold_targets = [t for t in gold.get("targets", []) if 13 <= t.get("page", 0) <= 19]
print(f"Total gold targets in Chunk 2: {len(gold_targets)}")

# Print first 20 gold targets
print("\n--- First 20 Gold Targets in Chunk 2 ---")
for t in gold_targets[:20]:
    print(f"  p{t.get('page')}: key={t.get('key')} | label={repr(t.get('label'))}")

JOB_ID = "89cfa529-2390-464c-9c3e-2446ef68c588"
all_pages = database.get_pages(JOB_ID)
pages = [p for p in all_pages if 13 <= p["page_num"] <= 19]

# Check what blocks exist on page 13 and 14
for pn in (13, 14):
    p = next(x for x in pages if x["page_num"] == pn)
    blocks, types, rids = reference_index.parse_page_blocks_with_row_ids(p.get("text") or "")
    print(f"\n--- Page {pn} Blocks (first 10) ---")
    for i in range(min(10, len(blocks))):
        print(f"  [{i}] rid={rids[i]} type={types[i]}: {repr(blocks[i][:60])}")
