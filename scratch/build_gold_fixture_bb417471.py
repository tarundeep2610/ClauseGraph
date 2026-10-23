"""Hand-authored gold fixture rebuild for job bb417471-561c-4029-bb21-3bdcf74e7dcb
(FAO-CARB_23_2019_PAPER_BOOK-1-100.pdf, 100-page arbitration appeal).

Reconstructed from the CURRENT committed tests/fixtures/gold_index_bb417471.json
(which already encodes the correct hierarchy from prior painstaking manual
correction — see memory project_gold_fixture_100page.md) plus real OCR text
pulled directly from the job's own pages for the ~37 `ground:*` targets and
`annexure:a-1`, neither of which carry a populated `label` (own_label is not
set for the "ground" leaf family, nor for the numbered "annexure" family).

Mirrors scratch/build_gold_fixture.py's exact process: AIHeading objects
replayed offline/deterministically (resolve_parent_via_ai=False,
provider_config=None) through the real seam (_ground_and_resolve_chunk ->
apply_heading_events_to_scope -> _persist_page_index -> build_mention_index_for_job).

`page:*`/`term:*`/`docref:*` targets are NOT hand-authored here — they are
fully auto-detected (detect_stamped_page_number in _persist_page_index;
build_docref_index_for_job/build_term_index_for_job inside
build_mention_index_for_job) once the real container/point/ground structure
below is replayed.

Run inside the CPU app container:
    docker cp scratch/build_gold_fixture_bb417471.py ocr-services-glm-ocr-api-1:/app/scratch_build_bb.py
    docker exec ocr-services-glm-ocr-api-1 python3 /app/scratch_build_bb.py
"""
import asyncio
import json
import re

import ai_indexing
import ai_reference_index
import database
import reference_index

DUP_JOB_ID = "bb417471-561c-4029-bb21-3bdcf74e7dcb"
AIHeading = ai_indexing.AIHeading

with open("/app/tests/fixtures/gold_index_bb417471.json", encoding="utf-8") as f:
    GOLD = json.load(f)

# Real OCR text pulled directly from the job's own pages for every target
# whose `label` is None (ground:* leaves, and annexure:a-1 whose real text
# is an OCR-garbled "AmneXureA-1" — the fuzzy-match seam resolves this the
# same way the already-shipped B6/B7 fixes do for other garbled headings).
REAL_TEXT_OVERRIDES = {
    (14, "ground:1"): "1. That the appellant is Haryana Vidyut Prasarn Nigam Limited",
    (14, "ground:2"): "2. That brief facts of the case which are essential for adjudication",
    (15, "ground:3"): "3. That the entire approach of the learned Additional District Judge-cum-Presiding Judge",
    (15, "ground:4"): "4. That the appellant allotted a tender to respondent no.1 for supply of equipment",
    (16, "ground:5"): "5. That the effective date of start of project was 07.05.2008",
    (16, "ground:6"): "6. That disputes and differences arose between the appellant and respondent no.1",
    (17, "ground:7"): "7. That in accordance with provisions of clause 48.2",
    (17, "ground:8"): "8. That the learned Arbitral Tribunal comprising respondent no. 2 to 4 delivered majority",
    (18, "ground:9"): "9. That the appellant being aggrieved by the above said awards",
    (18, "ground:10"): "10. That the award dated 19.02.2017 and 11.4.2017 and the impugned judgement",
    (19, "ground:i"): "(I) That the learned court below has failed to appreciate that the Arbitrators",
    (19, "ground:ii"): "(II) That the learned Arbitral Tribunal has shown complete non-application of mind",
    (19, "ground:iii"): "(III) That the learned court below as well as Arbitral Tribunal has erred",
    (20, "ground:iv"): "(IV) That the learned Arbitral Tribunal has partially allowed claim Nos. 1, 2.1",
    (20, "ground:v"): "(V) That the Arbitral Tribunal has erroneously held that respondent no.1 is liable for LD",
    (20, "ground:vi"): "(VI) That the Ld. Arbitral Tribunal has erroneously condoned the grave lapse",
    (21, "ground:vii"): "(VII) That the Ld. Arbitral Tribunal, whole holding appellant responsible for delay of 108 days",
    (23, "ground:viii"): "(VIII) That the Arbitral Tribunal also erred in observing that the justification",
    (23, "ground:ix"): "(IX) That the learned Arbitral Tribunal also overlooked the fact that as per schedule",
    (24, "ground:x"): "(X) That the learned Arbitral Tribunal further wrongly observed that the final GELO",
    (25, "ground:xi"): "(XI) That the learned Arbitral Tribunal also erred in holding that respondent no.1 was entitled",
    (27, "ground:xii"): "(XII) That furthermore, this finding of the Ld. Arbitral Tribunal is incorrect",
    (28, "ground:xiii"): "(XIII) That the Ld. Arbitral Tribunal has erroneously held that the appellant was responsible",
    (29, "ground:xiv"): "(XIV). That in evaluating delay in approval of 400/220 KV CTs drawing",
    (30, "ground:xv"): "(XV) That the Ld. Arbitral Tribunal has erroneously held that the delay in commissioning",
    (32, "ground:xvi"): "(XVI) That the Id. Arbitral Tribunal has erroneously held that respondent no.1 is entitled",
    (33, "ground:xvii"): "(XVII) That the learned Arbitral Tribunal has wrongly allowed refund of Rs. 26,58,714/-",
    (34, "ground:xviii"): "(XVIII) That the learned Arbitral Tribunal has further wrongly allowed refund of Rs. 1,07,18,800/-",
    (35, "ground:xix"): "(XIX) That the learned Arbitral Tribunal has wrongly held respondent No.1 entitled",
    (35, "ground:xx"): "(XX) That the Ld. Arbitral Tribunal had also wrongly awarded an amount of Rs. 64,73,754/-",
    (36, "ground:xxi"): "(XXI) That the learned Arbitral Tribunal has further erred in directing the appellant",
    (36, "ground:xxii"): "(XXII)",  # real continuation text not pulled; shape-only marker, sufficient for fuzzy grounding
    (37, "ground:11"): "11. That the appellant has not earlier such or similar appeal in this Hon'ble Court",
    (38, "ground:1"): "1. That the deponent is employed in the appellant corporation",
    (38, "ground:2"): "2. That the accompanying appeal has drafted under the instruction of deponent",
    (38, "ground:3"): "3. That the deponent has not earlier filed such or similar appeal in this Hon'bl",
    (61, "annexure:a-1"): "AmneXureA-1",
}

