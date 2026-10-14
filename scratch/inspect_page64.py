import sqlite3

conn = sqlite3.connect('data/ocr_jobs.db')
row = conn.execute("SELECT text FROM ocr_pages WHERE job_id='bb417471-561c-4029-bb21-3bdcf74e7dcb' AND page_num=64").fetchone()
if row:
    print(row[0][:2000])
else:
    print("Page 64 not found in DB")
