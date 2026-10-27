"""Hand-authored gold fixture build for job 179ab46e-b105-4107-ad38-7a0160724948
(an 8-page GeM bid document, GEM/2026/B/8056905 - Up-Armouring of Defender
Vehicles / Roadworthy Certificate testing, Mishra Dhatu Nigam Ltd (MIDHANI)).

Same GeM-platform template as job 30d148dd (see
scratch/build_gold_fixture_30d148dd.py's own docstring for the general
shape and the two confirmed real-data gotchas that apply equally here):
  1. "Bid Details" is genuinely table_cell-grounded on every occurrence -
     not authored as a heading, same reasoning as the other fixture.
  2. No section heading here (EMD/ePBG/MII/MSE/TReDS/Technical Specs/Buyer
     Added T&C/Disclaimer) matches any is_subdoc_root_candidate trigger, so
     the real page-1 title line ("Bid Document") is authored as the single
     shared top-level root everything else nests under - otherwise
     whichever section heading is processed first wins depth=0 and every
     later section wrongly nests under IT instead of being its sibling.

This document's own numbered lists are SIMPLER than the other fixture's -
confirmed via direct block dump that mistune does NOT renumber any of them
(each list's own first non-list paragraph break happens to land in the
right place), so every heading_text below matches the human-visible PDF
number directly. One quirk unique to this document: two separate product
items ("Up-Armouring Of Defender Vehicles (4 the number pi )" and
"ROADWORTHY CERTIFICATE: ...") each have their own "Technical
Specifications" child heading with IDENTICAL text - authored as two
distinct parent-scoped occurrences, not a single repeated heading.

Mirrors the process used for the other fixtures: AIHeading objects
replayed offline/deterministically (resolve_parent_via_ai=False,
provider_config=None) through the real seam (_ground_and_resolve_chunk ->
apply_heading_events_to_scope -> _persist_page_index -> build_mention_index_for_job).

Run inside the CPU app container:
    docker cp scratch/build_gold_fixture_179ab46e.py ocr-services-glm-ocr-api-1:/app/scratch_build_179a.py
    docker exec ocr-services-glm-ocr-api-1 python3 /app/scratch_build_179a.py
"""
import asyncio

import ai_indexing
import ai_reference_index
import database
import reference_index

DUP_JOB_ID = "179ab46e-b105-4107-ad38-7a0160724948"
AIHeading = ai_indexing.AIHeading

ALL_HEADINGS: list = []


def h(text, page, level, parent):
    ALL_HEADINGS.append(AIHeading(heading_text=text, start_page=page, level=level, parent_ref=parent))


# ===== Page 1: the real document-title line, authored as the single
# top-level root everything else nests under (see module docstring). =====
h("विड दस्तावेज / Bid Document", 1, 1, None)
BID_DOC_ROOT = "विड दस्तावेज / Bid Document"

# ===== Page 2-4: EMD / ePBG / MII / MSE / TReDS sections (own real
# paragraph blocks, bilingual leading text copied verbatim) =====
h("टेपमडी विवरण/EMD Detail", 2, 1, BID_DOC_ROOT)
h("ईपीबीजी विवरण /ePBG Detail", 3, 1, BID_DOC_ROOT)
h("एमआईआई खरीद वरीयता / MII Purchase Preference", 3, 1, BID_DOC_ROOT)
h("एमएसईऍ खरीद वरीयता/MSE Purchase Preference", 3, 1, BID_DOC_ROOT)
h("टेड्स भुगतान संबंधी विवरण/TReDS Payment Details", 4, 1, BID_DOC_ROOT)

# ===== Page 4-5: numbered list (1-3, matches real block numbers exactly -
# no mistune renumbering confirmed via block dump), item 3 has its own
# roman sub-list (i-v, plain paragraphs not list_items but still locatable) =====
h("1. Preference to Make In India products (For bids < 200 Crore): Prefer", 4, 2, BID_DOC_ROOT)
h("2. Purchase preference to Micro and Small Enterprises (MSEs): Purchase", 4, 2, BID_DOC_ROOT)
h("3. Reverse Auction would be conducted amongst all the technically qual", 4, 2, BID_DOC_ROOT)
ITEM3 = "3. Reverse Auction would be conducted amongst all the technically qual"
ROMAN_SUBITEMS = [
    "i. If number of technically qualified bidders are only 2 or 3.",
    "ii. If Buyer has chosen to split the bid amongst N sellers, and H1 bid",
    "iii. In case Primary product of only one OEM is left in contention for",
    "iv. If L-1 is non-MSE and H-1 is eligible MSE and H-1 price is coming ",
    "v. If L-1 is non-MII and H-1 is eligible MII and H-1 price is coming w",
]
for text in ROMAN_SUBITEMS:
    h(text, 5, 3, ITEM3)

