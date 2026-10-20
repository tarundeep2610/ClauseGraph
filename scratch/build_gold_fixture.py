"""Hand-authored gold fixture build for job 89cfa529-2390-464c-9c3e-2446ef68c588
([GOLD] duplicate of 3cafeb89-e2e0-428b-9fd1-73471232deae, Tenderdocument1759.pdf
"OPERATIONS, MAINTENANCE & MODERNIZATION OF ERP SYSTEM UNDER MPPKVVCL, JABALPUR").

Headings below are hand-authored from a direct reading of the job's own OCR
text (database.get_pages(DUP_JOB_ID), the 'text' field = what the AI pipeline
itself reads), never guessed or copied from the AI's own existing index on the
source job. Mirrors the process used for the 17-page/100-page FAO-CARB
fixtures: AIHeading objects replayed offline/deterministically
(resolve_parent_via_ai=False, provider_config=None) through the real seam
(_ground_and_resolve_chunk -> apply_heading_events_to_scope ->
_persist_page_index -> build_mention_index_for_job), batched to respect
AI_INDEX_MAX_HEADINGS_PER_CHUNK (200), chaining seed_scope across every page
and every batch.

heading_text only needs enough leading text to uniquely ground against the
page's own blocks (locate_heading_block does substring/fuzzy matching) - not
the full clause text.

Run inside the CPU app container:
    docker cp scratch/build_gold_fixture.py ocr-services-glm-ocr-api-1:/app/scratch_build.py
    docker exec ocr-services-glm-ocr-api-1 python3 /app/scratch_build.py
"""
import asyncio

import ai_indexing
import ai_reference_index
import database
import reference_index

DUP_JOB_ID = "89cfa529-2390-464c-9c3e-2446ef68c588"
AIHeading = ai_indexing.AIHeading

# ---------------------------------------------------------------------------
# Hand-authored headings, in document order. Populated incrementally while
# reading the OCR dump page by page.
# ---------------------------------------------------------------------------
ALL_HEADINGS: list = []


def h(text, page, level, parent):
    ALL_HEADINGS.append(AIHeading(heading_text=text, start_page=page, level=level, parent_ref=parent))


# ===== Front matter (pages 1-10) =====
h("TENDER NOTICE", 3, 1, None)
h("NOTE: -", 3, 2, "TENDER NOTICE")
for n, text, page in [
    (1, "Other details can be seen in the complete tender documents", 3),
    (2, "Tender Documents can be downloaded from main portal", 3),
    (3, "The bid data should be filled in and the bid seals", 3),
    (4, "The relevant portion of tender which tenderers have to fill online", 3),
    (5, "Since the online bidders are required to sign their bids online", 3),
    (6, "Bidders intending to participate in the Tender are required", 3),
    (7, "The required amount of EMD shall be accepted through online payment only", 4),
    (8, "No offer will be accepted without valid Earnest Money Deposit", 4),
    (9, "The corrigendum or addendum to the bidding documents", 4),
    (10, "Last date for online submission of bid documents", 4),
    (11, "The Tender document will be available on portal", 4),
    (12, "The Pre-bid meeting will be held on 09.10.2026", 4),
    (13, "Only online bids will be considered as valid.", 4),
]:
    h(f"({n}) {text}", page, 3, "NOTE: -")

h("Key Dates and Basic Tender Information", 5, 1, None)
h("Note: -", 5, 2, "Key Dates and Basic Tender Information")
h("(1) The Tenderers are required to submit all the desired documents", 5, 3, "Note: -")
h("(2) The Tenderers have to upload following documents online", 5, 3, "Note: -")
h("(I) IN COVER-1: -", 5, 4, "(2) The Tenderers have to upload following documents online")
h("(II) IN COVER-2: -", 5, 4, "(2) The Tenderers have to upload following documents online")
for marker, text, page in [
    ("i.", "TENDER Forms - upload duly sealed, signed & dated scan copy", 5),
    ("i.", "Annex-I Performa for Bid Form", 5),
    ("ii.", "Annex-II Performa for No Deviations from Terms and Conditions of Bid Document", 5),
    ("iii.", "Annex-III Performa for Undertakings", 5),
    ("iv.", "Annex-V Performa for Conflict of Interest", 5),
    ("v.", "Annex-VI Format for blacklisting", 5),
    ("vi.", "Annex-VII Performa for Pending Litigation", 5),
    ("ii.", "Documents required against eligibility criteria of the tender", 6),
]:
    h(f"{marker} {text}", page, 5, "(II) IN COVER-2: -")
h("(III) IN COVER -3: -", 6, 4, "(2) The Tenderers have to upload following documents online")
h("(3) In case of any of above dates is declared as holiday", 6, 3, "Note: -")

h("Section-I: Essential Tender Information", 7, 1, None)
for n, particulars, page in [
    (1, "Name of works/services to be procured", 7),
    (2, "Purchaser", 7),
    (3, "TENDER/TENDER Specification Number", 7),
    (4, "TENDER Type", 7),
    (5, "Contract Period", 7),
    (6, "TENDER/TENDER Currency Settings", 7),
    (7, "Non-refundable TENDER document fee/TENDER fee (INR)", 7),
    (8, "Earnest Money Deposit (INR)", 7),
    (9, "Availability of TENDER/TENDER documents starts on", 7),
    (10, "Availability of TENDER documents ends on", 7),
    (11, "TENDER submission ends on", 7),
    (12, "TENDER opens on", 7),
    (13, "Pre-Bid Meeting/Conference if applicable", 7),
    (14, "TENDER validity period (in days)", 7),
    (15, "Purchaser address for Correspondence", 7),
]:
    h(f"{n}. {particulars}", page, 2, "Section-I: Essential Tender Information")

h("Section-II: Eligibility Criteria for Tenderers", 8, 1, None)
h("(1) The essential eligibility criteria for Tenderers have been given in the table hereunder:", 8, 2, "Section-II: Eligibility Criteria for Tenderers")
for n, criteria, page in [
    (1, "The Sole Bidder/Lead Bidder and the Consortium Partner", 8),
    (2, "The bidder should be registered in India with GST", 8),
    (3, "The Sole bidder/Lead bidder and Consortium Partner (in case of consortium) should not have been blacklisted", 8),
    (4, "The Sole bidder/Lead bidder should have a Minimum Average Annual Turnover", 8),
    (5, "The Sole bidder/Lead bidder and Consortium Partner (in case of consortium) must have positive net worth", 8),
    (6, "The Sole bidder/Lead bidder should have successfully executed at least three projects", 9),
    (7, "The Sole Bidder, in case of an individual bid, or the Lead Bidder/Lead Consortium Member", 9),
    (8, "The Sole bidder/Lead bidder and Consortium Partner (in case of consortium) must have ISO 9001:2015", 9),
    (9, "The Sole Bidder/Lead Bidder shall be an active Oracle Partner Network (OPN) member", 9),
    (10, "The Sole bidder/Lead bidder should have at least 50", 9),
]:
    h(f"{n}. {criteria}", page, 2, "(1) The essential eligibility criteria for Tenderers have been given in the table hereunder:")
h("Note:", 10, 2, "Section-II: Eligibility Criteria for Tenderers")
h("(i) Bidders needs to mention page no. of the documents attached in the offer", 10, 3, "Note:")
h("(ii) For the purpose of eligibility criteria work order executed in India", 10, 3, "Note:")

# ===== Section-III: Instructions to Bidders (pages 11-22) =====
S3 = "Section-III: Instructions to Bidders"
h(S3, 11, 1, None)
h("1 Definition(s)", 11, 2, S3)
for n, label in [
    (1, "Addendum"), (2, "Agency/Service provider"), (3, "Award of Contract"), (4, "Bid/Tender"),
    (5, "Bidder"), (6, "Bid documents"), (7, "Bidding Consortium"), (8, "Lead Consortium Member"),
    (9, "Consortium Member"), (10, "BG"), (11, "Commencement period"), (12, "Companies Act"),
    (13, "Contract"), (14, "Contract value"), (15, "Circle office"), (16, "Division office"),
    (17, "Effective date of contract"), (18, "EMD"), (19, "Go-Live"), (20, "Instruction"),
    (21, "Month"), (22, "Notice in Writing"),
]:
    h(f"1.{n}. “{label}”", 11, 3, "1 Definition(s)")
for n, label in [
    (23, "Employer"), (24, "PSU"), (25, "Quarter"), (26, "Services"), (27, "Work Order"),
    (28, "Work"), (29, "Urban Area"), (30, "Zone"), (31, "IT services"), (32, "ERP"),
]:
    h(f"1.{n}. “{label}”", 12, 3, "1 Definition(s)")

