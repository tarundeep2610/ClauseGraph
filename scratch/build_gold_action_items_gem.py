"""Hand-curated gold action-items fixtures for the two new GeM bid documents
(AI_INDEXING_ACCURACY_INVESTIGATION.md §25). Built from a direct, full read
of the real OCR text for both jobs (10 + 8 pages) — same methodology as the
tender/appeal fixtures: verbatim evidence_quote, then mechanically validated
to ground against the real stored text before being trusted."""
import json
import os

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), "..")
FIXTURES_DIR = r"c:\Users\Tarundeep\Desktop\ocr-services\tests\fixtures"


def item(page, item_type, item_text, evidence_quote, date_text=None, date_iso=None,
         anchor_event=None, cadence=None, end_page=None, responsible_party=None, confidence="high"):
    return {
        "page": page, "item_type": item_type, "item_text": item_text,
        "date_text": date_text, "date_iso": date_iso, "anchor_event": anchor_event,
        "cadence": cadence, "end_page": end_page, "evidence_quote": evidence_quote,
        "responsible_party": responsible_party, "confidence": confidence,
    }


# ─── 30d148dd — DFCCIL Frauscher MSDAC AMC/CMC bid (10 pages) ───────────────
DFCCIL_ITEMS = [
    item(1, "deadline", "Bid End Date/Time",
         "बिड बंद होने की तारीख/समय /Bid End Date/Time</td><td>06-10-2026 15:00:00",
         date_text="06-10-2026 15:00:00", date_iso="2026-10-06", responsible_party="Bidder"),
    item(1, "deadline", "Bid Opening Date/Time",
         "बिड खुले की तारीख/समय /Bid Opening Date/Time</td><td>07-10-2026 15:00:00",
         date_text="07-10-2026 15:00:00", date_iso="2026-10-07", responsible_party="Purchaser"),
    item(1, "deadline", "Bid offer validity period, 90 days from Bid End Date",
         "बिड पेशकश वैंपत्ता (बंद होने की तारीख से)/Bid Offer Validity (From End Date)</td><td>90 (Days)",
         date_text="90 (Days) from End Date", anchor_event="Bid End Date", responsible_party="Bidder"),
    item(2, "deadline", "Seller payment due within 45 days of SDAC issuance and bill submission",
         "Payments shall be made to the Seller within 45 days of issue of service delivery acceptance certificate (SDAC) and on-line submission of bills",
         date_text="within 45 days", anchor_event="issue of service delivery acceptance certificate (SDAC)", responsible_party="Purchaser"),
    item(2, "deadline", "Time allowed for technical clarifications during technical evaluation",
         "तकनीकी मूल्यांकन के दौरान तकनीकी स्पष्टीकरण हेतु अनुमत समय /Time allowed for Technical Clarifications during technical evaluation</td><td>2 Days",
         date_text="2 Days", responsible_party="Bidder"),
    item(2, "condition", "Bid auto-extends by 7 days unless minimum bid count reached",
         "दिलों की संख्या, जिनके लिए बिड लगाने की समय-सीमा बढ़ई जाएगी / Number of days for which Bid would be auto-extended</td><td>7",
         date_text="7 days", responsible_party="Purchaser", confidence="medium"),
    item(3, "condition", "EMD exemption requires valid supporting documents for the relevant category",
         "The bidder seeking EMD exemption, must submit the valid supporting document for the relevant category as per GeM GTC with the bid.",
         responsible_party="Bidder"),
    item(3, "obligation", "TReDS-registered MSME sellers must register with a TReDS exchange to receive payment via that channel",
         "sellers intending to avail payment through TReDS are required to be registered with at least one TReDS exchange in which the buyer is registered.",
         responsible_party="Bidder (MSME seller)"),
    item(4, "obligation", "Turnover documentary evidence (audited balance sheets or CA certificate) must be uploaded with the bid",
         "Documentary evidence in the form of certified Audited Balance Sheets of relevant periods or a certificate from the Chartered Accountant / Cost Accountant indicating the turnover details for the relevant period shall be uploaded with the bid.",
         responsible_party="Bidder"),
    item(4, "condition", "MSE Seller given opportunity to match L-1 price if quoted within L-1+15% purchase-preference band",
         "If L-1 is not an MSE and MSE Seller (s) has / have quoted price within L-1+ 15% (Selected by Buyer) of margin of purchase preference /price band defined in relevant policy, such MSE Seller shall be given opportunity to match L-1 price and contract will be awarded for 100% (selected by Buyer) percentage of total quantity.",
         responsible_party="Purchaser"),
    item(4, "obligation", "Bidder must have Past Experience of Similar Services meeting one of three cost-threshold alternatives",
         "The bidder must have successfully executed/completed similar Services over the last three years i.e. the current financial year and the last three financial years",
         responsible_party="Bidder"),
    item(5, "condition", "Buyer may increase/decrease contract quantity or duration up to 25%; bidders bound to accept",
         "OPTION CLAUSE 25% : The buyer can increase or decrease the contract quantity or contract duration up to 25 percent at the time of issue of the contract. However, once the contract is issued, the contract quantity or contract duration can only be increased up to 25 percent. Bidders are bound to accept the revised quantity or duration.",
         responsible_party="Bidder"),
    item(5, "obligation", "Bidder must upload undertaking on financial standing (not under liquidation/receivership/bankruptcy)",
         "Bidder to upload undertaking to this effect with bid.",
         responsible_party="Bidder"),
    item(6, "obligation", "Bidders must upload signed Integrity Pact scanned copy with the bid",
         "Bidders shall have to upload scanned copy of signed integrity pact as per Buyer organizations policy along with bid.",
         responsible_party="Bidder"),
    item(6, "obligation", "Authorised distributors/service providers must furnish Manufacturer Authorisation Form/Certificate",
         "Authorisation Form /Certificate with OEM/Original Service Provider details such as name, designation, address, e-mail Id and Phone No. required to be furnished along with the bid",
         responsible_party="Bidder"),
    item(6, "obligation", "Seller must upload Malicious Code Certificate",
         "The seller should upload following certificate in the bid:-",
         responsible_party="Bidder"),
    item(6, "condition", "Seller must not assign or sub-contract the Contract without prior written consent of buyer",
         "The Seller shall not assign the Contract in whole or part without obtaining the prior written consent of buyer.",
         responsible_party="Seller"),
    item(6, "condition", "Bid summarily rejected if Pre Contract Integrity Pact not signed and uploaded",
         "The Bidder has to sign and upload Pre Contract Integrity Pact in the format attached and uploaded under clause for Integrity Pact. Failing of which the Bid shall summarily be rejected.",
         responsible_party="Bidder"),
    item(6, "deadline", "Antiprofiteering Declaration due within 15 days after successful award of contract",
         "The bidder has to submit the Antiproffeteering Declaration in the prescribed format uploaded as buyer Added ATC document after successful award of the Contract within 15 days.",
         date_text="within 15 days", anchor_event="successful award of the Contract", responsible_party="Bidder"),
    item(6, "condition", "Bid summarily rejected if signed Bid Document terms/conditions copy not uploaded",
         "The bidder has to sign (each page) of the Bid Document Terms and conditions attached as Scope of Work as acceptance of the Tender Conditions and upload the signed copy along with the Bid. Failing of which the Bid shall summarily be rejected.",
         responsible_party="Bidder"),
    item(6, "condition", "Bid summarily rejected if Self Declarations (financial standing, non-blacklisting) not uploaded",
         "Bidder has to upload Self Declarations for Financial Standing, Non- Blacklisting by any Central Govt./PSU /State Govt. organisations. Failing of which the Bid shall summarily be rejected.",
         responsible_party="Bidder"),
    item(7, "deadline", "EMD/Performance-Security instrument hardcopy must reach Buyer within 5 days of Bid End/Opening date",
         "Bidder has to upload scanned copy / proof of the DD along with bid and has to ensure delivery of hardcopy to the Buyer within 5 days of Bid End date / Bid Opening date.",
         date_text="within 5 days", anchor_event="Bid End date / Bid Opening date", responsible_party="Bidder"),
    item(8, "deadline", "Successful Bidder must deliver original Performance Security instrument within 15 days of award of contract",
         "After award of contract, Successful Bidder can upload scanned copy of the DD in place of PBG and has to ensure delivery of hard copy to the original DD to the Buyer within 15 days of award of contract.",
         date_text="within 15 days", anchor_event="award of contract", responsible_party="Bidder"),
    item(9, "condition", "Offer liable to be rejected if required certificates/documents not uploaded",
         "Bidder's offer is liable to be rejected if they don't upload any of the certificates / documents sought in the Bid document, ATC and Corrigendum if any.",
         responsible_party="Bidder"),
    item(9, "deadline", "EMD and signed Integrity Pact may be submitted in physical form within 5 days of bid opening",
         "Mandating submission of documents (other than EMD and signed Integrity Pact - which can be submitted within 5 days of bid opening) in physical form as a pre-requisite to qualify bidders.",
         date_text="within 5 days", anchor_event="bid opening", responsible_party="Bidder", confidence="medium"),
    item(10, "condition", "Land-border-sharing-country bidder must be registered with Competent Authority to be eligible",
         "any bidder from a country which shares a land border with India will be eligible to bid in this tender only if the bidder is registered with the Competent Authority.",
         responsible_party="Bidder"),
    item(10, "obligation", "Seller/Service Provider must ensure compliance with applicable labour laws and Labour Codes",
         "All GeM Sellers/Service Providers shall ensure full compliance with all applicable labour laws, including the provisions, rules, schemes and guidelines under the four Labour Codes",
         responsible_party="Seller"),
]

