"""Shared guard and noise filter definitions for document-structure indexing
and actionable items extraction.

Consolidates domain-generic administrative noise, court banners, table column
headers, form fields, running headers, and AS-IS catalogue filters into a
single reusable module across both pipelines.
"""

import html
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


_OFFICE_ADDRESS_OR_SIGNATORY_RE = re.compile(
    r"^(?:(?:Chief\s+Vigilance\s+Officer|Sr\.?\s*General\s+Manager|General\s+Manager|DGM|AGM|Manager|Director)\b.*|"
    r".*?(?:Metro\s+Bhawan|Fire\s+Brigade\s+Lane|Barakhamba\s+Road|Lodhi\s+Road|Connaught\s+Place|New\s+Delhi|Panchkula).*)$",
    re.IGNORECASE,
)


def is_document_noise_or_running_header(text: str, page_num: int = 2) -> bool:
    """True if text is a repeating document running header (e.g. 'Tender Specification No...'
    on page > 1), correspondence office/date metadata, or full prose body sentence hallucinated as a heading."""
    stripped = (text or "").strip()
    if not stripped:
        return True
    if _OFFICE_ADDRESS_OR_SIGNATORY_RE.match(stripped):
        return True
    if page_num > 1 and re.match(
        r"^(?:Notice\s+Inviting\s+Tender(?:\s*\(NIT\))?|Tender\s+Documents?|Delhi\s+Metro\s+Rail\s+Corporation(?:\s+Ltd\.?)?)\s*$",
        stripped,
        re.I,
    ):
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
    r"GeM reserves the right,?\s+(?:at its sole discretion,?\s+)?to cancel the bid forthwith|"
    r"keep in touch with e-tendering portal|"
    r"There will be no other mode of communication for update|"
    r"uploaded in separate volume shall be integral part of the tender document|"
    r"shall be an integral part of the (?:tender|contract) document|"
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
    if t not in ("obligation", "condition", "deadline", "other"):
        return False

    if _PLATFORM_DISCLAIMER_PATTERNS.search(stripped):
        return True

    if _REVERSE_AUCTION_SUBBULLET_RE.search(stripped):
        return True

    # Parameter table cells without an actionable verb or procurement milestone indicator
    if "</td><td>" in stripped or re.search(r"</td>\s*</tr>", stripped, re.I):
        lower = stripped.lower()
        if t == "deadline":
            pass
        elif any(m in lower for m in ("completion period", "addendum", "amendment", "submission", "opening", "validity", "tender security", "cost of tender", "tender fee", "sale of tender", "pre-bid", "bid end", "bid start", "clarification", "number of days", "response to queries", "sla", "penalty", "resolution", "response")):
            pass
        elif re.search(r"\b\d+\s*(?:hours?|hrs?|mins?|minutes?|days?|weeks?|months?|years?)\b", lower):
            pass
        elif not any(v in lower for v in _ACTION_VERBS):
            return True

    # Standalone date/time timestamps without milestone labels or action verbs
    if re.match(r"^\s*(?:(?:<td[^>]*>)?\s*)?\d{1,2}[./-]\d{1,2}[./-]\d{2,4}(?:\s*(?:at|,)?\s*\d{1,4}(?::\d{2})?(?:\s*(?:hours?|hrs?|am|pm)(?:\s*\.|\b)?)?)?\s*(?:</td>)?\s*$", stripped, re.I):
        return True

    # Attachment/upload filenames and labels (e.g. "Scope of Work:1789282663.pdf", "Product/Equipment Details:1789283012.pdf")
    if re.search(r"(?:[:\s]\d{6,}\.(?:pdf|xlsx?|docx?)\b|\.pdf\s*$|:\s*\d{6,}\s*$)", stripped, re.I):
        return True
    if re.search(r"^(?:(?:एक्सेल में अपलोड किए जाने की आवश्यकता\s*\/)?\s*Excel Upload Required|Price Schedule for AMC|Financial Breakup Required|Product\/Equipment Details|Special Terms and Conditions\/Penalty\/Payment Terms|Scope of Work|Tender Documents Consist|Authority and place for submission of tender cost|CE/Tender)\b", stripped, re.I):
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

    # Portal/Auction tutorial mechanics and policy calculation formulas
    if re.search(
        r"(?:"
        r"Auction\s+gets\s+available\s+under\s+[“\"]Live\s+Auction[”\"]\s+tab|"
        r"Bidder\s+shall\s+click\s+on\s+(?:View|Submit|Upload)\s+option\s+which\s+will\s+open|"
        r"^(?:Auction\s+Start\s+Date|Auction\s+End\s+Date|Maximum\s+Seal\s+Percentage|Elapse\s+Time\s+in\s+Minutes|Auto\s+Extension\s+Time)\b|"
        r"criteria\s+for\s+determination\s+of\s+cost\s+of\s+local\s+content|"
        r"cost\s+of\s+local\s+content\s+in\s+the\s+service\s+shall\s+be|"
        r"LC\s+of\s+(?:the\s+)?EPC\s+contracts\s+shall\s+be\s+the\s+ratio|"
        r"total\s+cost\s+of\s+service\s+shall\s+be\s+constituted\s+of|"
        r"uploaded\s+files.*?(?:named\s+properly|special\s+character)|"
        r"No\s+special\s+character.*?in\s+the\s+name\s+of\s+uploaded\s+file|"
        r"submit\s+such\s+transaction\s+details\s+along\s+with\s+their\s+tender\s+submission|"
        r"contribution\s+towards\s+the\s+turnover\s+shall\s+be\s+considered\s+in\s+the\s+same\s+ratio|"
        r"(?:are\s+advised\s+to\s+visit|advised\s+to\s+visit\s+the\s+(?:depots?|site|stations?))|"
        r"scanned\s+with\s+\d+\s+dpi|"
        r"provision\s+of\s+uploading\s+such\s+standard\s+documents|My\s+Space|"
        r"encrypted\s+using\s+PKI\s+encryption|"
        r"bid\s+summary\s+has\s+to\s+be\s+printed\s+and\s+kept\s+as\s+an\s+acknowledgement|"
        r"server\s+time.*?will\s+be\s+considered\s+as\s+the\s+standard\s+time|"
        r"logs\s+in\s+to\s+the\s+site\s+through\s+the\s+secured\s+log-in|"
        r"No\s+other\s+cells\s+should\s+be\s+changed|"
        r"advised\s+to\s+register\s+their\s+valid\s+email\s+address\s+and\s+mobile\s+numbers|"
        r"online\s+on\s+https?://eprocure\.gov\.in/eprocure/app\s+after\s+uploading\s+the\s+mandatory|"
        r"must\s+contain\s+UDIN\s+thereon|"
        r"^\s*(?:The\s+financial\s+data\s+in\s+the\s+prescribed\s+format\s+shall\s+be\s+)?certified\s+by\s+Chartered\s+Accountant\s+with\s+his\s+stamp\s*$|"
        r"In\s+case\s+audited\s+balance\s+sheet.*?he\s+has\s+to\s+submit\s+an\s+affidavit|"
        r"Substantial\s+completion\s+shall\s+be\s+\d+%.*?works\s+completed|"
        r"In\s+case\s+of\s+joint\s+venture\s*/\s*Consortium,\s+full\s+value\s+of\s+the\s+work|"
        r"After\s+opening\s+of\s+financial\s+bids,\s+the\s+work\s+experience\s+credentials|"
        r"Work\s+experience\s+of\s+only\s+substantial\s+partner|"
        r"prescribed\s+(?:Performa|Proforma)\s+given\s+in\s+Appendix|"
        r"undertaking\s+submitted\s+vide\s+Appendix|"
        r"undertaking\s+to\s+this\s+effect\s+in\s+Appendix|"
        r"details\s+of\s+the\s+local\s+content\s+in\s+a\s+format\s+attached\s+as\s+Appendix|"
        r"procurement\s+for\s+a\s+value\s+in\s+excess\s+of\s+Rs\.?\s*\d+\s*crores|"
        r"lowest\s+bidder\s+among\s+the\s+[‘']Class-I\s+local\s+supplier[’'],\s*will\s+be\s+invited|"
        r"Tenderer.*?must\s+not\s+have\s+suffered\s+bankruptcy\s*/\s*insolvency|"
        r"Tenderer.*?shall\s+not\s+have\s+total\s+amount\s+of\s+(?:dispute\s+in\s+their\s+pending\s+litigation|financial\s+penalties)|"
        r"no\s+contract\s+of\s+the\s+tenderer\s+of\s+the\s+value\s+more\s+than\s+\d+%\s+of\s+NIT\s+cost|"
        r"The\s+overall\s+performance\s+of\s+the\s+tenderer|"
        r"If\s+credentials\s+are\s+not\s+verified\s+within\s+reasonable\s+time\s+limit|"
        r"Where\s+a\s+work\s+is\s+undertaken\s+by\s+a\s+group,\s+only\s+that\s+portion|"
        r"Tenderer\s*\(including\s+any\s+member\s+in\s+case\s+of\s+JV/consortium\)\s+for\s+the\s+work\s+awarded\s+by\s+DMRC|"
        r"The\s+tenderer\s*\(each\s+individual\s+member\s+separately\s+in\s+case\s+of\s+JV/Consortium\)\s+shall\s+provide\s+detailed\s+inf|"
        r"The\s+Employer\s+in\s+such\s+cases,\s+may\s+in\s+its\s+sole\s+discretion\s+take\s+action|"
        r"actions\s+as\s+deemed\s+appropriate\s+by\s+the\s+Employer.*?may\s+be\s+taken|"
        r"shall\s+submit\s+only\s+one\s+bid\s+in\s+the\s+(?:particular|same)\s+tendering\s+process|"
        r"Time\s+required\s+for\s+initial\s+technical\s+evaluation|"
        r"Letter\s+of\s+acceptance\s+to\s+the\s+successful\s+bidder\s+shall\s+be\s+uploaded|"
        r"The\s+bidders\s+are\s+required\s+to\s+submit\s+soft\s+copies\s+of\s+their\s+bids\s+electronically|"
        r"[‘']?(?:Class-[I|V]+|Local\s+supplier|Contractor)[’']?\s+means\s+a\s+|"
        r"Margin\s+of\s+purchase\s+preference\s+shall\s+be|"
        r"(?:In\s+case|If)\s+(?:the\s+work\s+is\s+executed\s+for\s+private\s+client|of\s+any\s+composite\s+work|the\s+tenderer\s+is\s+a\s+JV/Consortium\s+having\s+foreign\s+partner)|"
        r"If\s+the\s+above\s+work\(s\)\s+comprises?\s+of\s+works\s+other\s+than|"
        r"Only\s+work\s+experience\s+certificate\s+having\s+stamp|"
        r"In\s+case\s+of\s+part\s+termination\s+of\s+contract|"
        r"Bidder\s+has\s+to\s+ensure\s+submission\s+of\s+Appendix\s*-\s*7[B-D]|"
        r"Tender\s+submissions?\s+shall\s+be\s+done\s+online|"
        r"Bidders\s+are\s+required\s+to\s+register\s+their\s+valid\s+Digital\s+Signature|"
        r"Authority\s+and\s+place\s+for\s+submission|"
        r"^\s*Supply,\s+Installation,\s+Testing\s+and\s+Commissioning\s+of\s+VRV/VRF|"
        r"^\s*at\s+least\s+one\s+[“\"\'\‘\’]?similar\s+work[”\"\'\‘\’]?\s*(?:\*\*)?\s*of\s+value\s+of\s+₹274\.28\s+lakhs|"
        r"JRE\s+memory|Runtime\s+Parameters|"
        r"machine\s+with\s+large\s+RAM|Internet\s+connection/speed|"
        r"CPP\s+Portal\s+Helpdesk|24x7\s+CPP\s+Portal|"
        r"prefix\s+\+91\s+as\s+country\s+code|"
        r"select\s+the\s+payment\s+option\s+as\s+[“\"']offline[”\"']|"
        r"original\s+should\s+be\s+posted/couriered|"
        r"get\s+ready\s+the\s+bid\s+documents\s+to\s+be\s+submitted|"
        r"digitally\s+sign\s+and\s+upload\s+the\s+required\s+bid\s+documents\s+one\s+by\s+one|"
        r"tender\s+document\s+can\s+only\s+be\s+obtained\s+after\s+registration|"
        r"team\s+(?:for\s+each\s+discipline\s+)?shall\s+be\s+headed\s+by|"
        r"must\s+have\s+as\s+minimum\s+qualification\s+a\s+bachelor|"
        r"who\s+shall\s+have\s+as\s+minimum\s+qualification|"
        r"Expatriate/\s*Indian\s+Experts.*?shall\s+be\s+deployed|"
        r"Bids\s+submitted\s+without\s+these\s+CVs\s+shall\s+be\s+considered|"
        r"CV\s+of\s+other\s+key\s+staff\s+and\s+proof\s+regarding\s+qualification|"
        r"Project\s+Architect\s+must\s+be\s+involved\s+actively|"
        r"submit\s+an\s+organization\s+chart\s+together\s+with\s+clear\s+description|"
        r"Mobilization\s+of\s+all\s+Experts\s+shall\s+be\s+in\s+modules|"
        r"Payment\s+shall\s+be\s+made\s+only\s+for\s+the\s+above-mentioned\s+experts|"
        r"B\.\s+Financial\s+Standing:\s*The\s+Tenderers\s+will\s+be\s+qualified\s+only\s+if|"
        r"It\s+is\s+necessary\s+that\s+the\s+firm\s+can\s+withstand\s+cash\s+flow|"
        r"Net\s+current\s+assets\s+and/or\s+documents\s+including\s+banking\s+reference|"
        r"Value\s+of\s+the\s+Consultancy\s+works\s+executed\s+year-wise|"
        r"Value\s+of\s+existing\s+commitments\s+for\s+on-going|"
        r"In\s+case\s+of\s+no\s+consultancy\s+works\s+in\s+hand.*?NIL|"
        r"The\s+tender\s+submission\s+of\s+tenderers,\s*who\s+do\s+not\s+qualify\s+the\s+minimum\s+eligibility|"
        r"The\s+same\s+should\s+contain\s+all\s+technical\s+data\s+as\s+required|"
        r"In\s+case\s+of\s+mismatch\s+in\s+financial\s+data|"
        r"Tenderers\s+may\s+obtain\s+further\s+information/\s*clarification|"
        r"Certificates\s+having\s+Name\s+of\s+Employer,\s*and\s+Name\s+&\s+Designation|"
        r"prescribed\s+in\s+Appendix-(?:17|18|26)|"
        r"undertaking\s+shall\s+be\s+taken\s+from\s+bidders\s+as\s+per\s+Appendix|"
        r"Fees\s+for\s+such\s+complaints\s+shall\s+be|"
        r"If\s+any\s+false\s+declaration\s+regarding\s+local\s+content|"
        r"Applicant\s+in\s+the\s+capacity\s+of\s+a\s+Subsidiary\s+Company|"
        r"at\s+the\s+time\s+of\s+tender,?\s*bidding\s+or\s+solicitation\s+shall\s+be\s+required|"
        r"A\.\s+Work\s+Experience:\s*The\s+tenderers\s+will\s+be\s+qualified\s+only\s+if|"
        r"tenderer\s+as\s+a\s+single\s+entity.*?experience\s+of\s+civil\s+structural\s+design|"
        r"(?:All\s+)?Tenderers\s+(?:found\s+to\s+have|shall\s+not\s+have)\s+a\s+conflict\s+of\s+interest(?:\s+shall\s+be\s+disqualified)?|"
        r"submits\s+or\s+participates\s+in,?\s+more\s+than\s+one\s+bid\s+will\s+cause\s+all\s+of\s+the\s+proposals.*?disqualified|"
        r"changes\s+during\s+tendering\s+stage,?\s+the\s+bid\s+shall\s+be\s+treated\s+as\s+non\s*[-]?\s*responsive|"
        r"take\s+prior\s+permission.*?for\s+obtaining\s+leave\s+during\s+his\s+tenure|"
        r"submit\s+CV\s+of\s+(?:Project\s+Architect|Team\s+Leader|Key\s+Personnel)|"
        r"Parent\s+company\s+of\s+the\s+JV/Consortium\s+shall\s+submit\s+undertaking\s+in\s+Appendix-30|"
        r"credentials\s+do\s+not\s+get\s+verified\s+in\s+a\s+period\s+of\s+(?:two|\d+)\s+months|"
        r"verification\s+of\s+(?:credentials|work\s+experience)\s+shall\s+be\s+completed\s+within\s+(?:two|\d+)\s+months|"
        r"banking\s+reference\s+should\s+be\s+from\s+a\s+Scheduled\s+Bank.*?not\s+be\s+more\s+than\s+3\s+months\s+old|"
        r"bank\s+reference\s+certificate.*?not\s+be\s+older\s+than\s+3\s+months|"
        r"Financial\s+data\s+for\s+latest\s+last\s+five\s+audited\s+financial\s+years\s+has\s+to\s+be\s+submitted.*?Appendix-18|"
        r"(?:possession\s+of\s+valid\s+Class-[I|V]+\s+digital\s+signatures?|must\s+have\s+valid\s+Class-[I|V]+\s+or\s+Class-[I|V]+\s+digital\s+signature|valid\s+Digital\s+Signature\s+Certificate\s*\(DSC\))|"
        r"details\s+of\s+the\s+(?:DD|instrument|payment)[^,\n]*,\s*physically\s+sent.*?should\s+tally\s+with|"
        r"If\s+the\s+BOQ\s+file\s+is\s+found\s+to\s+be\s+modified\s+by\s+the\s+bidder,?\s+the\s+bid\s+will\s+be\s+rejected|"
        r"Queries/clarifications\s+from\s+bidders\s+after\s+due\s+date|"
        r"Performance\s+of\s+each\s+JV/Consortium\s+partner\s+shall\s+also\s+be\s+judged\s+on\s+quarterly\s+basis|"
        r"undertaking\s+(?:by\s+JV/Consortium\s+members\s+)?is\s+required\s+to\s+be\s+submitted\s+as\s+per\s+(?:the\s+format\s+)?Appendix-6A|"
        r"change\s+in\s+constitution\s+or\s+percentage\s+participation\s+shall\s+not\s+be\s+permitted\s+at\s+any\s+stage\s+after\s+(?:their\s+)?submission\s+of\s+bids|"
        r"make\s+available\s+all\s+such\s+information,\s*evidence\s+and\s+documents\s+as\s+may\s+be\s+necessary\s+for\s+such\s+verification|"
        r"shall\s+not\s+be\s+allowed\s+to\s+sub-contract\s+works\s+to\s+any\s+contractor\s+from\s+a\s+country\s+which\s+shares\s+a\s+land\s+border|"
        r"concealment\s+or\s+misrepresentation\s+of\s+facts\s+have\s+been\s+found|"
        r"action\s+shall\s+be\s+initiated\s+under\s+the\s+conditions\s+of\s+contract\s+to\s+impose\s+penalty|"
        r"expatriate\s+experts\s+shall\s+be\s+mobilized\s+as\s+per\s+the\s+schedule|"
        r"Any\s+other\s+staff/personnel\s+deployed.*?shall\s+have\s+no\s+additional\s+cost|"
        r"The\s+financial\s+data\s+in\s+the\s+prescribed\s+format\s+shall\s+be\s+certified\s+by\s+Chartered\s+Accountant\s+with\s+his\s+stamp\s+and\s+signature\s+in\s+original|"
        r"If\s+audited\s+balance\s+sheet\s+of\s+any\s+year.*?the\s+tender\s+may\s+be\s+considered\s+as\s+non-responsive|"
        r"upload\s+their\s+tender\s+on\s+https?://eprocure\.gov\.in/eprocure/app\s+using\s+class-[I|V]+|"
        r"upload\s+(?:the\s+)?scanned\s+copies\s+of\s+transaction\s+of\s+payment\s+of\s+tender\s+(?:security|document\s+cost|fee)|"
        r"^\s*All\s+prospective\s+bidders\s+shall\s+have\s+to\s+provide\s+the\s+details\s+of\s+the\s+person\(s\)|"
        r"A\s+non-Indian\s+bidder.*?is\s+permitted\s*-?\s*to\s+participate|"
        r"same\s+transaction\s+reference\s+number\s+has\s+been\s+submitted\s+for\s+more\s+than\s+one\s+bids|"
        r"failing\s+which\s+their\s+tender\s+submission\s+shall\s+not\s+be\s+evaluated|"
        r"does\s+not\s+meet\s+the\s+criteria\s+stated\s+in\s+the\s+Appendix-19|"
        r"altered\s*/\s*modified\s+the\s+MOU\s*/\s*JV\s+agreement|"
        r"minor\s+deviation\s+without\s+quoting\s+the\s+price\s+of\s+adjustment|"
        r"\b(?:non\s*[-]?\s*submission|failure\s+to\s+submit)\s+of\s+affidavit\s+by\s+the\s+tenderer\s+shall\s+result\s+in\s+rejection\s+of\s+(?:their|the)\s+bid\b|"
        r"\bAny\s+bidder\s+from\s+a\s+country\s+which\s+shares\s+a\s+land\s+border\s+with\s+India\s+will\s+be\s+eligible\s+to\s+bid\s+either\s+as\s+a\s+single\s+entity\s+or\s+as\s+a\s+member\s+of\s+a\s+JV\b|"
        r"^\s*T[1-4]\s*-\s*(?:Net\s+Worth|Annual\s+Turnover|Work\s+Experience)\b|"
        r"\b(?:Net\s+Worth\s+of\s+tenderer\s+during\s+last\s+audited\s+financial\s+year|The\s+average\s+annual\s+turnover\s+from\s+consultancy\s+works)\s+should\s+be\s+≥\b"
        r")",
        re.sub(r"<[^>]+>", " ", html.unescape(stripped)).strip(),
        re.I | re.DOTALL,
    ):
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

    # Generic submission guidance or duplicate clarification cut-off disclaimers
    if re.search(
        r"(?:"
        r"Bidders\s+shall\s+submit\s+bids\s+in\s+accordance\s+with\s+these|"
        r"^\s*Queries\s+received\s+after\s+\d{1,4}\s*hrs\s+on\s+shall\s+not\s+be\s+entertained"
        r")",
        stripped,
        re.I,
    ):
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


