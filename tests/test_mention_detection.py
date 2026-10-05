"""Parity tests for Python backend mention detection against frontend markdown.test.js cases.

Verifies that detect_mentions_for_page produces identical verdicts across all edge cases:
- Exact & approximate resolution
- Code/link/image boundary safety
- Roman numeral normalization
- Range/list continuation expansion (inclusive to, ampersand, Oxford comma, 25-cap)
- Decimal-only restriction and year guard on continuations
- External statute citation guard
- External case law citation guard (forward & backward, across formatting)
- HTML block scanning and entity decoding (&amp;, &nbsp;)
- No-space/hyphen OCR tolerance
- Page footer guard
"""

import pytest
from reference_index import detect_mentions_for_page, TargetLookupIndex


def _resolving_keys(mentions):
    """Returns list of distinct keys with resolution in ('exact', 'approximate')."""
    seen = set()
    out = []
    for m in mentions:
        if m["resolution"] in ("exact", "approximate") and m["key"] not in seen:
            seen.add(m["key"])
            out.append(m["key"])
    return out


def test_exact_candidate_resolved():
    targets = {12: {"clause:8": [{"anchor_id": "xref-target-clause-8-p12-0", "collapsed_key": "clause:8"}]}}
    mentions = detect_mentions_for_page("See Clause 8 for the force majeure terms.", 5, targets)
    assert len(mentions) == 1
    assert mentions[0]["key"] == "clause:8"
    assert mentions[0]["raw_text"] == "Clause 8"
    assert mentions[0]["resolution"] == "exact"


def test_zero_candidates_suppressed_unresolved():
    mentions = detect_mentions_for_page("See Clause 99 for details.", 5, {})
    assert len(mentions) == 1
    assert mentions[0]["key"] == "clause:99"
    assert mentions[0]["resolution"] == "suppressed"
    assert mentions[0]["suppressed_reason"] == "unresolved"
    assert _resolving_keys(mentions) == []


def test_collapsed_key_collision_surfaces_as_approximate():
    targets = {7: {"clause:1.1": [{"anchor_id": "a1", "collapsed_key": "clause:11"}]}}
    mentions = detect_mentions_for_page("Refer to Clause 11 above.", 5, targets)
    assert len(mentions) == 1
    assert mentions[0]["key"] == "clause:11"
    assert mentions[0]["resolution"] == "approximate"


def test_markdown_link_and_codespan_skipped():
    targets = {12: {"clause:8": [{"anchor_id": "a1", "collapsed_key": "clause:8"}]}}
    mentions = detect_mentions_for_page("See [Clause 8](https://example.com) and `Clause 8` too.", 5, targets)
    assert len(mentions) == 0


def test_roman_numeral_mention_normalizes_to_arabic_key():
    targets = {3: {"article:2": [{"anchor_id": "a1", "collapsed_key": "article:2"}]}}
    mentions = detect_mentions_for_page("As per Article II above.", 5, targets)
    assert len(mentions) == 1
    assert mentions[0]["key"] == "article:2"
    assert mentions[0]["resolution"] == "exact"


def test_mention_nested_in_list_item():
    targets = {9: {"ground:2": [{"anchor_id": "a1", "collapsed_key": "ground:2"}]}}
    mentions = detect_mentions_for_page("- See Ground 2 above.\n- Another bullet.", 9, targets)
    assert _resolving_keys(mentions) == ["ground:2"]


def test_expand_inclusive_to_range():
    targets = {
        61: {"annexure:a-1": [{"anchor_id": "a1", "collapsed_key": "annexure:a1"}]},
        77: {"annexure:a-3": [{"anchor_id": "a3", "collapsed_key": "annexure:a3"}]},
    }
    mentions = detect_mentions_for_page("Annexure A-1 to A-5, which are very essential.", 7, targets)
    assert _resolving_keys(mentions) == ["annexure:a-1", "annexure:a-3"]
    all_keys = [m["key"] for m in mentions]
    assert all_keys == ["annexure:a-1", "annexure:a-2", "annexure:a-3", "annexure:a-4", "annexure:a-5"]


def test_expand_explicit_list_with_ampersand():
    targets = {
        8: {
            "point:1": [{"anchor_id": "p1", "collapsed_key": "point:1"}],
            "point:2": [{"anchor_id": "p2", "collapsed_key": "point:2"}],
        }
    }
    mentions = detect_mentions_for_page("para 1 & 2 of above affidavit are true.", 8, targets)
    assert _resolving_keys(mentions) == ["para:1", "para:2"]
    assert all(m["resolution"] == "approximate" for m in mentions)


def test_oxford_comma_continuation():
    targets = {
        61: {"annexure:a-1": [{"anchor_id": "a1", "collapsed_key": "annexure:a1"}]},
        70: {"annexure:a-2": [{"anchor_id": "a2", "collapsed_key": "annexure:a2"}]},
        77: {"annexure:a-3": [{"anchor_id": "a3", "collapsed_key": "annexure:a3"}]},
    }
    mentions = detect_mentions_for_page("Annexure A-1, A-2, and A-3 are attached.", 7, targets)
    assert _resolving_keys(mentions) == ["annexure:a-1", "annexure:a-2", "annexure:a-3"]


