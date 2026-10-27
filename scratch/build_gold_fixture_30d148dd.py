"""Hand-authored gold fixture build for job 30d148dd-c1e4-4e38-877b-608b5584ff4e
(a 10-page GeM bid document, GEM/2026/B/8032683 - Customized AMC/CMC for a
Frauscher MSDAC System, Dedicated Freight Corridor Corporation of India Ltd).

Headings below are hand-authored from a direct reading of the job's own
REAL PARSED BLOCKS (reference_index.parse_page_blocks_with_row_ids against
database.get_pages(DUP_JOB_ID)'s 'text' field) - not the human-rendered PDF
numbering. Confirmed real artifact: mistune's ordered-list parser renumbers
list_items by POSITION within one continuous markdown list, not by their
own literal written number, whenever no non-list paragraph breaks the list.
Page 4's "1/2/3" restarted sub-list under item 4 becomes "5/6/7" in the
actually-parsed blocks; the main "Buyer Added Bid Specific Terms and
Conditions" list (human-visible 1-17) becomes a different, non-monotonic
real sequence (1-10, its own restarted 1-13 ATC sub-list, then 14, then a
reset to 9, then 10-17) because of where non-list paragraphs happen to
fall. Every heading_text below is copied verbatim from the REAL block text
that will actually be located - not reconstructed from the rendered PDF's
own numbering, which the indexing pipeline never sees.

Mirrors the process used for the 100-page/135-page fixtures: AIHeading
objects replayed offline/deterministically (resolve_parent_via_ai=False,
provider_config=None) through the real seam (_ground_and_resolve_chunk ->
apply_heading_events_to_scope -> _persist_page_index -> build_mention_index_for_job).

"Bid Details" (pages 1-2) is deliberately NOT authored as a heading - its
own real block is `table_cell` on every occurrence (confirmed via direct
block dump), so it is genuinely table content, not a real structural
heading; the live pipeline's own prune step for exactly this shape
(table_cell-grounded subdoc, not an independent root candidate) would
correctly drop it too.

Run inside the CPU app container:
    docker cp scratch/build_gold_fixture_30d148dd.py ocr-services-glm-ocr-api-1:/app/scratch_build_30d.py
    docker exec ocr-services-glm-ocr-api-1 python3 /app/scratch_build_30d.py
"""
import asyncio

import ai_indexing
import ai_reference_index
import database
import reference_index

DUP_JOB_ID = "30d148dd-c1e4-4e38-877b-608b5584ff4e"
AIHeading = ai_indexing.AIHeading

ALL_HEADINGS: list = []


def h(text, page, level, parent):
    ALL_HEADINGS.append(AIHeading(heading_text=text, start_page=page, level=level, parent_ref=parent))


# ===== Page 1: the real document-title line ("Bid Document") is authored
# as the single top-level root everything else nests under. Confirmed
# necessary: none of "EMD Detail"/"ePBG Detail"/etc. match any trigger
# is_subdoc_root_candidate recognizes (no numbered keyword, no
# EXHIBIT/ATTACHMENT/standalone-legal-doc shape), so without a genuine
# root-candidate heading authored FIRST, whichever section heading
# happens to be processed first wins depth=0 and every later section
# wrongly nests under IT instead of being its sibling (the same
# `is_subdoc_root_candidate` trigger-vocabulary gap already identified
# and deliberately deferred for "Opening Sheet" in the 100-page fixture
# - not something this fixture works around by relying on a code change,
# since the real document DOES have its own natural top-level title to
# use instead). =====
h("विड दस्तावेज / Bid Document", 1, 1, None)
BID_DOC_ROOT = "विड दस्तावेज / Bid Document"

# ===== Page 2-3: EMD / ePBG / compliance sections (own real paragraph
# blocks, bilingual leading text copied verbatim) =====
h("ईरमडी विवरण/EMD Detail", 2, 1, BID_DOC_ROOT)
h("ईपीबीजी विवरण /ePBG Detail", 2, 1, BID_DOC_ROOT)
h("एमआईआई अनुपालन/MII Compliance", 3, 1, BID_DOC_ROOT)
h("एमएसई खरीद वरीयता/MSE Purchase Preference", 3, 1, BID_DOC_ROOT)
h("टेड्स भुगतान संबंधी विवरण/TReDS Payment Details", 3, 1, BID_DOC_ROOT)

# ===== Page 4: eligibility-criteria explanatory list - REAL parsed
# numbers 1,2,3,4 then a RESTART that mistune renumbers 5,6,7 (not 1,2,3)
# because items 4 and 5 are contiguous in the same markdown list. =====
h("1. The minimum average annual financial turnover of the bidder during", 4, 2, BID_DOC_ROOT)
h("2. Purchase preference to Micro and Small Enterprises (MSEs): Purchase", 4, 2, BID_DOC_ROOT)
h("3. Estimated Bid Value indicated above is being declared solely for th", 4, 2, BID_DOC_ROOT)
h("4. Past Experience of Similar Services: The bidder must have successfu", 4, 2, BID_DOC_ROOT)
for text in [
    "5. Three similar completed services costing not less than the amount e",
    "6. Two similar completed services costing not less than the amount equ",
    "7. One similar completed service costing not less than the amount equa",
]:
    h(text, 4, 2, "4. Past Experience of Similar Services: The bidder must have successfu")

