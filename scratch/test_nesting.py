import asyncio
import ai_indexing
import ai_reference_index
import reference_index

h0 = ai_indexing.AIHeading(heading_text="Key Dates and Basic Tender Information", start_page=5, level=1, parent_ref=None)
h1 = ai_indexing.AIHeading(heading_text="Note: -", start_page=5, level=2, parent_ref="Key Dates and Basic Tender Information")
h2 = ai_indexing.AIHeading(heading_text="(2) The Tenderers have to upload", start_page=5, level=3, parent_ref="Note: -")
h3 = ai_indexing.AIHeading(heading_text="(I) IN COVER-1: -", start_page=5, level=4, parent_ref="(2) The Tenderers have to upload")

async def test():
    pb = {5: ["Key Dates and Basic Tender Information", "Note: -", "(2) The Tenderers have to upload", "(I) IN COVER-1: -"]}
    ctx = ai_indexing.CarryContext(open_containers=[], last_page_tail="")
    events, warns = await ai_reference_index._ground_and_resolve_chunk([h0, h1, h2, h3], pb, ctx, "test", lambda p: False)
    print("Warns:", warns)
    for p, evs in events.items():
        for e in evs:
            print("depth:", e.get("depth"), "warn:", e.get("warn"), "text:", e.get("heading_text"))

    cands, scope = reference_index.apply_heading_events_to_scope(events.get(5, []), None, 5, False)
    print("\nResulting Candidates:")
    for c in cands:
        print(" ", c.get("key"), repr(c.get("own_label")), "scope_key:", c.get("scope_key"))

asyncio.run(test())