h("2 Availability of Bid documents", 12, 2, S3)
h("2.1. The bid document will be uploaded", 12, 3, "2 Availability of Bid documents")
h("3 Invitation of Bids", 12, 2, S3)
h("3.1. The Purchaser is hereby inviting unconditional bids", 12, 3, "3 Invitation of Bids")
h("4 Responsibility of bidder(s)", 12, 2, S3)
h("4.1. The Purchaser will not assume any responsibility", 12, 3, "4 Responsibility of bidder(s)")
h("4.2. Verbal agreement or conversation", 12, 3, "4 Responsibility of bidder(s)")
h("4.3. It shall be the sole responsibility of bidders", 12, 3, "4 Responsibility of bidder(s)")
h("5 Eligibility criteria", 12, 2, S3)
h("5.1. The bidder(s) shall furnish", 12, 3, "5 Eligibility criteria")
h("5.2. Notwithstanding anything stated therein", 12, 3, "5 Eligibility criteria")
h("5.3 The Sole Bidder/Lead Bidder shall be an active Oracle Partner Network", 13, 3, "5 Eligibility criteria")
h("6 Project site visit", 13, 2, S3)
h("6.1. The bidder is advised to visit and examine the site", 13, 3, "6 Project site visit")
h("6.2. The bidder and any of its authorized personnel", 13, 3, "6 Project site visit")
h("7 Process to be Confidential", 13, 2, S3)
h("7.1. This bid document and any other document(s) released", 13, 3, "7 Process to be Confidential")
h("8 Clarifications on bid documents", 13, 2, S3)
h("8.1. The bidders are expected to be fully conversant", 13, 3, "8 Clarifications on bid documents")
h("8.2. The queries shall be accepted only from the accredited", 13, 3, "8 Clarifications on bid documents")
h("8.3. The Purchaser will respond only to those queries", 13, 3, "8 Clarifications on bid documents")
h("8.4. The Purchaser reserves the right to defer/decline", 13, 3, "8 Clarifications on bid documents")
h("8.5. The copies of all the replies/clarifications", 13, 3, "8 Clarifications on bid documents")
h("9 Preparation of Bids", 13, 2, S3)
h("9.1. Bidder shall submit only one (1) unconditional bid", 13, 3, "9 Preparation of Bids")
h("9.2. The transfer of bid document(s) purchased", 14, 3, "9 Preparation of Bids")
h("9.3. The price bid should be duly filled", 14, 3, "9 Preparation of Bids")
h("9.4. All pages of the bid document(s)", 14, 3, "9 Preparation of Bids")
h("9.5. No corrections, even with signature", 14, 3, "9 Preparation of Bids")
h("9.6. Bid documents shall be written legibly", 14, 3, "9 Preparation of Bids")
h("9.7. General information, which is not specifically requested", 14, 3, "9 Preparation of Bids")
h("10 Deviations from terms and conditions of the bid", 14, 2, S3)
h("10.1. The bidder/Agency must upload Annex-II", 14, 3, "10 Deviations from terms and conditions of the bid")
h("11 Bid Forms", 14, 2, S3)
h("11.1. Wherever a specific form or schedule is prescribed", 14, 3, "11 Bid Forms")
h("11.2. For all other cases, the bidder shall design a form", 14, 3, "11 Bid Forms")
h("11.3. Purchaser shall in no case be bound", 14, 3, "11 Bid Forms")
h("12 Conditional Bid", 14, 2, S3)
h("12.1. The bidders shall offer only unconditional bid(s)", 14, 3, "12 Conditional Bid")
h("12.2. Bidder's terms & conditions enclosed", 14, 3, "12 Conditional Bid")
h("13 Deadline for submission of bids", 14, 2, S3)
h("13.1. Bids must be submitted online", 14, 3, "13 Deadline for submission of bids")
h("13.2. The Purchaser may, at its discretion", 14, 3, "13 Deadline for submission of bids")
h("14 Late Bid", 14, 2, S3)
h("14.1. Bid(s) received by the Purchaser after the bid submission", 14, 3, "14 Late Bid")
h("15 Withdrawal of Bids", 15, 2, S3)
h("15.1. The bidder shall not be permitted to withdraw", 15, 3, "15 Withdrawal of Bids")
h("16 Associated cost of bid preparation and submission", 15, 2, S3)
h("16.1. The bidder shall bear all the cost and expenses", 15, 3, "16 Associated cost of bid preparation and submission")
h("16.2. The Purchaser shall in no case be responsible", 15, 3, "16 Associated cost of bid preparation and submission")
h("17 Pre-bid conference", 15, 2, S3)
h("17.1. The bidders may request for clarification", 15, 3, "17 Pre-bid conference")
h("17.2. The Purchaser may modify the bid documents", 15, 3, "17 Pre-bid conference")
h("18 Language of the bid", 15, 2, S3)
h("18.1. The bid prepared by the bidder", 15, 3, "18 Language of the bid")
h("19 Amendment(s) to bid documents", 15, 2, S3)
h("19.1. At any time prior to the deadline for submission", 15, 3, "19 Amendment(s) to bid documents")
h("19.2. The amendment(s) to bid documents shall be binding", 15, 3, "19 Amendment(s) to bid documents")
h("19.3. In a scenario where the interested bidder", 15, 3, "19 Amendment(s) to bid documents")
h("20 Earnest Money Deposit (EMD)", 15, 2, S3)
h("20.1. No offer will be accepted without earnest money deposit", 15, 3, "20 Earnest Money Deposit (EMD)")
h("20.2. Any bid proposal/offer not accompanied by the EMD", 15, 3, "20 Earnest Money Deposit (EMD)")
h("20.3. If on opening of bid any discrepancy in EMD", 15, 3, "20 Earnest Money Deposit (EMD)")
h("20.4. In case of unsuccessful bidders, the EMD", 16, 3, "20 Earnest Money Deposit (EMD)")
h("20.5. If the successful bidder fails to furnish", 16, 3, "20 Earnest Money Deposit (EMD)")
h("20.6. In case the bidder is likely to be awarded", 16, 3, "20 Earnest Money Deposit (EMD)")
h("20.7. The Purchaser reserves the right to forfeit EMD", 16, 3, "20 Earnest Money Deposit (EMD)")
h("20.8. The bank charges (if any) shall be attributable", 16, 3, "20 Earnest Money Deposit (EMD)")
h("20.9. EMD shall be forfeited in case of withdrawal", 16, 3, "20 Earnest Money Deposit (EMD)")
h("20.10 The following are exempted from payment of EMD", 16, 3, "20 Earnest Money Deposit (EMD)")
h("20.11 The EMD may be forfeited", 16, 3, "20 Earnest Money Deposit (EMD)")
h("20.12 EMD shall be returned to the unsuccessful bidders", 17, 3, "20 Earnest Money Deposit (EMD)")
h("21 Documents comprising the bid", 17, 2, S3)
h("21.1. Part-I: Cover-1", 17, 3, "21 Documents comprising the bid")
h("21.2. Part-2: Cover-2", 17, 3, "21 Documents comprising the bid")
h("21.3. Part-3: Cover-3 Price Bid", 17, 3, "21 Documents comprising the bid")
h("22 Submission of bid", 17, 2, S3)
h("22.1. The bidders are requested to ensure that they furnish the Covers 1 and 2", 17, 3, "22 Submission of bid")
h("22.2. The Bidders are requested to ensure that all required schedules", 17, 3, "22 Submission of bid")
h("22.3. Transfer of online purchased documents", 17, 3, "22 Submission of bid")
h("22.4. The submission of a tender by the bidder implies", 17, 3, "22 Submission of bid")
h("22.5. Nobody is authorized to receive or issue receipt", 17, 3, "22 Submission of bid")
h("22.6. Post/Couriered/Telegraphic/Tele Fax/Telex/E-mail bids", 17, 3, "22 Submission of bid")
h("22.7. An authorized representative of the bidder shall initial", 17, 3, "22 Submission of bid")
h("22.8. Bid shall be submitted in the forms attached herein", 17, 3, "22 Submission of bid")
h("22.9. The bidder should quote the prices strictly online", 17, 3, "22 Submission of bid")
h("22.10. No Post Bid development shall be allowed", 17, 3, "22 Submission of bid")
h("22.11. The bid documents include certain statements", 18, 3, "22 Submission of bid")
h("23.1. The Purchaser shall open online bids", 18, 3, "22 Submission of bid")
h("23.2. The bidders' names, bid prices", 18, 3, "22 Submission of bid")
h("23.2.1. Opening of Cover-1", 18, 4, "23.2. The bidders' names, bid prices")
h("23.2.2. Opening of Cover-C- Price bid", 18, 4, "23.2. The bidders' names, bid prices")
h("23.3. In the event, the Purchaser, in its discretion, decides not to open the bid", 18, 3, "22 Submission of bid")
h("23.4. It is to be noted that the bid evaluation", 18, 3, "22 Submission of bid")
h("23.5 It may be noted that the due date/time of opening", 18, 3, "22 Submission of bid")
h("24 Bid Evaluation", 18, 2, S3)
h("24.1. Preliminary Examination", 18, 3, "24 Bid Evaluation")
h("24.1.1. The Purchaser will examine the bids", 18, 4, "24.1. Preliminary Examination")
h("24.1.2. The Purchaser may waive any minor informality", 18, 4, "24.1. Preliminary Examination")
h("24.2. Examination of Substantive responsiveness and Qualification requirement", 19, 3, "24 Bid Evaluation")
h("24.2.1. Prior to the detailed evaluation", 19, 4, "24.2. Examination of Substantive responsiveness and Qualification requirement")
h("24.2.2. During bid evaluation, the Purchaser may", 19, 4, "24.2. Examination of Substantive responsiveness and Qualification requirement")
h("24.2.3. Bids containing deviations from critical provisions", 19, 4, "24.2. Examination of Substantive responsiveness and Qualification requirement")
h("24.2.4. If a bid is not substantially responsive", 19, 4, "24.2. Examination of Substantive responsiveness and Qualification requirement")
h("24.2.5. The Purchaser will ascertain to its satisfaction", 19, 4, "24.2. Examination of Substantive responsiveness and Qualification requirement")
h("24.2.6. The determination will take into account the Bidder's financial", 19, 4, "24.2. Examination of Substantive responsiveness and Qualification requirement")
h("24.2.7. An affirmative determination will be a prerequisite", 19, 4, "24.2. Examination of Substantive responsiveness and Qualification requirement")
h("24.2.8. The bid from those bidders shall not be accepted", 19, 4, "24.2. Examination of Substantive responsiveness and Qualification requirement")
h("24.2.9 In case of consortium partners participating in the bid", 19, 4, "24.2. Examination of Substantive responsiveness and Qualification requirement")
h("24.3. Techno-commercial evaluation", 20, 3, "24 Bid Evaluation")
h("24.3.1. The bidders, whose bids are found to be substantially responsive", 20, 4, "24.3. Techno-commercial evaluation")
h("24.3.2. The Purchaser will carry out a detailed evaluation", 20, 4, "24.3. Techno-commercial evaluation")
h("24.4. Technical Evaluation", 20, 3, "24 Bid Evaluation")
h("24.5. Financial Evaluation", 20, 3, "24 Bid Evaluation")
h("24.5.1. The financial bids shall be opened online", 20, 4, "24.5. Financial Evaluation")
h("24.5.2. A Financial Evaluation Committee (FEC)", 20, 4, "24.5. Financial Evaluation")
h("24.5.3. Financial bids will be evaluated on the basis of total price", 20, 4, "24.5. Financial Evaluation")
h("24.5.4. COMPUTATION OF LOWEST ACCEPTABLE RATE (L-1)", 20, 4, "24.5. Financial Evaluation")
h("24.5.5. Based on the above criteria, the Purchaser will place Work order", 20, 4, "24.5. Financial Evaluation")
h("25 Canvassing of bids", 20, 2, S3)
h("25.1. Bid shall be deemed to be under consideration", 20, 3, "25 Canvassing of bids")
h("26 Purchaser's right to accept any bid, and to reject any or all bids", 21, 2, S3)
h("26.1. The Purchaser reserves the right to accept or reject any bid", 21, 3, "26 Purchaser's right to accept any bid, and to reject any or all bids")
h("27 Currency of Price Bid", 21, 2, S3)
h("27.1. For the works/services required in the bid", 21, 3, "27 Currency of Price Bid")
h("28 Interpretation of conditions of bid(s)", 21, 2, S3)
h("28.1. The Purchaser shall be the final authority", 21, 3, "28 Interpretation of conditions of bid(s)")
h("29 Validity of bids", 21, 2, S3)
h("29.1. The Bidder's proposal must remain valid for acceptance", 21, 3, "29 Validity of bids")
h("29.2. The bid along with the rates and condition thereby", 21, 3, "29 Validity of bids")
h("30 Issue of Letter of Award, Contract Signing and Issue of Work Order", 21, 2, S3)
h("30.1. CGM (Store & Purchase) shall award the Contract", 21, 3, "30 Issue of Letter of Award, Contract Signing and Issue of Work Order")
h("30.2. CGM (Store & Purchase) shall issue a detailed Letter of Award", 21, 3, "30 Issue of Letter of Award, Contract Signing and Issue of Work Order")
h("30.3. Within thirty (30) days from date of issue of letter of award, the successful bidder shall furnish requisite Contract performance", 21, 3, "30 Issue of Letter of Award, Contract Signing and Issue of Work Order")
h("30.4. Within thirty (30) days from date of issue of letter of award, the successful bidder shall register itself as Service Provider", 21, 3, "30 Issue of Letter of Award, Contract Signing and Issue of Work Order")
h("31 Project kick-off/ Effective date of contract", 21, 2, S3)
h("31.1. Effective date of contract shall 30 days", 21, 3, "31 Project kick-off/ Effective date of contract")
h("32 Commercial Bid rejection Criteria", 22, 2, S3)
h("32.1 Commercial bid will be summarily rejected", 22, 3, "32 Commercial Bid rejection Criteria")