# ─── 179ab46e — MIDHANI Up-Armouring of Defender Vehicles bid (8 pages) ─────
MIDHANI_ITEMS = [
    item(1, "deadline", "Bid End Date/Time",
         "बिड बंद होने की तारीख/समय /Bid End Date/Time</td><td>23-10-2026 11:00:00",
         date_text="23-10-2026 11:00:00", date_iso="2026-10-23", responsible_party="Bidder"),
    item(1, "deadline", "Bid Opening Date/Time",
         "बिड खुले की तारीख/समय /Bid Opening Date/Time</td><td>24-10-2026 11:00:00",
         date_text="24-10-2026 11:00:00", date_iso="2026-10-24", responsible_party="Purchaser"),
    item(1, "deadline", "Bid offer validity period, 90 days from Bid End Date",
         "बिड पेशकश वैंपत्ता (बंद होने की तारीख से)/Bid Offer Validity (From End Date)</td><td>90 (Days)",
         date_text="90 (Days) from End Date", anchor_event="Bid End Date", responsible_party="Bidder"),
    item(2, "deadline", "Seller payment due within 30 days of CRAC issuance and bill submission",
         "Payments shall be made to the Seller within 30 days of issue of consignee receipt-cum-acceptance certificate (CRAC) and on-line submission of bills",
         date_text="within 30 days", anchor_event="issue of consignee receipt-cum-acceptance certificate (CRAC)", responsible_party="Purchaser"),
    item(2, "deadline", "Time allowed for technical clarifications during technical evaluation",
         "तकनीकी मूल्यांकन के दौरान तकनीकी स्पष्टीकरण हेतु अनुमत समय /Time allowed for Technical Clarifications during technical evaluation</td><td>2 Days",
         date_text="2 Days", responsible_party="Bidder"),
    item(2, "condition", "Bid auto-extends by 3 days unless minimum bid count reached",
         "दिलों की संख्या, जिनके लिए बिड लगाने की समय-सीमा बढ़ई जाएगी / Number of days for which Bid would be auto-extended</td><td>3",
         date_text="3 days", responsible_party="Purchaser", confidence="medium"),
    item(3, "condition", "EMD exemption requires valid supporting documents for the relevant category",
         "The bidder seeking EMD exemption, must submit the valid supporting document for the relevant category as per GeM GTC with the bid.",
         responsible_party="Bidder"),
    item(4, "condition", "Class-I/Class-II local-content certificate required to claim Make-in-India purchase preference",
         "the bidder must upload a certificate from the OEM regarding the percentage of the local content and the details of locations at which the local value addition is made along with their bid, failing which no purchase preference shall be granted.",
         responsible_party="Bidder"),
    item(4, "condition", "Local-content declaration must be certified by statutory/cost auditor when bid value exceeds Rs 10 Crore",
         "In case the bid value is more than Rs 10 Crore, the declaration relating to percentage of local content shall be certified by the statutory auditor or cost auditor, if the OEM is a company",
         responsible_party="Bidder", confidence="medium"),
    item(4, "condition", "MSE Seller given opportunity to match L-1 price if quoted within L-1+15% purchase-preference band",
         "If L-1 is not an MSE and MSE Seller (s) has / have quoted price within L-1+ 15% (Selected by Buyer) of margin of purchase preference /price band defined in relevant policy, such MSE Seller shall be given opportunity to match L-1 price and contract will be awarded for 25% (selected by Buyer) percentage of total quantity.",
         responsible_party="Purchaser"),
    item(5, "condition", "H-1 bidder excluded from Reverse Auction unless one of five listed exception cases applies",
         "The technically qualified Highest Quoting bidder will not be allowed to participate in RA.",
         responsible_party="Purchaser", confidence="medium"),
    item(5, "deadline", "Delivery required within 90 days (per consignee delivery schedule)",
         "<td>1</td><td>**********</td><td>**********Hyderabad</td><td>4</td><td>90</td>",
         date_text="90 days", responsible_party="Bidder", confidence="medium"),
    item(6, "condition", "Purchaser may increase/decrease ordered quantity up to 50%, with delivery-period formula for increases",
         "OPTION CLAUSE: The Purchaser reserves the right to increase or decrease the quantity to be ordered up to 50 percent of bid quantity at the time of placement of contract.",
         responsible_party="Purchaser"),
    item(6, "obligation", "Bidders must upload signed Integrity Pact scanned copy with the bid",
         "Bidders shall have to upload scanned copy of signed integrity pact as per Buyer organizations policy along with bid.",
         responsible_party="Bidder"),
    item(7, "deadline", "EMD and signed Integrity Pact may be submitted in physical form within 5 days of bid opening",
         "Mandating submission of documents (other than EMD and signed Integrity Pact - which can be submitted within 5 days of bid opening) in physical form as a pre-requisite to qualify bidders.",
         date_text="within 5 days", anchor_event="bid opening", responsible_party="Bidder", confidence="medium"),
    item(7, "obligation", "Seller/Service Provider must ensure compliance with applicable labour laws and Labour Codes",
         "All GeM Sellers/Service Providers shall ensure full compliance with all applicable labour laws, including the provisions, rules, schemes and guidelines under the four Labour Codes",
         responsible_party="Seller"),
    item(8, "condition", "Land-border-sharing-country bidder must be registered with Competent Authority to be eligible",
         "any bidder from a country which shares a land border with India will be eligible to bid in this tender only if the bidder is registered with the Competent Authority.",
         responsible_party="Bidder"),
]


def main():
    dfccil_fixture = {
        "job_id": "30d148dd-c1e4-4e38-877b-608b5584ff4e",
        "total_pages": 10,
        "action_items": [dict(it, occurrence_index=i) for i, it in enumerate(DFCCIL_ITEMS)],
    }
    midhani_fixture = {
        "job_id": "179ab46e-b105-4107-ad38-7a0160724948",
        "total_pages": 8,
        "action_items": [dict(it, occurrence_index=i) for i, it in enumerate(MIDHANI_ITEMS)],
    }

    with open(os.path.join(FIXTURES_DIR, "gold_action_items_30d148dd.json"), "w", encoding="utf-8") as f:
        json.dump(dfccil_fixture, f, indent=2, ensure_ascii=False)
    with open(os.path.join(FIXTURES_DIR, "gold_action_items_179ab46e.json"), "w", encoding="utf-8") as f:
        json.dump(midhani_fixture, f, indent=2, ensure_ascii=False)

    print(f"30d148dd: {len(DFCCIL_ITEMS)} items")
    print(f"179ab46e: {len(MIDHANI_ITEMS)} items")


if __name__ == "__main__":
    main()
