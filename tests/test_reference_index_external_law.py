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