# ===== Section-IV: Scope of Work (pages 23-44) =====
S4 = "Section-IV: Scope of Work"
h(S4, 23, 1, None)
h("4.1. Introduction", 23, 2, S4)
h("4.1.1 Existing Oracle License and Parallel Contract Baseline", 23, 3, "4.1. Introduction")
for letter, text in [
    ("a", "For execution and pricing of this Tender"),
    ("b", "CSI No. 19320744 presently represents the Database"),
    ("c", "Oracle application/middleware and other entitlements"),
    ("d", "Existing ERPSI and other existing service arrangements"),
    ("e", "Within the HOTO/Discovery period the SI shall prepare"),
]:
    h(f"{letter}. {text}", 24 if letter != "a" else 23, 4, "4.1.1 Existing Oracle License and Parallel Contract Baseline")
h("4.1.2 Handing Over Taking Over (HOTO)", 25, 3, "4.1. Introduction")
for letter, text, page in [
    ("a", "The SI would understand, analyse and examine", 25),
    ("b", "The SI shall form and depute a transition team", 25),
    ("c", "The SI shall undertake takeover of equipment", 25),
    ("d", "The SI would be provided with the detailed exit management plan", 25),
    ("e", "The SI would perform business process study", 25),
    ("f", "The Transition Period which includes Handing Over", 26),
]:
    h(f"{letter}. {text}", page, 4, "4.1.2 Handing Over Taking Over (HOTO)")

h("4.2 Upgradation / Modernization of all existing modules of Oracle ERP", 26, 2, S4)
_P42 = "4.2 Upgradation / Modernization of all existing modules of Oracle ERP"
for letter, text, page in [
    ("a", "Once the HOTO is done, whole responsibility", 26),
    ("b", "The SI shall be responsible for the Upgradation of Oracle ERP system", 26),
    ("c", "The upgraded system must run on existing License", 26),
    ("d", "With decision to upgrade the Oracle EBS and database", 27),
    ("e", "Timelines for Implementation of these Up-gradation", 27),
    ("f", "Requirement Gathering and preparation / update of requirement document", 27),
    ("g", "Data migration and data entry of all existing data", 27),
    ("h", "Configuration of Production environment and Disaster recovery", 27),
    ("i", "GST module (managed separately) of Oracle ERP", 27),
    ("j", "Implementation approach shall be as per, but not limited to", 27),
    ("k", "Study of existing MPPKVVCL ERP Application", 27),
    ("l", "Deployment of Development Environment at MPPKVVCL premises", 27),
    ("m", "Transfer of Applications files, Database files, Source code", 28),
    ("n", "Development of up-gradation features in all deployed modules", 28),
    ("o", "Restructuring of existing ERP modules", 28),
    ("p", "Rectification of all issues in all the modules", 28),
    ("q", "End to end testing of ERP application", 28),
    ("r", "UAT Testing on testing environments", 28),
    ("s", "Post Successful User Acceptance Testing (UAT)", 28),
    ("t", "Existing ERP Data Migration and Data Entry", 28),
    ("u", "Dry Run / Stabilization Phase", 28),
    ("v", "Go-Live", 28),
    ("w", "Post go-live support to be provisioned", 28),
]:
    h(f"{letter}. {text}", page, 3, _P42)
h("4.2.1 Oracle AI Database 26ai Implementation and OCI Integration/Optimization", 28, 3, _P42)
for letter, text in [
    ("a", "The SI shall design and implement the target database architecture"),
    ("b", "The SI shall assess the existing OCI tenancy"),
    ("c", "The architecture shall avoid unnecessary vendor lock-in"),
    ("d", "The SI shall implement database performance baselines"),
    ("e", "The SI shall submit, before Target Architecture sign-off"),
]:
    h(f"{letter}. {text}", 28, 4, "4.2.1 Oracle AI Database 26ai Implementation and OCI Integration/Optimization")

h("4.3 Operations & Maintenance", 29, 2, S4)
h("4.3.1 Software Application Maintenance", 29, 3, "4.3 Operations & Maintenance")
for letter, text in [
    ("a", "SI shall be required to undertake the Application maintenance"),
    ("b", "The objective of software application maintenance"),
    ("c", "SI shall commit to provide all necessary resources"),
    ("d", "SI shall ensure that the entire solution as a whole is operational"),
    ("e", "SI shall ensure efficient knowledge transfer"),
    ("f", "ERP system shall be integrated with MPPKVVCL other IT systems"),
]:
    h(f"{letter}. {text}", 29, 4, "4.3.1 Software Application Maintenance")
