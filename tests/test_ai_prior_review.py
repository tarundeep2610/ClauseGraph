import pytest
from typing import Dict, Any, List

import config
import ai_indexing
from ai_indexing.prompt import (
    PageInput,
    ChunkPlan,
    build_structure_prompt,
    _render_rule_prior,
    _format_prior_line,
)
from ai_indexing.schema import (
    AIHeading,
    AIPriorRejection,
    AIStructureChunk,
    VALID_REJECTION_REASONS,
)
from ai_reference_index import (
    _merge_prior_into_headings,
    _check_rule_prior_key_drift,
    _norm_for_match,
)


def test_flag_off_prompt_byte_identity():
    pages = [PageInput(page_num=1, text="Page 1 text content"), PageInput(page_num=2, text="Page 2 text content")]
    chunk = ChunkPlan(pages=pages, total_chars=40)

    # Calling without any rule prior params
    sys_default, user_default = build_structure_prompt(chunk)

    # Calling with review_rule_prior=False explicitly
    sys_off, user_off = build_structure_prompt(chunk, review_rule_prior=False, rule_prior=[])

    assert sys_default == sys_off
    assert user_default == user_off


def test_flag_on_prompt_structure():
    pages = [PageInput(page_num=1, text="Page 1 content")]
    chunk = ChunkPlan(pages=pages, total_chars=14)
    priors = [{
        "prior_id": "R1",
        "page_num": 1,
        "depth": 1,
        "key": "section:1",
        "parent_label": None,
        "heading_text": "1. DEFINITIONS",
        "is_container": True,
        "rank": 1,
    }]

    sys_on, user_on = build_structure_prompt(chunk, review_rule_prior=True, rule_prior=priors)

    assert "RULE-DETECTED PRIOR DRAFT (REVIEW INSTRUCTIONS)" in sys_on
    assert "# RULE-DETECTED PRIOR DRAFT" in user_on
    assert 'R1 | p1 | depth 1 | key=section:1 | parent=None | "1. DEFINITIONS"' in user_on
    assert '"confirmed_prior_ids"' in user_on
    assert '"rejected_priors"' in user_on


def test_format_prior_line():
    row1 = {
        "prior_id": "R7",
        "page_num": 23,
        "depth": 2,
        "key": "clause:3.4",
        "parent_label": "3.0 CONDITIONS AND COVENANTS",
        "heading_text": "3.4 QUALITY PLANS",
    }
    line1 = _format_prior_line(row1)
    assert line1 == 'R7 | p23 | depth 2 | key=clause:3.4 | parent="3.0 CONDITIONS AND COVENANTS" | "3.4 QUALITY PLANS"'

    row2 = {
        "prior_id": "R1",
        "page_num": 5,
        "depth": 1,
        "key": "subdoc:1",
        "parent_label": None,
        "heading_text": "AGREEMENT",
    }
    line2 = _format_prior_line(row2)
    assert line2 == 'R1 | p5 | depth 1 | key=subdoc:1 | parent=None | "AGREEMENT"'


