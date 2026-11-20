"""Regression tests for real false-positive headings found on a real,
completed 955-page job (7f442300, the full FAO-CARB_23_2019_PAPER_BOOK.pdf)
whose Structure tree was checked directly against the actual stored OCR
text and database rows.

Two distinct root causes were confirmed by running `reference_index.py`'s
own parser directly against the real stored page text:

1. `DECIMAL_HEADING_RE` cannot tell a genuine standalone heading line from
   an ordinary markdown numbered-list item - page 91's 22-item addressee
   list ("1. Sr. PS to Chairman...", ..., "22. Supdt. (Purchase)/MM...")
   produced a spurious `numheading:20` from item 20 (the only one short
   enough to pass the label-length check; every other item only escaped by
   being too long, not because it was correctly recognized as non-heading
   text).
2. A bare/undotted single-segment number is trivially confused with an
   incidental number in running text: a running page header
   ("400" from "400 KV SUBSTATION KIRORI (HISAR)", page 94), a stamped date
   ("27" from "27 MAR 2019", page 38), a numbered judgment paragraph
   ("39" from "39. Consequently, the objection petition is hereby dismissed
   with costs", page 59, exactly 9 words), and a stray equipment-schedule
   line ("1" from "1 Ph Welding Transformer (Air Cooled)", page 169, which
   also incidentally popped the real, still-open "3.0 WELDING AND GAS
   CUTTING EQUIPMENTS..." container off the nesting stack) were all
   promoted to spurious headings.

By contrast, page 169's genuine dotted-decimal equipment-schedule headings
("3.1 Welding Generator...", "3.3 Welding Rectifier Set 40-400A", ...) are
all `paragraph` blocks (never `list_item`) and must keep matching correctly
- confirming the fix targets bare/undotted numbers and list-item blocks
specifically, never touching dotted-number matching.

The page texts below are this job's own real, unmodified `text` for each
page, pulled directly from the database - not synthetic fixtures."""

from reference_index import build_targets_for_page

_PAGE_91_ADDRESSEE_LIST = (
    "[NO TEXT]\n\n[Non-Text]\n\nEndst. No. Ch-1/HDP-1484 /REO-084/Xen(Projects) Dated: 20.05.2008.\n\n"
    "Copy of the above is forwarded to the following for information and necessary action please:\n\n"
    "1. Sr. PS to Chairman, HVPNL, Panchkula.\n\n"
    "2. Sr. PS to Director/Projects, HVPNL, Panchkula.\n\n"
    "3. Sr. PS to Directo/Technical, HVPNL, Panchkula.\n\n"
    "4. Chief Engineer/TG, HVPNL, Hisar.\n\n"
    "5. Chief Engineer/IMM, HVPNL, Panchkula.\n\n"
    "6. Chief Engineer/Flanning & Design, HVPNL, Panchkula.\n\n"
    "7. Chief Auditor, HVPNL, Panchkula.\n\n"
    "8. The Superintending Engineer/ TS, HVPNL, Hisar.\n\n"
    "9. The Superintending Engineer/ M&P cum Carrier, HVPNL, Hisar.\n\n"
    "10. The Superintending Engineer/MM-II, HVPNL, Panchkula.\n\n"
    "11. The Superintending Engineer/Design, HVPNL, Panchkula.\n\n"
    "12. The Superintending Engineer/ Civil Design, HVPNL, Panchkula.\n\n"
    "13. Dy. Secy. Projects, HVPNL, Panchkula.\n\n"
    "14. FA & CAO/MM, HVPNL, Panchkula.\n\n"
    "15. FA & CAO/MM, CPC CELL, HVPNL, Panchkula.\n\n"
    "16. Executive Engineer/Grid Design, HVPNL, Panchkula.\n\n"
    "17. Executive Engineer/Prot., HVPNL, Panchkula.\n\n"
    "18. Executive Engineer/ Civil Design, HVPNL, Panchkula.\n\n"
    "19. Executive Engineer/Quality Control, HVPNL, Panchkula.\n\n"
    "20. Executive Engineer/TS, iHVPNL, Hisar\n\n"
    "21. Executive Engineer/Civil Works Divn., HVPNL, Hisar.\n\n"
    "22. Supdt. (Purchase)/MM, HVPNL, Panchkula.\n\n"
    "This issues with the approval of the Special High Powered Purchase Committee in its meeting held on "
    "28.03.2008 and proceedings of the meeting is placed at Ch-86 of File No. REC-064 and after "
    "pre-audited by pre-audit cell of FAACAO/MM, HVPNL Panchkula.\n\n"
    "![](images/0.jpg)\n\nExecutive Engineer/Project for C.E./MM, HVPNL, Panchkula\n\n![](images/1.jpg)\n\n29"
)

