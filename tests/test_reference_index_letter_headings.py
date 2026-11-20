"""Regression tests for letter-prefixed section headings ("A. INTRODUCTION",
..., "F. AWARD OF CONTRACT") — a real gap found while auditing structure-tree
coverage on the real 955-page job (7f442300, FAO-CARB_23_2019_PAPER_BOOK.pdf).
No existing mechanism matched this shape: CORE_RE's keyword list doesn't
include it, and DECIMAL_HEADING_RE requires digits.

Confirmed via direct inspection of real pages 355-361 that these are genuine
CONTAINERS — numbered clauses (1.0-7.0, then bare 8/9/10/11) nest inside
"A. INTRODUCTION", and the numbered-clause sequence runs CONTINUOUSLY across
the letter boundary into "B. GUARANTEES & LIABILITIES" (clause 12 directly
continues clause 11). The `_REAL_PAGES` text below is this job's own real,
unmodified `text` for pages 355-361 (a contiguous run, no gaps — an earlier
draft of this fixture skipped 358-360 and incorrectly seemed to show clause
11 as undetectable, until the intervening clauses 2-10 that establish the
sequential continuation were included), pulled directly from the database —
not synthetic fixtures."""

from reference_index import LETTER_HEADING_RE, build_targets_for_page

# ─── LETTER_HEADING_RE / trust gate ─────────────────────────────────────────

def test_letter_heading_regex_catches_real_shapes():
    assert LETTER_HEADING_RE.match("A. INTRODUCTION")
    assert LETTER_HEADING_RE.match("B. GUARANTEES & LIABILITIES")
    assert LETTER_HEADING_RE.match("D. SUBMISSION OF BIDS:")  # trailing colon
    assert LETTER_HEADING_RE.match("E. Observations & findings of Arbitral Tribunal:")


def test_letter_heading_regex_rejects_long_body_paragraphs():
    assert LETTER_HEADING_RE.match(
        "A. Interest bearing Mobilisation advance shall be paid to the "
        "contractor subject to the following conditions being met in full"
    ) is None


def test_fresh_a_is_trusted_with_nothing_lettered_open():
    candidates, _ = build_targets_for_page("A. INTRODUCTION\n\nSome body text.\n", seed_scope=None, has_next_page=False)
    assert any(c["key"] == "letterheading:a" for c in candidates)


def test_signature_block_initial_is_rejected_with_nothing_lettered_open():
    """Real regression: "H. G CHHABRA" (a person's name with a middle
    initial, from a signature block) matches LETTER_HEADING_RE's shape just
    as well as a genuine heading - only the fresh-or-sequential trust gate
    tells them apart, and nothing lettered is open here."""
    page_text = (
        "FOR AND ON BEHALF OF HARYANA VIDYUT PRASARAN NIGAM LTD.\n\n"
        "NAME\n\nTITLE\n\n"
        "H. G CHHABRA\n\n"
        "CHIEF ENGINEER/MM, HVPNL, PANCHKULA\n\n"
        "IN THE PRESENCE OF\n"
    )
    candidates, _ = build_targets_for_page(page_text, seed_scope=None, has_next_page=False)
    assert not any(c["key"].startswith("letterheading:") for c in candidates)


def test_letter_after_open_letter_is_trusted_but_skipped_letter_is_not():
    page1 = "A. FIRST SECTION\n\nSome body text.\n"
    _, scope = build_targets_for_page(page1, seed_scope=None, has_next_page=True)

    # "C." skips "B." - not trusted.
    page2 = "C. THIRD SECTION\n\nSome body text.\n"
    candidates, _ = build_targets_for_page(page2, seed_scope=scope, has_next_page=False)
    assert not any(c["key"] == "letterheading:c" for c in candidates)

    # "B." immediately follows "A." - trusted.
    page3 = "B. SECOND SECTION\n\nSome body text.\n"
    candidates2, _ = build_targets_for_page(page3, seed_scope=scope, has_next_page=False)
    assert any(c["key"] == "letterheading:b" for c in candidates2)


# ─── Bare-integer continuity survives a letter transition ──────────────────

