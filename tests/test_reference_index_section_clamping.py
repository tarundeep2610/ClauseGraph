import pytest
import reference_index


def test_major_judicial_sections_matching():
    # Valid major section headings
    valid = [
        "FACTS",
        "FACTS:",
        "FACTS - ",
        "PETITIONER'S SUBMISSIONS",
        "PETITIONERS SUBMISSIONS",
        "PETITIONER'S CASE",
        "PETITIONER'S VERSION",
        "PETITIONER SUBMISSIONS",
        "RESPONDENT'S SUBMISSIONS",
        "RESPONDENTS SUBMISSIONS",
        "RESPONDENT'S CASE",
        "CHALLENGE TO IMPUGNED ORDER",
        "CHALLENGE TO ARBITRAL AWARD:",
        "ISSUES",
        "ISSUE:",
        "FINDINGS",
        "ANALYSIS",
        "SUBMISSIONS OF THE APPELLANT",
        "SUBMISSIONS OF COUNSEL",
        "CONCLUSION",
        "CONCLUSION:",
        "DISPOSAL",
        "ORDER",
        "ORDER:",
    ]
    for text in valid:
        assert reference_index._MAJOR_JUDICIAL_SECTIONS.match(text) is not None, f"Expected match for {text!r}"


def test_major_judicial_sections_negative_matching():
    # Sub-issues, statutes, or sentences should NOT match
    invalid = [
        "Issue No. 1: Jurisdiction",
        "Issue 2 - Limitation",
        "Order 39 Rule 1",
        "Order dated 12.03.2020",
        "Analysis of structural dynamics",
        "Facts of the case as stated by the witness",
        "Conclusion reached by the arbitrator was flawed",
    ]
    for text in invalid:
        assert reference_index._MAJOR_JUDICIAL_SECTIONS.match(text) is None, f"Expected NO match for {text!r}"


def test_apply_heading_events_clamping_to_depth_one():
    # Setup a nested stack of depth 3 under a root document
    scope = {
        "stack": [
            {"family": "subdoc", "key": "subdoc:1", "label": "APPEAL NO. 100 OF 2020"},
            {"family": "section", "key": "section:1", "label": "PETITIONER'S SUBMISSIONS"},
            {"family": "section", "key": "section:2", "label": "GROUND A"},
        ],
        "label_occurrence_counts": {},
    }

    # Event for RESPONDENT'S SUBMISSIONS with a falsely deep depth (e.g. depth 2)
    events = [
        {
            "heading_text": "RESPONDENT'S SUBMISSIONS",
            "depth": 2,
            "block_index": 5,
        }
    ]

    candidates, new_scope = reference_index.apply_heading_events_to_scope(
        events=events,
        seed_scope=scope,
        page_num=10,
        has_next_page=True,
    )

    # After applying, depth should have clamped to 1, popping "PETITIONER'S SUBMISSIONS" and "GROUND A"
    # The active stack should have root subdoc at index 0 and RESPONDENT'S SUBMISSIONS at index 1
    stack = new_scope["stack"]
    assert len(stack) == 2, f"Expected stack depth 2, got {len(stack)}: {stack}"
    assert stack[0]["label"] == "APPEAL NO. 100 OF 2020"
    assert "RESPONDENT" in stack[1]["label"]