h("4.3.2 End User Support", 30, 3, "4.3 Operations & Maintenance")
for letter, text in [
    ("a", "Use the defect logging system available with the Purchaser"),
    ("b", "Track each incident / call to resolution"),
    ("c", "SI shall document defects / bugs encountered"),
    ("d", "Escalate the calls to the appropriate levels"),
    ("e", "Coordinate with respective OEM for closure of calls"),
    ("f", "Analyze the incident / call statistics"),
    ("g", "The End User and Technical Support shall be available"),
]:
    h(f"{letter}. {text}", 30, 4, "4.3.2 End User Support")
h("4.3.3 Configuration Management and Version Control", 30, 3, "4.3 Operations & Maintenance")
for letter, text in [
    ("a", "As the application undergoes enhancements and modifications"),
    ("b", "With the objective of keeping track of the version updates"),
    ("c", "SI shall have to ensure that the production application"),
]:
    h(f"{letter}. {text}", 30, 4, "4.3.3 Configuration Management and Version Control")
h("4.3.4 Release Management", 30, 3, "4.3 Operations & Maintenance")
h("a. Release management procedure should be defined", 30, 4, "4.3.4 Release Management")
h("b. As part of the release management SI should perform", 30, 4, "4.3.4 Release Management")
h("4.3.5 AMC Administration", 31, 3, "4.3 Operations & Maintenance")
h("a. The SI shall obtain all necessary details for AMC", 31, 4, "4.3.5 AMC Administration")
h("b. SI shall track the Annual Maintenance Contracts", 31, 4, "4.3.5 AMC Administration")
h("4.3.6 MIS Reports and Incident Reporting", 31, 3, "4.3 Operations & Maintenance")

h("4.4 Application Enhancement / New Development", 32, 2, S4)
_P44 = "4.4 Application Enhancement / New Development"
for letter, text, page in [
    ("a", "The Application may require modifications and enhancements", 32),
    ("b", "Any kind of development/enhancement within existing modules", 32),
    ("c", "Oracle EBS 12.2.15 has additional built-in features", 32),
    ("d", "There are a number of Smartphone Apps", 32),
    ("e", "All development tools / licenses to develop APIs", 33),
    ("f", "MPPKVVCL might ask SI to redesign existing Oracle EBS Forms", 33),
    ("g", "SI should ensure that correct version of the application", 33),
    ("h", "SI should follow the configuration management plan", 33),
    ("i", "SI shall support MPPKVVCL in carrying out the UAT", 33),
    ("j", "whenever bug fixing/application enhancement or new development", 33),
]:
    h(f"{letter}. {text}", page, 3, _P44)

h("4.5 Migration of Existing GIS Applications to Oracle Platform", 33, 2, S4)
_P45 = "4.5 Migration of Existing GIS Applications to Oracle Platform"
for letter, text, page in [
    ("a", "The SI shall study, document, migrate/redevelop", 33),
    ("b", "The scope shall cover the complete existing GIS workflow", 33),
    ("c", "Existing GeoServer or other GIS middleware may be retained", 34),
    ("d", "The SI shall migrate all required spatial and non-spatial data", 34),
    ("e", "A GIS Migration Reconciliation Report shall be mandatory", 34),
    ("f", "The target GIS application shall support role-based access", 34),
    ("g", "The SI shall define archival and performance strategies", 34),
]:
    h(f"{letter}. {text}", page, 3, _P45)
h("4.5.1 Enterprise Integration and API Architecture", 34, 3, _P45)
for letter, text in [
    ("a", "The SI shall prepare and maintain an covering EBS, GIS"),
    ("b", "For each interface, the catalogue shall capture"),
    ("c", "New integrations shall preferably use secure REST APIs"),
    ("d", "The SI shall provide reusable API documentation"),
]:
    h(f"{letter}. {text}", 34, 4, "4.5.1 Enterprise Integration and API Architecture")
h("4.5.2 Enterprise AI, GenAI and Management Analytics", 34, 3, _P45)
for letter, text, page in [
    ("a", "The SI shall implement secure enterprise AI", 34),
    ("b", "Priority production use cases shall cover Projects", 34),
    ("c", "The solution may use approved Oracle AI Database 26ai capabilities", 34),
    ("d", "AI access shall honor the authorization and data-access privileges", 35),
    ("e", "No AI-generated recommendation shall directly create, modify or approve", 35),
    ("f", "The SI shall define measurable acceptance criteria for each AI use case", 35),
]:
    h(f"{letter}. {text}", page, 4, "4.5.2 Enterprise AI, GenAI and Management Analytics")
h("4.5.3 Cybersecurity, DR and DevSecOps for ERP/GIS/AI Platform", 35, 3, _P45)
for letter, text in [
    ("a", "The SI shall implement security-by-design for EBS"),
    ("b", "Secure coding and release controls shall include code review"),
    ("c", "Vulnerability assessment and penetration testing shall be conducted"),
    ("d", "The SI shall comply with applicable cyber-security directions"),
    ("e", "Purchaser data shall reside or be processed only in infrastructure"),
    ("f", "The SI shall maintain and test backup/restore, DR"),
]:
    h(f"{letter}. {text}", 35, 4, "4.5.3 Cybersecurity, DR and DevSecOps for ERP/GIS/AI Platform")
h("4.5.4 Parallel Contract / Licence Dependency Management and No-Duplication Rule", 35, 3, _P45)
for letter, text, page in [
    ("a", "The SI acknowledges that this Contract will operate concurrently", 35),
    ("b", "The SI shall not refuse end-to-end diagnosis", 35),
    ("c", "Expiry, termination, reduction or change of any parallel contract", 36),
    ("d", "No duplicate commercial claim shall be admissible", 36),
]:
    h(f"{letter}. {text}", page, 4, "4.5.4 Parallel Contract / Licence Dependency Management and No-Duplication Rule")
h("Implementation of Oracle Security Central, Audit & ERP Monitoring", 36, 2, S4)
_P_SEC = "Implementation of Oracle Security Central, Audit & ERP Monitoring"
h("Oracle Security Central and Centralized Audit Management:", 36, 3, _P_SEC)
h("Transaction-Level Audit, Monitoring and Compliance:", 36, 3, _P_SEC)
h("Implementation, Testing, Documentation and Knowledge Transfer:", 36, 3, _P_SEC)
# NOTE: p37's leading paragraph ("For your new ERP SI tender, I would recommend...")
# is a leaked editorial/LLM-style comment, not genuine tender content -- deliberately
# excluded as noise, not a heading.

h("4.6 Manpower Requirement", 37, 2, S4)
h("4.1 Manpower Requirement for ERP Application", 37, 3, "4.6 Manpower Requirement")
h("4.2 Manpower Requirement for GIS and Other IT Application(s)", 39, 3, "4.6 Manpower Requirement")
h("4.6.1 Manpower Deployment", 42, 2, S4)
for letter, text in [
    ("a", "The SI shall deploy dedicated manpower onsite"),
    ("b", "The SI shall not be permitted to change any resource"),
    ("c", "SI shall make their own judgement regarding the manpower"),
    ("d", "Replacement of Resources: The SI shall intimate MPPKVVCL"),
    ("e", "Withdrawal of Resources on directions from MPPKVVCL"),
    ("f", "Knowledge transfer on change of resources"),
]:
    h(f"{letter}. {text}", 42, 3, "4.6.1 Manpower Deployment")

h("4.7 Training of MPPKVVCL, Jabalpur Users", 42, 2, S4)
for letter, text, page in [
    ("a", "Technical Training: The SI shall provide structured hands-on", 42),
    ("b", "Functional Training: This training shall be provided", 43),
    ("c", "End Users Training: SI shall provide assistance", 43),
    ("d", "Knowledge Transfer Acceptance: Before final KT sign-off", 43),
    ("e", "Training shall include architecture and source-code walkthroughs", 43),
]:
    h(f"{letter}. {text}", page, 3, "4.7 Training of MPPKVVCL, Jabalpur Users")

h("4.8 Well Written Documentation for Use by Users and Technical Staff", 43, 2, S4)
for letter, text in [
    ("a", "The SI shall be responsible for preparation/updation of Training materials"),
    ("b", "All functional and technical documents as per industry standards"),
    ("c", "Mandatory documentation shall additionally include As-Is/To-Be"),
    ("d", "All documentation, source code, scripts, configuration exports"),
]:
    h(f"{letter}. {text}", 43, 3, "4.8 Well Written Documentation for Use by Users and Technical Staff")

h("4.9 New Development / Enhancements", 43, 2, S4)
h("Step 1: Submission of requirement for new development", 43, 3, "4.9 New Development / Enhancements")
h("Step 2: Decision from Nodal officer about the feasibility", 43, 3, "4.9 New Development / Enhancements")
h("Step 3: In case of feasible development/enhancement", 44, 3, "4.9 New Development / Enhancements")
h("Step 4: On the basis of finalised MoM by Nodal officer", 44, 3, "4.9 New Development / Enhancements")
h("Step 5: SI needs to submit a certificate against development work", 44, 3, "4.9 New Development / Enhancements")