SKIP_FAMILY_PREFIXES = ("page:", "term:", "docref:")

targets = [
    t for t in GOLD["targets"]
    if not t["key"].startswith(SKIP_FAMILY_PREFIXES)
]
targets.sort(key=lambda t: (t["page"] or 0, t["key"], t["occurrence_index"]))

# key -> real heading text, for resolving a scope_key's LAST segment into
# the literal parent text apply_heading_events_to_scope needs as parent_ref.
# Only container-shaped keys (subdoc/annexure/numheading) are ever used as a
# parent segment in a scope_key, so this subset is sufficient.
TEXT_BY_KEY: dict = {}
PAGE_BY_KEY: dict = {}
for t in targets:
    key, page = t["key"], t["page"]
    override = REAL_TEXT_OVERRIDES.get((page, key))
    text = override if override is not None else t.get("label")
    if text is not None and key not in TEXT_BY_KEY:
        TEXT_BY_KEY[key] = text
        PAGE_BY_KEY[key] = page

ALL_HEADINGS: list = []


def h(text, page, level, parent, family_hint=None):
    ALL_HEADINGS.append(AIHeading(heading_text=text, start_page=page, level=level, parent_ref=parent, family_hint=family_hint))


# Parent overrides for keys whose CURRENT committed scope_key is itself one
# of the confirmed cascading-scope bugs under audit — reusing it as this
# reconstruction's own parent_ref would just feed the bug back in and
# "verify" nothing. These 2 are genuine gold-authoring mistakes (the
# original build gave them a real but WRONG intended parent), not an
# engine bug: both get authored as fresh top-level roots instead.
PARENT_OVERRIDE = {
    (13, "subdoc:opening-sheet-for-civil-appeals"): None,
    (61, "annexure:a-1"): None,
}

