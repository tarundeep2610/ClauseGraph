"""Negative test suite locking in guards against recall regressions in actionable items.

Ensures that filters and noise cleaners NEVER re-trigger the v1/v3 regressions where
vital contractual obligations (TReDS, Integrity Pact, turnover eligibility, land-border
clauses) were erroneously suppressed or treated as administrative noise.
"""

try:
    import pytest
except ImportError:
    pytest = None
from ai_indexing.guards import (
    is_administrative_noise,
    is_table_header_or_form_field,
    is_document_noise_or_running_header,
    is_as_is_catalogue_other_leak,
    is_bare_parameter_or_platform_disclaimer,
    is_alternative_security_instrument_duplicate,
    is_ungated_sow_feature_description,
    is_statutory_policy_leak,
    is_contractor_evaluation_scoring_matrix,
    is_litigation_argument_or_past_recital,
    is_standalone_leaf_bullet,
)
from ai_indexing.harvesters import harvest_action_items_from_text


def test_treds_clause_is_not_administrative_noise():
    quote = (
        "sellers intending to avail payment through TReDS are required to be "
        "registered with at least one TReDS exchange"
    )
    assert not is_administrative_noise(quote)
    assert not is_table_header_or_form_field(quote)
    assert not is_document_noise_or_running_header(quote)


def test_integrity_pact_clause_is_not_administrative_noise():
    quote = "Bidders shall have to upload scanned copy of signed integrity pact along with bid."
    assert not is_administrative_noise(quote)
    assert not is_table_header_or_form_field(quote)
    assert not is_document_noise_or_running_header(quote)


def test_turnover_eligibility_criteria_is_not_administrative_noise():
    quote = (
        "The minimum average annual financial turnover of the bidder during the last three years, "
        "ending on 31st March of the previous financial year, should be as specified in the bid document."
    )
    assert not is_administrative_noise(quote)
    assert not is_table_header_or_form_field(quote)
    assert not is_document_noise_or_running_header(quote)


def test_land_border_eligibility_is_not_administrative_noise():
    quote = (
        "any bidder from a country which shares a land border with India will be eligible to bid in "
        "this tender only if the bidder is registered with the Competent Authority."
    )
    assert not is_administrative_noise(quote)
    assert not is_table_header_or_form_field(quote)
    assert not is_document_noise_or_running_header(quote)


def test_option_clause_is_not_administrative_noise():
    quote = (
        "The Purchaser reserves the right to increase or decrease the quantity to be ordered up to "
        "50 percent of bid quantity at the time of placement of contract."
    )
    assert not is_administrative_noise(quote)
    assert not is_table_header_or_form_field(quote)
    assert not is_document_noise_or_running_header(quote)


def test_genuine_administrative_noise_is_suppressed():
    # Page stamps
    assert is_administrative_noise("Page 12 of 85")
    assert is_administrative_noise("Page 4")
    assert is_administrative_noise("12 / 85")

    # Form blank lines
    assert is_table_header_or_form_field("Name: ........................................................")
    assert is_table_header_or_form_field("Signature of Tenderer: _____________________________________")

    # Pure column header fragments
    assert is_table_header_or_form_field("S.No | Description | Quantity | Unit Rate | Total Amount")


def test_as_is_catalogue_filter_strictly_scoped_to_other_in_as_is_section():
    # item_type='other' inside an as_is section is dropped
    assert is_as_is_catalogue_other_leak("other", "11kV VCB Panel with metering unit", "as_is")
    assert is_as_is_catalogue_other_leak("OTHER", "Existing ERP Server specs", "as_is")

    # Obligations inside as_is section are NOT dropped
    assert not is_as_is_catalogue_other_leak("obligation", "The contractor shall test 11kV VCB Panel", "as_is")

    # Deadlines inside as_is section are NOT dropped
    assert not is_as_is_catalogue_other_leak("deadline", "Inspection within 14 days", "as_is")

    # Conditions inside as_is section are NOT dropped
    assert not is_as_is_catalogue_other_leak("condition", "Penalty applies if inspection fails", "as_is")

    # Outside as_is section, 'other' is NEVER dropped (litigation prayers, dispositions, etc.)
    assert not is_as_is_catalogue_other_leak("other", "Prayer to set aside arbitral award", "general")
    assert not is_as_is_catalogue_other_leak("other", "Application for stay of proceedings", "scc")