# NOTE: the source document repeats a second, independently-numbered
# "4.3 Upgradation..." restatement on pages 44-45 (its own numbering clashes
# with the earlier 4.3 Operations/4.3.x sequence on pages 29-31) -- genuine
# document content, not an OCR artifact (distinct wording/order from the
# 4.2 a-w list), so it is captured as its own sibling under Section-IV.
_P43B = "4.3 Upgradation of all existing modules of Oracle ERP"
h(_P43B, 44, 2, S4)
for letter, text, page in [
    ("a", "Once the HOTO is done, whole responsibility for Oracle EBS version upgrade shall be on the successful Bidder, with no dependency", 44),
    ("b", "The SI shall be responsible for the Upgradation of Oracle ERP system as per functionalities listed in but not limited to Annexure-XIII.", 44),
    ("c", "With decision to upgrade the Oracle EBS and database, it is required to organise an approach", 44),
    ("d", "Timelines for Implementation of these modernization of Oracle ERP shall be as per Section VI", 44),
    ("e", "Requirement Gathering and preparation / update of requirement document SRS, FRS detailing structure", 44),
    ("f", "Data migration and data entry of all existing data of all existing modules.", 44),
    ("g", "Configuration of Production environment and Disaster recovery environment to ensure business continuity.", 44),
    ("h", "GST module of Oracle ERP is already implemented. This must work properly after ERP Modernization also.", 44),
    ("i", "Implementation approach shall be as per, but not limited to, below points.", 44),
    ("j", "Study of existing MPPKVVCL ERP Application", 44),
    ("k", "Deployment of Development Environment at MPPKVVCL premises/ Cloud, including provisioning of software as per scope of work (Details of existing ERP infrastructure is mentioned in Annexure-XII)", 45),
    ("l", "Transfer of Applications files, Database files, Source code, etc. of existing MPPKVVCL ERP application to Development Environment", 45),
    ("m", "Development of MODERNIZATION features in all deployed modules in ERP.", 45),
    ("n", "Restructuring of existing ERP modules to ensure working of all functionality and data sanctity in ERP.", 45),
    ("o", "Rectification of all issues in all the modules.", 45),
    ("p", "End to end testing of ERP application, including, but not limited to Unit Testing, Security Testing and Integration Testing.", 45),
    ("q", "UAT Testing on testing environments.", 45),
    ("r", "Post Successful User Acceptance Testing (UAT), deployment of updated source code on Production Environment.", 45),
    ("s", "Existing ERP Data Migration and Data Entry.", 45),
    ("t", "Dry Run / Stabilization Phase.", 45),
    ("u", "Go-Live", 45),
    ("v", "Post go-live support to be provisioned so that any issues arising post go-live should be addressed on priority.", 45),
]:
    h(f"{letter}. {text}", page, 3, _P43B)

h("4.10 Acceptance, Reconciliation and Go-Live Exit Criteria", 45, 2, S4)
for letter, text in [
    ("a", "Each major workstream - EBS/Database, GIS/APEX, integrations"),
    ("b", "EBS/Database acceptance shall include successful business regression"),
    ("c", "GIS migration acceptance shall require approved record-count"),
    ("d", "Integration acceptance shall require end-to-end transaction testing"),
    ("e", "AI use-case acceptance shall use agreed evaluation datasets"),
    ("f", "Go-Live shall be followed by an agreed stabilization period"),
]:
    h(f"{letter}. {text}", 45, 3, "4.10 Acceptance, Reconciliation and Go-Live Exit Criteria")
h("4.11 Source Code, Configuration and Exit Readiness", 45, 2, S4)
for letter, text in [
    ("a", "All Purchaser-specific source code, APEX application exports"),
    ("b", "The SI shall ensure that the Purchaser can build, deploy"),
    ("c", "At contract exit or change of SI, the incumbent SI shall provide"),
]:
    h(f"{letter}. {text}", 45, 3, "4.11 Source Code, Configuration and Exit Readiness")

# ===== Section-V: General Conditions of Contract (GCC) (pages 46-51) =====
S5 = "Section-V: General Conditions of Contract (GCC)"
h(S5, 46, 1, None)
_gcc_clauses = [
    (1, "Purchaser's Representative", [
        (1.1, "Purchaser shall appoint controlling Officer/Officer-in-charge"),
        (1.2, "Any decision, instruction or approval given by the Controlling Officer"),
        (1.3, "All notices, instructions, orders, consents, certificates"),
        (1.4, "The Controlling Officer/ Officer-in-charge may authorize his representative"),
    ], 46),
    (2, "Agency's Representative", [
        (2.1, "The Agency shall employ at least one competent representative"),
        (2.2, "The Controlling Officer/officer-in-charge shall be at liberty to object"),
    ], 46),
    (3, "Bankruptcy/Insolvency of the Agency", [
        (3.1, "If the Agency becomes insolvent, compound with his creditors"),
        (3.2, "To give such liquidate, received, or other person"),
    ], 46),
    (4, "Duty and Behavior of Agency's Personnel", [
        (4.1, "The personnel deployed by the Agency must always carry their Photo-ID"),
        (4.2, "The personnel deployed by the Agency shall maintain the decorum"),
        (4.3, "It shall be accepted as an inseparable part of the Contract"),
    ], 46),
    (5, "Checks and Supervision by the Agency", [
        (5.1, "The Agency shall ensure that its personnel perform their duties efficiently"),
    ], 47),
    (6, "Notices and Instructions", [
        (6.1, "The Agency shall furnish the complete address of its permanent office"),
    ], 47),
    (7, "Health of the Personnel deployed", [
        (7.1, "All persons deployed by the Agency shall be of sound physical and mental health"),
    ], 47),
    (8, "Issue of Photo-ID", [
        (8.1, "Each deputed person shall carry Photo-ID card issued by the Agency"),
    ], 47),
    (9, "Force Majeure", [
        (9.1, "Force Majeure means any event or circumstance or combination"),
        (9.2, "Act of God, including, but not limited to lightning, drought, fire"),
        (9.3, "A notification to this effect duly certified by statutory authorities"),
        (9.4, "The authority as decided by the Purchaser (including committee, etc.) shall decide"),
    ], 47),
    (10, "Damage to property and injury to personnel", [
        (10.1, "The Agency shall indemnify and keep indemnified the Purchaser"),
        (10.2, "The Purchaser shall not be liable for damage or compensation"),
        (10.3, "In the event of any accident and/or injury"),
        (10.4, "In case it is found that any theft or damage has occurred"),
        (10.5, "In case any personnel of the Agency is implicated in any law suit"),
        (10.6, "The Purchaser shall be deemed to be indemnified by the Agency"),
        (10.7, "Claims arising due to “any activity” shall be liable for adjustment"),
    ], 47),
    (11, "Settlement of Disputes and Arbitration", [
        (11.1, "If any dispute or difference of any kind whatsoever arises"),
        (11.2, "The arbitration proceedings shall be conducted in accordance with the Madhya Pradesh"),
        (11.3, "Notwithstanding any disputes with reference to the Contract pending for arbitration"),
    ], 48),
    (12, "Jurisdiction", [
        (12.1, "Any dispute or difference, arising under, out of, or in connection with Tender/ Contract"),
    ], 48),
    (13, "Blacklisting", [
        (13.1, "In case(s) of severe default(s) by the Agency"),
    ], 48),
    (14, "Necessary Compliances (applicable in accordance with Eligibility Criteria)", [
        (14.1, "The Agency shall provide and be responsible for payment of wages"),
        (14.2, "The Agency shall ensure all its employees are covered under national Accidental Insurance"),
        (14.3, "All personnel engaged under this Contract by Agency shall be employees"),
        (14.4, "The Agency shall at the time of execution of the Contract have PF code number"),
        (14.5, "The Agency shall maintain all records/registers"),
        (14.6, "The Agency shall also submit periodical reports on various labour laws"),
        (14.7, "The Agency shall pay the Minimum Wages (as applicable)"),
        (14.8, "The Agency shall provide their prescribed uniforms, identity card"),
        (14.9, "The Agency shall in the event of his workman / employees sustain any injury"),
        ("14.10", "If any of the persons engaged by the Agency misbehaves"),
        ("14.11", "The Agency shall ensure that necessary information regarding the persons engaged"),
        ("14.12", "The Agency should comply with all the applicable laws in force"),
        ("14.13", "Agency shall furnish indemnity bond at time of signing of Contract Agreement"),
        ("14.14", "Manpower deployed by the Agency shall be entitled to 15 days leave"),
    ], 49),
    (15, "Taxes", [
        (15.1, "The Agency shall be responsible for payment of GST & other Statutory dues."),
    ], 50),
    (16, "Patent Indemnity", [
        (16.1, "The bidder shall indemnify and hold harmless the Purchaser"),
    ], 50),
    (17, "Conflict of Interest", [
        (17.1, "Bidder shall not have a conflict of interest with one or more parties"),
        (17.2, "Have controlling share holders as his / her family members"),
        (17.3, "Have a relationship with each other, directly or through common third parties"),
        (17.4, "Has a close business or family relationship"),
    ], 50),
    (18, "Corrupt or Fraudulent Practice(s)", [
        (18.1, "The Purchaser requires that the bidders observe the highest standard of ethics"),
        (18.2, "In pursuance of this policy, the Purchaser defines"),
        (18.3, "“Corrupt practice” means the offering, giving, receiving or soliciting"),
        (18.4, "“Fraudulent practice” means misinterpretation of facts"),
        (18.5, "The Purchaser will reject a bid or cancel the Contract if already placed"),
        (18.6, "The Purchaser may declare a bidder ineligible for issue of Contract/Award"),
        (18.7, "Any act of fraudulence, which can be purported or being attempted"),
    ], 50),
    (19, "Cancellation/termination of order", [
        (19.1, "The Purchaser may upon written notice of default, terminate/cancel work order"),
    ], 51),
    (20, "Correspondence", [
        (20.1, "Copies of all-important correspondence on subject should be sent"),
    ], 51),
]
for num, title, subs, page in _gcc_clauses:
    heading_text = f"{num} {title}"
    h(heading_text, page, 2, S5)
    for sub_num, sub_text in subs:
        h(f"{sub_num}. {sub_text}", page, 3, heading_text)

