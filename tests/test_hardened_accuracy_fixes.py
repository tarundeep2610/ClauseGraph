import pytest
import re
import reference_index
import ai_indexing.schema as schema


def test_bare_number_phantom_parent_ref_is_dropped():
    # Bare page number with phantom carry reference (e.g. "c1") must be dropped
    chunk = schema.AIStructureChunk(
        headings=[
            schema.AIHeading(heading_text="5", start_page=7, level=1, parent_ref="c1"),
            schema.AIHeading(heading_text="7", start_page=7, level=2, parent_ref="c2"),
            schema.AIHeading(heading_text="1.", start_page=7, level=3, parent_ref="RESPECTFULLY SHOWETH:"),
            schema.AIHeading(heading_text="2.", start_page=7, level=1, family_hint="point"),
        ]
    )
    normalized, report = schema.normalize_chunk(chunk, [7])
    kept_texts = [h.heading_text for h in normalized.headings]
    assert "5" not in kept_texts
    assert "7" not in kept_texts
    assert "1." in kept_texts
    assert "2." in kept_texts
    assert report.dropped_empty == 2


def test_broadened_pleading_averment_matching():
    averments = [
        "2. Petitioner invited tenders on 26.09.2007",
        "8. Rejoinder controverting each and every fact",
        "9. Arguments have been advanced by Ms. Sonia Madan",
        "10. It has been argued by learned counsel",
        "1. That the appellant is filing the accompanying appeal",
        "2. The deponent is conversant with the facts",
        "(a) That the respondent failed to comply",
        "(i) The petitioner respectfully submits",
    ]
    for text in averments:
        assert reference_index._PLEADING_AVERMENT_RE.search(text) is not None, f"Failed to match: {text}"

    non_averments = [
        "1. Scope of work and specifications",
        "2. Payment terms shall be 30 days",
        "3. Technical data sheet",
    ]
    for text in non_averments:
        assert reference_index._PLEADING_AVERMENT_RE.search(text) is None, f"Should not match: {text}"


def test_standalone_document_root_reset():
    # 1. Under a generic subdoc root (e.g. Respectfully Showeth), Affidavit must reset to depth 0
    events_subdoc = [
        {
            "heading_text": "Affidavit of Ishwar Chand Balan",
            "depth": 2,
            "fingerprint": "affidavit_ishwar",
        }
    ]
    seed_scope_subdoc = {
        "stack": [{"family": "subdoc", "key": "subdoc:respectfully-showeth", "label": "RESPECTFULLY SHOWETH"}],
        "boundary_ordinal": 1,
        "label_occurrence_counts": {},
        "last_bare_integer": None,
        "last_letter_ordinal": None,
    }
    cands, new_scope = reference_index.apply_heading_events_to_scope(
        events_subdoc, seed_scope_subdoc, page_num=8, has_next_page=True
    )
    assert len(cands) == 1
    # Should have evicted respectfully-showeth and minted at depth 0
    assert len(new_scope["stack"]) == 1
    assert new_scope["stack"][0]["family"] == "subdoc"
    assert "affidavit" in new_scope["stack"][0]["key"].lower()
    assert cands[0]["scope_key"] == ""

    # 2. Under an Annexure root, Affidavit must NOT reset to depth 0 (should remain depth 1)
    events_annexure = [
        {
            "heading_text": "Affidavit of Ishwar Chand Balan",
            "depth": 1,
            "fingerprint": "affidavit_annex",
        }
    ]
    seed_scope_annex = {
        "stack": [{"family": "annexure", "key": "annexure:a-1", "label": "ANNEXURE A-1"}],
        "boundary_ordinal": 1,
        "label_occurrence_counts": {},
        "last_bare_integer": None,
        "last_letter_ordinal": None,
    }
    cands_annex, new_scope_annex = reference_index.apply_heading_events_to_scope(
        events_annexure, seed_scope_annex, page_num=62, has_next_page=True
    )
    assert len(cands_annex) == 1
    # Annexure A-1 remains the root at depth 0
    assert new_scope_annex["stack"][0]["key"] == "annexure:a-1"
    assert cands_annex[0]["scope_key"] == "annexure:a-1"