def test_bare_parameter_and_attachment_guards():
    # File attachments
    assert is_bare_parameter_or_platform_disclaimer("obligation", "Scope of Work:1789282663.pdf")
    assert is_bare_parameter_or_platform_disclaimer("obligation", "Product/Equipment Details:1789283012.pdf")
    assert is_bare_parameter_or_platform_disclaimer("obligation", "Financial Breakup Required:1789283018.pdf")
    assert is_bare_parameter_or_platform_disclaimer("obligation", "Excel Upload Required : Price Schedule for AMC CAMC Service - 178")

    # Bare section / parameter headers without action verbs
    assert is_bare_parameter_or_platform_disclaimer("obligation", "MII Compliance")
    assert is_bare_parameter_or_platform_disclaimer("obligation", "MSE Purchase Preference")
    assert is_bare_parameter_or_platform_disclaimer("obligation", "Financial Document Indicating Price Breakup Required")
    assert is_bare_parameter_or_platform_disclaimer("obligation", "तकनीकी विशिष्टियाँ /Technical Specifications")
    assert is_bare_parameter_or_platform_disclaimer("obligation", "Number of months within the contract period for which service is required : 51")

    # Platform disclaimer
    assert is_bare_parameter_or_platform_disclaimer("obligation", "The Buyer is not allowed to add following clauses in ATC")
    assert is_bare_parameter_or_platform_disclaimer("condition", "Reference of conditions published on any external site")

    # Real obligations must NOT be blocked
    assert not is_bare_parameter_or_platform_disclaimer("obligation", "The bidder shall submit technical specifications certificate")
    assert not is_bare_parameter_or_platform_disclaimer("obligation", "Payments shall be made to the Seller within 45 days of issue of service delivery acceptance certificate")
    assert not is_bare_parameter_or_platform_disclaimer("condition", "Bid auto-extends by 7 days unless minimum bid count reached")


def test_alternative_security_instrument_duplicate():
    # FDR alternative clause for EMD
    fdr_emd = (
        "Bidders can also submit the EMD with Fixed Deposit Receipt made in the name of... "
        "Bidder has to upload scanned copy / proof of the FDR online..."
    )
    assert is_alternative_security_instrument_duplicate("obligation", fdr_emd)

    # FDR alternative clause for PBG
    fdr_pbg = (
        "Successful Bidder can submit the Performance Security in the form of Fixed Deposit Receipt also... "
        "upload scanned copy of the FDR document in place of PBG..."
    )
    assert is_alternative_security_instrument_duplicate("obligation", fdr_pbg)

    # Banker's Cheque alternative clause
    bc_emd = (
        "Bidders can also submit the EMD with Banker's Cheque made in the name of... "
        "upload scanned copy of the Banker's Cheque online"
    )
    assert is_alternative_security_instrument_duplicate("obligation", bc_emd)

    # Online Payment RTGS alternative clause
    rtgs_emd = (
        "Bidder has to upload scanned copy of the Online Payment Transfer along with bid... "
        "indicate Contract number and name of Seller entity in the transaction details field"
    )
    assert is_alternative_security_instrument_duplicate("obligation", rtgs_emd)

    # Primary / Canonical Demand Draft (DD) clauses must NOT be filtered
    dd_emd = (
        "Bidders can also submit the EMD with Account Payee Demand Draft in favour of Buyer... "
        "Bidder has to upload scanned copy / proof of the DD along with bid and has to ensure "
        "delivery of hardcopy to the Buyer within 5 days of Bid End date / Bid Opening date"
    )
    assert not is_alternative_security_instrument_duplicate("obligation", dd_emd)

    dd_pbg = (
        "Successful Bidder can submit the Performance Security in the form of Account Payee Demand Draft... "
        "ensure delivery of hardcopy to Buyer within 15 days of award of contract"
    )
    assert not is_alternative_security_instrument_duplicate("obligation", dd_pbg)


