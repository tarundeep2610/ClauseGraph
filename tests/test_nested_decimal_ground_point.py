"""Regression tests for a confirmed live bug: a decimal ground/point nested
under ANOTHER decimal ground/point (e.g. "19. ... summarized under following
heads: 1. ... 2. ... 5. Award of amounts... detailed as under:-") lost its
parent when ITS OWN roman-numeral continuation ("(i)"-"(vii)") computed its
ancestor chain — producing a duplicate/phantom node once the frontend
materializes every scope_chain segment as a tree node (point:5 appeared both
correctly under point:19, and as a phantom sibling of point:19 holding the
roman sub-points).

Two bugs, found together on the same real document (job
108ba949-bb7b-4f29-b582-ce4f4a44631b, page 13):

1. AI path (apply_heading_events_to_scope): `last_decimal_ground["stack"]`
   stored `effective_stack[:-1]`, dropping the heading's OWN immediate
   parent — `effective_stack` is always an ancestor-only chain (never
   includes the heading's own key), so slicing it off silently removed a
   real ancestor instead of a non-existent "self" entry.
2. Rule path (build_targets_for_page): never had the nested-decimal-under-
   decimal / integer-sequence-resumption machinery at all (no
   outer_decimal_ground tracking), so the two detectors disagreed on this
   exact shape — rule output is both the per-chunk AI fallback and the
   prior fed into the rule-prior review pass, so this divergence mattered
   beyond just the rule-only path.
"""

import reference_index as ri
from reference_index import build_targets_for_page


def _event(text, depth=None, end_page=None, **kw):
    ev = {"heading_text": text, "depth": depth, "end_page": end_page}
    ev.update(kw)
    return ev


def _by_key(candidates):
    return {c["key"]: c for c in candidates}


# ─── AI path (apply_heading_events_to_scope) ───────────────────────────────

def _conclusion_seed():
    return {
        "stack": [{"family": "subdoc", "key": "subdoc:9", "label": "Conclusion"}],
        "boundary_ordinal": 9, "label_occurrence_counts": {},
        "last_bare_integer": None, "last_letter_ordinal": None,
    }


def test_ai_path_nested_decimal_point_keeps_its_parent_for_roman_children():
    events = [
        _event("18. From the facts and circumstances...", depth=1),
        _event("19. The dispute... summarized under following heads:", depth=1),
        _event("1. Delay in issuing the GELO.", depth=2),
        _event("2. Delay in front clearance of site.", depth=2),
        _event("5. Award of amounts under various heads detailed as under:-", depth=2),
        _event("(i) Award of Rs.43,46,080 on account of PLCC items.", depth=3),
        _event("(vii) Award of interest at the rate of 8.5% per annum.", depth=3),
    ]
    candidates, _ = ri.apply_heading_events_to_scope(events, _conclusion_seed(), page_num=13, has_next_page=True)
    by_key = _by_key(candidates)

    # point:5 must nest under point:19, not flatten to a sibling of it.
    assert by_key["point:5"]["scope_key"] == "subdoc:9/point:19"
    # The roman sub-points of point:5 must carry point:19 AND point:5 in
    # their chain — this is exactly the ancestor the old `[:-1]` slice
    # dropped, producing the phantom duplicate "point:5" node.
    assert by_key["point:i"]["scope_key"] == "subdoc:9/point:19/point:5"
    assert by_key["point:vii"]["scope_key"] == "subdoc:9/point:19/point:5"


def test_ai_path_integer_resumption_still_works_after_the_fix():
    events = [
        _event("19. The dispute... summarized under following heads:", depth=1),
        _event("1. Delay in issuing the GELO.", depth=2),
        _event("5. Award of amounts under various heads detailed as under:-", depth=2),
        _event("(i) Award of Rs.43,46,080 on account of PLCC items.", depth=3),
        _event("20. From perusal of the challenge made in this petition...", depth=1),
    ]
    candidates, _ = ri.apply_heading_events_to_scope(events, _conclusion_seed(), page_num=13, has_next_page=True)
    by_key = _by_key(candidates)
    # 20 resumes as 19's sibling (both direct children of Conclusion), not
    # nested inside the "5." sub-list it followed.
    assert by_key["point:20"]["scope_key"] == "subdoc:9"


# ─── Rule path (build_targets_for_page) — parity backport ─────────────────
#
# This is the REAL page 13 text of job 108ba949-bb7b-4f29-b582-ce4f4a44631b
# (the document this bug was found on), pulled directly from the database —
# not synthesized. A hand-written substitute was tried first and gave a
# false negative: mistune merges consecutive "N. text" paragraphs into one
# continuous ordered list and RENUMBERS every item after the first from its
# own start value, so synthetic "18./19./1./2./5." collapsed into a single
# auto-renumbered "18./19./20./21./22." list — a markdown-parsing quirk
# unrelated to the bug under test. The real OCR text doesn't hit this: "1."
# genuinely starts a fresh list (point:19, its introducer, is on the
# PRECEDING page), and 1-5 / 20-22 are already contiguous runs.
#
# Paragraph 19 ("...summarized under following heads:") is on page 12, not
# 13 — the seed scope below is exactly what build_targets_for_page would
# have carried forward from page 12 into page 13 in the real pipeline.