def test_explicit_family_precedence_over_pleading_point():
    # Under active pleading scope, an explicit Annexure heading must mint as annexure, not point
    seed_scope = {
        "stack": [{"family": "subdoc", "key": "subdoc:judgment", "label": "JUDGMENT"}],
        "boundary_ordinal": 1,
        "label_occurrence_counts": {},
        "last_bare_integer": None,
        "last_letter_ordinal": None,
    }
    events = [
        {
            "heading_text": "AmneXureA-1",
            "depth": 0,
            "fingerprint": "amnexurea1",
        }
    ]
    cands, new_scope = reference_index.apply_heading_events_to_scope(
        events, seed_scope, page_num=61, has_next_page=True
    )
    assert len(cands) == 1
    assert cands[0]["key"] == "annexure:a-1"
    assert not cands[0]["key"].startswith("point:")


def test_tribunal_and_form_noise_rejection():
    chunk = schema.AIStructureChunk(
        headings=[
            schema.AIHeading(heading_text="2. Mr. R.K. Aggarwal, Presiding Arbitrator, #607", start_page=1),
            schema.AIHeading(heading_text="(ORDER 41, RULE 1, CIVIL PROCEDURE CODE, 1908)", start_page=1),
            schema.AIHeading(heading_text="OPENING SHEET FOR CIVIL APPEALS", start_page=1),
            # "CONSTRUCTION OF ... SUB-STATION"/"KIRORI (HISAR)" removed from
            # this case: those were document-specific project-title strings
            # hardcoded into the supposedly "generic" noise filter (D14),
            # removed since they'd never match any other document's project
            # title and were overfit to this one fixture — see
            # test_document_specific_project_title_strings_are_not_generically_matched
            # in test_ai_indexing_schema_noise_filters.py.
            schema.AIHeading(heading_text="CLAIM IN APPEAL", start_page=1),
            schema.AIHeading(heading_text="Enactment and section under which appeal lies", start_page=1),
            # Legitimate heading to ensure it is kept
            schema.AIHeading(heading_text="GROUNDS OF APPEAL", start_page=1),
        ]
    )
    normalized, report = schema.normalize_chunk(chunk, [1])
    kept = [h.heading_text for h in normalized.headings]
    assert kept == ["GROUNDS OF APPEAL"]
    assert report.dropped_invalid >= 5


def test_contract_clauses_under_annexure_filters_narrative_prose():
    scope_annex = {
        "stack": [{"family": "annexure", "key": "annexure:a-1", "label": "ANNEXURE A-1"}],
    }
    sample_text = (
        "2.0 CONTRACT DOCUMENTS\n\n"
        "2.1 The contract shall be performed strictly as per the terms and conditions stipulated herein.\n\n"
        "3.0 CONDITIONS AND COVENANTS\n\n"
        "3.1 The scope of the contract is contained in HVPN's detailed award.\n\n"
        "3.3 TIME SCHEDULE\n\n"
        "3.4.1 The contractor is responsible for the proper execution of the quality plans.\n\n"
    )
    cands = reference_index.detect_contract_clauses_for_page(sample_text, scope_annex, [])
    keys = [c["key"] for c in cands]
    # Standalone titles are kept
    assert "numheading:2.0" in keys
    assert "numheading:3.0" in keys
    assert "numheading:3.3" in keys
    # Narrative paragraphs starting with decimals are rejected
    assert "numheading:2.1" not in keys
    assert "numheading:3.1" not in keys
    assert "numheading:3.4.1" not in keys


def test_contract_clauses_under_section_preserves_multiseg_clauses():
    scope_section = {
        "stack": [{"family": "section", "key": "section:6", "label": "SECTION VI - SPECIAL CONDITIONS OF CONTRACT"}],
    }
    sample_text = (
        "1.1.1 The contract period shall include initial two (2) weeks of resource deployment.\n\n"
        "1.1.2 However, in case the performance during the currency of the Contract is not satisfactory.\n\n"
    )
    cands = reference_index.detect_contract_clauses_for_page(sample_text, scope_section, [])
    keys = [c["key"] for c in cands]
    # Under section, multi-segment contract clauses must be preserved
    assert "numheading:1.1.1" in keys
    assert "numheading:1.1.2" in keys


def test_distribution_list_not_minted_as_pleading_points():
    scope_pleading = {
        "stack": [{"family": "subdoc", "key": "subdoc:despatch", "label": "DESPATCH OF MATERIAL"}],
    }
    sample_text = """
    Endst. No. 1234/HVPN
    Copy forwarded to the following for information and necessary action:-
    1. The Chief Engineer/MM, HVPNL, Panchkula.
    2. The Superintending Engineer/Design, HVPNL, Panchkula.
    3. The Executive Engineer/Projects, HVPNL, Hisar.
    """
    cands, _ = reference_index.detect_pleading_points_for_page(sample_text, scope_pleading, [])
    # None of the recipients in the distribution list should be minted as pleading points
    assert len(cands) == 0