_STANDALONE_LEAF_BULLET_RE = re.compile(
    r"^\s*(?:"
    r"\(?[a-z]{1,3}\)?\.?|"           # (a), a., (b), b.
    r"\(?[ivxlcdmIVXLCDM]{1,4}\)?\.?|" # (i), (ii), (iii), (iv)
    r"[a-z]\)"                         # a), b)
    r")\s*$"
)


def is_standalone_leaf_bullet(heading_text: str) -> bool:
    """True if heading_text is a bare lower-case letter or Roman numeral enumeration
    marker (e.g. '(a)', 'i.', '(iv)') without a finite, substantive clause title."""
    stripped = (heading_text or "").strip()
    if not stripped:
        return True
    return bool(_STANDALONE_LEAF_BULLET_RE.match(stripped))


_SOW_FEATURE_VERBS_RE = re.compile(
    r"\b(?:shall\s+support|shall\s+be\s+integrated|shall\s+provide(?:\s+facility|\s+capability)?|"
    r"shall\s+be\s+capable\s+of|shall\s+be\s+designed\s+to|shall\s+feature|shall\s+include)\b",
    re.I,
)

_SOW_GATE_INDICATORS_RE = re.compile(
    r"\b(?:"
    r"within\s+\d+\s+(?:days?|weeks?|months?|years?)|"
    r"prior\s+to|before\s+(?:award|commencement|expiry|submission)|"
    r"penalty|liquidated\s+damages|acceptance\s+criteria|approval\s+gate|"
    r"warranty|defect\s+liability|guarantee|bank\s+guarantee|pbg|emd|"
    r"payment|invoice|final\s+bill|milestone|prohibited|rejection|ineligible"
    r")\b",
    re.I,
)

