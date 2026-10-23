"""Tests for ai_indexing.build_action_item_prompt — the actionable-items
sibling to build_structure_prompt, deliberately simpler (no carry_context/
rule_prior, since action items are flat per-page leaves) but sharing the
same optional two-pass `macro_context` mechanism."""

import json

import ai_indexing


def _chunk(pages):
    return ai_indexing.ChunkPlan(pages=[ai_indexing.PageInput(page_num=n, text=t) for n, t in pages])


def test_prompt_states_valid_page_range():
    chunk = _chunk([(5, "EMD due 15.03.2025."), (6, "Further terms.")])
    _system, user = ai_indexing.build_action_item_prompt(chunk)
    assert "pages 5-6" in user
    assert json.dumps([5, 6]) in user


def test_prompt_wraps_each_page_in_open_close_markers():
    chunk = _chunk([(12, "EMD shall be submitted on or before 15.03.2025.")])
    _system, user = ai_indexing.build_action_item_prompt(chunk)
    assert "<<<PAGE 12>>>" in user
    assert "EMD shall be submitted on or before 15.03.2025." in user
    assert "<<<END PAGE 12>>>" in user


def test_prompt_includes_document_hint_when_given():
    chunk = _chunk([(1, "text")])
    _system, user = ai_indexing.build_action_item_prompt(chunk, document_hint="MPPKVVCL ERP tender")
    assert "<<<HINT>>>" in user
    assert "MPPKVVCL ERP tender" in user


def test_prompt_omits_hint_block_when_not_given():
    chunk = _chunk([(1, "text")])
    _system, user = ai_indexing.build_action_item_prompt(chunk)
    assert "DOCUMENT HINT" not in user


def test_system_prompt_names_all_four_item_types():
    system = ai_indexing.ACTION_ITEM_SYSTEM_PROMPT
    for t in ("deadline", "obligation", "condition", "other"):
        assert f'"{t}"' in system


def test_output_contract_includes_new_relative_deadline_fields():
    # anchor_event/cadence are the two fields reconnaissance surfaced as
    # missing from a naive deadline/obligation/condition schema.
    contract = ai_indexing.ACTION_ITEM_OUTPUT_CONTRACT
    assert "anchor_event" in contract
    assert "cadence" in contract


def test_output_contract_is_appended_after_pages():
    chunk = _chunk([(1, "text")])
    _system, user = ai_indexing.build_action_item_prompt(chunk)
    pages_idx = user.index("# PAGES")
    contract_idx = user.index("OUTPUT FORMAT")
    assert contract_idx > pages_idx


def test_macro_context_omitted_when_not_given():
    chunk = _chunk([(1, "text")])
    _system, user = ai_indexing.build_action_item_prompt(chunk)
    assert "ACTIVE DOCUMENT DIVISION" not in user


def test_macro_context_renders_root_and_section_when_given():
    chunk = _chunk([(113, "Treasury (Finance) catalogue row.")])
    macro_ctx = {"root_container": "Annexure-XIII – ERP Modules implemented in MPPKVVCL", "parent_clause": "Treasury"}
    _system, user = ai_indexing.build_action_item_prompt(chunk, macro_context=macro_ctx)
    assert "ACTIVE DOCUMENT DIVISION" in user
    assert "Annexure-XIII" in user
    assert "Treasury" in user


def test_macro_context_as_is_guidance_is_present_when_rendered():
    chunk = _chunk([(113, "text")])
    macro_ctx = {"root_container": "Annexure-XIII – ERP Modules implemented in MPPKVVCL", "parent_clause": None}
    _system, user = ai_indexing.build_action_item_prompt(chunk, macro_context=macro_ctx)
    assert "AS-IS" in user
    assert "background" in user


def test_macro_context_block_appears_before_pages_block():
    chunk = _chunk([(113, "text")])
    macro_ctx = {"root_container": "Annexure-XIII", "parent_clause": None}
    _system, user = ai_indexing.build_action_item_prompt(chunk, macro_context=macro_ctx)
    assert user.index("ACTIVE DOCUMENT DIVISION") < user.index("# PAGES")


def test_empty_macro_context_dict_renders_nothing():
    chunk = _chunk([(1, "text")])
    _system, user = ai_indexing.build_action_item_prompt(chunk, macro_context={})
    assert "ACTIVE DOCUMENT DIVISION" not in user
