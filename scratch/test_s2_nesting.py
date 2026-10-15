import asyncio
import ai_indexing
import ai_reference_index
import database
import reference_index

job_id = "89cfa529-2390-464c-9c3e-2446ef68c588"
pages = database.get_pages(job_id)
page_text_by_num = {p["page_num"]: (p.get("text") or "") for p in pages}

h_s2 = ai_indexing.AIHeading(heading_text="Section-II: Eligibility Criteria for Tenderers", start_page=8, level=1, parent_ref=None)
h_intro = ai_indexing.AIHeading(
    heading_text="(1) The essential eligibility criteria for Tenderers have been given in the table hereunder:",
    start_page=8, level=2, parent_ref="Section-II: Eligibility Criteria for Tenderers"
)
h_rows_p8 = [
    ai_indexing.AIHeading(
        heading_text=f"{i} {title}", start_page=8, level=2,
        parent_ref="Section-II: Eligibility Criteria for Tenderers"
    )
    for i, title in [
        (1, "The bidder should be a Company registered in India"),
        (2, "The bidder should be registered in India with"),
        (3, "The Sole bidder/Lead bidder and Consortium Partner"),
        (4, "The Sole bidder/Lead bidder should have a Minimum Average Annual Turnover"),
        (5, "The Sole bidder/Lead bidder and Consortium Partner (in case of consortium) must have positive net worth"),
    ]
]
h_rows_p9 = [
    ai_indexing.AIHeading(
        heading_text=f"{i} {title}", start_page=9, level=2,
        parent_ref="Section-II: Eligibility Criteria for Tenderers"
    )
    for i, title in [
        (6, "The Sole bidder/Lead bidder should have successfully executed at least three projects"),
        (7, "The Sole Bidder, in case of an individual bid, or the Lead Bidder/Lead Consortium Member"),
        (8, "The Sole bidder/Lead bidder shall be an active Oracle Partner Network"),
        (9, "The Sole Bidder, in case of an individual bid, or the Lead Bidder/Lead Consortium Member, in case of a Consortium, shall possess ISO 9001"),
        (10, "The Sole Bidder/Lead Member of Consortium shall have minimum 100 IT professionals"),
    ]
]

async def test():
    all_h = [h_s2, h_intro] + h_rows_p8 + h_rows_p9
    pb = {
        8: reference_index.parse_page_blocks(page_text_by_num[8]),
        9: reference_index.parse_page_blocks(page_text_by_num[9]),
    }
    ctx = ai_indexing.CarryContext(open_containers=[], last_page_tail="")
    events, warns = await ai_reference_index._ground_and_resolve_chunk(all_h, pb, ctx, "test", lambda p: False)
    print("Warns:", warns)

    seed = None
    for p in (8, 9):
        cands, seed = reference_index.apply_heading_events_to_scope(events.get(p, []), seed, p, p < 9)
        print(f"\nResulting Candidates on Page {p}:")
        for c in cands:
            print(f"  p{p} {c.get('key'):12s} {c.get('own_label')!r:50s} scope_key: {c.get('scope_key')}")

asyncio.run(test())
