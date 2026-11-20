"""Regression tests for _is_external_law()'s case-law-discussion exclusion —
specifically the "(supra)"/"(2015) 3 SCC 49" false positive found on a real
100-page judgment, where a block quoting a Supreme Court precedent about
"Section 28(3)" was wrongly registered as a document-internal target."""

from reference_index import build_targets_for_page


def _keys(page_text):
    candidates, _ = build_targets_for_page(page_text, seed_scope=None, has_next_page=False)
    return [c["key"] for c in candidates]


def test_supra_case_citation_excluded():
    page_text = (
        "Section 28(3) has also been amended to bring it in line with the "
        "judgment of this Court in Associate Builders (supra), making it "
        "clear that the construction of the terms of a contract is primarily "
        "for an arbitrator to decide."
    )
    assert _keys(page_text) == []


def test_scc_report_citation_excluded():
    page_text = (
        "(2015) 3 SCC 49. Section 28(3) has also been amended to bring it in "
        "line with the judgment of this Court."
    )
    assert _keys(page_text) == []


def test_existing_act_citation_still_excluded():
    page_text = "Section 34 of the Arbitration and Conciliation Act 1996 provides for setting aside an award."
    assert _keys(page_text) == []


def test_existing_cpc_reference_still_excluded():
    page_text = "Section 151 CPC empowers the court to make orders necessary for the ends of justice."
    assert _keys(page_text) == []


def test_genuine_in_document_section_not_excluded():
    page_text = (
        "Section 28. Payment terms shall be as specified in Annexure C, and "
        "the Contractor shall submit invoices monthly."
    )
    assert _keys(page_text) == ["section:28"]


def test_contract_word_boundary_not_excluded():
    page_text = "Clause 11 of the contract provides for liquidated damages."
    assert _keys(page_text) == ["clause:11"]


def test_all_caps_act_with_comma_excluded():
    page_text = "SECTION 34 OF THE ARBITRATION AND CONCILIATION ACT, 1996."
    assert _keys(page_text) == []


def test_dotted_cpc_excluded():
    page_text = "Section 151 C.P.C. empowers the court to make orders."
    assert _keys(page_text) == []


def test_dotted_crpc_excluded():
    page_text = "Section 482 Cr.P.C. provides inherent powers."
    assert _keys(page_text) == []


def test_air_and_scc_online_and_scr_excluded():
    assert _keys("Authority AIR 2015 SC 620 wherein Section 10 was discussed.") == []
    assert _keys("As held in 2018 SCC OnLine SC 243 regarding Section 11.") == []
    assert _keys("Reported in [2019] 4 SCR 123 concerning Clause 5.") == []


def test_air_and_scc_ordinary_headings_not_excluded():
    # Bare SCC in tender heading or AIR in air conditioning must not exclude
    assert _keys("Clause 4.1 Special Conditions of Contract (SCC) shall prevail over GCC.") == ["clause:4.1"]


def test_external_law_samples_fixture():
    import json
    import os
    fixture_path = os.path.join(os.path.dirname(__file__), "fixtures", "external_law_samples.json")
    with open(fixture_path, "r", encoding="utf-8") as f:
        samples = json.load(f)

    for item in samples:
        keys = _keys(item["text"])
        if item.get("expected_keys") is not None:
            assert keys == item["expected_keys"], f"Failed for '{item['text']}': got {keys}, expected {item['expected_keys']} ({item.get('description')})"

