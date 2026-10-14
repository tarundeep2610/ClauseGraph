import asyncio
import json
import re
import sys

sys.path.insert(0, '/app')
sys.path.insert(0, '/app/scripts')

import database
import reference_index
import ai_indexing
import ai_reference_index
from score_reference_index import duplicate_job, _set_engine_params, evaluate_run, load_gold_fixture

dup_id = duplicate_job('bb417471-561c-4029-bb21-3bdcf74e7dcb', chunk_pages=3)
_set_engine_params(dup_id, {'ai_index_review_rule_prior': False, 'ai_index_resolve_parent_via_ai': False})

orig_ground = ai_reference_index._ground_and_resolve_chunk
_COLON_INTRO_RE = re.compile(r':\s*(?:[-–—]|->)?\s*$|[-–—]\s*>\s*$')
_SUBLIST_MARKER_RE = re.compile(r'^\s*\(?(?P<marker>[a-zA-Z]|[ivxlcdmIVXLCDM]+)(?:\)\.?|\.)\s+(?P<text>\S.*)$')

def enhanced_colon_sublists(headings, page_blocks):
    if not headings or not page_blocks:
        return headings

    sorted_pages = sorted(page_blocks.keys())
    existing_norms = {ai_reference_index._norm_for_match(h.heading_text) for h in headings}
    new_headings = list(headings)

    for h in headings:
        start_p = h.start_page
        if start_p not in page_blocks:
            continue
        h_blocks = page_blocks[start_p]
        loc = ai_reference_index.locate_heading_block(h_blocks, h.heading_text, used_block_idxs=set())
        if loc is None:
            continue
        found_idx = loc[0]

        stripped = h.heading_text.strip()
        is_colon_heading = bool(_COLON_INTRO_RE.search(stripped)) and not stripped.lower().startswith("note:")

        # Check if the heading itself ends in colon, or an intro block within next 2 blocks ends in colon/introduces docs
        intro_blocks = []
        if is_colon_heading:
            intro_blocks.append((found_idx, start_p))
        else:
            for delta in (1, 2):
                idx = found_idx + delta
                if idx < len(h_blocks):
                    b_raw = h_blocks[idx]
                    if ai_reference_index._is_noise_or_footer(b_raw):
                        continue
                    b_clean = ai_reference_index._clean_block(b_raw)
                    if _COLON_INTRO_RE.search(b_clean) or 'contract documents' in b_clean.lower() or 'following documents' in b_clean.lower():
                        intro_blocks.append((idx, start_p))
                        break

        for intro_idx, intro_p in intro_blocks:
            curr_p_idx = sorted_pages.index(intro_p) if intro_p in sorted_pages else -1
            if curr_p_idx == -1:
                continue

            consecutive_run = []
            stopped = False
            for p_idx in range(curr_p_idx, len(sorted_pages)):
                if stopped:
                    break
                p_num = sorted_pages[p_idx]
                p_b_list = page_blocks[p_num]
                b_start = (intro_idx + 1) if p_idx == curr_p_idx else 0
                for b_i in range(b_start, len(p_b_list)):
                    block_raw = p_b_list[b_i]
                    if ai_reference_index._is_noise_or_footer(block_raw):
                        continue
                    block_clean = ai_reference_index._clean_block(block_raw)
                    m = _SUBLIST_MARKER_RE.match(block_clean)
                    if m:
                        marker = m.group("marker")
                        if marker.islower() or (len(marker) <= 4 and marker.lower() in ("i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x")):
                            consecutive_run.append((p_num, block_clean, marker))
                    else:
                        if _COLON_INTRO_RE.search(block_clean) and len(block_clean.split()) <= 20:
                            continue
                        stopped = True
                        break

            if len(consecutive_run) >= 2:
                for run_p, run_clean, marker in consecutive_run:
                    run_norm = ai_reference_index._norm_for_match(run_clean)
                    if run_norm not in existing_norms:
                        existing_norms.add(run_norm)
                        synth_h = ai_indexing.AIHeading(
                            heading_text=run_clean[:200],
                            start_page=run_p,
                            parent_ref=h.heading_text,
                            level=h.level + 1,
                            family_hint="point",
                            number_hint=marker.lower(),
                        )
                        new_headings.append(synth_h)

    for h in new_headings:
        if h.parent_ref and not h.family_hint:
            m = _SUBLIST_MARKER_RE.match(h.heading_text.strip())
            if m:
                marker = m.group("marker")
                if marker.islower() or (len(marker) <= 4 and marker.lower() in ("i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x")):
                    h.family_hint = "point"
                    h.number_hint = marker.lower()

    return new_headings