_PAGE_13_TEXT = (
    "Haryana Vidyut Prasaran Nigam Ltd. vs. M/s GE T&D India Ltd.\n\n"
    "49\n\n"
    "-:12:-\n\n"
    "1. Delay in issuing the GELO.\n\n"
    "2. Delay in front clearance of site.\n\n"
    "3. Delay in approval of equipment drawings.\n\n"
    "4. Delay in availability of power at site.\n\n"
    "5. Award of amounts under various heads detailed as under:-\n\n"
    "(i) Award of Rs.43,46,080 on account of PLCC items.\n\n"
    "(ii) Award of Rs.26,58,714/- on account of non-supply of complete AC kiosks.\n\n"
    "(iii) Award of Rs.1,07,18,800/- on account of non-commissioning of BCUs.\n\n"
    "(iv) Award of Rs.76,00,000/- on account of miscellaneous recovery.\n\n"
    "(v) Award of Rs.64,73,754/- for additional work.\n\n"
    "(vi) Award of Rs.1,48,09,280/- on account of performance bank guarantee.\n\n"
    "(vii) Award of interest at the rate of 8.5% per annum.\n\n"
    "20. From perusal of the challenge made in this petition under section 34 "
    "of Arbitration Act, it can be fairly concluded that all these aspects "
    "have been adjudicated in detail.\n\n"
    "21. For example on delay in issuing the GELO, the petitioner has claimed "
    "that respondent no.1 has not started the work in time.\n\n"
    "22. Whereas the Arbitral Tribunal after hearing the rival submissions "
    "has observed that the GELO is most important and fundamental.\n"
)


def _page_12_seed():
    """What page 12 hands off to page 13 in the real pipeline: Conclusion
    (subdoc:9) open, point:19 ("...summarized under following heads:") is
    the most recent decimal ground/point, no nested sub-list opened yet."""
    return {
        "stack": [{"family": "subdoc", "key": "subdoc:9", "label": "Conclusion"}],
        "boundary_ordinal": 9, "label_occurrence_counts": {},
        "last_bare_integer": None, "last_letter_ordinal": None,
        "last_ground_letter_ordinal": None,
        "last_decimal_ground": {
            "key": "point:19", "label": "Point 19",
            "stack": [{"family": "subdoc", "key": "subdoc:9", "label": "Conclusion"}],
            "int_val": 19,
        },
        "outer_decimal_ground": None,
    }


def test_rule_path_nested_decimal_point_keeps_its_parent_for_roman_children():
    candidates, _ = build_targets_for_page(_PAGE_13_TEXT, seed_scope=_page_12_seed(), has_next_page=False)
    by_key = _by_key(candidates)
    assert by_key["point:5"]["scope_key"] == "subdoc:9/point:19"
    assert by_key["point:i"]["scope_key"] == "subdoc:9/point:19/point:5"
    assert by_key["point:vii"]["scope_key"] == "subdoc:9/point:19/point:5"


def test_rule_path_integer_resumption():
    candidates, _ = build_targets_for_page(_PAGE_13_TEXT, seed_scope=_page_12_seed(), has_next_page=False)
    by_key = _by_key(candidates)
    # 20-22 resume as point:19's siblings under Conclusion, not nested
    # inside the "5." sub-list they followed.
    assert by_key["point:20"]["scope_key"] == "subdoc:9"
    assert by_key["point:22"]["scope_key"] == "subdoc:9"


def test_rule_and_ai_paths_agree_on_page_13_shape():
    """The two detectors must not disagree about nesting on this real
    document's shape — rule output is both the per-chunk AI fallback and
    the prior fed into the rule-prior review pass, so a divergence here
    silently produces different structure depending on which path
    happened to run."""
    rule_candidates, _ = build_targets_for_page(_PAGE_13_TEXT, seed_scope=_page_12_seed(), has_next_page=False)
    rule_by_key = _by_key(rule_candidates)

    events = [
        _event("1. Delay in issuing the GELO.", depth=2),
        _event("2. Delay in front clearance of site.", depth=2),
        _event("5. Award of amounts under various heads detailed as under:-", depth=2),
        _event("(i) Award of Rs.43,46,080 on account of PLCC items.", depth=3),
        _event("(vii) Award of interest at the rate of 8.5% per annum.", depth=3),
        _event("20. From perusal of the challenge made in this petition.", depth=1),
    ]
    ai_seed = _conclusion_seed()
    ai_seed["last_decimal_ground"] = dict(_page_12_seed()["last_decimal_ground"])
    ai_candidates, _ = ri.apply_heading_events_to_scope(events, ai_seed, page_num=13, has_next_page=True)
    ai_by_key = _by_key(ai_candidates)

    for key in ("point:5", "point:i", "point:vii", "point:20"):
        rule_suffix = rule_by_key[key]["scope_key"].split("/", 1)[-1]
        ai_suffix = ai_by_key[key]["scope_key"].split("/", 1)[-1]
        assert rule_suffix == ai_suffix, f"{key}: rule={rule_by_key[key]['scope_key']!r} ai={ai_by_key[key]['scope_key']!r}"
