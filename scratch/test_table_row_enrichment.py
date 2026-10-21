import asyncio
import json
import re
import database
import reference_index
import ai_indexing
import ai_reference_index

DUP_JOB_ID = "89cfa529-2390-464c-9c3e-2446ef68c588"

# 77 headings emitted by Qwen for Chunk 2 (pages 13-19)
# From task-642 output:
CHUNK2_RAW_HEADINGS = [
    (13, 3, "6", "6.1."),
    (13, 3, "6", "6.2."),
    (13, 3, "7", "7.1."),
    (13, 3, "8", "8.1."),
    (13, 3, "8", "8.2."),
    (13, 3, "8", "8.3."),
    (13, 3, "8", "8.4."),
    (13, 3, "8", "8.5."),
    (13, 3, "9", "9.1."),
    (13, 3, "9", "9.2."),
    (13, 3, "9", "9.3."),
    (13, 3, "9", "9.4."),
    (13, 3, "9", "9.5."),
    (13, 3, "9", "9.6."),
    (13, 3, "9", "9.7."),
    (13, 3, "10", "10.1."),
    (13, 3, "11", "11.1."),
    (13, 3, "11", "11.2."),
    (13, 3, "11", "11.3."),
    (13, 3, "12", "12.1."),
    (13, 3, "12", "12.2."),
    (13, 3, "13", "13.1."),
    (13, 3, "13", "13.2."),
    (13, 3, "14", "14.1."),
]

def is_bare_marker(text: str) -> bool:
    """True if text is just a number/marker like '6', '6.1.', '10.1.', '(1)', 'i.' without a substantive label."""
    stripped = text.strip()
    dm = reference_index._DECIMAL_NUMBER_HEAD_RE.match(stripped)
    if dm:
        label = stripped[dm.end():].strip()
        return len(label) < 2
    if re.match(r"^\(?\d+\)?\.?$", stripped):
        return True
    if re.match(r"^\(?[a-zA-Z]\)?\.?$", stripped):
        return True
    return False

def enrich_table_heading(page_blocks, page_block_types, page_row_ids, page_num, heading):
    """If heading matches a table_cell that is a bare number/marker, enrich with companion cell in same row."""
    blocks = page_blocks.get(page_num, [])
    types = page_block_types.get(page_num, [])
    rids = page_row_ids.get(page_num, [])
    if not blocks or not rids:
        return heading

    # Try locating the bare heading in blocks
    res = ai_reference_index.locate_heading_block(blocks, heading.heading_text, used_block_idxs=set())
    if res is None:
        return heading

    idx, kind, ratio = res
    if idx < len(types) and types[idx] == "table_cell" and idx < len(rids) and rids[idx] is not None:
        rid = rids[idx]
        if is_bare_marker(heading.heading_text):
            # Find companion cells in the same row
            companion_idxs = [j for j, r in enumerate(rids) if r == rid and j != idx]
            if companion_idxs:
                comp_text = blocks[companion_idxs[0]].strip()
                if len(comp_text) >= 2:
                    clean_marker = heading.heading_text.rstrip(".")
                    enriched_text = f"{clean_marker}. {comp_text}"
                    print(f"  [ENRICH] p{page_num}: {repr(heading.heading_text)} -> {repr(enriched_text[:60])}")
                    heading.heading_text = enriched_text
    return heading

async def run_test():
    all_pages = database.get_pages(DUP_JOB_ID)
    pages = [p for p in all_pages if 13 <= p["page_num"] <= 14]
    page_blocks = {}
    page_block_types = {}
    page_row_ids = {}
    for p in pages:
        txt = p.get("text") or ""
        texts, types, row_ids = reference_index.parse_page_blocks_with_row_ids(txt)
        page_blocks[p["page_num"]] = texts
        page_block_types[p["page_num"]] = types
        page_row_ids[p["page_num"]] = row_ids

    # Build AIHeadings
    headings = []
    for p, lvl, pref, txt in CHUNK2_RAW_HEADINGS:
        h = ai_indexing.AIHeading(heading_text=txt, start_page=p, level=lvl, parent_ref=pref)
        h = enrich_table_heading(page_blocks, page_block_types, page_row_ids, p, h)
        headings.append(h)

    carry_context = ai_indexing.CarryContext(open_containers=[{"ref_id": "root", "label": "Section-III", "level": 1}])
    events_by_page, warns = await ai_reference_index._ground_and_resolve_chunk(
        headings, page_blocks, carry_context, "qwen3:8b",
        has_next_page_fn=lambda pn: True,
        page_block_types=page_block_types, page_row_ids=page_row_ids,
    )
    
    print("\n--- Events Summary ---")
    total_events = sum(len(evs) for evs in events_by_page.values())
    print(f"Total events grounded: {total_events}")
    for p, evs in events_by_page.items():
        print(f"Page {p}: {len(evs)} events")
        for ev in evs[:5]:
            print(f"  {repr(ev['heading_text'][:50])} (depth={ev['depth']})")

    # Now apply events to scope
    seed_scope = None
    all_minted_targets = []
    for p in sorted(events_by_page.keys()):
        evs = events_by_page[p]
        res_targets, seed_scope = reference_index.apply_heading_events_to_scope(
            evs, seed_scope=seed_scope, page_num=p, has_next_page=True,
        )
        for t in res_targets:
            t["page"] = p
        all_minted_targets.extend(res_targets)

    print(f"\nTotal targets minted: {len(all_minted_targets)}")
    for t in all_minted_targets[:10]:
        print(f"  (p{t.get('page')}, key={t.get('key')}): {repr(t.get('label'))[:50]}")

    # Load gold fixture to check matches
    with open("/app/tests/fixtures/gold_index_89cfa529.json", "r", encoding="utf-8") as f:
        gold = json.load(f)
    gold_p13_14 = [t for t in gold.get("targets", []) if 13 <= t.get("page", 0) <= 14]
    gold_keys = {(t.get("page"), t.get("key")) for t in gold_p13_14}
    act_keys = {(t.get("page"), t.get("key")) for t in all_minted_targets}
    matched = gold_keys.intersection(act_keys)
    print(f"\nGold targets on p13-14: {len(gold_p13_14)}")
    print(f"Actual targets minted on p13-14: {len(all_minted_targets)}")
    print(f"MATCHED TARGETS: {len(matched)}")
    for p, k in sorted(matched):
        print(f"  MATCH: p{p} {k}")

if __name__ == "__main__":
    asyncio.run(run_test())