def test_prior_truncation_drops_rank_none_leaves_first():
    # Construct priors with:
    # - container (never dropped)
    # - leaf with rank (dropped second)
    # - leaf with rank=None (dropped first)
    priors = [
        {"prior_id": "R1", "page_num": 1, "depth": 1, "key": "section:1", "parent_label": None, "heading_text": "Section 1", "is_container": True, "rank": 1},
        {"prior_id": "R2", "page_num": 2, "depth": 2, "key": "clause:1.1", "parent_label": "Section 1", "heading_text": "Clause 1.1", "is_container": False, "rank": 2},
        {"prior_id": "R3", "page_num": 3, "depth": 3, "key": "point:1", "parent_label": "Clause 1.1", "heading_text": "Point 1", "is_container": False, "rank": None},
        {"prior_id": "R4", "page_num": 4, "depth": 3, "key": "point:2", "parent_label": "Clause 1.1", "heading_text": "Point 2", "is_container": False, "rank": None},
    ]

    # Max 3 rows: R4 (rank=None, highest page 4) should be dropped first
    rendered = _render_rule_prior(priors, max_rows=3)
    assert "R1" in rendered
    assert "R2" in rendered
    assert "R3" in rendered
    assert "R4" not in rendered
    assert "[... 1 lower-priority prior rows omitted" in rendered

    # Max 2 rows: R4 and R3 (both rank=None) dropped
    rendered2 = _render_rule_prior(priors, max_rows=2)
    assert "R1" in rendered2
    assert "R2" in rendered2
    assert "R3" not in rendered2
    assert "R4" not in rendered2
    assert "[... 2 lower-priority prior rows omitted" in rendered2

    # Max 1 row: R4, R3 (rank=None), then R2 (rank=2 leaf) dropped. R1 (container) survives!
    rendered3 = _render_rule_prior(priors, max_rows=1)
    assert "R1" in rendered3
    assert "R2" not in rendered3
    assert "[... 3 lower-priority prior rows omitted" in rendered3


def test_prior_truncation_never_drops_container():
    priors = [
        {"prior_id": "R1", "page_num": 1, "depth": 1, "key": "subdoc:1", "parent_label": None, "heading_text": "Subdoc 1", "is_container": True, "rank": 0},
        {"prior_id": "R2", "page_num": 2, "depth": 1, "key": "subdoc:2", "parent_label": None, "heading_text": "Subdoc 2", "is_container": True, "rank": 0},
    ]
    # Even if max_rows=1, neither container should be dropped
    rendered = _render_rule_prior(priors, max_rows=1)
    assert "R1" in rendered
    assert "R2" in rendered
    assert "omitted" not in rendered


def test_verified_rejection():
    priors = [
        {"prior_id": "R1", "page_num": 1, "depth": 1, "key": "section:34", "parent_label": None, "heading_text": "Section 34", "is_container": False, "rank": 1},
    ]
    page_texts = {1: "In accordance with Section 34 of the Arbitration and Conciliation Act, 1996, the petition is filed."}
    page_blocks = {1: ["In accordance with Section 34 of the Arbitration and Conciliation Act, 1996, the petition is filed."]}

    result = ai_indexing.ChunkStructureResult(
        outcome=ai_indexing.ChunkOutcome.OK,
        page_nums=[1],
        headings=[],
        confirmed_prior_ids=[],
        rejected_priors=[
            AIPriorRejection(
                prior_id="R1",
                reason_code="external_citation",
                evidence_quote="Arbitration and Conciliation Act, 1996",
            )
        ],
    )
    rejected_so_far = set()
    warning_counts = {}

    merged, verified_rejections = _merge_prior_into_headings(
        result, priors, page_texts, page_blocks, rejected_so_far, warning_counts,
    )

    assert "R1" in verified_rejections
    assert (1, _norm_for_match("Section 34")) in rejected_so_far
    # Since it was verified rejected, it must NOT appear in merged headings
    assert len(merged) == 0


def test_unverified_rejection_too_short_quote():
    priors = [
        {"prior_id": "R1", "page_num": 1, "depth": 1, "key": "clause:1", "parent_label": None, "heading_text": "1. SCOPE", "is_container": False, "rank": 1},
    ]
    page_texts = {1: "1. SCOPE OF WORK\nThe contractor shall execute..."}
    page_blocks = {1: ["1. SCOPE OF WORK", "The contractor shall execute..."]}

    result = ai_indexing.ChunkStructureResult(
        outcome=ai_indexing.ChunkOutcome.OK,
        page_nums=[1],
        headings=[],
        confirmed_prior_ids=[],
        rejected_priors=[
            AIPriorRejection(
                prior_id="R1",
                reason_code="body_sentence",
                evidence_quote="short",  # < 8 chars!
            )
        ],
    )
    rejected_so_far = set()
    warning_counts = {}

    merged, verified_rejections = _merge_prior_into_headings(
        result, priors, page_texts, page_blocks, rejected_so_far, warning_counts,
    )

    assert "R1" not in verified_rejections
    assert warning_counts.get("rule_prior_rejection_unverified") == 1
    # Survives with low confidence and unverified reason
    assert len(merged) == 1
    assert merged[0].heading_text == "1. SCOPE"
    assert merged[0].heading_confidence == "low"
    assert "rule_prior_rejection_unverified" in merged[0].heading_confidence_reasons


