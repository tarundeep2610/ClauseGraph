import sqlite3

conn = sqlite3.connect('/app/data/ocr_jobs.db')
c = conn.cursor()
job_id = '108ba949-bb7b-4f29-b582-ce4f4a44631b'

c.execute('''
    SELECT page, key, own_label, scope_key, scope_label, snippet, detection_source
    FROM ocr_reference_targets
    WHERE job_id = ?
    ORDER BY page, rowid
''', (job_id,))
rows = c.fetchall()

print(f"Total targets: {len(rows)}")
for i, r in enumerate(rows):
    page, key, own_label, scope_key, scope_label, snippet, src = r
    indent = "  " * (len(scope_key.split("/")) if scope_key else 0)
    snip = (snippet or "").strip().replace("\n", " ")[:60]
    print(f"#{i+1:2d} P.{page:2d} [{src:4s}] {indent}Key: {key:18s} | ScopeKey: {str(scope_key):25s} | Label: {str(own_label):40s}")
    if snip:
        print(f"       {indent}   Snippet: {snip}")