def test_garbled_runaway_range_capped_at_25():
    targets = {5: {"para:1": [{"anchor_id": "p1", "collapsed_key": "para:1"}]}}
    mentions = detect_mentions_for_page("para 1 to 9999 of the record.", 5, targets)
    assert len(mentions) == 26  # base + 25 expanded
    assert mentions[0]["key"] == "para:1"


def test_reject_word_resembling_roman_continuation():
    targets = {
        61: {"annexure:a-1": [{"anchor_id": "a1", "collapsed_key": "annexure:a1"}]},
        90: {"annexure:c-43": [{"anchor_id": "bogus", "collapsed_key": "annexure:c43"}]},
    }
    mentions = detect_mentions_for_page("Annexure A-1, civil appeal is pending.", 7, targets)
    assert _resolving_keys(mentions) == ["annexure:a-1"]


def test_reject_bare_four_digit_year_after_comma():
    targets = {
        5: {"clause:5": [{"anchor_id": "c5", "collapsed_key": "clause:5"}]},
        6: {"clause:1996": [{"anchor_id": "bogus", "collapsed_key": "clause:1996"}]},
    }
    mentions = detect_mentions_for_page("Clause 5, 1996 edition applies.", 5, targets)
    assert _resolving_keys(mentions) == ["clause:5"]


def test_external_statute_guard_suppresses_cross_family_fallback():
    targets = {56: {"numheading:34": [{"anchor_id": "nh34", "collapsed_key": "numheading:34"}]}}
    mentions = detect_mentions_for_page(
        "objection filed under section 34 of the Arbitration Act, 1996 as Annexure A-1.", 7, targets,
    )
    sec34 = [m for m in mentions if m["key"] == "section:34"]
    assert len(sec34) == 1
    assert sec34[0]["resolution"] == "suppressed"
    assert sec34[0]["suppressed_reason"] == "external_law"
    assert "section:34" not in _resolving_keys(mentions)


def test_genuine_same_family_target_not_suppressed_by_external_law():
    targets = {10: {"section:34": [{"anchor_id": "s34", "collapsed_key": "section:34"}]}}
    mentions = detect_mentions_for_page("as per section 34 of the Arbitration Act, 1996.", 7, targets)
    assert _resolving_keys(mentions) == ["section:34"]
    assert mentions[0]["resolution"] == "exact"


def test_genuine_internal_citation_with_cross_family_fallback():
    targets = {25: {"numheading:9.0": [{"anchor_id": "nh9", "collapsed_key": "numheading:90"}]}}
    mentions = detect_mentions_for_page("clause 9.0 ITB, Vol-II, Contract, which reads as follows.", 26, targets)
    assert _resolving_keys(mentions) == ["clause:9.0"]
    assert mentions[0]["resolution"] == "approximate"


def test_external_statute_guard_not_applied_to_para_family():
    targets = {8: {"point:2": [{"anchor_id": "p2", "collapsed_key": "point:2"}]}}
    mentions = detect_mentions_for_page("para 2 of the Companies Act, 2013 defines this term.", 8, targets)
    assert _resolving_keys(mentions) == ["para:2"]
    assert mentions[0]["resolution"] == "approximate"


def test_roman_numeral_base_mention_no_continuation():
    targets = {
        14: {"ground:4": [{"anchor_id": "g4", "collapsed_key": "ground:4"}]},
        15: {"ground:5": [{"anchor_id": "bogus", "collapsed_key": "ground:5"}]},
    }
    mentions = detect_mentions_for_page("As per Ground iv and v above.", 14, targets)
    assert _resolving_keys(mentions) == ["ground:4"]


def test_word_boundary_regression_contract():
    targets = {25: {"numheading:9.0": [{"anchor_id": "nh9", "collapsed_key": "numheading:90"}]}}
    mentions = detect_mentions_for_page("as per clause 9.0, ITB, Vol.-II, of the contract.", 43, targets)
    assert _resolving_keys(mentions) == ["clause:9.0"]


def test_case_citation_preceding_suppresses_para():
    targets = {51: {"point:22": [{"anchor_id": "p22", "collapsed_key": "point:22"}]}}
    mentions = detect_mentions_for_page(
        'Authority AIR 2015 SC620 wherein it has been held in para No.22 that: "Here again..."',
        49, targets,
    )
    p22 = [m for m in mentions if m["key"] == "para:22"]
    assert len(p22) == 1
    assert p22[0]["resolution"] == "suppressed"
    assert p22[0]["suppressed_reason"] == "case_citation"
    assert "para:22" not in _resolving_keys(mentions)


