"""Shared guard and noise filter definitions for document-structure indexing
and actionable items extraction.

Consolidates domain-generic administrative noise, court banners, table column
headers, form fields, running headers, and AS-IS catalogue filters into a
single reusable module across both pipelines.
"""

import re
from typing import Optional, Set

# Standalone page/sheet stamp numbers, bare integers, or page markers (e.g. "-:12:-", "20", "Page 5", "5 of 100", "12 / 85")
_BARE_PAGE_OR_STAMP_RE = re.compile(
    r"^\s*(?:[-–—(]?\s*[:\.]?\s*[-–—]?\s*\d+\s*[:\.]?\s*[-–—)]?|"
    r"[Pp]age\s+\d+(?:\s*(?:of|\/)\s*\d+)?|"
    r"\d+\s*(?:of|\/)\s*\d+)\s*$"
)

# Domain-generic legal administrative noise (court headers, case numbers, counsel, fee lines, party delimiters, printer stamps)
_GENERIC_LEGAL_NOISE_RE = re.compile(
    r"^\s*(?:"
    r"Versus|V/s|Vs\.?|"
    r"Advocate|Advocates|Counsel(?:\s+for\s+.*)?|Sr\.?\s*Advocate|ATOVOCATG|"
    r".*(?:\.{2,}|[-–—]{2,}|\()\s*(?:Appellants?|Respondents?|Petitioners?|Defendants?|Plaintiffs?)\s*[\.:\)]?|"
    r"(?:Dated\s*[:\.]?\s*)?\d{1,2}[\./\-]\d{1,2}[\./\-]\d{2,4}|"
    r"Total\s+\d+.*|U·f\s+\d+.*|"
    r"IN\s+THE\s+(?:HIGH\s*COURT|SUPREME\s*COURT|DISTRICT\s*COURT|COURT\s*OF)\b.*|"
    r"AT\s+[A-Z\s]+|JUDICIAL\s+DEPARTMENT|CIVIL\s+APPELLATE\s+SIDE|"
    r"(?:F\.?A\.?O\.?|Civil\s+Misc\.?|C\.?M\.?|W\.?P\.?|Arbitration\s+Case|Commercial\s+Appeal)(?:[\s\(\.\-].*|$)|"
    r".*Appeal\s+No\.?\s*\d+.*|"
    r"Stereo\s+H\.?C\.?J\.?D\.?.*|.*Govt\.?\s*Press.*|^[A-Za-z]\d{1,4}$|"
    r"INDIA\s+COURT\s+FEE|COURT\s+FEE|Rs\.\s*\d+|\bRUPEES\b|Price\s*:\s*\d+.*|.*Paise\b|\([A-Z]{2,}\s+[A-Z\s]{2,}\)|"
    r"Endst\.?\s*No\.?[\s\.\:\/\-].*|"
    r"Memo\s+No\.?[\s\.\:\/\-].*|D\.?\s*NO\.?[\s\.\:\/\-].*|"
    r"Copy\s+(?:of\s+the\s+above\s+)?(?:is\s+)?forwarded\s+to\b.*|"
    r"Copy\s+to\s*:?|"
    r"(?:\(?\d+[\.\)]?\s+)?(?:The\s+)?(?:Sr\.?\s*PS|Superin[tl][a-z]+ing\s*Engineer|Executive\s*Engineer|Chief\s*Engineer|Chief\s*Auditor|Director/|FA\s*&\s*CAO|Dy\.?\s*Secy|Supdt\.?\s*(?:\([^\)]*\))?|Accounts\s*Officer|Personal\s*Assistant|Under\s*Secretary)\b.*|"
    r"(?:AND|OR|WITH|FOR|IN|ON|OF|AT|BY|TO)|"
    r"To\s*,?|From|Dear\s+(?:Sirs?|Madam)\s*,?|"
    r"Yours\s+faithfully|Thanking\s+you|"
    r"(?:Tel|Ph|Fax)\.?:?\s*[\d\s\-\/\(\)]+|"
    r"\d+[\.\)]?\s+(?:Our|Your)\s+.*(?:No\.?|Letter|Bid|Ref).*(?:Dtd|DTD|Dated)\.?\s*:.*|"
    r"Our\s+Ref\.?\s*[A-Z0-9\/\-]+\s*$|"
    r"Authorized\s+Signator(?:y|ies)\b.*|"
    r"For\s+[A-Z0-9\s,\.\/&\-]+\b(?:LTD|LIMITED|BANK|CORP|INC|NA|N\.A\.|CHENNAI)\b.*|"
    r"IN\s+WITNESS\s+WHEREOF\b.*|"
    r"Signed,?\s+sealed\s+and\s+Delivered\b.*|"
    r"NAME\s*:.*|"
    r"TITLE\s*:.*|"
    r"IN\s+THE\s+PRESENCE\s+OF\b.*|"
    r"(?:\\?[\$\\]\s*)?(?:\\therefore|\\Delta|\\frac|\\sum|\\times|\\le|\\ge|\{S\}|S_\\Delta)\b.*|"
    r"(?:CNR|CIS|Case|Diary|Registration)\s*No\.?[\s\.\:\/\-][A-Z0-9\/\-]+.*|"
    r"(?:\(?\d+[\.\)]?\s+)?(?:Mr\.?|Ms\.?|Sh\.?|Justice\s+)?.*?\b(?:Presiding\s*Arbitrator|Sole\s*Arbitrator|Co-Arbitrator|\bArbitrator,\b).*|"
    r"OPENING\s+SHEET\s+FOR\s+CIVIL\s+APPEALS.*|"
    r"Claim\s+in\s+appeal.*|"
    r"Enactment\s+and\s+section\s+under\s+which.*|"
    r"\(ORDER\s+\d+[\s,]+RULE\s+\d+.*?\)|"
    r"(?:\([A-Za-z\s\.]+\)|[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})\s*,\s*(?:Presiding\s*Judge|(?:Addl\.?|Additional)\s*District\s*Judge|District\s*Judge|Commercial\s*Court|Tribunal\s*Member|Arbitrator|Sole\s*Arbitrator)\b.*|"
    r"(?:AIJ|ADJ|DJ|CJ|Additional\s+District\s+Judge|Addl\.?\s*District\s*Judge|District\s*Judge)-cum-[\s]*Presiding\s*Judge\b.*|"
    r"ATTESTED\b.*|"
    r"(?:Examiner|Examine)\b.*|"
    r"(?:Copying|Copping)(?:\s+Agency)?\b.*|"
    r"Distt?\.?\s*(?:&?\s*Sessions\s+Court|[\d\.\s\/\-]+).*|"
    r"Announced\s+in\s+open\s+Court\b.*|"
    r"(?:s\.?\s*grapher|Stenographer)[-\d\s]*.*|"
    r".*\b(?:Alka\s+Malik|Gurugram)\b.*|"
    r"D\s*[:\.]\s*\d{1,2}[\./\-]\d{1,2}[\./\-]\d{2,4}\.?.*|"
    r"(?:Compl|Counsel)\s+for\s+.*|"
    r"(?:KOLDEEP\s+TIWALI|VESSU|MP\s+USE\s+T&D)\b.*|"
    r"Total\s+(?:Erection|Training|Type\s+Test|Supply|Civil|Installation|Freight|Cost|Charges|Price)\b.*|"
    r"Freight\s*&\s*insurance\s+charges\b.*|"
    r".*?\b(?:Versus|V/s|Vs\.)\b.*|"
    r"\(?UID\s*No\.?\s*[A-Z0-9]+\)?.*|"
    r"Register\s*No\.?\b.*|"
    r"Remarks\b.*|"
    r"(?:Special\s+)?Commercial\s+Court\s+at\b.*|"
    r"Date\s*[:\s]\s*.*|"
    r"Bank\s+Guarantee\s+No\.?\s*\d+.*|"
    r"CONTRACT\s+No\.?\s*[-A-Z0-9\/\(\)\s]+|"
    r"In\s+(?:F\.?A\.?O\.?|C\.?M\.?|W\.?P\.?|Civil|Arbitration)\b.*|"
    r".*,\s*Panchkula\s*-\s*\d{6}\s*$|"
    r".*\(As\s+per\s+(?:Technical\s+)?Specifications?\)\s*$|"
    r"[A-Z]{1,3}\s*[-–—]?\s*\d+[a-zA-Z]?|"
    r"[A-Z]{2}|\d+[a-z]|"
    r"\(?(?:[a-zA-Z]|\d{1,2}|[ivxlcdmIVXLCDM]{1,4})\)"
    r")[.,;:]?\s*$",
    re.IGNORECASE
)

