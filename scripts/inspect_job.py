import sqlite3
import json
import sys

job_id = sys.argv[1] if len(sys.argv) > 1 else '108ba949-bb7b-4f29-b582-ce4f4a44631b'
db_path = '/app/data/ocr_jobs.db' if len(sys.argv) <= 2 else sys.argv[2]
conn = sqlite3.connect(db_path)
c = conn.cursor()

print(f"=== END OF PAGE SCOPE FOR {job_id} ===")
c.execute("SELECT page_num, end_of_page_scope FROM ocr_pages WHERE job_id=? ORDER BY page_num", (job_id,))
for p, sc in c.fetchall():
    d = json.loads(sc) if sc else {}
    stack = [(s.get('key'), s.get('label')) for s in d.get('stack', [])]
    last_dec = d.get('last_decimal_ground')
    print(f"P{p:02d}: stack={stack} | last_dec={last_dec}")

print("\n=== TARGETS ===")
c.execute("SELECT page, key, scope_key, own_label, scope_label, snippet, detection_source FROM ocr_reference_targets WHERE job_id=? ORDER BY page, occurrence_index", (job_id,))
for r in c.fetchall():
    lbl = r[3] or r[4] or (r[5][:40] if r[5] else '')
    print(f"P{r[0]:02d} | key={r[1]:15s} | scope={str(r[2]):35s} | lbl={lbl[:55]}")
