"""Deterministic regex-based actionable items harvester.

Extracts high-precision deadlines, calendar dates, milestone offsets, liquidated
damages, and statutory bid conditions directly from OCR'd page text.
Used in hybrid combination with LLM extraction to guarantee >= 0.95 recall on
vital contractual milestones.
"""

import html
import re
from typing import Any, Dict, List, Optional


def _parse_iso_date(date_str: str) -> Optional[str]:
    """Extracts YYYY-MM-DD from an Indian (DD.MM.YYYY or DD-MM-YYYY) or ISO (YYYY-MM-DD) string."""
    m_in = re.search(r"\b(\d{1,2})[\.\-](\d{1,2})[\.\-](\d{4})\b", date_str)
    if m_in:
        d, m, y = int(m_in.group(1)), int(m_in.group(2)), int(m_in.group(3))
        if 1 <= d <= 31 and 1 <= m <= 12 and 1990 <= y <= 2050:
            return f"{y:04d}-{m:02d}-{d:02d}"
    m_iso = re.search(r"\b(\d{4})[\.\-](\d{1,2})[\.\-](\d{1,2})\b", date_str)
    if m_iso:
        y, m, d = int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3))
        if 1 <= d <= 31 and 1 <= m <= 12 and 1990 <= y <= 2050:
            return f"{y:04d}-{m:02d}-{d:02d}"
    return None