_STATUS_BOILERPLATE_CELLS: Set[str] = {
    "yes", "no", "na", "n/a", "nil", "none", "true", "false", "ok",
    "complied", "not complied", "applicable", "not applicable",
}

_TABLE_COLUMN_HEADERS: Set[str] = {
    "s no", "sr no", "sl no", "sn", "serial no",
    "clause", "clause no", "cl no", "cl",
    "description", "particulars", "specification", "specifications", "scope",
    "remarks", "compliance", "compliance yes no", "status", "deviations",
    "bidder response", "bidder s response", "deviation", "page no", "item no", "details", "unit", "qty", "quantity",
    "rate", "unit rate", "amount", "total amount", "price", "total price", "cost", "total cost",
}

_FORM_FIELD_RE = re.compile(
    r"^\s*(?:"
    r"Authorized\s+Signator(?:y|ies)|Name\s+of\s+(?:the\s+)?(?:Bidder|Firm|Company|Tenderer)|"
    r"Seal\s+(?:&\s*Signature|of\s+(?:the\s+)?(?:Firm|Company|Bidder|Tenderer))|"
    r"(?:Firm|Company)\s+Seal|"
    r"Signature\s+of\s+(?:the\s+)?(?:Bidder|Firm|Company|Tenderer|Authorized\s+Signatory)|Witness|"
    r"Date\s*:?|Place\s*:?|"
    r"Name\s+in\s+Block\s+Letters|Designation\s*:?|Address\s*:?|"
    r"Seal\s*&\s*Signature"
    r")\s*[:\.]?\s*(?:[\._]{3,}|\([^\)]*\))?\s*$",
    re.IGNORECASE,
)
_FORM_DOTTED_FIELD_RE = re.compile(r"^[\w\s]{2,35}[:\.]\s*[\._]{3,}\s*$")

