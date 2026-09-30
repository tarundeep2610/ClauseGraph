import asyncio, ai_reference_index, ai_indexing, database, config

async def test():
    h = ai_indexing.AIHeading(heading_text="Respondent's case", start_page=6, level=2, parent_ref='JUDGMENT:')
    carry_ref_map = {}
    chunk_heading_depths = {
        'memo of parties': 0, 'judgment': 0, 'petitioner s version': 1, 'facts': 1,
        '2 petitioner invited tenders on 26 09 2007': 2, 'challenge to the arbitral award': 1,
        '3 the arbitral award has been challenged by the petitioner interalia on following grounds': 2,
        'a that the award is based on no evidence': 3, 'o it is further urged that the arbitral tribunal': 3
    }
    # In test_ground.py, what was sim_stack_labels before Respondent's case?
    # Let's see:
    # 1. MEMO OF PARTIES (depth 0, rank 0 subdoc) -> sim_stack_labels = ['MEMO OF PARTIES']
    # 2. JUDGMENT (depth 0, rank 0 subdoc) -> sim_stack_labels = ['JUDGMENT']
    # 3. Petitioner's version (depth 1, level 2) -> sim_stack_labels = ['JUDGMENT', "Petitioner's version"]
    # 4. FACTS (depth 1, level 2) -> sim_stack_labels = ['JUDGMENT', 'FACTS']
    # 5. 2. Petitioner invited tenders... (words > 9, is_point_leaf = True) -> not pushed
    # 6. Challenge to the Arbitral Award. (depth 1, level 2) -> sim_stack_labels = ['JUDGMENT', 'Challenge to the Arbitral Award.']
    # 7. 3. The Arbitral Award... (words > 9, is_point_leaf = True) -> not pushed
    # 8. (A) That the award... -> what did (A) push?!
    # Let's check what (A) minted!
    print("Checking (A):")
    mf, k, l, _, _ = ai_reference_index.reference_index.mint_key_for_heading('(A) That the award is based on no evidence', 0, {})
    f_def = ai_reference_index.reference_index.FAMILIES.get(mf)
    print('(A) minted_family:', mf, 'rank:', f_def.get('rank') if f_def else None)

asyncio.run(test())