def test_ungated_sow_feature_description():
    # Ungated software/system feature sentences in sow division are filtered
    assert is_ungated_sow_feature_description(
        "obligation", "The system shall support automatic failover across redundant power supplies.", "sow"
    )
    assert is_ungated_sow_feature_description(
        "obligation", "The software shall be integrated with existing SCADA system.", "sow"
    )
    assert is_ungated_sow_feature_description(
        "obligation", "The portal shall provide facility for online grievance redressal.", "sow"
    )
    assert is_ungated_sow_feature_description(
        "obligation", "The equipment shall be capable of operating at temperatures up to 50 degrees.", "sow"
    )

    # Gated / Milestone / Timed duties in sow division are NOT filtered
    assert not is_ungated_sow_feature_description(
        "obligation", "The contractor shall provide facility for online grievance redressal within 30 days of mobilization.", "sow"
    )
    assert not is_ungated_sow_feature_description(
        "obligation", "The vendor shall support automatic failover subject to acceptance criteria defined in Section 4.", "sow"
    )
    assert not is_ungated_sow_feature_description(
        "obligation", "The contractor shall be capable of operating the plant, failing which penalty will be levied.", "sow"
    )

    # Outside sow division (scc, eligibility, general), features are NOT filtered
    assert not is_ungated_sow_feature_description(
        "obligation", "The system shall support automatic failover across redundant power supplies.", "scc"
    )
    assert not is_ungated_sow_feature_description(
        "obligation", "The software shall provide facility for online grievance redressal.", "general"
    )

    # Non-obligation items in sow (deadline, condition) are NOT filtered
    assert not is_ungated_sow_feature_description(
        "deadline", "Inspection within 14 days", "sow"
    )
    assert not is_ungated_sow_feature_description(
        "condition", "Penalty applies if inspection fails", "sow"
    )


def test_litigation_argument_or_past_recital():
    # Historical counsel or arbitrator arguments are filtered
    assert is_litigation_argument_or_past_recital(
        "The learned counsel for the appellant submitted that the arbitrator committed a patent illegality."
    )
    assert is_litigation_argument_or_past_recital(
        "Learned Senior Advocate for respondent argued that the claims were barred by limitation."
    )
    assert is_litigation_argument_or_past_recital(
        "The learned Arbitrator held that the claimant was entitled to claim no. 3."
    )
    assert is_litigation_argument_or_past_recital(
        "The petitioner contended that the contract was terminated without notice."
    )

    # Prayers for relief and court dispositions / orders must NOT be filtered
    assert not is_litigation_argument_or_past_recital(
        "It is therefore prayed that the impugned arbitral award may kindly be set aside."
    )
    assert not is_litigation_argument_or_past_recital(
        "The appeal is accordingly dismissed with costs."
    )
    assert not is_litigation_argument_or_past_recital(
        "The respondent shall pay the sum of Rs 10,00,000 within 60 days."
    )


