"""Regression tests for the `ground` target detector in reference_index.py —
specifically GROUND_DIGIT_RE/GROUND_ROMAN_RE's support for parenthesized
enumeration ("(I) That...") alongside the original period-terminated style
("I. That...")."""

from reference_index import build_targets_for_page


def _keys(page_text):
    candidates, _ = build_targets_for_page(page_text, seed_scope=None, has_next_page=False)
    return [c["key"] for c in candidates]


def test_period_style_grounds_still_detected():
    page_text = (
        "GROUNDS OF APPEAL\n\n"
        "I. That the findings recorded below are perverse.\n\n"
        "II. That the learned court erred in fact.\n"
    )
    assert _keys(page_text) == ["ground:i", "ground:ii"]


def test_parenthesized_roman_grounds_detected():
    page_text = (
        "GROUNDS OF APPEAL\n\n"
        "(I) That the findings recorded below are perverse.\n\n"
        "(II) That the learned court erred in fact.\n"
    )
    assert _keys(page_text) == ["ground:i", "ground:ii"]


def test_parenthesized_digit_grounds_detected():
    page_text = (
        "GROUNDS OF APPEAL\n\n"
        "(1) That the findings recorded below are perverse.\n\n"
        "(2) That the learned court erred in fact.\n"
    )
    assert _keys(page_text) == ["ground:1", "ground:2"]


def test_bare_pronoun_i_not_treated_as_ground():
    page_text = (
        "GROUNDS OF APPEAL\n\n"
        "(I) That the findings recorded below are perverse.\n\n"
        "I have already stated that this finding is unfair.\n"
    )
    assert _keys(page_text) == ["ground:i"]


def test_parenthesized_roman_ignored_outside_ground_scope():
    page_text = "(I) That the findings recorded below are perverse.\n"
    assert _keys(page_text) == []


def test_bare_date_in_prose_is_never_misdetected_as_a_ground():
    """Confirmed real bug (job bb417471): a bare date at the start of a
    page-continuation paragraph ("22.10.2008. Moreover, respondent...")
    matches GROUND_DIGIT_RE's \\d+(?:\\.\\d+)* shape exactly the same way a
    real numbered ground does — must be rejected, not detected as
    ground:22.10.2008."""
    page_text = (
        "GROUNDS OF APPEAL\n\n"
        "1. First ground.\n\n"
        "22.10.2008. Moreover, respondent did not place any document on record.\n"
    )
    assert _keys(page_text) == ["ground:1"]


def test_genuine_multi_segment_ground_number_is_unaffected():
    """Regression guard: the date-shape rejection must only reject a
    4-digit segment specifically — an ordinary multi-level decimal number
    (all short segments) must still be detected normally."""
    page_text = "GROUNDS OF APPEAL\n\n3.1.2. That the findings are perverse.\n"
    assert _keys(page_text) == ["ground:3.1.2"]