_PAGE_169_EQUIPMENT_SCHEDULE = (
    "151\n\nA\n\nARENA\n\nTransmission & Distribution\n\nSL.NO. DESCRIPTION RANGE CAPACITY/NOS.\n\n"
    "2.6 Cable Jack Sets 5.00 MT 10.00 MT\n\n"
    "3.0 - WEI.DING AND GAS CUTTING EQUIPMENTS AND ACCESSORIES\n\n"
    "3.1 Welding Generator (A4vani/ 300/320A Kirkosar)\n\n"
    "3.2 3 Ph Welding Transformer(Oil/30-140A An Coold)\n\n"
    "1 Ph Welding Transformer (Air Cooled)\n\n"
    "3.3 Welding Rectifier Set 40-400A\n\n"
    "3.4 Argon TIG Welding Set (Gas 20PA cooled)\n\n"
    "3.5 Welding Regulators\n\n"
    "3.6 Oxygen Regulators 14 Kg./Cm.Sq.\n\n"
    "3.7 Acetylene Regulators 5 Kg./Cm.Sq.\n\n"
    "3.8 Argon Regulators 3.4 Kg./Cm.Sq.\n\n"
    "3.9 Electrode Store & Drying Ovens 25 Kgs.\n\n"
    "3.10 Portable Electrode Drying Ovens 4 Kgs.\n\n"
    "SL.NO. DESCRIPTION RANGE\n\nCAPACITY/\n\nNGS\n\nPage 2 of 2\n\n[9]\n\n![](images/0.jpg)"
)

_PAGE_38_AFFIDAVIT_WITH_STAMPED_DATE = (
    "36\n\nIn the High Court of Punjab and Haryana at Chandigarh\n\nIn F.A.O. (Comm.Arb.) 23 2019\n\n"
    "Haryana Vidyut Prasaran Nigam Ltd. ... Appellant\n\nVersus\n\n"
    "M/s GE T&D India Ltd. and others .... Respondents\n\n"
    "Affidavit of Ishwar Chand Balan aged 46 years S/o Shri Amar Singh presently working as XEN, HVPNL, Panchkula.\n\n"
    "I, the above named deponent do hereby solemnly affirm and state as under:-\n\n"
    "1. That the deponent is employed in the appellant corporation and as such competent to swear this affidavit.\n\n"
    "2. That the accompanying appeal has drafted under the instruction of deponent and the deponent has carefully gone through the same.\n\n"
    "3. That the deponent has not earlier filed such or similar appeal in this Hon'ble Court or in the Hon'ble Supreme Court of India.\n\n"
    "Panchkula\n\nDated: 27.03.2019\n\nB\n\nDeponent\n\nVerification:\n\nEXECUTIVE ENGINEER/T\n\n"
    "Verified that the contents of para 1 to 3 of above affidavit\n\n"
    "are true and correct to my knowledge. No part of it is false and\n\n"
    "nothing has been concealed therein.\n\n"
    "27 MAR 2019\n\nPanchkula\n\nDated: 27.03.2019\n\n![](images/0.jpg)\n\nBade\n\nDeponent\n\n"
    "EXECUTIVE ENG1\n\nH.V.P.N. PAN\n\nTTP\n\nA"
)