def test_harvest_rules_17_and_20_to_24():
    # Rule 17: EMD & Integrity Pact 5-day physical submission -> high confidence deadline
    rule17_text = (
        "Mandating submission of documents (other than EMD and signed Integrity Pact - which can "
        "be submitted within 5 days of bid opening) in physical form as a pre-requisite to qualify bidders."
    )
    items17 = harvest_action_items_from_text(7, rule17_text)
    assert len(items17) == 1
    assert items17[0]["item_type"] == "deadline"
    assert items17[0]["confidence"] == "high"
    assert "within 5 days" in items17[0]["date_text"]

    # Rule 19: EMD / PBG physical instrument hardcopy delivery within days
    rule19_text = (
        "Bidder has to upload scanned copy / proof of the DD along with bid and has to ensure "
        "delivery of hardcopy to the Buyer within 5 days of Bid End date / Bid Opening date."
    )
    items19 = harvest_action_items_from_text(7, rule19_text)
    assert len(items19) == 1
    assert items19[0]["item_type"] == "deadline"
    assert items19[0]["confidence"] == "high"
    assert "within 5 days" in items19[0]["date_text"]

    # Rule 20: MAF
    rule20_text = (
        "Authorisation Form / Certificate with OEM/Original Service Provider details such as "
        "name, designation, address, e-mail Id and Phone No. required to be furnished along with the bid."
    )
    items20 = harvest_action_items_from_text(6, rule20_text)
    assert any(
        it["item_type"] == "obligation"
        and it["confidence"] == "high"
        and "Manufacturer Authorisation" in it["item_text"]
        for it in items20
    )

    # Rule 21: Malicious Code Certificate
    rule21_text = (
        "The seller should upload following certificate in the bid:-\n"
        "(a) This is to certify that the Hardware and the Software being offered as part of the contract does not contain Embedded Malicious code"
    )
    items21 = harvest_action_items_from_text(7, rule21_text)
    assert any(
        it["item_type"] == "obligation"
        and it["confidence"] == "high"
        and "The seller should upload following certificate in the bid:-" in it["evidence_quote"]
        for it in items21
    )

    # Rule 22: Non-assignment & Subcontracting
    rule22_text = (
        "The Seller shall not assign the Contract in whole or part without obtaining the prior written consent of buyer."
    )
    items22 = harvest_action_items_from_text(8, rule22_text)
    assert any(
        it["item_type"] == "condition"
        and it["confidence"] == "high"
        and "not assign or sub-contract" in it["item_text"]
        for it in items22
    )

    # Rule 23: Self Declarations
    rule23_text = (
        "Bidder has to upload Self Declarations for Financial Standing, Non- Blacklisting and other criteria "
        "as per bid document. Failing of which the Bid shall summarily be rejected."
    )
    items23 = harvest_action_items_from_text(9, rule23_text)
    assert any(
        it["item_type"] == "condition"
        and it["confidence"] == "high"
        and "Self Declarations" in it["item_text"]
        for it in items23
    )

    # Rule 24: MSE Price Matching Band (L-1 + 15%)
    rule24_text = (
        "If L-1 is not an MSE and MSE Seller (s) has / have quoted price within L-1+ 15% of margin of "
        "purchase preference /price band defined in relevant policy, such MSE Seller shall be given "
        "opportunity to match L-1 price and contract will be awarded for 25% percentage of total quantity."
    )
    items24 = harvest_action_items_from_text(3, rule24_text)
    assert any(
        it["item_type"] == "condition"
        and it["confidence"] == "high"
        and "L-1+15%" in it["item_text"]
        for it in items24
    )


def test_is_standalone_leaf_bullet():
    # Standalone bullets / micro-enumerations
    assert is_standalone_leaf_bullet("(a)")
    assert is_standalone_leaf_bullet("(b)")
    assert is_standalone_leaf_bullet("(i)")
    assert is_standalone_leaf_bullet("(iv)")
    assert is_standalone_leaf_bullet("a.")
    assert is_standalone_leaf_bullet("i.")
    assert is_standalone_leaf_bullet("b)")

    # Numbers like 1. or (1) are numeric clauses, not leaf letter bullets
    assert not is_standalone_leaf_bullet("(1)")
    assert not is_standalone_leaf_bullet("1.")

    # Legitimate titled headings must not be treated as standalone bullets
    assert not is_standalone_leaf_bullet("(a) General Description")
    assert not is_standalone_leaf_bullet("1.1 Scope of Work")
    assert not is_standalone_leaf_bullet("SECTION I - INSTRUCTIONS TO BIDDERS")
    assert not is_standalone_leaf_bullet("ANNEXURE A-1")


