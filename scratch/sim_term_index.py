import database
import reference_index
import re

job_id = "89cfa529-2390-464c-9c3e-2446ef68c588"
version = database.get_reference_index_version(job_id)
pages = database.get_pages(job_id)
total_pages = len(pages)
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
                start_char = m.start()
                snippet = clean_b_text[max(0, start_char - 40):min(len(clean_b_text), start_char + len(m.group(0)) + 60)].replace("\n", " ").strip()
                definitions.append({
                    "key": key, "term": abbr, "page": page_num, "block_index": block_idx,
                    "start": start_char, "raw_text": m.group(0), "snippet": snippet, "is_abbr": True,
                })
        for m in reference_index.HEREINAFTER_RE.finditer(clean_b_text):
            term = m.group("term").strip().strip("\"'“”‘’")
            if term:
                norm = reference_index.normalize_term_key(term)
                key = f"term:{norm}"
                start_char = m.start()
                snippet = clean_b_text[max(0, start_char - 40):min(len(clean_b_text), start_char + len(m.group(0)) + 60)].replace("\n", " ").strip()
                definitions.append({
                    "key": key, "term": term, "page": page_num, "block_index": block_idx,
                    "start": start_char, "raw_text": m.group(0), "snippet": snippet, "is_abbr": False,
                })
        block_idx += 1

by_key = {}
for d in definitions:
    by_key.setdefault(d["key"], []).append(d)

first_by_key = {}
for key, def_list in by_key.items():
    def_list.sort(key=lambda d: (d["page"], d["block_index"], d["start"]))
    first_by_key[key] = def_list[0]

term_specs = []
for key, first in first_by_key.items():
    term_text = first["term"]
    if first["is_abbr"]:
        pat = re.compile(rf"\b{re.escape(term_text)}\b")
    else:
        pat = re.compile(rf"\b{re.escape(term_text)}\b", re.IGNORECASE)
    term_specs.append((key, term_text, pat))

pages_seen_by_key = {key: {d["page"] for d in def_list} for key, def_list in by_key.items()}
for page in pages:
    page_num = page["page_num"]
    tokens, _ = reference_index._get_parser().parse(page.get("text") or "")
    for b_type, b_text in reference_index._walk_blocks_from_tokens(tokens):
        clean_b_text = re.sub(r"<\|/?det\|>.*?<\|/?det\|>", "", b_text)
        clean_b_text = re.sub(r"<\|[^>]*\|>", "", clean_b_text)
        for key, term_text, pat in term_specs:
            for m in pat.finditer(clean_b_text):
                pages_seen_by_key[key].add(page_num)

max_pages = reference_index.TERM_MAX_PAGE_COVERAGE * total_pages
survivors = {key for key, pg_set in pages_seen_by_key.items() if len(pg_set) <= max_pages}

print("Surviving term targets count:", len(survivors))
for key, first in sorted(first_by_key.items(), key=lambda x: (x[1]["page"], x[0])):
    if key in survivors:
        print(f"  {first['page']:3d} {key:12s} {first['raw_text']!r}")
    else:
        print(f"  EXCLUDED (>max_pages): {key}")