def test_unverified_rejection_quote_not_on_page():
    priors = [
        {"prior_id": "R1", "page_num": 1, "depth": 1, "key": "clause:1", "parent_label": None, "heading_text": "1. SCOPE", "is_container": False, "rank": 1},
    ]
    page_texts = {1: "1. SCOPE OF WORK\nThe contractor shall execute..."}
    page_blocks = {1: ["1. SCOPE OF WORK", "The contractor shall execute..."]}

    result = ai_indexing.ChunkStructureResult(
        outcome=ai_indexing.ChunkOutcome.OK,
        page_nums=[1],
        headings=[],
        confirmed_prior_ids=[],
        rejected_priors=[
            AIPriorRejection(
                prior_id="R1",
                reason_code="body_sentence",
                evidence_quote="This quote does not exist on the page at all",
            )
        ],
    )
    rejected_so_far = set()
    warning_counts = {}

    merged, verified_rejections = _merge_prior_into_headings(
        result, priors, page_texts, page_blocks, rejected_so_far, warning_counts,
    )

    assert "R1" not in verified_rejections
    assert warning_counts.get("rule_prior_rejection_unverified") == 1
    assert len(merged) == 1
    assert merged[0].heading_confidence == "low"
    assert "rule_prior_rejection_unverified" in merged[0].heading_confidence_reasons


def test_unreviewed_row_retention_silence_is_never_deletion():
    priors = [
        {"prior_id": "R1", "page_num": 1, "depth": 1, "key": "clause:1", "parent_label": None, "heading_text": "1. SCOPE", "is_container": False, "rank": 1},
        {"prior_id": "R2", "page_num": 1, "depth": 1, "key": "clause:2", "parent_label": None, "heading_text": "2. PAYMENT", "is_container": False, "rank": 1},
    ]
    page_texts = {1: "1. SCOPE OF WORK\n2. PAYMENT TERMS"}
    page_blocks = {1: ["1. SCOPE OF WORK", "2. PAYMENT TERMS"]}

    # Model only confirms R1 and says nothing about R2
    result = ai_indexing.ChunkStructureResult(
        outcome=ai_indexing.ChunkOutcome.OK,
        page_nums=[1],
        headings=[],
        confirmed_prior_ids=["R1"],
        rejected_priors=[],
    )
    rejected_so_far = set()
    warning_counts = {}

    merged, verified_rejections = _merge_prior_into_headings(
        result, priors, page_texts, page_blocks, rejected_so_far, warning_counts,
    )

    assert len(merged) == 2
    # R1 is confirmed -> high confidence
    h1 = [h for h in merged if h.prior_id == "R1"][0]
    assert h1.heading_confidence == "high"

    # R2 is unreviewed -> survives with low confidence and rule_prior_unreviewed
    h2 = [h for h in merged if h.prior_id == "R2"][0]
    assert h2.heading_confidence == "low"
    assert "rule_prior_unreviewed" in h2.heading_confidence_reasons
    assert warning_counts.get("rule_prior_unreviewed") == 1


