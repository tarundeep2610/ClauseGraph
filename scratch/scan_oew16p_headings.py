import sys, os
sys.path.insert(0, os.path.abspath("."))
import database
import re

job_id = "0a42f322-858a-45c3-90af-30438476884c"
conn = database.get_db_connection()
c = conn.cursor()
c.execute("SELECT page_num, text FROM ocr_pages WHERE job_id = ? ORDER BY page_num", (job_id,))
pages = c.fetchall()

print(f"Loaded {len(pages)} pages from {job_id}")
for p_num, text in pages:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    headings = []
    for line in lines:
        if re.match(r"^(?:(?:1\.\d+(?:\.\d+)?|[ivxlcdm]+\.|\([a-z0-9]+\)|[A-Z]\.|\d+\.)\s+[A-Z]|T\d+[\s\-])", line):
            headings.append(line[:70])
    if headings:
        print(f"\n--- Page {p_num} ---")
        for h in headings:
            print("  ", h)