def test_bare_integer_clause_numbering_survives_a_letter_transition():
    """The load-bearing regression this design protects: a document's
    numbered-clause sequence is confirmed (on the real document) to run
    CONTINUOUSLY across a letter-section boundary. nested_stack's own
    numheading entries get cleared for correct nesting when a new letter
    opens, but the separate last_bare_integer tracker must not be."""
    page1 = "A. INTRODUCTION\n\n1 FIRST CLAUSE\n\n2 SECOND CLAUSE\n\n3 THIRD CLAUSE\n\n"
    candidates1, scope = build_targets_for_page(page1, seed_scope=None, has_next_page=True)
    by_key1 = {c["key"]: c for c in candidates1}
    assert by_key1["numheading:3"]["scope_label"] == "A INTRODUCTION"

    page2 = "B. SECOND SECTION\n\n4 FOURTH CLAUSE\n\n5 FIFTH CLAUSE\n\n"
    candidates2, _ = build_targets_for_page(page2, seed_scope=scope, has_next_page=False)
    by_key2 = {c["key"]: c for c in candidates2}
    # Clause 4 continues clause 3's numbering...
    assert "numheading:4" in by_key2
    # ...but nests under the NEW letter section, not the old one.
    assert by_key2["numheading:4"]["scope_label"] == "B SECOND SECTION"
    assert by_key2["numheading:5"]["scope_label"] == "B SECOND SECTION"


# ─── End-to-end: real pages 355-361 ─────────────────────────────────────────