def test_contradiction_precedence():
    priors = [
        {"prior_id": "R1", "page_num": 1, "depth": 1, "key": "clause:1", "parent_label": None, "heading_text": "1. SCOPE", "is_container": False, "rank": 1},
        {"prior_id": "R2", "page_num": 1, "depth": 1, "key": "clause:2", "parent_label": None, "heading_text": "2. PAYMENT", "is_container": False, "rank": 1},
        {"prior_id": "R3", "page_num": 1, "depth": 1, "key": "clause:3", "parent_label": None, "heading_text": "3. TERMINATION", "is_container": False, "rank": 1},
    ]
    page_texts = {1: "1. SCOPE\n2. PAYMENT of Arbitration Act, 1996\n3. TERMINATION CLAUSE"}
    page_blocks = {1: ["1. SCOPE", "2. PAYMENT of Arbitration Act, 1996", "3. TERMINATION CLAUSE"]}

    # R1: both correction (heading) and rejection -> correction wins!
    # R2: both confirmed and rejected (verified rejection) -> rejection wins!
    # R3: both confirmed and rejected (unverified rejection) -> confirmation wins!
    result = ai_indexing.ChunkStructureResult(
        outcome=ai_indexing.ChunkOutcome.OK,
        page_nums=[1],
        headings=[
            AIHeading(heading_text="1. SCOPE REVISED", start_page=1, prior_id="R1"),
        ],
        confirmed_prior_ids=["R2", "R3"],
        rejected_priors=[
            AIPriorRejection(prior_id="R1", reason_code="body_sentence", evidence_quote="1. SCOPE OF WORK"),
            AIPriorRejection(prior_id="R2", reason_code="external_citation", evidence_quote="Arbitration Act, 1996"),
            AIPriorRejection(prior_id="R3", reason_code="body_sentence", evidence_quote="nonexistent text"),
        ],
    )
    rejected_so_far = set()
    warning_counts = {}

    merged, verified_rejections = _merge_prior_into_headings(
        result, priors, page_texts, page_blocks, rejected_so_far, warning_counts,
    )

    # R1 correction was kept
    assert any(h.heading_text == "1. SCOPE REVISED" for h in merged)
    # R2 verified rejection won over confirmation
    assert "R2" in verified_rejections
    assert not any(h.prior_id == "R2" for h in merged)
    # R3 confirmation won over unverified rejection
    h3 = [h for h in merged if h.prior_id == "R3"][0]
    assert h3.heading_confidence == "high"


def test_unknown_prior_id():
    priors = [
        {"prior_id": "R1", "page_num": 1, "depth": 1, "key": "clause:1", "parent_label": None, "heading_text": "1. SCOPE", "is_container": False, "rank": 1},
    ]
    page_texts = {1: "1. SCOPE"}
    page_blocks = {1: ["1. SCOPE"]}

    result = ai_indexing.ChunkStructureResult(
        outcome=ai_indexing.ChunkOutcome.OK,
        page_nums=[1],
        headings=[],
        confirmed_prior_ids=["R99"],
        rejected_priors=[
            AIPriorRejection(prior_id="R88", reason_code="external_citation", evidence_quote="Arbitration Act, 1996"),
        ],
    )
    rejected_so_far = set()
    warning_counts = {}

    _merge_prior_into_headings(
        result, priors, page_texts, page_blocks, rejected_so_far, warning_counts,
    )

    assert warning_counts.get("rule_prior_unknown_id") == 2


def test_check_rule_prior_key_drift():
    priors = [
        {"prior_id": "R1", "key": "clause:1.1"},
        {"prior_id": "R2", "key": "section:2"},
    ]
    cands = [
        {"key": "clause:1.1", "heading_confidence_reasons": []},
        {"key": "article:2", "heading_confidence_reasons": []},  # drifted from section:2!
    ]
    events = [
        {"prior_id": "R1"},
        {"prior_id": "R2"},
    ]
    warning_counts = {}

    _check_rule_prior_key_drift(cands, events, priors, warning_counts)

    assert warning_counts.get("rule_prior_key_drift") == 1
    assert "rule_prior_key_drift" not in cands[0]["heading_confidence_reasons"]
    assert "rule_prior_key_drift" in cands[1]["heading_confidence_reasons"]
