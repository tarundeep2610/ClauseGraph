import database
import reference_index
import re

job_id = "89cfa529-2390-464c-9c3e-2446ef68c588"
pages = database.get_pages(job_id)
_TOC_BLOCK_RE = re.compile(r"\b(?:INDEX|TABLE OF CONTENTS)\b", re.IGNORECASE)

definitions = []
for page in pages:
    page_num = page["page_num"]
    tokens, _ = reference_index._get_parser().parse(page.get("text") or "")
    block_idx = 0
    for b_type, b_text in reference_index._walk_blocks_from_tokens(tokens):
        clean_b_text = re.sub(r"<\|/?det\|>.*?<\|/?det\|>", "", b_text)
        clean_b_text = re.sub(r"<\|[^>]*\|>", "", clean_b_text)
        if page_num <= 2 and _TOC_BLOCK_RE.search(clean_b_text):
            block_idx += 1
            continue
        for m in reference_index.PAREN_ABBR_RE.finditer(clean_b_text):
            full = m.group("full").strip()
            abbr = m.group("abbr").strip()
            if reference_index._is_acronym_of(abbr, full):
                key = f"term:{re.sub(r'[^A-Za-z0-9]', '', abbr).lower()}"
                definitions.append((key, abbr, page_num, m.group(0)))
        for m in reference_index.HEREINAFTER_RE.finditer(clean_b_text):
            term = m.group("term").strip().strip("\"'“”‘’")
            if term:
                norm = reference_index.normalize_term_key(term)
                key = f"term:{norm}"
                definitions.append((key, term, page_num, m.group(0)))
        block_idx += 1

print("Total definitions found:", len(definitions))
for d in definitions:
    if any(k in d[0] for k in ("gcc", "itb", "scc")):
        print("  ->", d)