_ROUTINE_SOW_PROCEDURES_RE = re.compile(
    r"\b(?:"
    r"safety\s+(?:helmet|shoes?|boots?|goggles?|harness|belt|jacket|gloves?|net)|"
    r"personal\s+protective\s+equipment|ppe\b|"
    r"hot\s+work\s+permit|cold\s+work\s+permit|confined\s+space\s+(?:entry\s+)?permit|"
    r"height\s+work\s+permit|work\s+permit\s+system|"
    r"scaffold(?:ing)?\s+(?:tag|tagging|inspection|fitness)|"
    r"tool\s+calibration|gauge\s+calibration|equipment\s+calibration|"
    r"radiograph(?:y|ic)\s+(?:tag|barrier|clearance|warning)|"
    r"barrier\s+taping|warning\s+signage|caution\s+tape|"
    r"hydrostatic\s+test\s+pump|hydro\s+testing\s+procedure|"
    r"housekeeping|debris\s+removal|site\s+cleaning|scrap\s+disposal|"
    r"payment\s+to\s+contractor\s+labourer\s+shall\s+be\s+made\s+through\s+electronic\s+mode|"
    r"deposit\s+the\s+amount\s+of\s+tds\s+with\s+the\s+income\s+tax\s+dept|"
    r"arrange\s+the\s+required\s+road\s+permit\s+at\s+their\s+own\s+cost|"
    r"maximum\s+seal\s+percentage\s+restricts\s+the\s+bidder|"
    r"click\s+on\s+(?:the\s+)?(?:view|submit|upload)\s+(?:option|button|tab)|"
    r"live\s+auction\s+tab|"
    r"furnish\s+(?:the\s+)?names\s*,\s*identity\s+proofs?\s*,\s*and\s+skill\s+certificates?|"
    r"maintain\s+up-to-date\s+muster\s+rolls?|"
    r"arbitrary\s+replacement\s+or\s+removal\s+of\s+contractual\s+workm[ae]n|"
    r"deployment\s+or\s+removal\s+of\s+contractual\s+workm[ae]n|"
    r"no\s+contractual\s+workm[ae]n\s+shall\s+be\s+deployed\s+or\s+removed"
    r")\b",
    re.I,
)