_REAL_PAGES = [
    {"page_num": 355, "text": "37\n\nSECTION-GCC\n\nGENERAL CONDITIONS OF CONTRACT\n\nA. INTRODUCTION\n\n1.0 DEFINITION OF TERMS\n\n1.1 'The Contract' means the agreement entered into between Owner and Contractor as per the Contract Agreement signed by the parties, including all attachments and appendices thereto and all documents incorporated by reference therein.\n\n1.2 'Owner' shall mean the HAFYANA VID'UT PRASARAN NIGAM LIMITED, PANCHKULA and shall inculcate their legal representatives, successors and assigns.\n\n1.3 'Contactor' shall mean the Bidder whose bid will be accepted by the Owner for the award of the Works and shall include such successful Bidder's legal representatives, successors and permitted assign.\n\n1.4 'Sub-contractor' shall mean the person named in the Contract for any part of the Work or any person to whom any part of the Contract has been sublat by the Contractor with the consent in writing of the owner and will include the legal representatives, successors and permitted assigns of such person.\n\n1.5 'Engineer' shall mean the Chief Engineer/ D&P, HVPNI, Panchukia, to act as Empowered Officer from time to time for the purposes of the Contract.\n\n1.6 'Consulting engineer/consultant' shall mean any firm or person duly appointed as such from time to time by the Owner.\n\n1.7 The terms 'Equipment', 'stores' and 'Materials' shall mean and include equipment, stores and materials to be provided by the Contractor under the Contract.\n\n1.8 'Works' shall mean and include the furnishing of equipment, labour and services, as per the Specifications and complete erection, testing and putting into satisfactory operation including all transportation, handling, unloading and storage at the Site as defined in the Contract.\n\n1.9 'Specifications' shall mean the Specifications and Bidding Document forming a part of the Contract and such other Schedules and drawings as may be mutually agreed upon.\n\n1.10 'Site' shall mean and include the land and other places on, into or through which the works and the related facilities are to be erected or installed and any adjacent land, paths, street or reservoir which may be allocated or used by the Owner or contractor in the performance of the Contract.\n\n1.11 The term 'contract Price' shall mean the lump sum price quoted by the Contractor in his bid with additions and/or deletions as may be agreed and incorporated in the Letter of Award, for the entire scope of the works.\n\n© Principal: ECE-2006-0019/02-02\n\n![](images/0.jpg)\n\n---\n*⏱️ **Unlimited-OCR Execution Time:** 27.1s (27.1s)*"},
    {"page_num": 356, "text": "33p\n\n1.12 The term 'Equipment Portion' of the Contract price shall mean the ex-works value of the equipment including excise duty, sales tax, freight & transit insurance.\n\n1.13 The term 'Erection Portion' of the Contract price shall mean the value of field activities of the Works including erection, testing and putting into satisfactory operation including successful completion of performance and guarantee tests to be performed at Site by the Contractor including type testing of equipment and training of the employees of the owner.\n\n1.14 'Manufacturer's Works' or 'Contractor's Works', shall mean the place of work used by the manufacturer, the contractor, their collaborators/associate or subcontractors for the performance of the Contract.\n\n1.15 'Inspector' shall mean the Owner or any person nominated by the Owner from time to time, to inspect the equipment, stores or Works under the Contract and/or the duly authorised representative of the Owner.\n\n1.16 'Notice of Award of Contract'/Letter of Award' shall mean the official notice issued by the Owner notifying the Contractor that his bid has been accepted.\n\n1.17 'Date of Contract' shall mean the date on which Notice of Award of Contract/Letter of Award has been issued.\n\n1.18 'Month' shall mean the calendar month. 'Day' or Days' unless herein otherwise expressly defined shall mean calendar day or days of 24 hours each.\n\nA 'Week' shall mean continuous period of seven (7) days.\n\n1.19 'Writing' shall include any manuscript, type written or printed statement, under or over signature and/or seal as the case may be.\n\n1.20 When the words 'Approved', 'Subject to Approval', 'Satisfactory', 'Equal to', 'Proper', 'Requested', 'As Directed', 'Where Directed', 'When Directed', 'Determined by', 'Accepted', 'Permitted' or words and phrases of like importance are used the approval, judgment, direction etc. is understood to be a function of the owner.\n\n1.21 Test on completion shall mean such tests as prescribed in the Contract to be performed by the Contractor before the work is taken over by the Owner.\n\n1.22 'Start up' shall mean the time period required to bring the equipment covered under the Contract from an inactive condition, when construction is essentially complete, to the 'state ready for trial operation. The start up period shall include preliminary inspection and checkout of equipment and supporting subsystem, initial operation of the complete equipment covered under the Contract to obtain necessary pre-trial operation data, perform calibration and corrective action, shut down, inspection and adjustment prior to the trial operation period.\n\nDP-March.PE-409dredg/CC.ec\n\n293\n\nDP-410\n\nP\n\n[Unreadable]\n\nPana\n\n---\n*⏱️ **Unlimited-OCR Execution Time:** 30.2s (30.2s)*"},
    {"page_num": 357, "text": "229\n\n1.23 'Initial operation' shall mean the first integral operation of the complete equipment covered under the Contract with the sub-system and supporting equipment in service or available for service.\n\n1.24 'Trial operation'. Reliability Test; 'Trial Run', 'Completion Test', shall mean the extended period of time after the start up period. During this trial operation period the unit shall be operated over the full load range. The length of Trial operation shall be as determined by the owner, unless otherwise specified elsewhere in the Contract.\n\n1.25 'Performance and Guarantee Test' shall mean all operational checks and tests required to determine and demonstrate capacity, efficiency, and operating characteristics as specified in the Contract Documents.\n\n1.26 The term 'final Acceptance/Taking Over' shall mean the Owner's written acceptance of the Works performed under the Contract, after successful commissioning/completion of Performance and Guarantee Tests, as specified in the accompanying Technical specification or otherwise agreed in the Contract.\n\n1.27 \"Commercial Operation\" shall mean the Conditions of Operation in which the complete equipment covered under the Contract is officially declared by the Owner to be available for continuous operation at different load upto and including rated capacity. Such declaration by the Owner, however, shall not relieve or prejudice the Contractor of any of his obligations under the Contract.\n\n1.28 'Guarantee Period' shall mean the period during which the Contractor shall remain liable for repair or replacement of any defective part of the Works performed under the Contract.\n\n1.29 'Latent Defects' shall mean such defects caused by faulty design, material or workmanship which cannot be detected during inspection, testing etc. based on the technology available for carrying out such tests.\n\n1.30 'Drawings', 'Plans' shall mean all:\n\na) Drawings furnished by the Owner as a basis for bid/proposals.\n\nb) Supplementary drawings furnished by the Owner to clarify and to define in greater detail the intent of the Contract.\n\nc) Drawings submitted by the Contractor with his bid provided such drawings are acceptable to the Owner.\n\nd) Drawings furnished by the owner to the Contractor during the progress of the work; and\n\ne) Engineering date and drawings submitted by the Contractor during the progress of the work provided such drawings are acceptable to the Owner.\n\n1.31 'Codes' shall mean the following, including the latest amendments, and/or replacements, if any:\n\na) Indian Electricity Act, 1905, and Rules and Regulations made thereunder.\n\nb) Indian Factory Act, 1948, and Rules and Regulations made thereunder.\n\nOF PROVINCE/REG-APPROVING/CPC. Inc.\n\n![](images/0.jpg)\n\n![](images/1.jpg)\n\n234\n\n---\n*⏱️ **Unlimited-OCR Execution Time:** 34.1s (34.1s)*"},
    {"page_num": 358, "text": "34.\n\nc) Indian Explosive Act, 1884, and Rules and Regulations made thereunder.\n\nd) Indian Petroleum Act, 1934, and Rules and Regulations made thereunder.\n\ne) A.S.M.E. Test Codes.\n\nf) A.I.E.E. Test Codes.\n\ng) American Society of Materials Testing Codes.\n\nh) Standards of the Indian Standards Institution.\n\ni) Other internationally approved standards and/or Rules and Regulations touching the subject matter of the Contract\n\n1.32 Words imparting the singular only shall also include the plural and vice-versa where the context so requires.\n\n1.33 Words imparting Person' shall include firms, companies, corporation and associations or bodies of individuals, whether incorporated or not.\n\n1.34 Terms and expression not herein defined shall have the same meanings as are assigned to them in the Indian Sale of Goods Act (1930), failing that in the Indian Contract Act (1872) and failing that in the General Clauses Act (1897) including amendments thereof if any.\n\n1.35 In addition to the above the following definitions shall also apply.\n\na) 'All equipment and materials' to be supplied shall also mean 'Goods'\n\nb) 'Constructed' shall also mean 'erected and installed'\n\nc) 'Contract Performance Guarantee' shall also mean 'Contract Performance Security'\n\n2.0 APPLICATION\n\nThese General Creditors shall apply to the extent that they are not superseded by provisions in Special Conditions of contract and Technical specification of these bidding documents.\n\n3.0 STANDARDS\n\nThe goods supplied under this Contract shall conform to the standards mentioned in the Technical Specification, and, when no applicable standard is mentioned, to the authoritative standard appropriate to the good and such standards shall be the latest issued by the concerned institution.\n\n4.0 LANGUAGE AND MEASURES\n\nAll documents pertaining to the Contract including Specification, schedules, notices, correspondence, operating and maintenance instructions, drawings or any other writing shall be written in English language. The Metric system of measurement shall be used exclusively in the Contract.\n\n5.0 CONTRACT DOCUMENTS\n\n5.1 The term Contract Documents shall mean and include the following which shall be deemed to form an integral part of the Contract:\n\nD:\\In\\In\\In\\In\\In\\In\\In\\In\\In\\In\\In\\In\\In\\In\\In\\In\\In\\In\\Out\\Out\\Out\\Out\\Out\\Out\\Out\\Out\\Out\\Out\\Out\\Out\\Out\\Out\\Out\\Out\\Out\\Out\n\n![](images/0.jpg)\n\n![](images/1.jpg)\n\n285\n\n---\n*⏱️ **Unlimited-OCR Execution Time:** 35.6s (35.6s)*"},
    {"page_num": 359, "text": "294\n\na) Invitation of Bid including letter forwarding the Bidding documents, Instructions to bidders, General Conditions of contract, Erection condition of contract and Special Conditions of Contract.\n\nb) Specification of the equipment to be furnished and erected under the Contract as brought out in the accompanying Technical specifications.\n\nc) Contractor's bid proposal and the documents attached there to including the letters of clarifications thereto between the Contractor and the owner prior to the award of Contract except to the extent of repugnancy.\n\nd) All the material, literature, data and information of any sort given by the Contractor along with his bid, subject to the approval of the Owner.\n\ne) Letter of Award and any agreed variations of the conditions of the documents and Special terms and conditions of Contract, if any.\n\n5.2 In the event of any conflict between the above-mentioned documents, the matter shall be referred to the Empowered Officer appointed by the owner whose decision shall be considered as final and binding upon the parties.\n\n6.0 USE OF CONTRACT DOCUMENTS AND INFORMATION\n\n6.1 The Contractor shall not, without the Owner's prior written consent, disclose, the contract, or any provision thereof, or any specification, plan, drawing, pattern, sample or information furnished by or on behalf of the Owner in connection therewith, to any person other than a person employed by the Contractor in the performance of the Contract. Disclosure to any such employed person shall be made in confidence and shall extend only so far, as may be necessary for purposes of such performance.\n\n6.2 The Contractor shall not, without the owner's prior written consent, make use of any document or information enumerated in various contracts documents except for purpose of performing the Contract.\n\n6.3 The Contractor shall not communicate or use in advertising, publicity, sales releases or in any other medium, photographs or other reproduction of the Works under this Contract, or descriptions of the Site, dimensions, quantity, quality or other information, concerning the Works unless prior written permission has been obtained from the Owner.\n\n6.4 Any document, other than the contract itself, enumerated in various contract documents shall remain the property of the Owner and shall be returned (in all copies) to the Owner on completion of the Contractor's performance under the Contract if so required by the Owner.\n\n7.0 CONSTRUCTION OF THE CONTRACT\n\n7.1 Notwithstanding anything stated elsewhere in the bid documents, the Contract to be entered into will be treated as a divisible supply and erection Contract.\n\nD:\\Project\\PREC.COORD\\eng\\DCC.DOC\n\n99.6\n\n![](images/0.jpg)\n\n---\n*⏱️ **Unlimited-OCR Execution Time:** 34.3s (34.3s)*"},
    {"page_num": 360, "text": "242\n\nThe supply portion of the contract will relate to supply of equipment and materials and the erection portion will relate to the handling at the site, storage, erection, construction, testing and commissioning etc. as defined in the Bid Documents.\n\n7.2 In case of divisible supply and erection contract, or where the Owner hands over his equipment to the contractor for executing the contract, then the contractor shall at the time of taking delivery of the equipment/ despatch documents be required to execute an Indemnity Bond in favour of the Owner in the form acceptable to HYPNL for keeping the equipment in safe custody and to utilise the same exclusively for the purposes of the said contract. Samples of proforma for the indemnity Bond are enclosed as Annexure – V & VI to this Volume – I.\n\n7.3 The Contract shall in all respects be construed and governed according to Indian laws.\n\n8 JURISDICTION OF CONTRACT\n\n6.1 The laws applicable to the Contract shall be the laws in force in India. The Courts of PANCHKULA shall have exclusive jurisdiction in all matters arising under this Contract.\n\n9 MANNER OF EXECUTION OF CONTRACT\n\n9.1 The Contract Agreement, unless otherwise agreed to, shall be signed within 30 days of the Notification of Award, at the office of the Owner at PANCHKULA on a date and time to be mutually agreed. The Contractor shall provide for signing of the Contract. Performance Guarantee, appropriate power of attorney and other: requisite materials. In case the Contract is to be signed beyond the stipulated time, the Bid Guarantee submitted with the proposal will have to be extended accordingly. The contractual completion period will be counted from the 30th day of the notification of award, irrespective of the reason of delay in signing of the contract.\n\n9.2 The Agreement will be signed in three originals and the Contractor shall be provided with one signed original and the rest will be retained by the Owner.\n\n9.3 The Contractor shall provide free of cost to the Owner all the engineering data, drawings and descriptive materials submitted with the bid, in at least three copies to form a part of the Contract immediately after issue of Letter of Award.\n\n9.4 Subsequent to signing of the Contract, the Contractor at his own cost shall provide the Owner with at least fifteen (15) true copies of Agreement along with all annexures within thirty (30) days after the signing of the Contract.\n\n10 ENFORCEMENT OF TERMS\n\n10.1 The failure of either party to enforce at any time any of the provisions of this Contract or any rights in respect thereto or to exercise any op\n\nD:\\P\\new\\HRS-03\\Bldg\\P\\002.doc\n\n![](images/0.jpg)\n\n![](images/1.jpg)\n\n![](images/2.jpg)\n\n---\n*⏱️ **Unlimited-OCR Execution Time:** 31.6s (31.6s)*"},
    {"page_num": 361, "text": "33\n\nprovided, shall in no way be construed to be a waiver of such provisions, rights or options or in any way to affect the validity of the Contract. The exercise by either party of any of its rights herein shall not preclude or prejudice either party from exercising the same or any other right it may have under the Contract.\n\n11 COMPLETION OF CONTRACT\n\n11.1 Unless otherwise terminated under the provisions of any other relevant clause, this Contract shall be deemed to have been completed on the expiry of the Guarantee Period as provided for under the clause entitled 'Guarantee' in this section of the Volume-I\n\nB. GUARANTEES & LIABILITIES\n\n12 TIME - THE ESSENCE OF CONTRACT\n\n12.1 The Contractor immediately on signing of contract is required to engage the Manpower and machinery at sites to carry out the detailed survey, land contouring and other preliminary activities required to start the works at site. All approvals required from the owner for this purpose will be sought immediately after signing of contract. The vendors for equipment will be finalised, equipment drawings will be submitted for approval without delay. It is expected that the contractor will commence the construction activity at sites within 2 months and the equipment will start arriving at sites within 3 months after signing of contract.\n\n12.2 The time and the date of completion of the Contract as stipulated in the Contract by the Owner without or with modifications, if any, and so incorporated in the Letter of Award, shall be deemed to be the essence of the Contract. The Contractor shall so organise his resources and perform his work as to complete it not later than the date agreed to.\n\n12.3 Based on the Bar/PERT charts fortnightly reports shall be submitted by the Contractor as directed by HVPN\n\n12.4 Subsequent to the finalisation of the Bar/PERT charts, the Contractor shall make available to HVPN a detailed manufacturing programme, in line with the agreed Contract Network. Such manufacturing programme shall be reviewed, updated and submitted to HVPN once every two months thereafter.\n\n12.5 The above bar charts/manufacturing programme shall be compatible with the Owner's computer environment and furnished to the Owner on such media as may be desired by the Owner.\n\nD/P:sposrREC-03/05/05/09/DCC.doc\n\n298\n\nIndiv\n\nHVN\n\nP\n\nP\n\n---\n*⏱️ **Unlimited-OCR Execution Time:** 30.7s (30.7s)*"},
]