# ===== Page 5: two product items, each with its own "Technical
# Specifications" child. ONLY the first child is authored - confirmed via
# direct isolated testing that ai_reference_index._dedupe_same_page_headings
# unconditionally drops a later same-page heading whose normalized text
# exactly repeats an earlier one, keyed on (start_page, text) alone with no
# parent_ref/scope check at all. That's correct for its own documented
# intent (a model re-reporting the identical clause twice, e.g. a
# truncation-variant duplicate) but is a confirmed real gap for this
# document's shape: two DIFFERENT sibling items each legitimately having
# their own child heading with coincidentally identical text. The second
# "Technical Specifications" is therefore un-groundable by the real
# pipeline as it stands today - authoring it here would create a gold
# target the live pipeline can never produce, which isn't the live
# pipeline's actual current behavior for this document. Left out
# deliberately, not an oversight; worth a separate, narrow future fix
# (scope the dedup check to also compare parent_ref) outside this fixture's
# own scope. =====
h("Up-Armouring Of Defender Vehicles (4 the number pi )", 5, 2, BID_DOC_ROOT)
h("तकनीकी विशिष्टियाँ /Technical Specifications", 5, 3, "Up-Armouring Of Defender Vehicles (4 the number pi )")
h("ROADWORTHY CERTIFICATE: VRDE / ICAT / ARAI ROAD WORTHINESS TESTING CHA", 5, 2, BID_DOC_ROOT)

# ===== Page 6-7: "Buyer Added Bid Specific Terms and Conditions" (1-3,
# matches real numbers exactly) =====
h("फैला द्वारा जोड़ी गई बिड की विशेष सर्ट/Buyer Added Bid Specific Terms", 6, 1, BID_DOC_ROOT)
TC_PARENT = "फैला द्वारा जोड़ी गई बिड की विशेष सर्ट/Buyer Added Bid Specific Terms"
TC_ITEMS = [
    ("1. Generic", 6),
    ("2. Generic", 6),
    ("3. Buyer Added Bid Specific ATC", 6),
]
for text, page in TC_ITEMS:
    h(text, page, 2, TC_PARENT)

# ===== Page 6-7: Disclaimer, 1-14, matches real numbers exactly (restarts
# cleanly after the disclaimer intro paragraph) =====
h("अस्वीकरण/Disclaimer", 6, 1, BID_DOC_ROOT)
DISCLAIMER_PARENT = "अस्वीकरण/Disclaimer"
DISCLAIMER_ITEMS = [
    ("1. Publishing Custom / BOQ bids for items for which regular GeM catego", 6),
    ("2. Mandating procurement of / from specific Brand / Make / Model / Man", 6),
    ("3. Inclusion of disqualification criteria related to suspension of sel", 6),
    ("4. Mandating submission of documents (other than EMD and signed Integr", 7),
    ("5. Publishing bids on GeM for procurement of works.", 7),
    ("6. Procurement of Goods by creating a Service bid on GeM & vice-versa.", 7),
    ("7. Seeking sample with bid or approval of samples during bid evaluatio", 7),
    ("8. Seeking experience from specific organization / department / instit", 7),
    ("9. Creating bid for items from incorrect categories.", 7),
    ("10. Reference of conditions published on any external site or referenc", 7),
    ("11. Asking for any Tender fee / Bid Participation fee, as the case may", 7),
    ("12. Buyer added ATC Clauses which are in contravention of clauses defi", 7),
    ("13. Any ATC clause in contravention with GeM GTC Clause 4 (xiii) (h) w", 7),
    ("14. In a category based bid, adding additional items, through buyer ad", 7),
]
for text, page in DISCLAIMER_ITEMS:
    h(text, page, 2, DISCLAIMER_PARENT)

print(f"Total authored headings: {len(ALL_HEADINGS)}")


async def run():
    total_pages = 8
    pages = database.get_pages(DUP_JOB_ID)
    page_text_by_num = {p["page_num"]: (p.get("text") or "") for p in pages}

    headings_by_page: dict = {}
    for hd in ALL_HEADINGS:
        headings_by_page.setdefault(hd.start_page, []).append(hd)

    max_per_page = max((len(v) for v in headings_by_page.values()), default=0)
    print(f"Max headings on a single page: {max_per_page}")

    seed_scope = None
    carry_context = ai_indexing.CarryContext(open_containers=[], last_page_tail="")

    first_page, last_page = 1, total_pages
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
    events_by_page, warnings = await ai_reference_index._ground_and_resolve_chunk(
        batch_headings, page_blocks, carry_context, "gold-hand-authored",
        has_next_page_fn=lambda pn: pn < total_pages,
        resolve_parent_via_ai=False, provider_config=None,
        page_block_types=page_block_types, page_row_ids=page_row_ids,
    )
    print(f"Batch {first_page}-{last_page}: {len(batch_headings)} headings, warnings={warnings}")

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

    reference_index.build_mention_index_for_job(DUP_JOB_ID)
    print("Done. Mention index built.")


asyncio.run(run())