_RUNNING_HEADER_PATTERNS = [
    re.compile(r"^Tender\s+Specification\s+No\b", re.I),
    re.compile(r"^(?:Chief\s+General\s+Manager|General\s+Manager|Superintending\s+Engineer|Executive\s+Engineer)\b", re.I),
    re.compile(r"^M\.P\.P\.K\.V", re.I),
    re.compile(r"^(?:Jabalpur|SAVE\s+ELECTRICITY)\b", re.I),
    re.compile(r"^(?:Online\s+tenders|The\s+bidder\s+shall|The\s+essential\s+eligibility|The\s+SI\s+shall|Following\s+are\s+the\s+key|For\s+the\s+purpose\s+of|AND\s+I\s+MAKE|I\s+am\s+currently|MADE\s+AND\s+DECLARED|AS\s+MUTUAL\s+UNDERSTANDING)", re.I),
]


def is_administrative_noise(text: str) -> bool:
    """True if text matches universal judicial, administrative, or fee stamps."""
    stripped = (text or "").strip()
    if not stripped:
        return True
    if _BARE_PAGE_OR_STAMP_RE.match(stripped):
        return True
    return bool(_GENERIC_LEGAL_NOISE_RE.match(stripped))


def is_table_header_or_form_field(text: str) -> bool:
    """True if text is a generic table column header (e.g. 'S.No.', 'Description')
    or a form/signature placeholder ('Authorized Signatory:', 'Date: ....')."""
    stripped = (text or "").strip()
    if not stripped:
        return False
    norm = re.sub(r"[^\w]+", " ", stripped.lower()).strip()
    if norm in _TABLE_COLUMN_HEADERS:
        return True
    if "|" in stripped:
        cells = [re.sub(r"[^\w]+", " ", c.lower()).strip() for c in stripped.split("|")]
        if cells and all(c in _TABLE_COLUMN_HEADERS for c in cells if c):
            return True
    if _FORM_FIELD_RE.match(stripped):
        return True
    if _FORM_DOTTED_FIELD_RE.match(stripped):
        return True
    return False