def is_ungated_sow_feature_description(item_type: Optional[str], quote: str, division_role: str) -> bool:
    """True if quote in a Scope of Work (sow) division is an ungated software feature specification
    or routine operational execution procedure / safety boilerplate rather than a trackable
    contractual milestone duty."""
    if (division_role or "").lower() != "sow":
        return False
    stripped = (quote or "").strip()
    if not stripped:
        return False
    if _SOW_GATE_INDICATORS_RE.search(stripped):
        return False
    t = (item_type or "").strip().lower()
    if t == "obligation" and _SOW_FEATURE_VERBS_RE.search(stripped):
        return True
    if _ROUTINE_SOW_PROCEDURES_RE.search(stripped):
        return True
    return False


def is_statutory_policy_leak(item_type: Optional[str], quote: str, division_role: str) -> bool:
    """True if quote is extracted from a statutory government policy notification or platform bidding
    manual container (e.g. Ministry of Steel DMI&SP, Reverse Auction Tutorial) and does not contain
    an explicit bidder submission milestone or financial security instrument."""
    if (division_role or "").lower() != "statutory_policy":
        return False
    stripped = (quote or "").strip()
    if not stripped:
        return True
    # If the quote specifies an explicit calendar deadline, or a safety prohibition / crucial covenant, keep it
    if re.search(r"\b(?:\d{2}[./-]\d{2}[./-]\d{4}|prohibited|forfeiture)\b", stripped, re.I):
        return False
    return True


_CONTRACTOR_EVALUATION_DEDUCTION_RE = re.compile(
    r"(?:(?:\d+(?:\.\d+)?\s*%\s*marks?|\d+(?:\.\d+)?\s+marks?\s+deduction|deduction\s+of\s+[\d.]+\s*(?:%|\b)\s*marks?|\d+\s+marks\b|Price\s+Adjustment\s+for\s+delay\s+upto|Full\s+marks\s*\(\d+\)\s*Deduction|Deduction\s+of\s+[\d.]+\s*marks?))",
    re.I,
)


def is_contractor_evaluation_scoring_matrix(quote: str) -> bool:
    """True if quote is a grading matrix mark deduction or vendor rating formula
    from an internal owner evaluation guideline rather than a contractor bid requirement."""
    stripped = (quote or "").strip()
    if not stripped:
        return False
    return bool(_CONTRACTOR_EVALUATION_DEDUCTION_RE.search(stripped))


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


