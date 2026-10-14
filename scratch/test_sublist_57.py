import json
import reference_index
import ai_reference_index
import ai_indexing

rec = json.load(open('data/ai_recordings/bb417471-561c-4029-bb21-3bdcf74e7dcb_full_trial1_1791546500.json'))
chunk_raw = rec['chunks']['55_56_57']['raw_text']

parsed = json.loads(chunk_raw)
headings = [ai_indexing.AIHeading(**h) for h in parsed['headings']]

# Get page blocks for 55, 56, 57 from DB via container or local if DB exists
import sqlite3
# Let's see if we have DB locally
try:
    conn = sqlite3.connect('ocr_jobs.db')
    p57 = conn.execute("SELECT text FROM ocr_pages WHERE page_num=57").fetchone()
    print("Local DB found!")
except Exception:
    print("No local DB")