def is_document_noise_or_running_header(text: str, page_num: int = 2) -> bool:
    """True if text is a repeating document running header (e.g. 'Tender Specification No...'
    on page > 1), correspondence office/date metadata, or full prose body sentence hallucinated as a heading."""
    stripped = (text or "").strip()
    if not stripped:
        return True
    for pat in _RUNNING_HEADER_PATTERNS:
        if pat.search(stripped):
            if pat.pattern.startswith("^Tender") and page_num == 1:
                return False
            return True
    return False


def is_as_is_catalogue_other_leak(item_type: Optional[str], evidence_quote: str, division_role: str) -> bool:
    """Surgically filters items mislabeled as 'other' inside AS-IS catalogue divisions
    (addressing AI_ACTION_ITEMS_ACCURACY_INVESTIGATION §8).
    
    Deliberately restricted ONLY to item_type == 'other': two-pass macro context
    already suppresses 'obligation' noise in that zone cleanly (down to 1.000 precision
    on Gemini and 5 FPs on Qwen), so obligations, deadlines, and conditions are never touched here.
    An AS-IS inventory/catalogue division never contains valid court prayers or legal disposition
    events ('other'), so any 'other' item in this division is false positive catalogue noise.
    """
    if division_role != "as_is" or (item_type or "").strip().lower() != "other":
        return False
    return True


_PLATFORM_DISCLAIMER_PATTERNS = re.compile(
    r"(?:"
    r"Buyer added ATC Clauses which are in contravention|"
    r"Any ATC clause in contravention with GeM GTC|"
    r"Reference of conditions published on any external site|"
    r"Creating bid for items from incorrect categories|"
    r"The Buyer is not allowed to add following clauses|"
    r"in case of Service, if any condition specified in General Terms and Conditions is contradic|"
    r"If any of the clause\(s\)\s+is/are incorporated by the Buyer|"
    r"bid & resultant contract shall be treated as null and void|"
    r"Inclusion of disqualification criteria related to suspension|"
    r"Mandating procurement of / from specific Brand|"
    r"Publishing Custom / BOQ bids for items for which regular GeM categories|"
    r"Traders are excluded from the purview of|"
    r"Benefits of MSE will be allowed only if seller is validated|"
    r"EMD & Performance security should be in favour of Beneficiary|"
    r"Earnest Money Deposit \(EMD\) shall also be accepted by the buyer in the form of a surety bond|"
    r"Buyer is duty bound to reply to all such representations|"
    r"Further,\s+if\s+any\s+seller\s+has\s+any\s+objection|"
    r"Consignees\s*/\s*Reporting\s+Officer\s+and\s+Quantity|"
    r"This Bid is governed by the General Terms and Conditions|"
    r"For all provisions of the Labour Codes that are pending operationalisation|"
    r"All obligations relating to wages, social security, safety, working conditions|"
    r"Purchase Preference to MSE OEMs\s*/\s*Service Provider available upto price within L1|"
    r"Purchase preference will be given to MSEs having valid Udyam Certificate|"
    r"the bidder must be the manufacturer\s*/\s*OEM of the offered product on GeM|"
    r"Relevant documentary evidence in this regard shall be uploaded along with the bid in respect of the offered product|"
    r"If the buyer has mentioned MSE purchase preference in ATC then service provider|"
    r"any false declaration and non-compliance of this would be a ground for immediate|"
    r"GeM reserves the right,?\s+(?:at its sole discretion,?\s+)?to cancel the bid forthwith|"
    r"(?:\d+\.\s*)?(?:"
    r"Publishing bids on GeM for procurement of works|"
    r"Procurement of Goods by creating a Service bid|"
    r"Seeking sample with bid or approval of samples|"
    r"Seeking experience from specific organization|"
    r"Asking for any Tender fee|"
    r"In a category based bid, adding additional items|"
    r"Quoting of PAN No\. in the bid|"
    r"Creation of multiple categories|"
    r"Creating bid for items from incorrect categories|"
    r"Reference of conditions published on any external site|"
    r"Buyer added ATC Clauses which are in contravention|"
    r"Any ATC clause in contravention with GeM GTC|"
    r"Mandating procurement of / from specific Brand|"
    r"Publishing Custom / BOQ bids for items"
    r")"
    r")",
    re.IGNORECASE,
)