for t in targets:
    key, page = t["key"], t["page"]
    override = REAL_TEXT_OVERRIDES.get((page, key))
    text = override if override is not None else t.get("label")
    if text is None:
        raise SystemExit(f"No real text available for ({page}, {key}) — add a REAL_TEXT_OVERRIDES entry")

    if (page, key) in PARENT_OVERRIDE:
        # "AmneXureA-1" is an OCR-garbled "Annexure A-1" — a real AI call
        # would report family_hint="annexure" here (it's exactly the kind
        # of garbled-but-recognizable spelling the fuzzy-match seam is
        # built to handle). Without it, is_subdoc_root_candidate's early
        # root-decision call (which checks family_hint, not the text's own
        # minted family) can't recognize it as a genuine root candidate,
        # and the generic root-protection guard pulls it back under the
        # stale open document.
        hint = "annexure" if key.startswith("annexure:") else None
        h(text, page, 1, PARENT_OVERRIDE[(page, key)], family_hint=hint)
        continue

    scope_key = t.get("scope_key") or ""
    segments = [s for s in scope_key.split("/") if s]
    level = len(segments) + 1
    parent_label = TEXT_BY_KEY.get(segments[-1]) if segments else None

    # Page 38: the real OCR text has an explicit "Affidavit of Ishwar Chand
    # Balan..." trigger line immediately before "I, the above named
    # deponent..." (confirmed by direct read) — the current committed
    # fixture never authored it as its own heading at all, so the
    # _STANDALONE_LEGAL_DOCS_RE root-reset mechanism (which matches
    # "AFFIDAVIT" but not "I, the above named deponent...") never got a
    # chance to fire. Authoring it here as a fresh top-level root restores
    # the missing signal.
    if (page, key) == (38, "subdoc:i-the-above-named-deponent-do-2"):
        h("AFFIDAVIT OF ISHWAR CHAND BALAN", page, 1, None)

    h(text, page, level, parent_label)

print(f"Total authored headings: {len(ALL_HEADINGS)}")


async def run():
    total_pages = 100
    pages = database.get_pages(DUP_JOB_ID)
    page_text_by_num = {p["page_num"]: (p.get("text") or "") for p in pages}

    headings_by_page: dict = {}
    for hd in ALL_HEADINGS:
        headings_by_page.setdefault(hd.start_page, []).append(hd)

    max_per_page = max((len(v) for v in headings_by_page.values()), default=0)
    print(f"Max headings on a single page: {max_per_page}")

    MAX_PER_BATCH = 180
    batches = []
    batch_start = 1
    batch_count = 0
    for pn in range(1, total_pages + 1):
        n = len(headings_by_page.get(pn, []))
        if batch_count + n > MAX_PER_BATCH and batch_count > 0:
            batches.append((batch_start, pn - 1))
            batch_start = pn
            batch_count = 0
        batch_count += n
    batches.append((batch_start, total_pages))
    print("Batches (first_page, last_page):", batches)

    seed_scope = None
    last_page_text = ""
    carry_context = ai_indexing.CarryContext(open_containers=[], last_page_tail="")

    for first_page, last_page in batches:
        batch_headings = []
        for pn in range(first_page, last_page + 1):
            batch_headings.extend(headings_by_page.get(pn, []))
        page_blocks = {}
        page_block_types = {}
        page_row_ids = {}
        for pn in range(first_page, last_page + 1):
            texts, types, row_ids = reference_index.parse_page_blocks_with_row_ids(page_text_by_num.get(pn) or "")
            page_blocks[pn] = texts
            page_block_types[pn] = types
            page_row_ids[pn] = row_ids
        if batch_headings:
            events_by_page, warnings = await ai_reference_index._ground_and_resolve_chunk(
                batch_headings, page_blocks, carry_context, "gold-hand-authored",
                has_next_page_fn=lambda pn: pn < total_pages,
                resolve_parent_via_ai=False, provider_config=None,
                page_block_types=page_block_types, page_row_ids=page_row_ids,
            )
            print(f"Batch {first_page}-{last_page}: {len(batch_headings)} headings, warnings={warnings}")
        else:
            events_by_page, warnings = {}, {}

        for pn in range(first_page, last_page + 1):
            text = page_text_by_num.get(pn) or ""
            events = events_by_page.get(pn, [])
            cands, new_scope = reference_index.apply_heading_events_to_scope(
                events, seed_scope, pn, has_next_page=pn < total_pages,
            )
            reference_index.validate_scope_shape(new_scope)
            reference_index._persist_page_index(
                DUP_JOB_ID, pn, text, cands, new_scope, provisional=False,
                detection_source="ai", detection_model="gold-hand-authored",
            )
            current_frontier = database.get_reference_index_frontier(DUP_JOB_ID) or 0
            database.set_reference_index_frontier(DUP_JOB_ID, max(current_frontier, pn))
            seed_scope = new_scope
            last_page_text = text

        carry_context = ai_reference_index._carry_in_summary(seed_scope, last_page_text)

    reference_index.build_mention_index_for_job(DUP_JOB_ID)
    print("Done. Mention index built.")


asyncio.run(run())