h("तकनीकी विशिष्टियाँ /Technical Specifications", 4, 1, BID_DOC_ROOT)

# ===== Page 5-9: "Buyer Added Bid Specific Terms and Conditions" - REAL
# parsed sequence, confirmed via direct block dump (NOT the rendered PDF's
# own 1-17 numbering): 1-10 at top level, item 10's own restarted 1-13 ATC
# sub-list, then 14 (continuing from the ATC sub-list, no reset), then a
# reset to 9, then 10-17 cleanly to the end. =====
h("फैला द्वारा जोड़ी गई बिड की विशेष शर्त/Buyer Added Bid Specific Terms", 5, 1, BID_DOC_ROOT)
TC_PARENT = "फैला द्वारा जोड़ी गई बिड की विशेष शर्त/Buyer Added Bid Specific Terms"
TC_ITEMS = [
    ("1. Generic", 5),
    ("2. Generic", 5),
    ("3. Generic", 6),
    ("4. Generic", 6),
    ("5. Generic", 6),
    ("6. Generic", 6),
    ("7. The Seller shall not assign the Contract in whole or part without o", 6),
    ("8. The Seller shall not sub-contract the Contract in whole or part to ", 6),
    ("9. The Seller shall, notwithstanding the consent and assignment/sub-co", 6),
    ("10. Buyer Added Bid Specific ATC", 6),
    ("14. Buyer Added Bid Specific ATC", 7),
    ("9. Buyer Added Bid Specific Scope Of Work(SOW)", 7),
    ("10. Forms of EMD and PBG", 7),
    ("11. Forms of EMD and PBG", 7),
    ("12. Forms of EMD and PBG", 8),
    ("13. Forms of EMD and PBG", 8),
    ("14. Forms of EMD and PBG", 8),
    ("15. Forms of EMD and PBG", 8),
    ("16. Forms of EMD and PBG", 8),
    ("17. Certificates", 9),
]
for text, page in TC_ITEMS:
    h(text, page, 2, TC_PARENT)

# Item "10. Buyer Added Bid Specific ATC"'s own restarted 1-13 sub-list
# (the "Buyer Added text based ATC clauses" body), pages 6-7 - this list
# genuinely restarts at 1 because of the intervening non-list paragraph.
ATC_PARENT = "10. Buyer Added Bid Specific ATC"
ATC_SUBITEMS = [
    ("1. The Bidder has to sign and upload Pre Contract Integrity Pact in th", 6),
    ("2. The bidder has to submit the Antiproffeteering Declaration in the p", 6),
    ("3. The bidder has to sign (each page) of the Bid Document Terms and co", 6),
    ("4. No counter conditions by bidder shall be accepted.", 6),
    ("5. Bidder has to upload Self Declarations for Financial Standing, Non-", 6),
    ("6. Technical and financial eligibility criteria for qualification of t", 6),
    ("7. Wherever GST is applicable, payment will be released against e-Invo", 6),
    ("8. GST as per GST Laws shall be payable as quoted and agreed.", 7),
    ("9. In case of purchases of goods/services from unregistered dealers un", 7),
    ("10. If the vendor is registered under GST, vendor shall mention the HS", 7),
    ("11. In case, DFCCIL is unable to avail ITC, supplier/contractor at the", 7),
    ("12. If the vendor is registered under GST, the GST registration number", 7),
    ("13. If the vendor is registered under GST, vendor shall file all appli", 7),
]
for text, page in ATC_SUBITEMS:
    h(text, page, 3, ATC_PARENT)

# ===== Page 9: Disclaimer, with its own restarted 1-14 sub-list (restarts
# cleanly - confirmed via block dump, a real non-list paragraph break
# precedes it) =====
h("अस्वीकरण/Disclaimer", 9, 1, BID_DOC_ROOT)
DISCLAIMER_PARENT = "अस्वीकरण/Disclaimer"
DISCLAIMER_ITEMS = [
    "1. Publishing Custom / BOQ bids for items for which regular GeM catego",
    "2. Mandating procurement of / from specific Brand / Make / Model / Man",
    "3. Inclusion of disqualification criteria related to suspension of sel",
    "4. Mandating submission of documents (other than EMD and signed Integr",
    "5. Publishing bids on GeM for procurement of works.",
    "6. Procurement of Goods by creating a Service bid on GeM & vice-versa.",
    "7. Seeking sample with bid or approval of samples during bid evaluatio",
    "8. Seeking experience from specific organization / department / instit",
    "9. Creating bid for items from incorrect categories.",
    "10. Reference of conditions published on any external site or referenc",
    "11. Asking for any Tender fee / Bid Participation fee, as the case may",
    "12. Buyer added ATC Clauses which are in contravention of clauses defi",
    "13. Any ATC clause in contravention with GeM GTC Clause 4 (xiii) (h) w",
    "14. In a category based bid, adding additional items, through buyer ad",
]
for text in DISCLAIMER_ITEMS:
    h(text, 9, 2, DISCLAIMER_PARENT)

print(f"Total authored headings: {len(ALL_HEADINGS)}")


async def run():
    total_pages = 10
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
