import sqlite3
import json

conn = sqlite3.connect('/app/data/ocr_jobs.db')
c = conn.cursor()

c.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in c.fetchall()]
print("Tables:", tables)

for t in tables:
    c.execute(f"SELECT count(*) FROM {t}")
    cnt = c.fetchone()[0]
    print(f"  {t}: {cnt} rows")

job_id = '108ba949-bb7b-4f29-b582-ce4f4a44631b'
if 'ocr_pages' in tables:
    c.execute("PRAGMA table_info(ocr_pages)")
    print("ocr_pages schema:", [col[1] for col in c.fetchall()])
    for p in range(1, 14):
        c.execute("SELECT end_of_page_scope FROM ocr_pages WHERE job_id = ? AND page_num = ?", (job_id, p))
        row = c.fetchone()
        if row and row[0]:
            sc = json.loads(row[0])
            stack = [(s.get('key'), s.get('label')) for s in (sc.get('stack') or [])]
            dec = sc.get('last_decimal_ground', {}).get('key') if sc.get('last_decimal_ground') else None
            print(f"Page {p:2d}: stack={stack} | last_dec={dec}")




    c.execute("SELECT ai_index_report FROM ocr_jobs WHERE job_id = ?", (job_id,))
    row = c.fetchone()
    if row and row[0]:
        report = json.loads(row[0])
        print("Keys in ai_index_report:", report.keys())
        chunks = report.get("chunks", [])
        print(f"Total chunks: {len(chunks)}")
        for idx, ch in enumerate(chunks):
            print(f"\n--- Chunk {idx+1} keys: {ch.keys()} ---")
            for k, v in ch.items():
                if isinstance(v, list):
                    print(f"  {k}: list of length {len(v)}")
                    if v:
                        print(f"    first elem: {v[0]}")
                elif isinstance(v, dict):
                    print(f"  {k}: dict with keys {v.keys()}")
                else:
                    print(f"  {k}: {v}")