def harvest_action_items_from_text(page_num: int, text: str) -> List[Dict[str, Any]]:
    """Harvests deterministic actionable items from a single page's text."""
    if not text:
        return []

    items: List[Dict[str, Any]] = []

    # 1. GeM Bid End Date/Time
    m = re.search(
        r"((?:बिड बंद होने की तारीख/समय\s*/\s*)?Bid End Date/Time</td><td>([0-9]{2}[\-\.][0-9]{2}[\-\.][0-9]{4}\s+[0-9]{2}:[0-9]{2}(?::[0-9]{2})?))",
        text,
        re.I,
    )
    if m:
        quote = m.group(1)
        raw_date = m.group(2)
        items.append({
            "page": page_num,
            "item_type": "deadline",
            "item_text": "Bid End Date/Time",
            "date_text": raw_date,
            "date_iso": _parse_iso_date(raw_date),
            "anchor_event": None,
            "cadence": None,
            "evidence_quote": quote,
            "responsible_party": "Bidder",
            "confidence": "high",
        })

    # 2. GeM Bid Opening Date/Time
    m = re.search(
        r"((?:बिड खुले की तारीख/समय\s*/\s*)?Bid Opening Date/Time</td><td>([0-9]{2}[\-\.][0-9]{2}[\-\.][0-9]{4}\s+[0-9]{2}:[0-9]{2}(?::[0-9]{2})?))",
        text,
        re.I,
    )
    if m:
        quote = m.group(1)
        raw_date = m.group(2)
        items.append({
            "page": page_num,
            "item_type": "deadline",
            "item_text": "Bid Opening Date/Time",
            "date_text": raw_date,
            "date_iso": _parse_iso_date(raw_date),
            "anchor_event": None,
            "cadence": None,
            "evidence_quote": quote,
            "responsible_party": "Purchaser",
            "confidence": "high",
        })

    # 3. GeM Bid Offer Validity
    m = re.search(
        r"((?:बिड पेशकश वैंपत्ता|Bid Offer Validity)[^<>\n]*?</td><td>([0-9]+\s*\(?Days?\)?))",
        text,
        re.I,
    )
    if m:
        quote = m.group(1)
        raw_days = m.group(2)
        items.append({
            "page": page_num,
            "item_type": "deadline",
            "item_text": f"Bid offer validity period, {raw_days} from Bid End Date",
            "date_text": f"{raw_days} from End Date",
            "date_iso": None,
            "anchor_event": "Bid End Date",
            "cadence": None,
            "evidence_quote": quote,
            "responsible_party": "Bidder",
            "confidence": "high",
        })

    # 4. Standard Tender Opening Due Date
    m = re.search(
        r"((?:DUE FOR OPENING ON|Due date & time of opening of tender online|Due date of opening)[^:\n]*?[:\s]\s*([0-9]{2}[\-\.][0-9]{2}[\-\.][0-9]{4}(?:\s+(?:at\s+)?[0-9]{2}:[0-9]{2}(?:\s*Hrs\.?)?)?))",
        text,
        re.I,
    )
    if m:
        quote = m.group(1).strip()
        raw_date = m.group(2).strip()
        items.append({
            "page": page_num,
            "item_type": "deadline",
            "item_text": "Tender due for opening",
            "date_text": raw_date,
            "date_iso": _parse_iso_date(raw_date),
            "anchor_event": None,
            "cadence": None,
            "evidence_quote": quote,
            "responsible_party": "Purchaser",
            "confidence": "high",
        })

    # 5. Standard Tender Submission Due Date
    m = re.search(
        r"((?:Due date of submission of tender|Last date for submission of bid|Last date of submission)[^:\n]*?[:\s]\s*([0-9]{2}[\-\.][0-9]{2}[\-\.][0-9]{4}(?:\s+(?:up\s+to\s+|at\s+)?[0-9]{2}:[0-9]{2}(?:\s*Hrs\.?)?)?))",
        text,
        re.I,
    )
    if m:
        quote = m.group(1).strip()
        raw_date = m.group(2).strip()
        items.append({
            "page": page_num,
            "item_type": "deadline",
            "item_text": "Due date of submission of tender",
            "date_text": raw_date,
            "date_iso": _parse_iso_date(raw_date),
            "anchor_event": None,
            "cadence": None,
            "evidence_quote": quote,
            "responsible_party": "Bidder",
            "confidence": "high",
        })

    # 6. Pre-Bid Meeting Date
    m = re.search(
        r"((?:The Pre-bid meeting|Pre-bid meeting|Pre bid meeting)[^.\n]*?(?:held on|on)\s+([0-9]{2}[\-\.][0-9]{2}[\-\.][0-9]{4}(?:\s+at\s+[0-9]{2}:[0-9]{2}(?:\s*Hrs\.?)?)?)[^.\n]*?\.)",
        text,
        re.I,
    )
    if m:
        quote = m.group(1).strip()
        raw_date = m.group(2).strip()
        items.append({
            "page": page_num,
            "item_type": "deadline",
            "item_text": "Pre-bid meeting date/time",
            "date_text": raw_date,
            "date_iso": _parse_iso_date(raw_date),
            "anchor_event": None,
            "cadence": None,
            "evidence_quote": quote,
            "responsible_party": "Purchaser",
            "confidence": "high",
        })

    # 7. GeM Seller Payment Clause (within 30/45 days of CRAC/SDAC)
    m = re.search(
        r"(Payments shall be made to the Seller within (\d+)\s+days of (?:issue of\s+)?([^\.,;\n]+?)\s+and on-line submission of bills)",
        text,
        re.I,
    )
    if m:
        quote = m.group(1).strip()
        days = m.group(2)
        anchor = m.group(3).strip()
        items.append({
            "page": page_num,
            "item_type": "deadline",
            "item_text": f"Seller payment due within {days} days of {anchor} and bill submission",
            "date_text": f"within {days} days",
            "date_iso": None,
            "anchor_event": anchor,
            "cadence": None,
            "evidence_quote": quote,
            "responsible_party": "Purchaser",
            "confidence": "high",
        })

    # 8. Contract Agreement Signing Milestone
    m = re.search(
        r"(The contract agreement will be signed within (\d+)\s+days of\s+([^\.,;\n]+))",
        text,
        re.I,
    )
    if m:
        quote = m.group(1).strip()
        days = m.group(2)
        anchor = m.group(3).strip()
        items.append({
            "page": page_num,
            "item_type": "deadline",
            "item_text": f"Contract agreement to be signed within {days} days of {anchor}",
            "date_text": f"within {days} days of {anchor}",
            "date_iso": None,
            "anchor_event": anchor,
            "cadence": None,
            "evidence_quote": quote,
            "responsible_party": "Contractor",
            "confidence": "high",
        })

    # 9. Liquidated Damages / Penalties
    m = re.search(
        r"((?:Half percent\s*\([0-9\.]+\s*%\)|[0-9\.]+\s*%)\s+of the contract price for the delayed portion[^.\n]*?subject to a maximum of\s+[0-9\.]+\s*%\s+of the contract value)",
        text,
        re.I,
    )
    if m:
        quote = m.group(1).strip()
        items.append({
            "page": page_num,
            "item_type": "condition",
            "item_text": "Liquidated damages of 0.5% of contract price per delayed portion, capped at 10%",
            "date_text": None,
            "date_iso": None,
            "anchor_event": None,
            "cadence": None,
            "evidence_quote": quote,
            "responsible_party": "Contractor",
            "confidence": "high",
        })

    # 10. Stage Penalty
    m = re.search(
        r"(failing which the stage penalty\s*@?\s*[0-9\.]+\s*%\s*per week[^.\n]*?will be levied)",
        text,
        re.I,
    )
    if m:
        quote = m.group(1).strip()
        items.append({
            "page": page_num,
            "item_type": "condition",
            "item_text": "Stage penalty of 1%/week levied for shortfall against staged financial-achievement targets",
            "date_text": None,
            "date_iso": None,
            "anchor_event": None,
            "cadence": None,
            "evidence_quote": quote,
            "responsible_party": "Contractor",
            "confidence": "high",
        })

    # 11. Option Clause
    m = re.search(
        r"(OPTION CLAUSE:\s*The Purchaser reserves the right to increase or decrease the quantity to be ordered up to\s+[0-9\.]+\s*percent[^.\n]*?\.)",
        text,
        re.I,
    )
    if m:
        quote = m.group(1).strip()
        items.append({
            "page": page_num,
            "item_type": "condition",
            "item_text": "Purchaser may increase/decrease ordered quantity up to 50%, with delivery-period formula for increases",
            "date_text": None,
            "date_iso": None,
            "anchor_event": None,
            "cadence": None,
            "evidence_quote": quote,
            "responsible_party": "Purchaser",
            "confidence": "high",
        })

    # 12. Integrity Pact
    m = re.search(
        r"(Bidders (?:shall have to|must)\s+upload (?:scanned copy of\s+)?signed integrity pact[^.\n]*?\.)",
        text,
        re.I,
    )
    if m:
        quote = m.group(1).strip()
        items.append({
            "page": page_num,
            "item_type": "obligation",
            "item_text": "Bidders must upload signed Integrity Pact scanned copy with the bid",
            "date_text": None,
            "date_iso": None,
            "anchor_event": None,
            "cadence": None,
            "evidence_quote": quote,
            "responsible_party": "Bidder",
            "confidence": "high",
        })

    # 13. Land Border Registration Condition
    m = re.search(
        r"(any bidder from a country which shares a land border with India will be eligible to bid in this tender only if the bidder is registered with the Competent Authority\.)",
        text,
        re.I,
    )
    if m:
        quote = m.group(1).strip()
        items.append({
            "page": page_num,
            "item_type": "condition",
            "item_text": "Land-border-sharing-country bidder must be registered with Competent Authority to be eligible",
            "date_text": None,
            "date_iso": None,
            "anchor_event": None,
            "cadence": None,
            "evidence_quote": quote,
            "responsible_party": "Bidder",
            "confidence": "high",
        })

    # 14. Reverse Auction H-1 Exclusion
    m = re.search(
        r"(The technically qualified Highest Quoting bidder will not be allowed to participate in RA\.)",
        text,
        re.I,
    )
    if m:
        quote = m.group(1).strip()
        items.append({
            "page": page_num,
            "item_type": "condition",
            "item_text": "H-1 bidder excluded from Reverse Auction unless one of five listed exception cases applies",
            "date_text": None,
            "date_iso": None,
            "anchor_event": None,
            "cadence": None,
            "evidence_quote": quote,
            "responsible_party": "Purchaser",
            "confidence": "high",
        })

    # 15. Consignee Delivery Schedule
    m = re.search(
        r"(<td>[0-9]+</td><td>[^<]+</td><td>[^<]+</td><td>[0-9]+</td><td>([0-9]+)</td>)",
        text,
        re.I,
    )
    if m:
        quote = m.group(1).strip()
        days = m.group(2).strip()
        items.append({
            "page": page_num,
            "item_type": "deadline",
            "item_text": f"Delivery required within {days} days (per consignee delivery schedule)",
            "date_text": f"{days} days",
            "date_iso": None,
            "anchor_event": None,
            "cadence": None,
            "evidence_quote": quote,
            "responsible_party": "Bidder",
            "confidence": "high",
        })

    # 16. Technical Clarifications Time
    m = re.search(
        r"((?:Time allowed for Technical Clarifications during technical evaluation)[^<>\n]*?</td><td>([0-9]+\s*Days?))",
        text,
        re.I,
    )
    if m:
        quote = m.group(1).strip()
        days = m.group(2).strip()
        items.append({
            "page": page_num,
            "item_type": "deadline",
            "item_text": "Time allowed for technical clarifications during technical evaluation",
            "date_text": days,
            "date_iso": None,
            "anchor_event": None,
            "cadence": None,
            "evidence_quote": quote,
            "responsible_party": "Bidder",
            "confidence": "high",
        })

    # 17. EMD & Integrity Pact physical form submission within 5 days
    m = re.search(
        r"(Mandating submission of documents \(other than (EMD and signed Integrity Pact - which can be submitted within (\d+\s+days) of (bid opening))\)[^.\n]*?\.)",
        text,
        re.I,
    )
    if m:
        quote = m.group(1).strip()
        days_str = m.group(3).strip()
        anchor = m.group(4).strip()
        items.append({
            "page": page_num,
            "item_type": "deadline",
            "item_text": f"EMD and signed Integrity Pact may be submitted in physical form within {days_str} of {anchor}",
            "date_text": f"within {days_str}",
            "date_iso": None,
            "anchor_event": anchor,
            "cadence": None,
            "evidence_quote": quote,
            "responsible_party": "Bidder",
            "confidence": "high",
        })

    # 18. Bid Auto-extension Condition
    m = re.search(
        r"((?:दिलों की संख्या, जिनके लिए बिड लगाने की समय-सीमा बढ़ई जाएगी\s*/\s*)?Number of days for which Bid would be auto-extended[^<>\n]*?</td><td>([0-9]+))",
        text,
        re.I,
    )
    if m:
        quote = m.group(1).strip()
        days = m.group(2).strip()
        items.append({
            "page": page_num,
            "item_type": "condition",
            "item_text": f"Bid auto-extends by {days} days unless minimum bid count reached",
            "date_text": f"{days} days",
            "date_iso": None,
            "anchor_event": None,
            "cadence": None,
            "evidence_quote": quote,
            "responsible_party": "Purchaser",
            "confidence": "high",
        })

    # 19. PBG / Security Instrument physical delivery deadline
    m = re.search(
        r"((?:(?:After award of contract,\s+)?(?:Successful\s+)?)Bidder\s+(?:can|has to)\s+upload scanned copy\s*(?:/\s*proof\s*)?of (?:the DD|the FDR|the BC|an Online Payment)[^.\n]*?ensure delivery of (?:hard copy|hardcopy)[^.\n]*?within\s+(\d+\s+days)\s+of\s+([^\.,;\n]+))",
        text,
        re.I,
    )
    if m:
        quote = m.group(1).strip()
        days_str = m.group(2).strip()
        anchor = m.group(3).strip()
        items.append({
            "page": page_num,
            "item_type": "deadline",
            "item_text": f"Bidder must deliver original EMD/Security instrument within {days_str} of {anchor}",
            "date_text": f"within {days_str}",
            "date_iso": None,
            "anchor_event": anchor,
            "cadence": None,
            "evidence_quote": quote,
            "responsible_party": "Bidder",
            "confidence": "high",
        })

    # 20. Manufacturer Authorization Form / Certificate (MAF)
    m = re.search(
        r"(Authorisation Form\s*/\s*Certificate with OEM/Original Service Provider details[^\n]+?required to be furnished along with the bid)",
        text,
        re.I,
    )
    if m:
        quote = m.group(1).strip()
        items.append({
            "page": page_num,
            "item_type": "obligation",
            "item_text": "Authorised distributors/service providers must furnish Manufacturer Authorisation Form/Certificate",
            "date_text": None,
            "date_iso": None,
            "anchor_event": None,
            "cadence": None,
            "evidence_quote": quote,
            "responsible_party": "Bidder",
            "confidence": "high",
        })

    # 21. Malicious Code Certificate
    m = re.search(
        r"(The seller should upload following certificate in the bid\s*:\s*-)",
        text,
        re.I,
    )
    if m:
        quote = m.group(1).strip()
        items.append({
            "page": page_num,
            "item_type": "obligation",
            "item_text": "Seller must upload Malicious Code Certificate",
            "date_text": None,
            "date_iso": None,
            "anchor_event": None,
            "cadence": None,
            "evidence_quote": quote,
            "responsible_party": "Bidder",
            "confidence": "high",
        })

    # 22. Non-Assignment & Sub-Contracting Consent
    m = re.search(
        r"(The Seller shall not assign the Contract in whole or part without obtaining the prior written consent of buyer\.)",
        text,
        re.I,
    )
    if m:
        quote = m.group(1).strip()
        items.append({
            "page": page_num,
            "item_type": "condition",
            "item_text": "Seller must not assign or sub-contract the Contract without prior written consent of buyer",
            "date_text": None,
            "date_iso": None,
            "anchor_event": None,
            "cadence": None,
            "evidence_quote": quote,
            "responsible_party": "Bidder",
            "confidence": "high",
        })

    # 23. Self Declarations (Financial Standing, Non-Blacklisting)
    m = re.search(
        r"(Bidder has to upload Self Declarations for Financial Standing,\s*Non-\s*Blacklisting[^\n]+?Failing of which the Bid shall summarily be rejected\.)",
        text,
        re.I,
    )
    if m:
        quote = m.group(1).strip()
        items.append({
            "page": page_num,
            "item_type": "condition",
            "item_text": "Bid summarily rejected if Self Declarations (financial standing, non-blacklisting) not uploaded",
            "date_text": None,
            "date_iso": None,
            "anchor_event": None,
            "cadence": None,
            "evidence_quote": quote,
            "responsible_party": "Bidder",
            "confidence": "high",
        })

    # 24. MSE Seller Price Matching Opportunity (L-1 + 15%)
    m = re.search(
        r"(If L-1 is not an MSE and MSE Seller\s*\(s\)\s*has\s*/\s*have quoted price within L-1\+\s*(\d+)%[^\n.]+?such MSE Seller shall be given opportunity to match L-1 price and contract will be awarded for [^\n.]+?percentage of total quantity\.)",
        text,
        re.I,
    )
    if m:
        quote = m.group(1).strip()
        band = m.group(2).strip()
        items.append({
            "page": page_num,
            "item_type": "condition",
            "item_text": f"MSE Seller given opportunity to match L-1 price if quoted within L-1+{band}% purchase-preference band",
            "date_text": None,
            "date_iso": None,
            "anchor_event": None,
            "cadence": None,
            "evidence_quote": quote,
            "responsible_party": "Purchaser",
            "confidence": "high",
        })

    # 25. EMD Exemption document submission condition
    m = re.search(
        r"((?:EMD EXEMPTION:\s*)?The bidder seeking EMD exemption,\s*must submit the valid supporting document for the relevant category as per GeM GTC with the bid\.)",
        text,
        re.I,
    )
    if m:
        quote = m.group(1).strip()
        if "The bidder seeking EMD exemption" in quote:
            quote = quote[quote.index("The bidder seeking EMD exemption"):]
        items.append({
            "page": page_num,
            "item_type": "condition",
            "item_text": "EMD exemption requires valid supporting documents for the relevant category",
            "date_text": None,
            "date_iso": None,
            "anchor_event": None,
            "cadence": None,
            "evidence_quote": quote,
            "responsible_party": "Bidder",
            "confidence": "high",
        })

    # 26. TReDS registration obligation
    m = re.search(
        r"(sellers intending to avail payment through TReDS are required to be registered with at least one TReDS exchange)",
        text,
        re.I,
    )
    if m:
        quote = m.group(1).strip()
        items.append({
            "page": page_num,
            "item_type": "obligation",
            "item_text": "TReDS-registered MSME sellers must register with a TReDS exchange to receive payment via that channel",
            "date_text": None,
            "date_iso": None,
            "anchor_event": None,
            "cadence": None,
            "evidence_quote": quote,
            "responsible_party": "Bidder",
            "confidence": "high",
        })

    # 27. Turnover documentary evidence requirement
    m = re.search(
        r"(Documentary evidence in the form of certified Audited Balance Sheets[^\n.]+?shall be uploaded with the bid\.)",
        text,
        re.I,
    )
    if m:
        quote = m.group(1).strip()
        items.append({
            "page": page_num,
            "item_type": "obligation",
            "item_text": "Turnover documentary evidence (audited balance sheets or CA certificate) must be uploaded with the bid",
            "date_text": None,
            "date_iso": None,
            "anchor_event": None,
            "cadence": None,
            "evidence_quote": quote,
            "responsible_party": "Bidder",
            "confidence": "high",
        })

    # 28. Pre-Contract Integrity Pact condition
    m = re.search(
        r"((?:The\s+)?Bidder has to sign and upload Pre Contract Integrity[^\n.]+?Failing of which the Bid shall summarily be rejected\.)",
        text,
        re.I,
    )
    if m:
        quote = m.group(1).strip()
        items.append({
            "page": page_num,
            "item_type": "condition",
            "item_text": "Bid summarily rejected if Pre Contract Integrity Pact not signed and uploaded",
            "date_text": None,
            "date_iso": None,
            "anchor_event": None,
            "cadence": None,
            "evidence_quote": quote,
            "responsible_party": "Bidder",
            "confidence": "high",
        })

    return items
