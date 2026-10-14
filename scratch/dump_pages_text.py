"""Dump the duplicate gold job's per-page 'text' field (the OCR-layout-annotated
text the AI pipeline actually reads and parse_page_blocks parses) to a single
plain-text file with page markers, for manual reading while hand-authoring
gold-fixture headings.
"""
import database

JOB_ID = "89cfa529-2390-464c-9c3e-2446ef68c588"

pages = database.get_pages(JOB_ID)
pages.sort(key=lambda p: p["page_num"])

total_chars = 0
with open("/app/data/dump_3cafeb89_text.txt", "w", encoding="utf-8") as f:
    for p in pages:
        text = p.get("text") or ""
        total_chars += len(text)
        f.write(f"\n===== PAGE {p['page_num']} (status={p['status']}, chars={len(text)}) =====\n")
        f.write(text)
        f.write("\n")

print(f"Wrote {len(pages)} pages, {total_chars} total chars, to /app/data/dump_3cafeb89_text.txt")
