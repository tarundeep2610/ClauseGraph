import pytest
import reference_index as ri
from ai_reference_index import _is_document_noise_or_running_header


def test_detect_contract_clauses_recovers_multi_segment_decimals():
    page_text = """
1.11.8. Confidentiality, data-protection and data-return obligations shall survive expiry.

1.11.9. Any violation of confidentiality may attract financial penalty.

1.12. Acceptance or Rejection of bid/offer

1.12.1. The Purchaser reserves the right to accept or reject any or all bids.
"""
    scope = {
        "stack": [
            {"family": "section", "key": "section:6", "label": "Section-VI: Special Conditions of Contract"}
        ]
    }
    existing_cands = [
        {"key": "numheading:1.12", "fingerprint": "1.12. Acceptance or Rejection"}
    ]
    
    results = ri.detect_contract_clauses_for_page(page_text, scope, existing_cands)
    keys = [r["key"] for r in results]
    
    # 1.12 was already in existing_cands, so it should not be duplicated
    assert "numheading:1.12" not in keys
    # 1.11.8, 1.11.9, 1.12.1 should be recovered
    assert "numheading:1.11.8" in keys
    assert "numheading:1.11.9" in keys
    assert "numheading:1.12.1" in keys


def test_detect_contract_clauses_recovers_letter_points():
    page_text = """
a. The SI shall prepare and maintain an interface catalogue.

b. For each interface, the catalogue shall specify protocols and formats.

c. Existing GeoServer or other GIS middleware shall be integrated.
"""
    scope = {
        "stack": [
            {"family": "clause", "key": "clause:2-1", "label": "2.1 Scope of Work"}
        ]
    }
    results = ri.detect_contract_clauses_for_page(page_text, scope, [])
    keys = [r["key"] for r in results]
    assert keys == ["point:a", "point:b", "point:c"]


def test_detect_contract_clauses_suppressed_in_listing_or_pleading_containers():
    page_text = """
1.1.1. Spares for 400KV Transformer.
a. Bushings and connectors.
"""
    # Listing container
    scope_listing = {
        "stack": [
            {"family": "subdoc", "key": "subdoc:price-schedule", "label": "Price Schedule"}
        ]
    }
    assert ri.detect_contract_clauses_for_page(page_text, scope_listing, []) == []

    # Pleading container
    scope_pleading = {
        "stack": [
            {"family": "subdoc", "key": "subdoc:petition", "label": "PETITION UNDER SECTION 34"}
        ]
    }
    assert ri.detect_contract_clauses_for_page(page_text, scope_pleading, []) == []


def test_detect_contract_clauses_recovers_bare_integer_clause_under_schedule_parent():
    # Real text from a 135-page tender doc's "Note:" block under "Annexure IV:
    # Format for Price Schedule" — a verb-bearing bare single-integer clause
    # that the blanket listing-container suppression used to drop entirely.
    page_text = """
5. GST as applicable shall be paid extra. Statutory variations in applicable taxes, if applicable during the contractual period shall be paid extra against documentary evidence.
"""
    scope = {
        "stack": [
            {"family": "section", "key": "section:7", "label": "Section VII: Annexures"},
            {"family": "annexure", "key": "annexure:iv", "label": "Annexure IV: Format for Price Schedule"},
        ]
    }
    results = ri.detect_contract_clauses_for_page(page_text, scope, [])
    keys = [r["key"] for r in results]
    assert "numheading:5" in keys


def test_detect_contract_clauses_still_suppresses_verb_free_bare_integer_row_under_schedule_parent():
    # Regression guard: a genuine data-table row (no finite verb/modal) under
    # the same schedule-labeled parent must still be suppressed as noise.
    page_text = """
3. Oracle ERP License Module 10 Nos
"""
    scope = {
        "stack": [
            {"family": "section", "key": "section:7", "label": "Section VII: Annexures"},
            {"family": "annexure", "key": "annexure:iv", "label": "Annexure IV: Format for Price Schedule"},
        ]
    }
    assert ri.detect_contract_clauses_for_page(page_text, scope, []) == []


def test_detect_contract_clauses_known_limitation_verb_free_instruction_stays_suppressed():
    # Known, accepted limitation: this instruction has no word from
    # _CLAUSE_VERB_RE ("wants"/"enter" aren't recognized modals/verbs), so it
    # is shape-indistinguishable from table noise and stays suppressed even
    # after the bare-integer-clause fix. Documented here, not silently gapped.
    page_text = """
11. If user wants to enter any decimal value than please enter as per given example i.e. "0.4" instead of ".4"
"""
    scope = {
        "stack": [
            {"family": "section", "key": "section:7", "label": "Section VII: Annexures"},
            {"family": "annexure", "key": "annexure:iv", "label": "Annexure IV: Format for Price Schedule"},
        ]
    }
    assert ri.detect_contract_clauses_for_page(page_text, scope, []) == []


def test_document_noise_or_running_header_filter():
    # Page 1 keeps document title
    assert not _is_document_noise_or_running_header("Tender Specification No. Discom/EZ/Pur/TS No. 1759", page_num=1)
    
    # Page > 1 drops running header
    assert _is_document_noise_or_running_header("Tender Specification No. Discom/EZ/Pur/TS No. 1759", page_num=2)
    assert _is_document_noise_or_running_header("Tender Specification No. Discom/EZ/Pur/TS No. 1759 (E-TS-2026_PKVVC_539545_1)", page_num=10)
    
    # Correspondence & Administrative metadata dropped
    assert _is_document_noise_or_running_header("Chief General Manager (S&P)", page_num=4)
    assert _is_document_noise_or_running_header("M.P.P.K.V.V.Co.Ltd., Jabalpur", page_num=10)
    assert _is_document_noise_or_running_header("Jabalpur, dtd:- 29.09.2026", page_num=4)
    assert _is_document_noise_or_running_header("SAVE ELECTRICITY", page_num=4)
    
    # Body sentence hallucinations dropped
    assert _is_document_noise_or_running_header("The bidder shall have to meet the following eligibility criteria", page_num=8)
    assert _is_document_noise_or_running_header("Following are the key requirements", page_num=44)
    assert _is_document_noise_or_running_header("Online tenders are invited for following services", page_num=3)
    
    # Genuine headings preserved
    assert not _is_document_noise_or_running_header("SECTION-VI: SPECIAL CONDITIONS OF CONTRACT", page_num=51)
    assert not _is_document_noise_or_running_header("ANNEXURE-XI: SERVICE LEVEL AGREEMENT", page_num=76)
    assert not _is_document_noise_or_running_header("4.3 Upgradation of all existing modules of Oracle ERP", page_num=44)