def test_sow_routine_procedures_filtered_and_commercial_gates_preserved():
    # Routine PPE and safety permits without commercial gate are filtered in sow
    assert is_ungated_sow_feature_description(
        "obligation", "Contractor shall ensure safety helmet and safety boots for all workmen at site.", "sow"
    )
    assert is_ungated_sow_feature_description(
        "obligation", "Hot work permit shall be obtained from safety officer before starting welding work.", "sow"
    )
    assert is_ungated_sow_feature_description(
        "obligation", "Scaffolding tag must be displayed on every working platform.", "sow"
    )
    assert is_ungated_sow_feature_description(
        "obligation", "Contractor shall ensure regular housekeeping and scrap disposal at site.", "sow"
    )

    # Substantive commercial gates and milestones in sow are PRESERVED
    assert not is_ungated_sow_feature_description(
        "obligation", "For dewatering from open pit, use of portable electrical submersible pump is prohibited", "sow"
    )
    assert not is_ungated_sow_feature_description(
        "obligation", "Contractor shall replace defective equipment within warranty period of 12 months.", "sow"
    )
    assert not is_ungated_sow_feature_description(
        "obligation", "Final bill payment will be released upon submission of stage completion certificate.", "sow"
    )
    assert not is_ungated_sow_feature_description(
        "obligation", "The contractor shall rectify defects within 14 days, failing which penalty will apply.", "sow"
    )


def test_portal_auction_tutorial_filtered():
    assert is_bare_parameter_or_platform_disclaimer(
        "obligation", 'Auction gets available under "Live Auction" tab and open for bidders to quote'
    )
    assert is_bare_parameter_or_platform_disclaimer(
        "obligation", "Bidder shall click on View option which will open a separate page having basic details"
    )
    assert is_bare_parameter_or_platform_disclaimer(
        "obligation", "Maximum Seal Percentage: 25%"
    )
    assert is_bare_parameter_or_platform_disclaimer(
        "obligation", "criteria for determination of cost of local content in the service shall be as follows"
    )


def test_table_milestones_and_bare_dates():
    # Table milestones MUST be preserved (not dropped by bare parameter guard)
    assert not is_bare_parameter_or_platform_disclaimer(
        "condition", "Completion period of the Work</td><td>60 (Sixty) Months</td>"
    )
    assert not is_bare_parameter_or_platform_disclaimer(
        "deadline", "<td>Last date of issuing addendum(if any)</td><td>27.02.2026</td>"
    )
    # Standalone date timestamps without milestone label MUST be filtered
    assert is_bare_parameter_or_platform_disclaimer("deadline", "27.02.2026")
    assert is_bare_parameter_or_platform_disclaimer("deadline", "20.02.2026 at 1100 hrs.")
    assert is_bare_parameter_or_platform_disclaimer("deadline", "<td>27.02.2026</td>")


def test_consultancy_noise_filtered():
    # Category B: Personnel leave / CV submission guidelines
    assert is_bare_parameter_or_platform_disclaimer("condition", "He/She must take prior permission from DMRC for obtaining leave during his tenure as Project Architect")
    assert is_bare_parameter_or_platform_disclaimer("obligation", "At bidding stage, bidder is required to submit CV of Project Architect, Team Leaders of each discipline")

    # Category C: JV internal equity & employer verification duration
    # Lead partner 26% and Non-substantial partner 20% are valid procurement eligibility (gold items in dmrc-oew-16p)
    assert not is_bare_parameter_or_platform_disclaimer("obligation", "Lead partner must be substantial partner in the JV/Consortium i.e. it should have a minimum of 26% participation")
    assert not is_bare_parameter_or_platform_disclaimer("obligation", "Each non-substantial partner should have a minimum of 20% participation in the JV/ Consortium")
    assert is_bare_parameter_or_platform_disclaimer("obligation", "The Parent company of the JV/Consortium shall submit undertaking in Appendix-30 of Form of Tender.")
    assert is_bare_parameter_or_platform_disclaimer("condition", "If credentials do not get verified in a period of two months from the date of issue of Letter of Acceptance (LOA)")
    assert is_bare_parameter_or_platform_disclaimer("obligation", "The banking reference should be from a Scheduled Bank in India... and it should not be more than 3 months old")

    # Category D: Portal hardware tokens & BOQ tampering
    assert is_bare_parameter_or_platform_disclaimer("obligation", "The authorized signatory of intending tenderer, as per Power of Attorney (POA), must have valid Class-II or Class-III digital signature.")
    assert is_bare_parameter_or_platform_disclaimer("condition", "The details of the DD/any other accepted instrument, physically sent, should tally with the details available in the scanned copy")
    assert is_bare_parameter_or_platform_disclaimer("condition", "If the BOQ file is found to be modified by the bidder, the bid will be rejected.")

    # Category E: Procedural rejection warnings, statutory recitals, and qualification table rows
    assert is_bare_parameter_or_platform_disclaimer("condition", "Non submission of affidavit by the tenderer shall result in rejection of their bid")
    assert is_bare_parameter_or_platform_disclaimer("condition", "Any bidder from a country which shares a land border with India will be eligible to bid either as a single entity or as a member of a JV / Consortium with others, in any procurement whether of goods, services")
    assert is_bare_parameter_or_platform_disclaimer("obligation", "T3 - Net Worth: Net Worth of tenderer during last audited financial year should be ≥ INR 1.73 crore.")
    assert is_bare_parameter_or_platform_disclaimer("obligation", "T4 - Annual Turnover: The average annual turnover from consultancy works of last five financial years should be ≥ INR 6.93 crore.")