# ===== Section-VI: Special Conditions of Contract (SCC) (pages 52-...) =====
S6 = "Section-VI: Special Conditions of Contract (SCC)"
h(S6, 52, 1, None)
h("1.1. Period of Contract", 52, 2, S6)
for sub, text, page in [
    ("1.1.1.", "The contract period shall include initial two (2) weeks of resource deployment", 52),
    ("1.1.2.", "However, in case the performance during the currency of the Contract", 52),
    ("1.1.3.", "Total initial period of contract shall not be more than the period specified", 52),
    ("1.1.4.", "The SI shall maintain service continuity during the modernization period", 52),
]:
    h(f"{sub} {text}", page, 3, "1.1. Period of Contract")
h("1.2. Quoting of Rates", 52, 2, S6)
for sub, text in [
    ("1.2.1.", "The Agency shall quote its rates/prices as per the Price Schedule"),
    ("1.2.2.", "OEM licenses/subscriptions, Oracle support, OCI/cloud consumption"),
    ("1.2.3.", "Existing Oracle license/support items under CSI Nos. 18310212"),
]:
    h(f"{sub} {text}", 52, 3, "1.2. Quoting of Rates")
h("1.3. Submission of Bills & Payment Procedure", 52, 2, S6)
for sub, text, page in [
    ("1.3.1.", "Four copies of each invoice along with the respective documents", 52),
    ("1.3.2.", "Nodal Officer shall issue acceptance certificates to the SI", 53),
    ("1.3.3.", "All payments shall be made after adjusting penalty amount payable", 53),
    ("1.3.4.", "Taxes, duties, levies etc. will be paid as per prevailing rate", 53),
    ("1.3.5.", "The Purchaser shall make above payment directly in the SI's Bank account", 53),
    ("1.3.6.", "Charges required by Bank in this regard will be debited to SI's Account", 53),
]:
    h(f"{sub} {text}", page, 3, "1.3. Submission of Bills & Payment Procedure")
h("1.4. Project Schedule", 53, 2, S6)
for sub, text in [
    ("1.4.1.", "The indicative modernization schedule for the new tender shall be as below"),
    ("1.4.2.", "The Bidder shall deploy its required resources in accordance with the approved schedule"),
    ("1.4.3.", "Milestone dates shall be finalized after HOTO/Discovery"),
]:
    h(f"{sub} {text}", 54, 3, "1.4. Project Schedule")
h("1.5. Payment Terms", 54, 2, S6)
h("1.5.1. The first O&M payment after HOTO shall be subject to HOTO acceptance", 56, 3, "1.5. Payment Terms")
h("1.5.2. Price Escalation", 56, 3, "1.5. Payment Terms")
h("1.6. Contract Performance Guarantee (CPG)", 56, 2, S6)
for sub, text, page in [
    ("1.6.1.", "The Agency shall furnish Bank Guarantee", 56),
    ("1.6.2.", "Within thirty (30) days from date of issue of letter of award, the successful bidder shall furnish requisite Contract performance guarantee and execute an agreement with the Controlling Officer", 56),
    ("1.6.3.", "Failure by the successful bidder to furnish the prescribed Contract performance guarantee", 57),
    ("1.6.4.", "The Contract performance guarantee shall only be released", 57),
]:
    h(f"{sub} {text}", page, 3, "1.6. Contract Performance Guarantee (CPG)")
h("1.7. Termination of Contract", 57, 2, S6)
h("1.7.1. Event of Default", 57, 3, "1.7. Termination of Contract")
h("1.7.2. If the Agency fails to start the works/services on specified date", 57, 3, "1.7. Termination of Contract")
h("1.7.3. If at any stage during the period of the Contract any case involving moral turpitude", 57, 3, "1.7. Termination of Contract")
h("1.7.4. Material default shall also include", 57, 3, "1.7. Termination of Contract")
h("1.8. Termination upon occurrence of Event of Default", 57, 2, S6)
h("1.8.1. In case of occurrence of Event of default", 57, 3, "1.8. Termination upon occurrence of Event of Default")
h("1.8.2. In case of non-mitigation, non-responsiveness", 57, 3, "1.8. Termination upon occurrence of Event of Default")
h("1.8.3. For a critical cyber incident, data leakage", 57, 3, "1.8. Termination upon occurrence of Event of Default")
h("1.8.4. Obligations on Termination", 57, 3, "1.8. Termination upon occurrence of Event of Default")
h("1.9. Termination for convenience", 58, 2, S6)
h("1.9.1. The Purchaser shall be entitled to terminate the Contract without assigning any reason", 58, 3, "1.9. Termination for convenience")
h("1.9.2. The Agency shall continue essential services and transition support", 58, 3, "1.9. Termination for convenience")
h("1.9.3. In the event of termination under Clause 6.9.1", 58, 3, "1.9. Termination for convenience")
h("1.10. Effect of Termination", 58, 2, S6)
h("1.11. Confidentiality of data or information", 58, 2, S6)
for sub, text, page in [
    ("1.11.1.", "It is a condition of this Bid that the information provided herein", 58),
    ("1.11.2.", "Information relating to examination, clarification, evaluation", 58),
    ("1.11.3.", "Successful bidder shall be required to sign an undertaking", 58),
    ("1.11.4.", "Agency shall maintain full confidentiality of data", 58),
    ("1.11.5.", "8.5 All access to Purchaser data shall be role-based", 58),
    ("1.11.6.", "No Purchaser data, prompts, documents, embeddings, vectors", 58),
    ("1.11.7.", "The Agency shall notify Purchaser immediately upon becoming aware", 58),
    ("1.11.8.", "Confidentiality, data-protection and data-return/deletion obligations shall survive", 59),
    ("1.11.9.", "Any violation of confidentiality may attract financial penalty", 59),
]:
    h(f"{sub} {text}", page, 3, "1.11. Confidentiality of data or information")
h("1.12. Acceptance or Rejection of bid/offer", 59, 2, S6)
h("1.12.1. The Purchaser reserves the right to accept or reject any or all bids", 59, 3, "1.12. Acceptance or Rejection of bid/offer")
h("1.12.2. Any Agency having an adverse performance/default history", 59, 3, "1.12. Acceptance or Rejection of bid/offer")
h("1.13. Subletting / Subcontracting", 59, 2, S6)
h("1.13.1. Subletting of work shall not be allowed under any circumstances", 59, 3, "1.13. Subletting / Subcontracting")
h("1.13.2. Any subcontractor/sub-processor requiring access to Purchaser data", 59, 3, "1.13. Subletting / Subcontracting")
h("1.14. Limitation of Liability", 59, 2, S6)
h("1.14.1. Except in cases of gross negligence or willful misconduct", 59, 3, "1.14. Limitation of Liability")
h("1.14.2. Indirect liabilities and consequential damages", 59, 3, "1.14. Limitation of Liability")
h("1.15.Cybersecurity, Regulatory and Audit Obligations", 59, 2, S6)
for sub, text, page in [
    ("1.15.1.", "Agency shall comply with Purchaser security policies", 59),
    ("1.15.2.", "Agency shall support VAPT, security audit, configuration review", 60),
    ("1.15.3.", "Purchaser/auditor shall have reasonable rights to audit service records", 60),
    ("1.15.4.", "Critical vulnerabilities or exposures presenting material business risk", 60),
]:
    h(f"{sub} {text}", page, 3, "1.15.Cybersecurity, Regulatory and Audit Obligations")
h("1.16. Intellectual Property, Source Code and Configuration Ownership", 60, 2, S6)
for sub, text in [
    ("1.16.1.", "All Purchaser-specific source code (as applicable), APEX applications/exports"),
    ("1.16.2.", "Pre-existing bidder/OEM intellectual property shall remain with its owner"),
    ("1.16.3.", "No technical lock-in, undisclosed dependency, encrypted source/configuration"),
]:
    h(f"{sub} {text}", 60, 3, "1.16. Intellectual Property, Source Code and Configuration Ownership")
h("1.17. Cloud, OCI and Account/Tenancy Control", 60, 2, S6)
for sub, text in [
    ("1.17.1.", "Where cloud services are used, production tenancy/account/subscription"),
    ("1.17.2.", "Agency shall not create production resources in a personal/vendor-owned cloud account"),
    ("1.17.3.", "Cloud architecture changes affecting cost, security, residency"),
    ("1.17.4.", "Agency shall provide monthly utilization/cost/FinOps reporting"),
]:
    h(f"{sub} {text}", 60, 3, "1.17. Cloud, OCI and Account/Tenancy Control")
