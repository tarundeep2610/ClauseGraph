import asyncio, ai_reference_index, ai_indexing, database, config

async def run():
    pages = database.get_pages('108ba949-bb7b-4f29-b582-ce4f4a44631b')
    import reference_index
    page_blocks = {p['page_num']: reference_index.parse_page_blocks(p.get('text') or '') for p in pages if 1 <= p['page_num'] <= 10}
    
    headings = [
        ai_indexing.AIHeading(heading_text='MEMO OF PARTIES', start_page=1, level=1, parent_ref=None),
        ai_indexing.AIHeading(heading_text='JUDGMENT', start_page=2, level=1, parent_ref=None),
        ai_indexing.AIHeading(heading_text="Petitioner's version", start_page=3, level=2, parent_ref='JUDGMENT:'),
        ai_indexing.AIHeading(heading_text='FACTS', start_page=3, level=2, parent_ref='JUDGMENT:'),
        ai_indexing.AIHeading(heading_text='2. Petitioner invited tenders on 26.09.2007', start_page=3, level=3, parent_ref='FACTS:'),
        ai_indexing.AIHeading(heading_text='Challenge to the Arbitral Award.', start_page=4, level=2, parent_ref='JUDGMENT:'),
        ai_indexing.AIHeading(heading_text='3. The Arbitral Award has been challenged by the petitioner interalia on following grounds:', start_page=4, level=3, parent_ref='Challenge to the Arbitral Award.'),
        ai_indexing.AIHeading(heading_text='(A) That the award is based on no evidence', start_page=4, level=4, parent_ref='3. The Arbitral Award has been challenged by the petitioner interalia on following grounds:'),
        ai_indexing.AIHeading(heading_text="(O) It is further urged that the Arbitral Tribunal", start_page=6, level=4, parent_ref='3. The Arbitral Award has been challenged by the petitioner interalia on following grounds:'),
        ai_indexing.AIHeading(heading_text="Respondent's case", start_page=6, level=2, parent_ref='JUDGMENT:'),
        ai_indexing.AIHeading(heading_text="4. Petition is resisted on behalf of respondent", start_page=6, level=3, parent_ref="Respondent's case"),
        ai_indexing.AIHeading(heading_text="5. It has been further urged on merits", start_page=7, level=3, parent_ref="Respondent's case"),
        ai_indexing.AIHeading(heading_text="Submissions of petitioner", start_page=8, level=2, parent_ref='JUDGMENT:'),
        ai_indexing.AIHeading(heading_text="10. It has been argued by learned counsel", start_page=8, level=3, parent_ref="Submissions of petitioner"),
    ]
    carry_context = ai_indexing.CarryContext(open_containers=[], last_page_tail='')
    events_by_page, warnings = await ai_reference_index._ground_and_resolve_chunk(
        headings, page_blocks, carry_context, 'gemini', lambda p: True,
        resolve_parent_via_ai=True, provider_config=ai_indexing.ProviderConfig('gemini', 'gemini-3.1-flash-lite', api_key=config.AI_INDEX_API_KEY)
    )
    for p, evs in events_by_page.items():
        for ev in evs:
            print(f'P{p} depth={ev.get("depth")} text={ev.get("heading_text")[:35]}')

asyncio.run(run())