def test_contractor_evaluation_scoring_matrix_guards():
    """Verify that contractor evaluation mark deductions, scoring sheets, and penalty tables are identified."""
    sample_deductions = [
        "Violation of applicable safety, health and environment related norms Deduction of 0.5 marks",
        "Violation of applicable safety, health and environment related norms Deduction of 3 marks",
        "Fatal Accident:Full marks (25) Deduction",
        "Delay upto 7 days4 Marks Delay more than 7 days but less than 15 days3 Marks",
        "Letter issued to contractor during execution to ensure sufficient resources Deduction of 1.5% mark",
        "IOCL forced to arrange manpower, material or any other Resource Deduction of 5 marks",
        "Contract Terminated due to poor progressFull marks (15) Deduction",
        "Direct payment by IOCL to sub-contractor/ Labour:Deduction of 2.5 marks",
        "1 incidence (More than 1 working day each) - 3 marks",
        "Price Adjustment for delay upto 1.5 % of the Total Contract Value - 7.5 marks",
        "Rework done upto 2 times in contract periodUniform Deduction of 0.5 mark",
        "Delay upto 1 weekDeduction of 1 mark",
        "Cleared Site after written communication/Reminder from EICE- Deduction of 1 mark",
        "Partially cleared hence penalty/recovery imposed by EICEDeduction of 2.5 marks",
    ]
    for quote in sample_deductions:
        assert is_contractor_evaluation_scoring_matrix(quote), f"Failed to filter contractor evaluation deduction: {quote}"

    # Valid contract requirements must NOT be flagged as scoring deductions
    assert not is_contractor_evaluation_scoring_matrix("The contractor shall submit Initial Security Deposit (ISD) within 15 days.")
    assert not is_contractor_evaluation_scoring_matrix("Validity of quoted rates shall be 04 (Four) months from scheduled due date.")


def test_all_gold_action_items_fixtures_invariant():
    """Mathematical invariant: ZERO gold items across all benchmark fixtures may be filtered by noise guards."""
    import glob
    import json
    import os

    fixtures = glob.glob(os.path.join(os.path.dirname(__file__), "fixtures", "gold_action_items_*.json"))
    assert len(fixtures) >= 5, f"Expected at least 5 fixtures, found {len(fixtures)}"

    total_tested = 0
    for fpath in fixtures:
        with open(fpath, encoding="utf-8") as fp:
            data = json.load(fp)
            items = data.get("action_items", [])
            for item in items:
                quote = item.get("evidence_quote") or ""
                itype = item.get("item_type") or "obligation"
                # Neither the bare parameter guard, contractor scoring guard, nor noise guard may reject a gold item
                assert not is_bare_parameter_or_platform_disclaimer(itype, quote), (
                    f"Gold item erroneously filtered by is_bare_parameter_or_platform_disclaimer in {os.path.basename(fpath)}: {quote[:100]}"
                )
                assert not is_contractor_evaluation_scoring_matrix(quote), (
                    f"Gold item erroneously filtered by is_contractor_evaluation_scoring_matrix in {os.path.basename(fpath)}: {quote[:100]}"
                )
                total_tested += 1

    assert total_tested >= 100, f"Expected at least 100 gold items tested, got {total_tested}"