def test_case_citation_following_suppresses_para():
    targets = {51: {"point:5": [{"anchor_id": "p5", "collapsed_key": "point:5"}]}}
    mentions = detect_mentions_for_page("as held in para 5 of AIR 2015 SC 620.", 49, targets)
    p5 = [m for m in mentions if m["key"] == "para:5"]
    assert len(p5) == 1
    assert p5[0]["resolution"] == "suppressed"
    assert p5[0]["suppressed_reason"] == "case_citation"


def test_case_citation_across_formatting_boundary():
    targets = {51: {"point:22": [{"anchor_id": "p22", "collapsed_key": "point:22"}]}}
    mentions = detect_mentions_for_page('as held in *AIR 2015 SC 620* in para 22 that: "..."', 49, targets)
    p22 = [m for m in mentions if m["key"] == "para:22"]
    assert len(p22) == 1
    assert p22[0]["resolution"] == "suppressed"
    assert p22[0]["suppressed_reason"] == "case_citation"


def test_genuine_internal_para_without_citation_unaffected():
    targets = {8: {"point:1": [{"anchor_id": "p1", "collapsed_key": "point:1"}]}}
    mentions = detect_mentions_for_page("para 1 of above affidavit are true and correct.", 8, targets)
    assert _resolving_keys(mentions) == ["para:1"]


def test_real_same_family_para_wins_over_citation():
    targets = {10: {"para:22": [{"anchor_id": "p22real", "collapsed_key": "para:22"}]}}
    mentions = detect_mentions_for_page('AIR 2015 SC620 held in para No.22 that: "..."', 49, targets)
    assert _resolving_keys(mentions) == ["para:22"]
    assert mentions[0]["resolution"] == "exact"


def test_html_table_mention_scanning():
    targets = {61: {"annexure:a-1": [{"anchor_id": "a1", "collapsed_key": "annexure:a1"}]}}
    html = "<table><tr><td>9.</td><td>Annexure A-1 copy of supply of contract agreement</td></tr></table>"
    mentions = detect_mentions_for_page(html, 1, targets)
    assert _resolving_keys(mentions) == ["annexure:a-1"]


def test_html_table_ampersand_decoding():
    targets = {
        8: {
            "point:1": [{"anchor_id": "p1", "collapsed_key": "point:1"}],
            "point:2": [{"anchor_id": "p2", "collapsed_key": "point:2"}],
        }
    }
    html = "<table><tr><td>Verified that the contents of para 1 &amp; 2 of above affidavit are true.</td></tr></table>"
    mentions = detect_mentions_for_page(html, 8, targets)
    assert _resolving_keys(mentions) == ["para:1", "para:2"]


def test_html_table_nbsp_decoding():
    targets = {61: {"annexure:a-1": [{"anchor_id": "a1", "collapsed_key": "annexure:a1"}]}}
    html = "<table><tr><td>Annexure&nbsp;A-1</td></tr></table>"
    mentions = detect_mentions_for_page(html, 1, targets)
    assert _resolving_keys(mentions) == ["annexure:a-1"]


def test_html_table_case_citation_guard():
    targets = {51: {"point:22": [{"anchor_id": "p22", "collapsed_key": "point:22"}]}}
    html = '<td>AIR 2015 SC 620 held in para 22 that: "..."</td>'
    mentions = detect_mentions_for_page(html, 49, targets)
    assert "para:22" not in _resolving_keys(mentions)


def test_no_space_hyphen_exhibit_prefix():
    targets = {76: {"annexure:b-1": [{"anchor_id": "b1", "collapsed_key": "annexure:b1"}]}}
    mentions = detect_mentions_for_page("ANNEXURE-B1", 76, targets)
    assert _resolving_keys(mentions) == ["annexure:b-1"]


def test_no_space_hyphen_plain_number():
    targets = {72: {"annexure:1": [{"anchor_id": "a1", "collapsed_key": "annexure:1"}]}}
    mentions = detect_mentions_for_page("Annexure-1", 72, targets)
    assert _resolving_keys(mentions) == ["annexure:1"]


def test_page_footer_suppressed():
    targets = {5: {"page:3": [{"anchor_id": "pg3", "collapsed_key": "page:3"}]}}
    text = "32008-PRICE BRANCH SEC-64.xls/ECN\n\nPage 3 of 21"
    mentions = detect_mentions_for_page(text, 98, targets)
    assert "page:3" not in _resolving_keys(mentions)
    pg3 = [m for m in mentions if m["key"] == "page:3"]
    assert len(pg3) == 1
    assert pg3[0]["resolution"] == "suppressed"
    assert pg3[0]["suppressed_reason"] == "page_footer"


def test_page_citation_no_footer_resolved():
    targets = {5: {"page:3": [{"anchor_id": "pg3", "collapsed_key": "page:3"}]}}
    mentions = detect_mentions_for_page("see page 3 above for the full text.", 10, targets)
    assert _resolving_keys(mentions) == ["page:3"]
    assert mentions[0]["resolution"] == "exact"
