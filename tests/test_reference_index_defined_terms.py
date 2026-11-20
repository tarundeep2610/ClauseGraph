import pytest
from reference_index import (
    _is_acronym_of,
    PAREN_ABBR_RE,
    HEREINAFTER_RE,
    normalize_term_key,
    detect_mentions_for_page,
)


def test_paren_abbr_captures_nit_and_loa_for_short():
    # Notice Inviting Tender (NIT)
    text1 = "consisting of Notice Inviting Tender (NIT) and other files"
    m1 = PAREN_ABBR_RE.search(text1)
    assert m1 is not None
    assert m1.group("full").strip() == "Notice Inviting Tender"
    assert m1.group("abbr").strip() == "NIT"
    assert _is_acronym_of(m1.group("abbr"), m1.group("full")) is True

    # Letter of Acceptance (LOA, for short)
    text2 = "Letter of Acceptance (LOA, for short) vide memo No. CH-90"
    m2 = PAREN_ABBR_RE.search(text2)
    assert m2 is not None
    assert m2.group("full").strip() == "Letter of Acceptance"
    assert m2.group("abbr").strip() == "LOA"
    assert _is_acronym_of(m2.group("abbr"), m2.group("full")) is True


def test_is_acronym_of_accepts_scc_and_rejects_units_and_noise():
    # Special Conditions of Contract (SCC)
    assert _is_acronym_of("SCC", "Special Conditions of Contract") is True
    assert _is_acronym_of("GCC", "General Conditions of Contract") is True
    assert _is_acronym_of("ITB", "Instruction to Bidders") is True

    # Rejection of units and noise
    assert _is_acronym_of("mm", "Thickness of Radiator tube") is False
    assert _is_acronym_of("VA", "Rated Burden") is False
    assert _is_acronym_of("Projects", "XEN") is False


def test_hereinafter_defines_term_contractor():
    text = "herein after referred to as the Contractor which expression shall include..."
    m = HEREINAFTER_RE.search(text)
    assert m is not None
    assert m.group("term").strip() == "Contractor"
    assert normalize_term_key(m.group("term")) == "contractor"
    assert f"term:{normalize_term_key(m.group('term'))}" == "term:contractor"


def test_hereinafter_variants_in_real_text():
    # Quoted terms and "here in after"
    text1 = 'here in after referred to as "owner" or "HVPNL" which expression'
    m1 = HEREINAFTER_RE.search(text1)
    assert m1 is not None
    assert m1.group("term").strip() == "owner"
    assert normalize_term_key(m1.group("term")) == "owner"

    text2 = 'herein after called the "Contractor" which expression'
    m2 = HEREINAFTER_RE.search(text2)
    assert m2 is not None
    assert m2.group("term").strip() == "Contractor"
    assert normalize_term_key(m2.group("term")) == "contractor"


def test_short_abbreviations_do_not_match_ordinary_prose():
    # When target PO or CB is in target_index, short abbreviations match case-sensitively, whole-word only
    target_po = {"key": "term:po", "page": 1, "occurrence_index": 0}
    target_cb = {"key": "term:cb", "page": 1, "occurrence_index": 0}

    # "po" inside words (tempo, position) or lowercase "po" in prose must NOT match
    page_text_prose = "The tempo was fast and we saw positive results. Also po box 100."
    mentions_prose = detect_mentions_for_page(page_text_prose, 2, target_index=[target_po, target_cb])
    assert not any(m["key"] == "term:po" for m in mentions_prose)

    # Bare uppercase "PO" matches
    page_text_real = "As agreed in PO and CB specifications."
    mentions_real = detect_mentions_for_page(page_text_real, 2, target_index=[target_po, target_cb])
    found_keys = {m["key"] for m in mentions_real}
    assert "term:po" in found_keys
    assert "term:cb" in found_keys