h("1.18. Parallel Contracts, Existing Licenses and Interface Responsibility", 60, 2, S6)
for sub, text, page in [
    ("1.18.1.", "The Agency shall perform the Contract with full knowledge that Oracle licence/support arrangements", 60),
    ("1.18.2.", "The Agency shall prepare and maintain the approved Four-CSI + Parallel-Contract", 60),
    ("1.18.3.", "The Agency shall provide technical information and reasonable assistance required by the separate CSI", 60),
    ("1.18.4.", "Expiry or termination of a parallel contract shall not constitute an automatic extension", 61),
]:
    h(f"{sub} {text}", page, 3, "1.18. Parallel Contracts, Existing Licenses and Interface Responsibility")
h("1.19. Enterprise AI / GenAI Specific Conditions", 61, 2, S6)
for sub, text in [
    ("1.19.1.", "Only Purchaser-approved AI models/services and data sources"),
    ("1.19.2.", "AI access shall respect the underlying authorization"),
    ("1.19.3.", "AI-generated recommendations shall not directly create, approve or post financial"),
    ("1.19.4.", "Existing OCI GPU resources shall be assessed and used"),
    ("1.19.5.", "Prompt, output, embedding/vector and model-data retention"),
]:
    h(f"{sub} {text}", 61, 3, "1.19. Enterprise AI / GenAI Specific Conditions")
h("1.20. Change Control and Technology Currency", 61, 2, S6)
for sub, text in [
    ("1.20.1.", "All material scope, architecture, production configuration"),
    ("1.20.2.", "Where these tender references a product/release"),
    ("1.20.3.", "Agency shall maintain an annual technology roadmap"),
]:
    h(f"{sub} {text}", 61, 3, "1.20. Change Control and Technology Currency")
h("1.21. Exit Management and Transition Assistance", 61, 2, S6)
for sub, text in [
    ("1.21.1.", "Agency shall submit an Exit Management Plan 90 days prior to the Contract expiry"),
    ("1.21.2.", "At least 90 days before normal expiry, or immediately upon termination notice"),
    ("1.21.3.", "Exit deliverables shall include complete inventory/CMDB"),
    ("1.21.4.", "Final payment and formal exit acceptance shall be subject to completion"),
]:
    h(f"{sub} {text}", 61, 3, "1.21. Exit Management and Transition Assistance")

# ===== Section VII: Annexures (pages 63-135) =====
# Mostly blank proforma/forms to be filled by the bidder -- given light
# structure (field labels, not real sub-headings), each Annexure's own title
# is the real navigable unit; finer nesting is only added where an Annexure
# contains genuine substantive sub-clauses (e.g. Annexure-IX, XI).
S7 = "Section VII: Annexures"
h(S7, 63, 1, None)
h("Annex-I: Performa for Bid Form", 63, 2, S7)
h("Annex-II: Performa for \u201cNo Deviations\u201d from Terms and Conditions of Bid Document", 64, 2, S7)
h("Annex-III: Performa for Undertaking", 65, 2, S7)
h("(A) Regarding Conflicting Interest:-", 65, 3, "Annex-III: Performa for Undertaking")
h("(B) \u201cRegarding Black listing/Debarring of the firm.\u201d", 65, 3, "Annex-III: Performa for Undertaking")
h("(C) \u201cRegarding information/details given\u201d", 65, 3, "Annex-III: Performa for Undertaking")
h("Annexure IV: Format for Price Schedule", 66, 2, S7)
h("Item-I : Cost for Oracle ERP Version Upgrade", 66, 3, "Annexure IV: Format for Price Schedule")
h("Item II - Cost for Operations and Maintenance for five (5) Years", 66, 3, "Annexure IV: Format for Price Schedule")
h("Item III \u2013 Cost for GIS Migration/Redevelopment to Oracle Apex, Spatial & ORDS", 67, 3, "Annexure IV: Format for Price Schedule")
h("Item IV \u2013 Cost for GIS Operations & Maintenance for four (4) Years", 67, 3, "Annexure IV: Format for Price Schedule")
h("Note:", 68, 3, "Annexure IV: Format for Price Schedule")
for n, text in [
    (1, "Successful bidder (L1) will be declared on the basis of grand total amount."),
    (2, "Grand total amount should be same in both words & figures."),
    (3, "The total amount quoted against Line Item 1 & Item 3 (Oracle ERP Version Upgrade)"),
    (4, "It may be noted that Payment for services to be performed under this Contract"),
    (5, "GST as applicable shall be paid extra."),
    (6, "The financial evaluation shall be done as per procedure prescribed in Section III"),
    (7, "Only blank fields are mandatory to be filled."),
    (8, "In case of discrepancy in the price quoted by the Bidder"),
    (9, "The UNIT of measurement lump sum (LS) for quoting rate of the item specified"),
    (10, "It is to certify that the relevant portion of Input Tax Credit"),
    (11, "If user wants to enter any decimal value than please enter as per given example"),
]:
    h(f"{n}. {text}", 68, 4, "Note:")
h("Annex-V: Performa for Conflict of Interest", 69, 2, S7)
h("Annex-VI: Performa for Blacklisting", 70, 2, S7)
h("Annex-VII: Performa for Pending Litigation", 71, 2, S7)
h("Annex VIII (a): Performa for Bank Guarantee (For Contract Performance)", 72, 2, S7)

h("Bank Guarantee", 72, 3, "Annex VIII (a): Performa for Bank Guarantee (For Contract Performance)")
h("Annex-VIII (b) : Bank Guarantee checklist", 74, 2, S7)
h("Annexure IX: Guidelines for E-Procurement System", 75, 2, S7)
h("Annexure X: Pre-bid Query format", 76, 2, S7)

h("Annexure XI: Service Level Agreement", 77, 2, S7)
_SLA = "Annexure XI: Service Level Agreement"
h("a. Purpose of this Agreement", 77, 3, _SLA)
h("b. Description of Services Provided", 77, 3, _SLA)
h("c. Duration of SLA", 77, 3, _SLA)
h("d. Service Level Agreements and Targets", 77, 3, _SLA)
h("e. General principle of Service Level Agreements", 77, 3, _SLA)
h("I. Project Management", 77, 4, "e. General principle of Service Level Agreements")
h("II. Availability Management", 78, 4, "e. General principle of Service Level Agreements")
h("III. Service Management", 79, 4, "e. General principle of Service Level Agreements")
h("IV. Application Performance Management", 80, 4, "e. General principle of Service Level Agreements")
h("Note:", 81, 4, "e. General principle of Service Level Agreements")
h("f. Breach of SLA", 81, 3, _SLA)
h("g. Monitoring & Auditing", 81, 3, _SLA)
h("h. Reporting Procedure", 81, 3, _SLA)
h("i. Issue Management Procedures", 81, 3, _SLA)

h("• Issue Management Process", 82, 4, "i. Issue Management Procedures")
h("j. SLA Change Control", 82, 3, _SLA)
h("a) General", 82, 4, "j. SLA Change Control")
h("b) SLA Change Process", 82, 4, "j. SLA Change Control")
h("c) Version Control", 82, 4, "j. SLA Change Control")
h("d) Management Escalation Procedures", 82, 4, "j. SLA Change Control")

h("Annexure-XII: List of Software Oracle ERP licenses available under MPPKVVCL, Jabalpur", 83, 2, S7)
h("NEW_Annexure XII-A: Minimum Parallel Contract / Responsibility Matrix", 84, 2, S7)

# Annexure-XIII is a dense module-by-module ERP feature catalogue
# (Process name/Function/Seeded/Customization data tables, pages 85-~120+).
# Per the established noise-pruning precedent, individual catalogue rows are
# data-table content, not navigable headings -- only each module's own
# section header is captured.
h("Annexure-XIII \u2013 ERP Modules implemented in MPPKVVCL", 85, 2, S7)
_A13 = "Annexure-XIII \u2013 ERP Modules implemented in MPPKVVCL"
h("Human Resources", 85, 3, _A13)
h("Payroll", 91, 3, _A13)
h("Pension", 95, 3, _A13)
h("Oracle Learning Management (Under HRMS)", 96, 3, _A13)
h("Projects", 99, 3, _A13)

h("Accounts Payable (Finance)", 104, 3, _A13)
h("General Ledger (Finance)", 108, 3, _A13)
h("Fixed Assets (Finance)", 109, 3, _A13)
h("Treasury (Finance)", 109, 3, _A13)
h("Cash Management (Finance)", 110, 3, _A13)
h("Audit", 111, 3, _A13)
h("Sourcing (Supply Chain Management)", 112, 3, _A13)
h("Purchase (Supply Chain Management)", 112, 3, _A13)
h("Inventory and Order Management (Supply Chain Management)", 116, 3, _A13)

h("Annexure XIV: CV Format (At most 2 pages)", 121, 2, S7)

