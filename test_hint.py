import asyncio, ai_reference_index, ai_indexing, config

async def test():
    cfg = ai_indexing.describe_active_config()
    p_cfg = ai_indexing.ProviderConfig(provider=cfg['provider'], model=cfg['model'], api_key=config.AI_INDEX_API_KEY)
    provider = await ai_indexing.get_provider(p_cfg)
    
    candidates = ['JUDGMENT', 'Challenge to the Arbitral Award.', '3. The Arbitral Award has been challenged by the petitioner interalia on following grounds:']
    
    # Prompt with explicit distinction:
    sys2 = (
        "You are placing ONE heading into an existing document outline. "
        "You are told the chain of sections currently open, from outermost to innermost.\n"
        "CRITICAL RULES:\n"
        "1. Opposing party submissions (e.g. 'Respondent\\'s case', 'Submissions of petitioner', 'Submissions of respondent') "
        "are top-level sibling divisions of the lawsuit/judgment. They belong directly inside the outer document ('JUDGMENT'), "
        "NEVER inside the other party's section (like 'Challenge to the Arbitral Award').\n"
        "2. If an 'Extracted parent reference' is provided, that indicates the parent extracted from the full text. "
        "Choose that parent's option.\n"
        "Reply with ONLY the single letter of your answer, nothing else — no punctuation, no explanation."
    )
    candidates = ['JUDGMENT', 'Challenge to the Arbitral Award.', "Respondent's case"]
    lines = ['Heading to place: "Submissions of petitioner"', 'Extracted parent reference: "JUDGMENT"', '', 'Currently open (outermost first):']
    for i, label in enumerate(candidates):
        lines.append(f'  {i + 1}. {label!r}')
    lines.append('')
    lines.append('Where does the heading above belong? Options, innermost first:')
    n = len(candidates)
    for i in range(n):
        item_num = n - i
        lines.append(f'{ai_reference_index._PARENT_CHOICE_LETTERS[i]}) Directly inside {candidates[item_num - 1]!r} (item {item_num} above)')
    lines.append(f'{ai_reference_index._PARENT_CHOICE_LETTERS[n]}) Top-level — not inside any of the sections above')
    u2 = '\n'.join(lines)
    
    res2 = await provider.complete(system_prompt=sys2, user_prompt=u2)
    ans2 = ai_reference_index._parse_parent_choice_answer(res2.text, len(candidates))
    print('With explicit rule and 3 candidates:', res2.text.strip(), '-> depth', ans2)
    
    res2 = await provider.complete(system_prompt=sys2, user_prompt=u2)
    ans2 = ai_reference_index._parse_parent_choice_answer(res2.text, len(candidates))
    print('With hint:', res2.text.strip(), '-> depth', ans2)

asyncio.run(test())