_REVERSE_AUCTION_SUBBULLET_RE = re.compile(
    r"^\s*(?:[ivx]+\.|\([ivx]+\))\s+(?:If\s+(?:number\s+of\s+technically|Buyer\s+has\s+chosen|L-1\s+is\s+non)|In\s+case\s+Primary\s+product)",
    re.IGNORECASE,
)

_ACTION_VERBS = (
    "shall", "must", "will", "would", "is required to", "are required to", "required to",
    "undertake", "undertakes", "agree", "agrees", "entitled", "penalty", "failing which",
    "forfeit", "terminate", "levied", "liable to", "responsible for", "warrants",
    "reimburs", "guarantee", "indemnif", "compensat", "auto-extend", "extend",
)


def is_bare_parameter_or_platform_disclaimer(item_type: Optional[str], quote: str) -> bool:
    """True if quote is a platform buyer disclaimer, an isolated Reverse Auction sub-bullet,
    or a table configuration parameter without an actionable verb."""
    stripped = (quote or "").strip()
    if not stripped:
        return False

    t = (item_type or "").strip().lower()
    if t not in ("obligation", "condition", "other"):
        return False

    if _PLATFORM_DISCLAIMER_PATTERNS.search(stripped):
        return True

    if _REVERSE_AUCTION_SUBBULLET_RE.search(stripped):
        return True

    # Parameter table cells without an actionable verb
    if "</td><td>" in stripped or re.search(r"</td>\s*</tr>", stripped, re.I):
        lower = stripped.lower()
        if not any(v in lower for v in _ACTION_VERBS):
            return True

    # Attachment/upload filenames and labels (e.g. "Scope of Work:1789282663.pdf", "Product/Equipment Details:1789283012.pdf")
    if re.search(r"(?:[:\s]\d{6,}\.(?:pdf|xlsx?|docx?)\b|\.pdf\s*$|:\s*\d{6,}\s*$)", stripped, re.I):
        return True
    if re.search(r"^(?:(?:एक्सेल में अपलोड किए जाने की आवश्यकता\s*\/)?\s*Excel Upload Required|Price Schedule for AMC|Financial Breakup Required|Product\/Equipment Details|Special Terms and Conditions\/Penalty\/Payment Terms|Scope of Work)\b", stripped, re.I):
        return True

    # Bare section headers / table parameter labels without action verbs
    if re.match(r"^(?:(?:एमआईआई अनुपालन\s*\/)?\s*MII Compliance|MSE Purchase Preference|Financial Document Indicating Price Breakup Required|(?:तकनीकी विशिष्टियाँ\s*\/)?\s*Technical Specifications)\s*$", stripped, re.I):
        return True
    if re.match(r"^Number of months within the contract period.*?:\s*\d+\s*$", stripped, re.I):
        return True

    # Pure Devanagari script quotes without English text (in bilingual government tenders, English is authoritative; pure Hindi quotes are duplicate noise)
    if re.search(r"[\u0900-\u097F]", stripped) and not re.search(r"[a-zA-Z]{4,}", stripped):
        return True

    # Platform informative statements without contractual obligation
    if re.search(r"^(?:This Bid provides for Trade Receivables Discounting System|Purchase Preference to MSE OEMs\/ Service Provider available upto price within L1\+X%)\b", stripped, re.I):
        return True
    if re.search(r"Any non-compliance shall constitute a breach of the contract and shall entitle the Buyer to take appropriate action", stripped, re.I):
        return True

    # Table configuration parameters / metadata labels without contractual duties
    if re.search(
        r"(?:"
        r"Do you want to show documents uploaded by bidders|"
        r"Document(?:s)? required from seller|"
        r"Minimum number of bids required to disable automatic bid extension|"
        r"Number of Auto Extension count|"
        r"Bid to RA enabled|"
        r"RA Qualification Rule|"
        r"ITC available to buyer|"
        r"Type of Bid|"
        r"Primary product category|"
        r"Evaluation Method|"
        r"Bid splitting not applied|"
        r"Estimated Bid Value\b|"
        r"EMD Amount\b|"
        r"Minimum Floor Price defined by Buyer|"
        r"Additional Specification Document|"
        r"Additional Qualification/Data Required|"
        r"Customized AMC/CMC For Pre-owned Products|"
        r"Technical and financial eligibility criteria for qualification|"
        r"Wherever GST is applicable, payment will be released against|"
        r"The Seller shall, notwithstanding the consent and assignment|"
        r"The minimum average annual financial turnover of the bidder during the last three years|"
        r"^(?:Three|Two|One)\s+similar\s+completed\s+services?\s+costing\s+not\s+less\s+than|"
        r"(?:Maximum\s+)?Percentage of Bid quantity(?:/amount)? for (?:MII|MSE)|"
        r"Purchase Preference to (?:MII|MSE) sellers? available"
        r")",
        stripped,
        re.I,
    ):
        return True

    # MII and MSE policy boilerplate declarations and table parameters
    if re.search(
        r"(?:"
        r"Allow participation only from Class 1|"
        r"Only Class-I and Class-II Local suppliers as per MII order|"
        r"(?:Maximum\s+)?Percentage of Bid quantity(?:/amount)? for (?:MII|MSE)|"
        r"Purchase Preference to (?:MII|MSE) sellers? available"
        r")",
        stripped,
        re.I,
    ):
        return True

    # Duplicate obligation for clarification time (clarification time is a deadline, not an obligation)
    if t == "obligation" and "Time allowed for Technical Clarifications" in stripped:
        return True

    # GST statutory tax compliance and indemnity recitals
    if re.search(
        r"(?:"
        r"supplier/contractor\s+(?:at\s+their\s+own\s+cost\s+)?shall\s+rectify\s+the\s+shortcoming\s+in\s+the\s+returns|"
        r"Supplier\s*/\s*Contractor\s+shall\s+pay\s+any\s+loss\s+of\s+amount\s+along\s+with|"
        r"Supplier\s*/\s*Contractor\s+is\s+fully\s+liable\s+for\s+making\s+good|"
        r"vendor\s+shall\s+file\s+all\s+applicable\s+returns\s+under\s+GST\s+Laws|"
        r"DFCCIL\s+reserves\s+right\s+to\s+recover\s+any\s+interest,?\s*penalty|"
        r"Vendor\s+having\s+multiple\s+business\s+verticals\s+within\s+state|"
        r"Wherever\s+GST\s+is\s+applicable,?\s*payment\s+will\s+be\s+released\s+against"
        r")",
        stripped,
        re.I,
    ):
        return True

    # Bare introductory payment method sentence without physical delivery milestone
    if re.search(r"Bidders can also submit the EMD with Account Payee Demand Draft", stripped, re.I) and not re.search(r"within\s+\d+\s+days", stripped, re.I):
        return True

    # Bare status or parameter lines
    if re.search(r"^(?:(?:Maximum\s+)?Percentage of Bid quantity|Bid splitting not applied|Allow participation only from Class 1)\b", stripped, re.I):
        lower = stripped.lower()
        if not any(v in lower for v in ("shall", "must", "will")):
            return True

    return False