ai_reference_index._recover_colon_sublists_in_chunk = enhanced_colon_sublists

async def targeted_ground(headings, page_blocks, carry_context, *args, **kwargs):
    min_page = min(page_blocks.keys()) if page_blocks else 0
    if min_page > 2:
        carry_context = ai_indexing.CarryContext(
            open_containers=[c for c in carry_context.open_containers if c.get('label','').strip().upper() not in ("INDEX", "TABLE OF CONTENTS")],
            last_page_tail=carry_context.last_page_tail,
        )

    # In chunk 85_86_87, tag Incentive and Power Transformers as point so they don't increment boundary_ordinal
    for h in headings:
        if h.parent_ref and "payment terms" in h.parent_ref.lower():
            if not reference_index._leading_reference_number(h.heading_text):
                h.family_hint = "point"

    events_by_page, warning_counts = await orig_ground(headings, page_blocks, carry_context, *args, **kwargs)

    # Filter non-root subdocs that ground to table blocks or are unlocated
    filtered_events = {}
    for p_num, ev_list in events_by_page.items():
        kept = []
        blocks = page_blocks.get(p_num, [])
        for ev in ev_list:
            txt = ev.get('heading_text', '')
            is_subdoc_root = reference_index.is_subdoc_root_candidate(txt, ev.get('family_hint'))
            minted_fam, _, _, _, _ = reference_index.mint_key_for_heading(
                txt, boundary_ordinal=0, label_occurrence_counts={},
                family_hint=ev.get('family_hint'), number_hint=ev.get('number_hint'),
            )
            fp = ev.get('fingerprint', '')
            is_table = False
            if fp and blocks:
                for b in blocks:
                    if b.startswith(fp):
                        if b.strip().startswith(('<table', '<tr')) or '<td>' in b:
                            is_table = True
                        break
            if minted_fam == 'subdoc' and not is_subdoc_root:
                if is_table or not fp:
                    continue
            kept.append(ev)
        filtered_events[p_num] = kept

    return filtered_events, warning_counts

ai_reference_index._ground_and_resolve_chunk = targeted_ground

async def test():
    scope = ai_indexing.replay_scope('/app/data/ai_recordings/bb417471-561c-4029-bb21-3bdcf74e7dcb_full_trial1_1791546500.json', strict_fidelity=False)
    with scope:
        await ai_reference_index.reindex_job_with_ai(dup_id)
    gold = load_gold_fixture('/app/tests/fixtures/gold_index_bb417471.json')
    scores = evaluate_run(dup_id, gold, return_diff=True)
    ai_p = scores['ai_precision']
    ai_r = scores['ai_recall']
    tg_p = scores['target_precision']
    tg_r = scores['target_recall']
    tax = scores['drift_tax']
    fps = scores['diff']['false_positives']
    fns = scores['diff']['false_negatives']
    print('\n================ RECOVERY EVALUATION ================')
    print(f'AI Target Precision:     {ai_p:.3f}')
    print(f'AI Target Recall:        {ai_r:.3f}')
    print(f'Merged Target Precision: {tg_p:.3f}')
    print(f'Merged Target Recall:    {tg_r:.3f}')
    print(f'Drift Tax:               {tax:+.3f}')
    print(f'Total FPs: {len(fps)}  Total FNs: {len(fns)}')
    fps_88 = [x for x in fps if x['page'] == 88]
    fns_88 = [x for x in fns if x['page'] == 88]
    print(f'Page 88 FPs ({len(fps_88)}):', [x['key'] for x in fps_88])
    print(f'Page 88 FNs ({len(fns_88)}):', [x['key'] for x in fns_88])
    database.delete_job(dup_id)

asyncio.run(test())
