import sys
sys.path.insert(0, "/app")
import database
import reference_index

JOB_ID = "89cfa529-2390-464c-9c3e-2446ef68c588"
pages = [p for p in database.get_pages(JOB_ID) if 13 <= p["page_num"] <= 19]

print("Total pages:", len(pages))
for p in pages:
    raw_len = len(p.get("text") or "")
    annotated = reference_index.build_layout_annotated_text(p.get("text") or "", p.get("raw_ocr_text") or "")
    print(f"Page {p['page_num']}: raw={raw_len} chars, annotated={len(annotated)} chars")