h("Annexure XV: List of Major Integrations & Customizations", 122, 2, S7)
_A15 = "Annexure XV: List of Major Integrations & Customizations"
h("Finance", 122, 3, _A15)
h("Meter Reader Invoicing:", 122, 4, "Finance")
h("Employee Asset Tracking:", 122, 4, "Finance")
h("Bank EFT generation:", 122, 4, "Finance")
h("Supplier Deductions:", 122, 4, "Finance")
h("Supplier Deposits:", 122, 4, "Finance")
h("GST e-Invoicing for B2B and B2C:", 122, 4, "Finance")
h("Customer Management:", 122, 4, "Finance")

h("Smart Bijli integration with AR:", 123, 4, "Finance")
h("CRA:", 123, 4, "Finance")
h("TCS.1% Process", 123, 4, "Finance")
h("Projects", 123, 3, _A15)
h("Smart Bijli app integration to create Estimate:", 123, 4, "Projects")
h("Item Transfer between Projects:", 123, 4, "Projects")
h("Labor Contract Automation:", 123, 4, "Projects")
h("Turnkey Estimate Process and Measurement Book:", 123, 4, "Projects")
h("Work order:", 124, 4, "Projects")
h("Budget Form and Workflow (Estimate BoQ):", 124, 4, "Projects")
h("Project workflow:", 124, 4, "Projects")
h("Project Funds Control:", 124, 4, "Projects")
h("Asset creation and capitalization automation.", 124, 4, "Projects")
h("Move Order Release:", 124, 4, "Projects")
h("Web-Adi Project Upload:", 124, 4, "Projects")
h("SCM", 125, 3, _A15)
h("Employee as Supplier creation:", 125, 4, "SCM")
h("Return Indent:", 125, 4, "SCM")
h("Serial Number correction:", 125, 4, "SCM")
h("PO Control:", 125, 4, "SCM")
h("HRMS", 125, 3, _A15)
h("Travel process:", 125, 4, "HRMS")
h("Appraisal Process(ACR):", 125, 4, "HRMS")
h("Employee Number Generation program:", 125, 4, "HRMS")
h("GPF Process:", 125, 4, "HRMS")
h("Leave process :", 125, 4, "HRMS")
h("ESS processes :", 126, 4, "HRMS")
h("D17 Process: All Class I and II fills D17 details", 126, 5, "ESS processes :")
h("Transfer Process: Transfer process can be initiated by supervisors", 126, 5, "ESS processes :")
h("Annual Increment Program:", 126, 4, "HRMS")
h("Increment reversal Program :", 126, 4, "HRMS")
h("DA Arrear Program :", 126, 4, "HRMS")
h("Seven Pay Matrix :", 126, 4, "HRMS")

h("Annexure XIII-A: GIS / GeoPortal Functionalities, Modernization & Enterprise Integration", 127, 2, S7)
_A13A = "Annexure XIII-A: GIS / GeoPortal Functionalities, Modernization & Enterprise Integration"
h("1. Objective", 127, 3, _A13A)
h("2. Major GIS Functionalities", 127, 3, _A13A)
h("3. Data Migration & Reconciliation", 129, 3, _A13A)
h("4. Technology & Interoperability", 129, 3, _A13A)
h("5. Functional Continuity", 129, 3, _A13A)

h("Annexure XVI: Check List", 130, 2, S7)
for n, criteria, page in [
    (1, "The Sole Bidder/Lead Bidder and Consortium Partner (in case of consortium) shall be a company registered", 130),
    (2, "The bidder should be registered in India with GST", 130),
    (3, "Bidder should not have been blacklisted by any entity of GoMP", 130),
    (4, "The Sole bidder/Lead bidder should have a Minimum Average Annual Turnover", 130),
    (5, "The bidder must have positive net worth during last all three (03) financial years", 130),
    (6, "The Sole bidder/Lead bidder should have successfully executed at least three projects", 130),
    (7, "The Sole Bidder, in case of an individual bid, or the Lead Bidder/Lead Consortium Member, in case of", 130),
    (8, "The Sole bidder/Lead bidder and Consortium Partner (in case of consortium) must have ISO 9001:2015", 131),
    (9, "The Sole Bidder/Lead Bidder shall be an active Oracle PartnerNetwork (OPN) member", 131),
    (10, "The Sole bidder/Lead bidder should have at least 50 Full-Time Technical Professionals", 131),
]:
    h(f"{n}. {criteria}", page, 3, "Annexure XVI: Check List")

h("Annexure XVII", 132, 2, S7)
h("Consortium Agreement", 132, 3, "Annexure XVII")
_A17 = "Consortium Agreement"
h("a. Submit a response jointly to Bid", 132, 4, _A17)
h("b. Provide and perform the services which would be ordered", 132, 4, _A17)
h("c. This Agreement shall not be construed as establishing", 132, 4, _A17)
h("d. The Parties shall be jointly and severally responsible", 132, 4, _A17)
h("e. ---- (Name of Party) shall act as Lead Partner of the Consortium", 133, 4, _A17)
h("i. To ensure the technical, commercial and administrative co-ordination", 133, 5, "e. ---- (Name of Party) shall act as Lead Partner of the Consortium")
h("ii. To lead the contract negotiations of the work package", 133, 5, "e. ---- (Name of Party) shall act as Lead Partner of the Consortium")
h("iii. The Lead partner is authorized to receive instructions", 133, 5, "e. ---- (Name of Party) shall act as Lead Partner of the Consortium")
h("iv. In case of an award, act as channel of communication", 133, 5, "e. ---- (Name of Party) shall act as Lead Partner of the Consortium")
h("f. That the consortium partner <......> shall carry out all responsibilities", 133, 4, _A17)
h("g. That the broad roles and the responsibilities of each Party at each stage of the Bidding", 133, 4, _A17)
h("h. That the broad roles and the responsibilities of each Party at each stage of the Project Execution", 133, 4, _A17)
h("i. That the Parties affirm that they shall implement the Project in good faith", 133, 4, _A17)
h("j. That this MoU shall be governed in accordance with the laws of India", 133, 4, _A17)

h("Annexure XVIII", 135, 2, S7)
h("Schedule - . Format of Power of Attorney by Consortium Member in favour of Lead Consortium Member (Self Format)", 135, 3, "Annexure XVIII")

print(f"Total authored headings: {len(ALL_HEADINGS)}")


async def run():
    total_pages = 135
    pages = database.get_pages(DUP_JOB_ID)
    page_text_by_num = {p["page_num"]: (p.get("text") or "") for p in pages}

    headings_by_page: dict = {}
    for hd in ALL_HEADINGS:
        headings_by_page.setdefault(hd.start_page, []).append(hd)

    max_per_page = max((len(v) for v in headings_by_page.values()), default=0)
    print(f"Max headings on a single page: {max_per_page}")

    MAX_PER_BATCH = 180
    batches = []
    batch_start = 1
    batch_count = 0
    for pn in range(1, total_pages + 1):
        n = len(headings_by_page.get(pn, []))
        if batch_count + n > MAX_PER_BATCH and batch_count > 0:
            batches.append((batch_start, pn - 1))
            batch_start = pn
            batch_count = 0
        batch_count += n
    batches.append((batch_start, total_pages))
    print("Batches (first_page, last_page):", batches)

    seed_scope = None
    last_page_text = ""
    carry_context = ai_indexing.CarryContext(open_containers=[], last_page_tail="")

    for first_page, last_page in batches:
        batch_headings = []
        for pn in range(first_page, last_page + 1):
            batch_headings.extend(headings_by_page.get(pn, []))
        page_blocks = {}
        page_block_types = {}
        page_row_ids = {}
        for pn in range(first_page, last_page + 1):
            texts, types, row_ids = reference_index.parse_page_blocks_with_row_ids(page_text_by_num.get(pn) or "")
            page_blocks[pn] = texts
            page_block_types[pn] = types
            page_row_ids[pn] = row_ids
        if batch_headings:
            events_by_page, warnings = await ai_reference_index._ground_and_resolve_chunk(
                batch_headings, page_blocks, carry_context, "gold-hand-authored",
                has_next_page_fn=lambda pn: pn < total_pages,
                resolve_parent_via_ai=False, provider_config=None,
                page_block_types=page_block_types, page_row_ids=page_row_ids,
            )
            print(f"Batch {first_page}-{last_page}: {len(batch_headings)} headings, warnings={warnings}")
        else:
            events_by_page, warnings = {}, {}

        for pn in range(first_page, last_page + 1):
            text = page_text_by_num.get(pn) or ""
            events = events_by_page.get(pn, [])
            cands, new_scope = reference_index.apply_heading_events_to_scope(
                events, seed_scope, pn, has_next_page=pn < total_pages,
            )
            reference_index.validate_scope_shape(new_scope)
            reference_index._persist_page_index(
                DUP_JOB_ID, pn, text, cands, new_scope, provisional=False,
                detection_source="ai", detection_model="gold-hand-authored",
            )
            current_frontier = database.get_reference_index_frontier(DUP_JOB_ID) or 0
            database.set_reference_index_frontier(DUP_JOB_ID, max(current_frontier, pn))
            seed_scope = new_scope
            last_page_text = text

        carry_context = ai_reference_index._carry_in_summary(seed_scope, last_page_text)

    reference_index.build_mention_index_for_job(DUP_JOB_ID)
    print("Done. Mention index built.")


asyncio.run(run())