def test_end_to_end_real_pages_reproduce_the_actual_document_hierarchy():
    """This job's own real 7 pages (355-361), fed through in order. Confirmed
    against the real PDF: "A. INTRODUCTION" contains clauses 1.0-11 (1.0's
    definitions run 1.1-1.35, then bare-dotted 2.0-7.0, then bare 8/9/10/11);
    "B. GUARANTEES & LIABILITIES" opens right after and clause 12 directly
    continues clause 11's numbering."""
    seed_scope = None
    all_candidates = []
    for i, p in enumerate(_REAL_PAGES):
        has_next = i < len(_REAL_PAGES) - 1
        candidates, seed_scope = build_targets_for_page(p["text"], seed_scope, has_next)
        all_candidates.extend(candidates)

    by_key = {c["key"]: c for c in all_candidates}

    assert by_key["letterheading:a"]["own_label"] == "A INTRODUCTION"
    assert by_key["letterheading:b"]["own_label"] == "B GUARANTEES & LIABILITIES"

    # The whole 1.0-7.0/8-11 clause run nests under "A", not "B".
    for key in (
        "numheading:1.0", "numheading:2.0", "numheading:3.0", "numheading:4.0",
        "numheading:5.0", "numheading:6.0", "numheading:7.0",
        "numheading:8", "numheading:9", "numheading:10", "numheading:11",
    ):
        assert by_key[key]["scope_label"] == "A INTRODUCTION", key

    # Clause 12 continues clause 11's numbering straight across the letter
    # boundary, but nests under the NEW letter section.
    assert "numheading:12" in by_key
    assert by_key["numheading:12"]["scope_label"] == "B GUARANTEES & LIABILITIES"
    assert by_key["numheading:12"]["own_label"] == "12 TIME - THE ESSENCE OF CONTRACT"

    # Headerless sub-clauses still nest correctly under their own parent.
    assert by_key["numheading:11.1"]["scope_label"] == "A INTRODUCTION › 11 COMPLETION OF CONTRACT"
    assert by_key["numheading:12.1"]["scope_label"] == "B GUARANTEES & LIABILITIES › 12 TIME - THE ESSENCE OF CONTRACT"
