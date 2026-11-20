import asyncio
import ai_indexing
import ai_reference_index
import database
import reference_index

job_id = "89cfa529-2390-464c-9c3e-2446ef68c588"
pages = database.get_pages(job_id)
page_text_by_num = {p["page_num"]: (p.get("text") or "") for p in pages}

h_s1 = ai_indexing.AIHeading(heading_text="Section-I: Essential Tender Information", start_page=7, level=1, parent_ref=None)
h_rows = [
    ai_indexing.AIHeading(
        heading_text=f"{i}. {title}", start_page=7, level=2,
        parent_ref="Section-I: Essential Tender Information"
    )
    for i, title in [
        (1, "Name of works/services to be procured"),
        (2, "Purchaser"),
        (3, "TENDER/TENDER Specification Number"),
        (4, "TENDER Type"),
        (5, "Contract Period"),
        (6, "TENDER/TENDER Currency Settings"),
        (7, "Non-refundable TENDER document fee/TENDER fee (INR)"),
        (8, "Earnest Money Deposit (INR)"),
        (9, "Availability of TENDER/TENDER documents starts on"),
        (10, "Availability of TENDER documents ends on"),
        (11, "TENDER submission ends on"),
        (12, "TENDER opens on"),
        (13, "Pre-Bid Meeting/Conference if applicable"),
        (14, "TENDER validity period (in days)"),
        (15, "Purchaser address for Correspondence"),
    ]
]

async def test():
    all_h = [h_s1] + h_rows
    pb = {7: reference_index.parse_page_blocks(page_text_by_num[7])}
    ctx = ai_indexing.CarryContext(open_containers=[], last_page_tail="")
    events, warns = await ai_reference_index._ground_and_resolve_chunk(all_h, pb, ctx, "test", lambda p: False)
    print("Warns:", warns)
    cands, scope = reference_index.apply_heading_events_to_scope(events.get(7, []), None, 7, False)
    print("\nResulting Candidates on Page 7:")
    for c in cands:
        print(" ", c.get("key"), repr(c.get("own_label")), "scope_key:", c.get("scope_key"))

asyncio.run(test())