_PAGE_59_JUDGMENT_NUMBERED_PARAGRAPHS = (
    "57\n\nHaryana Vidyut Prasaran Nigam Ltd. vs. M/s GE T&D India Ltd.\n\n:-20:-\n\n"
    "of the contract is primarily for the arbitrator to decide unless it is found that such a construction "
    "is not a possible one\".\n\n"
    "38. It can be thus fairly concluded from the above discussion that challenge to the award as raised by "
    "virtue to this petition does not hold good in the facts of the case in hand and there is nothing before "
    "this court to seek indulgence under section 34 of the Arbitration Act.\n\n"
    "39. Consequently, the objection petition is hereby dismissed with costs\n\n"
    "Memo of costs be drawn accordingly. Consign the file to records and the arbitration record be kept till "
    "the expiry of limitation period of appeal/revision and thereafter, be sent back to the concerned arbitrator.\n\n"
    "![](images/0.jpg)\n\nAnnounced in open Court\n\nD£ 05.02.2019.\n\n(New Area)\n\n* - region: 1\n\n"
    "(Alka Malik)\n\nAdditional District Judge-cum-\n\nPresiding Judge, Special Commercial\n\n"
    "Court at Gurugram. (UID No. HR0089).\n\nATTESTED\n\n![](images/1.jpg)"
)

_PAGE_94_RUNNING_HEADER_400KV = (
    "72\n\nHARYANA VIDYUT PRASARAN NIGAM LIMITED\n\n400 KV SUBSTATION KIRORI (HISAR)\n\n"
    "<table><tr><td>ENQUIRY No.</td><td>REC-064; DATED : 26-SEP-2007 (NIT NO. : 456)</td></tr>"
    "<tr><td>AREVA REF No.</td><td>IE070151; DTD. : 29-AFR-2098</td></tr>"
    "<tr><td>DOC TITLE</td><td>SUMMARY PRICE PROPOSAL</td></tr></table>\n\n"
    "![](images/0.jpg)\n\nSpecification No.: REC-064\n\nSCHEDULE-1A\n\n400 KV SUBSTATION\n\n"
    "(Summary Price Proposal)\n\nAreva T & D India Ltd.\n\nA-17, Sector-16\n\nNoids-201301 (U.P.)\n\n"
    "Deal : 75,\n\nTo\n\nChief Engineer/ MMR\n\nHVPN, Panchkula\n\nHaryana-134109\n\n"
    "'decadre that in terms of clause 12.0, Section II, Volume-1 of Bid Documents, the following are total "
    "prices (including type test) in Rupees for the entire scope of work (including type test) as specified "
    "in Schedules 3 to 6."
)


def test_addressee_list_items_never_become_headings():
    """Page 91's 22-item addressee list - all mistune list_items - must
    never produce a numheading node, including item 20 (the real
    regression: short enough to have previously passed the label-length
    check on its own)."""
    candidates, _ = build_targets_for_page(_PAGE_91_ADDRESSEE_LIST, seed_scope=None, has_next_page=False)
    numheadings = [c["key"] for c in candidates if c["key"].startswith("numheading:")]
    assert numheadings == []


def test_addressee_list_items_never_become_subdoc_candidates_either():
    """A second, distinct false-positive on this exact page, caught by a
    different mechanism: _looks_like_generic_heading_shape's residual
    fallback can't tell "Chief Engineer/MM, HVPNL, Panchkula." apart from a
    real open-vocabulary heading on shape alone - confirmed live (19 of 22
    addressee lines minted as spurious subdoc: candidates on the 100-page
    fixture). The "Copy ... forwarded to the following"/"Copy to:" intro
    line gates this off for the rest of the page. Real subdoc detection
    elsewhere (e.g. a genuine "BANK GUARANTEE FOR CONTRACT PERFORMANCE"
    heading) is untouched - this only suppresses pages that actually open
    with a distribution-list intro."""
    candidates, _ = build_targets_for_page(_PAGE_91_ADDRESSEE_LIST, seed_scope=None, has_next_page=False)
    addressee_subdocs = [c["key"] for c in candidates if c["key"].startswith("subdoc:")]
    assert addressee_subdocs == []