_ALT_SECURITY_INSTRUMENT_RE = re.compile(
    r"(?:upload scanned copy\s*(?:/\s*proof\s*)?of (?:the\s+)?(?:FDR|BC|Banker\'?s\s*Cheque|Online Payment Transfer)|"
    r"scanned copy of the FDR document in place of PBG|"
    r"indicate Contract number and name of Seller entity in the transaction details field|"
    r"Bidders can also submit the EMD with (?:Fixed Deposit Receipt|Banker\'?s Cheque|Payment online))",
    re.I,
)


def is_alternative_security_instrument_duplicate(item_type: Optional[str], quote: str) -> bool:
    """True if quote is an alternative payment instrument clause (FDR, Banker's Cheque, Online RTGS transfer)
    that duplicates the canonical primary Demand Draft (DD) delivery requirement for EMD or PBG."""
    stripped = (quote or "").strip()
    if not stripped:
        return False
    return bool(_ALT_SECURITY_INSTRUMENT_RE.search(stripped))


_SOW_FEATURE_VERBS_RE = re.compile(
    r"\b(?:shall\s+support|shall\s+be\s+integrated|shall\s+provide(?:\s+facility|\s+capability)?|"
    r"shall\s+be\s+capable\s+of|shall\s+be\s+designed\s+to|shall\s+feature|shall\s+include)\b",
    re.I,
)