def test_bracketed_ocr_placeholder_tags_never_become_subdoc_candidates():
    """[NO TEXT]/[Non-Text] (literal OCR-engine placeholder tags for a
    region it couldn't read, both present on this same page 91 fixture)
    must never mint as their own subdoc: target — confirmed real bug:
    _looks_like_generic_heading_shape strips non-word characters before
    testing capitalization, so the bracket contributes no signal and the
    placeholder slips through as if it were a real Title-Case heading.
    Previously worked around by excluding subdoc:no-text/subdoc:non-text
    from the assertion above rather than actually fixed."""
    candidates, _ = build_targets_for_page(_PAGE_91_ADDRESSEE_LIST, seed_scope=None, has_next_page=False)
    keys = {c["key"] for c in candidates}
    assert "subdoc:no-text" not in keys
    assert "subdoc:non-text" not in keys


def test_equipment_schedule_dotted_headings_still_match_but_stray_bare_integer_does_not():
    """Page 169's genuine dotted-decimal headings (3.1, 3.3-3.8, and the
    real container 3.0) must keep matching exactly as before - this fix
    must never touch dotted-number matching. The stray bare "1" from "1 Ph
    Welding Transformer (Air Cooled)" (an equipment-schedule line, not a
    heading) must be rejected, and must NOT evict the real, still-open
    "3.0" container the way it did before this fix."""
    candidates, _ = build_targets_for_page(_PAGE_169_EQUIPMENT_SCHEDULE, seed_scope=None, has_next_page=False)
    by_key = {c["key"]: c for c in candidates}

    for key in ("numheading:3.0", "numheading:3.1", "numheading:3.3", "numheading:3.4",
                "numheading:3.5", "numheading:3.6", "numheading:3.7", "numheading:3.8"):
        assert key in by_key, key

    # The stray bare "1" never becomes its own node...
    assert "numheading:1" not in by_key
    # ...and real headings AFTER it are still correctly nested under "3.0",
    # not orphaned by the "1" false positive popping it off the stack.
    # (own_label is just the bare "3.0" - its own descriptive text fails
    # DECIMAL_HEADING_RE only because of the leading "- ", not because it
    # isn't a real heading; see _headerless_decimal_match's docstring.)
    assert by_key["numheading:3.3"]["scope_label"] == "3.0"
    assert by_key["numheading:3.8"]["scope_label"] == "3.0"


def test_stamped_date_never_becomes_a_heading():
    """Page 38's stamped "27 MAR 2019" (a paragraph block, not a list item)
    must not produce a spurious numheading:27 - nothing numbered is open at
    that point in the document, and "27" isn't "1"."""
    candidates, _ = build_targets_for_page(_PAGE_38_AFFIDAVIT_WITH_STAMPED_DATE, seed_scope=None, has_next_page=False)
    numheadings = [c["key"] for c in candidates if c["key"].startswith("numheading:")]
    assert numheadings == []


def test_numbered_judgment_paragraph_never_becomes_a_heading():
    """Page 59's "39. Consequently, the objection petition is hereby
    dismissed with costs" is exactly 9 words - short enough to pass
    DECIMAL_HEADING_RE's label-length check - but a bare "39" with nothing
    numbered open must still be rejected as a heading."""
    candidates, _ = build_targets_for_page(_PAGE_59_JUDGMENT_NUMBERED_PARAGRAPHS, seed_scope=None, has_next_page=False)
    numheadings = [c["key"] for c in candidates if c["key"].startswith("numheading:")]
    assert numheadings == []


def test_running_page_header_quantity_never_becomes_a_heading():
    """Page 94's repeated running header "400 KV SUBSTATION..." must not
    produce a numheading:400 node - "400" is a voltage rating, not a clause
    number, and nothing numbered is open when it appears."""
    candidates, _ = build_targets_for_page(_PAGE_94_RUNNING_HEADER_400KV, seed_scope=None, has_next_page=False)
    numheadings = [c["key"] for c in candidates if c["key"].startswith("numheading:")]
    assert numheadings == []


def test_bare_one_still_opens_a_fresh_outline_with_nothing_numbered_open():
    """The positive case this fix must not break: a bare "1" IS trusted as
    a heading when it's the first heading of a fresh numbered outline
    (matches the 6-page job's real "1. Article")."""
    candidates, _ = build_targets_for_page("1. Article\n\nSome clause body.\n", seed_scope=None, has_next_page=False)
    assert any(c["key"] == "numheading:1" for c in candidates)