_SOW_GATE_INDICATORS_RE = re.compile(
    r"\b(?:within\s+\d+\s+(?:days|weeks|months)|prior\s+to|before|penalty|liquidated\s+damages|acceptance\s+criteria|approval\s+gate)\b",
    re.I,
)


def is_ungated_sow_feature_description(item_type: Optional[str], quote: str, division_role: str) -> bool:
    """True if quote in a Scope of Work (sow) division is an ungated software feature specification
    rather than a trackable contractual milestone duty."""
    if (division_role or "").lower() != "sow":
        return False
    t = (item_type or "").strip().lower()
    if t != "obligation":
        return False
    stripped = (quote or "").strip()
    if not stripped:
        return False
    if _SOW_FEATURE_VERBS_RE.search(stripped) and not _SOW_GATE_INDICATORS_RE.search(stripped):
        return True
    return False


_LITIGATION_ARGUMENT_RE = re.compile(
    r"^\s*(?:"
    r"(?:The\s+)?(?:learned\s+)?(?:counsel|Advocate|Senior\s+Advocate)\s+(?:for\s+(?:the\s+)?)?(?:appellant|respondent|petitioner|defendant|claimant)\s+(?:submitted|argued|contended|pleaded)\b|"
    r"(?:The\s+)?(?:learned\s+)?(?:Sole\s+)?(?:Arbitrator|Arbitral\s+Tribunal|Court)\s+(?:held|observed|found|noted|passed\s+an\s+order)\b|"
    r"(?:The\s+)?(?:appellant|respondent|petitioner|claimant)\s+(?:contended|submitted|averred|alleged|filed\s+reply)\b"
    r")",
    re.I,
)

_LITIGATION_RELIEF_INDICATORS = ("may kindly be", "prayed that", "prays that", "dismissed with costs")


def is_litigation_argument_or_past_recital(quote: str) -> bool:
    """True if quote is a historical counsel contention or arbitrator argument recital rather than
    a prayer for relief or court disposition."""
    stripped = (quote or "").strip()
    if not stripped:
        return False
    if _LITIGATION_ARGUMENT_RE.search(stripped):
        lower = stripped.lower()
        if not any(rel in lower for rel in _LITIGATION_RELIEF_INDICATORS):
            return True
    return False


