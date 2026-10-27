"""Pydantic output models for the AI structure-detection contract, plus
normalize_chunk() — the explicit, reported cleanup pass that runs AFTER
schema validation (every field has a safe default so a partially-filled
response still validates; semantic cleanup is separate and reports what it
dropped rather than silently swallowing it).
"""

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

import config

# ─── markdown-artifact sanitization ────────────────────────────────────────
# Models routinely echo markdown formatting inside a JSON string field even
# when told the field is plain text ("### 3.4 QUALITY PLANS", "**3.4 QUALITY
# PLANS**") — and the page's real OCR blocks never contain that formatting,
# so an un-sanitized heading_text would both fail block-grounding AND leak
# markdown syntax into the Structure tree's rendered sidebar. Applied once,
# here, and the sanitized text is what's used and stored everywhere
# downstream (own_label/label/fingerprint) — not just transiently inside the
# block-grounding matcher.
_MD_HEADING_PREFIX_RE = re.compile(r"^\s*#{1,6}\s+")
_MD_EMPHASIS_RE = re.compile(r"(\*\*|\*|__|_|`)")
_TRAILING_COLON_RE = re.compile(r"\s*:\s*$")
# Standalone page/sheet stamp numbers, bare integers, or page markers (e.g. "-:12:-", "20", "Page 5", "5 of 100")
_BARE_PAGE_OR_STAMP_RE = re.compile(
    r"^\s*(?:[-–—(]?\s*[:\.]?\s*[-–—]?\s*\d+\s*[:\.]?\s*[-–—)]?|"
    r"[Pp]age\s+\d+(?:\s+of\s+\d+)?)\s*$"
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


def sanitize_heading_text(text: str) -> str:
    if not text:
        return ""
    text = _MD_HEADING_PREFIX_RE.sub("", text)
    text = _MD_EMPHASIS_RE.sub("", text)
    text = _TRAILING_COLON_RE.sub("", text)
    return text.strip()


VALID_REJECTION_REASONS = {
    "external_citation",
    "reproduced_text",
    "body_sentence",
    "table_or_toc",
    "running_header",
    "duplicate_of_prior",
    "not_on_page",
}


class AIPriorRejection(BaseModel):
    prior_id: str = Field(default="")
    reason_code: str = Field(default="unspecified")
    evidence_quote: str = Field(default="")

    model_config = {"extra": "ignore"}

    @field_validator("prior_id", mode="before")
    @classmethod
    def _coerce_prior_id(cls, v):
        return str(v).strip() if v else ""

    @field_validator("reason_code", mode="before")
    @classmethod
    def _coerce_reason_code(cls, v):
        code = str(v).strip().lower() if v else "unspecified"
        return code if code in VALID_REJECTION_REASONS else "unspecified"

    @field_validator("evidence_quote", mode="before")
    @classmethod
    def _truncate_quote(cls, v):
        return str(v).strip()[:120] if v else ""


class AIHeading(BaseModel):
    heading_text: str = Field(default="")
    start_page: int = 0
    start_line_hint: Optional[int] = None
    end_page: Optional[int] = None
    still_open_at_chunk_end: bool = False
    parent_ref: Optional[str] = None
    level: int = 1
    family_hint: Optional[str] = None
    number_hint: Optional[str] = None
    prior_id: Optional[str] = None
    evidence_quote: Optional[str] = None
    heading_confidence: Optional[str] = None
    heading_confidence_reasons: Optional[List[str]] = None

    model_config = {"extra": "ignore"}  # a provider adding a field must not fail the chunk

    @field_validator("prior_id", mode="before")
    @classmethod
    def _truncate_prior_id(cls, v):
        if v is None or v == "":
            return None
        return str(v).strip()[:32]

    @field_validator("evidence_quote", mode="before")
    @classmethod
    def _truncate_evidence_quote(cls, v):
        if v is None or v == "":
            return None
        return str(v).strip()[:120]

    @field_validator("heading_text", mode="before")
    @classmethod
    def _sanitize(cls, v):
        # Truncated, never rejected — a length cap here MUST NOT raise: a
        # single over-length item (confirmed via a real run: the model
        # mis-classified a body paragraph as a heading, well past any sane
        # length) would otherwise fail Pydantic validation for the WHOLE
        # headings list at once, discarding every other, genuinely good
        # heading in the same chunk instead of just this one bad item. Real
        # headings are short; a too-long one is almost certainly body text
        # anyway, so truncating (not rejecting) both avoids the all-or-
        # nothing failure and keeps this codebase's established "recall
        # over precision, let a human judge" stance instead of a silent drop.
        return sanitize_heading_text(str(v))[:300] if v else ""

    @field_validator("parent_ref", mode="before")
    @classmethod
    def _truncate_parent_ref(cls, v):
        if v is None or v == "":
            return None
        return str(v)[:120]

    @field_validator("family_hint", "number_hint", mode="before")
    @classmethod
    def _coerce_hint(cls, v):
        # Defensive only — reference_index._resolve_ai_hint does the real
        # validation (against the known family vocabulary and a real
        # number/roman-numeral shape). This just guarantees a non-string
        # or empty value never reaches that far as anything but None.
        if v is None or v == "":
            return None
        return str(v)[:32]

    @field_validator("level", mode="before")
    @classmethod
    def _coerce_level(cls, v):
        try:
            n = int(v)
        except (TypeError, ValueError):
            return 1
        return max(1, min(n, 16))

    @field_validator("start_page", mode="before")
    @classmethod
    def _coerce_start_page(cls, v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0

    @field_validator("end_page", "start_line_hint", mode="before")
    @classmethod
    def _coerce_optional_int(cls, v):
        if v is None or v == "":
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            return None


class AIStructureChunk(BaseModel):
    headings: List[AIHeading] = Field(default_factory=list)
    notes: str = Field(default="", max_length=500)
    confirmed_prior_ids: List[str] = Field(default_factory=list)
    rejected_priors: List[AIPriorRejection] = Field(default_factory=list)

    model_config = {"extra": "ignore"}

    @field_validator("confirmed_prior_ids", mode="before")
    @classmethod
    def _coerce_confirmed_prior_ids(cls, v):
        if not v:
            return []
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
        return []


@dataclass
class NormalizationReport:
    dropped_invalid: int = 0
    dropped_offpage: int = 0
    dropped_empty: int = 0
    dropped_duplicate: int = 0
    dropped_overflow: int = 0
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dropped_invalid": self.dropped_invalid,
            "dropped_offpage": self.dropped_offpage,
            "dropped_empty": self.dropped_empty,
            "dropped_duplicate": self.dropped_duplicate,
            "dropped_overflow": self.dropped_overflow,
            "warnings": list(self.warnings),
        }


def _dedupe_key(h: AIHeading) -> tuple:
    return (h.start_page, h.heading_text.casefold().strip())


def normalize_chunk(
    chunk: AIStructureChunk, expected_pages: List[int], order_by_line_hint: bool = False,
) -> "tuple[AIStructureChunk, NormalizationReport]":
    """Lenient, order-preserving cleanup. Drops (never crashes on) headings
    that are off-page, blank, or exact-duplicate; caps to
    config.AI_INDEX_MAX_HEADINGS_PER_CHUNK, keeping document order. Every
    drop is counted in the returned report — never silently swallowed."""
    report = NormalizationReport()
    expected = set(expected_pages)
    seen: set = set()
    kept: List[AIHeading] = []

    for h in chunk.headings:
        if h.start_page not in expected:
            report.dropped_offpage += 1
            continue
        stripped_text = h.heading_text.strip()
        if not stripped_text:
            report.dropped_empty += 1
            continue

        # Outline context for bare numbers and single-character enumeration markers:
        # A bare number (e.g. "1.", "2.", "5") is ONLY an outline item if it has true outline
        # semantics (point/ground/clause family, or level > 1 with a genuine non-carry parent_ref).
        # A phantom carry-in reference (e.g. "c1", "c2") or level <= 1 must NEVER protect a bare
        # integer from being dropped as a standalone page stamp.
        parent_ref_clean = (h.parent_ref or "").strip()
        is_real_parent_ref = bool(parent_ref_clean) and not re.match(r"^c\d+$", parent_ref_clean)
        has_outline_context = (
            h.family_hint in ("point", "ground", "clause", "section", "subpoint")
            or (h.level > 1 and is_real_parent_ref)
        )

        # 1. Standalone stamps, bare page numbers, or titleless number strings (e.g. "1.2", "20", "079789")
        is_bare_number = bool(re.match(r"^\s*\(?\d+(?:\.\d+)*[\.\)]?\s*$", stripped_text))
        if _BARE_PAGE_OR_STAMP_RE.match(stripped_text) or is_bare_number:
            if not (is_bare_number and has_outline_context):
                report.dropped_empty += 1
                continue

        # 2. Devanagari text on Indian court stamp papers
        if any("\u0900" <= ch <= "\u097F" for ch in stripped_text):
            report.dropped_invalid += 1
            continue

        # 3. Universal legal administrative noise
        if _GENERIC_LEGAL_NOISE_RE.match(stripped_text):
            is_enum_token = bool(re.match(r"^\s*\(?(?:[a-zA-Z]|\d{1,2}|[ivxlcdmIVXLCDM]{1,4})\)[.,;:]?\s*$", stripped_text))
            if not (is_enum_token and has_outline_context):
                report.dropped_invalid += 1
                continue

        # 3a. Bare LaTeX math expressions or symbols
        if re.match(r"^\s*(?:\\\(.*\\\)|\\\$|\\\$.*\\\$)\s*$", stripped_text):
            report.dropped_invalid += 1
            continue

        # 3b. Cause title entity / party lines and mid-sentence continuation fragments
        has_leading_num = bool(re.match(r"^\s*\(?(?:\d+|[a-zA-Z]|[ivxlcdmIVXLCDM]+)[\.\)]\s+", stripped_text))
        if not has_leading_num:
            if re.match(r"^\s*(?:M/s\b.*|.*(?:\bLtd\.?|\bLimited|\band\s+others?)[.,;:]?\s*$)", stripped_text, re.IGNORECASE):
                report.dropped_invalid += 1
                continue
            if re.match(r"^[a-z]", stripped_text) and not re.match(r"^[a-z]+\)", stripped_text):
                report.dropped_invalid += 1
                continue

        # 4. Narrative body paragraphs (specifically multi-segment decimal clauses >15 words and unnumbered prose >15 words)
        words = stripped_text.split()
        if len(words) > 15:
            is_multiseg_decimal = bool(re.match(r"^\s*\d+\.\d+(?:\.\d+)*", stripped_text))
            is_single_point = bool(re.match(r"^\s*\(?(?:\d+|[a-zA-Z]|[ivxlcdmIVXLCDM]+)[\.\)]\s+", stripped_text))
            first_lower = words[0].lower() if words else ""
            if is_multiseg_decimal:
                # In contract/tender tables, decimal clauses often include full sentence text.
                # Rather than dropping valid clauses like 24.2.1, keep them with trimmed label:
                h.heading_text = " ".join(words[:10])
            elif not is_single_point:
                if not any(first_lower.startswith(kw) for kw in ("annexure", "schedule", "appendix", "article", "section", "clause")):
                    report.dropped_invalid += 1
                    continue
        dk = _dedupe_key(h)
        if dk in seen:
            report.dropped_duplicate += 1
            continue
        seen.add(dk)
        kept.append(h)

    # A chunk's page order is authoritative, not the AI's list order —
    # stable sort (Python's sorted() preserves relative order among equal
    # keys) so same-page headings keep whatever relative order the model
    # gave them, which is the only ordering signal available for those —
    # UNLESS order_by_line_hint is on and every heading on that page
    # reports a non-null start_line_hint, in which case that hint is used
    # as a secondary key instead. Conservative and opt-in (default off,
    # mirroring ai_index_resolve_parent_via_ai's own rollout pattern): the
    # hint is documented in the prompt itself as "stored for possible
    # future use only — not required to be precise", with no measured
    # accuracy data behind it, so an ALL-OR-NOTHING guard per page is used
    # rather than ever interleaving a hinted heading among unhinted ones
    # via a sentinel value, which could actively make ordering worse.
    if order_by_line_hint:
        page_all_hinted: Dict[int, bool] = {}
        for h in kept:
            if h.start_page not in page_all_hinted:
                page_all_hinted[h.start_page] = True
            if h.start_line_hint is None:
                page_all_hinted[h.start_page] = False
        kept.sort(key=lambda h: (
            h.start_page,
            h.start_line_hint if page_all_hinted.get(h.start_page) else 0,
        ))
    else:
        kept.sort(key=lambda h: h.start_page)

    if len(kept) > config.AI_INDEX_MAX_HEADINGS_PER_CHUNK:
        report.dropped_overflow = len(kept) - config.AI_INDEX_MAX_HEADINGS_PER_CHUNK
        kept = kept[: config.AI_INDEX_MAX_HEADINGS_PER_CHUNK]

    if kept:
        pages_with_headings = {h.start_page for h in kept}
        if len(pages_with_headings) == 1 and len(expected) > 1:
            report.warnings.append(
                f"all {len(kept)} heading(s) attributed to a single page "
                f"({next(iter(pages_with_headings))}) out of {len(expected)} in this chunk — "
                "possible page-attribution failure."
            )

    return AIStructureChunk(
        headings=kept,
        notes=chunk.notes,
        confirmed_prior_ids=chunk.confirmed_prior_ids,
        rejected_priors=chunk.rejected_priors,
    ), report


# ─── Actionable-items extraction (ai_action_items.py) — a separate pass from
# the structure/heading detection above: flat per-page items, not a
# hierarchy, so none of normalize_chunk's heading-shape noise filters apply
# here (a full sentence with a finite verb is exactly what's wanted, not a
# false-positive signal). See ai_action_items.py's module docstring for the
# grounding/anti-hallucination discipline this schema feeds into. ──────────

VALID_ACTION_ITEM_TYPES = {"deadline", "obligation", "condition", "other"}

_DATE_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class AIActionItem(BaseModel):
    item_text: str = Field(default="")
    item_type: str = Field(default="other")
    date_text: Optional[str] = None
    date_iso: Optional[str] = None
    # The named trigger a relative deadline counts from (e.g. "Effective
    # Date of Contract", "Design Freeze") when no absolute date is
    # computable — see ai_indexing/prompt.py's ACTION_ITEM_SYSTEM_PROMPT.
    # Confirmed necessary, not speculative: real tenders define entire
    # milestone cascades purely as offsets from an anchor that's never
    # itself dated anywhere in the document.
    anchor_event: Optional[str] = None
    # "weekly"/"monthly"/"quarterly"/"annual"/etc. for a recurring duty that
    # is neither a single deadline nor a date-less one-off obligation.
    cadence: Optional[str] = None
    page: int = 0
    end_page: Optional[int] = None
    still_open_at_chunk_end: bool = False
    evidence_quote: Optional[str] = None
    responsible_party: Optional[str] = None
    confidence: Optional[str] = None

    model_config = {"extra": "ignore"}

    @field_validator("item_type", mode="before")
    @classmethod
    def _coerce_item_type(cls, v):
        # Unlike AIHeading's validators (which coerce toward a plausible
        # default), an item_type the model didn't intend as deadline/
        # obligation/condition must land on "other" — a deliberate,
        # visible classification — never silently relabeled as
        # "obligation", which would misrepresent something that may not be
        # a real obligation at all (confirmed real case: litigation relief/
        # disposition items that don't fit any of the three contract-shaped
        # types).
        t = str(v).strip().lower() if v else "other"
        return t if t in VALID_ACTION_ITEM_TYPES else "other"

    @field_validator("item_text", mode="before")
    @classmethod
    def _truncate_item_text(cls, v):
        # Truncated, never rejected — same all-or-nothing-failure reasoning
        # as AIHeading._sanitize: one over-length item must not invalidate
        # every other good item in the same chunk.
        return str(v).strip()[:300] if v else ""

    @field_validator("date_text", mode="before")
    @classmethod
    def _truncate_date_text(cls, v):
        if v is None or v == "":
            return None
        return str(v).strip()[:120]

    @field_validator("date_iso", mode="before")
    @classmethod
    def _validate_date_iso(cls, v):
        # A malformed/non-calendar date_iso must never fail the item —
        # fall back to None and keep date_text as the only date signal,
        # same "coerce, don't raise" discipline as every other field here.
        if v is None or v == "":
            return None
        s = str(v).strip()
        return s if _DATE_ISO_RE.match(s) else None

    @field_validator("anchor_event", mode="before")
    @classmethod
    def _truncate_anchor_event(cls, v):
        if v is None or v == "":
            return None
        return str(v).strip()[:160]

    @field_validator("cadence", mode="before")
    @classmethod
    def _truncate_cadence(cls, v):
        if v is None or v == "":
            return None
        return str(v).strip()[:40]

    @field_validator("evidence_quote", mode="before")
    @classmethod
    def _truncate_evidence_quote(cls, v):
        if v is None or v == "":
            return None
        return str(v).strip()[:300]

    @field_validator("responsible_party", mode="before")
    @classmethod
    def _truncate_responsible_party(cls, v):
        if v is None or v == "":
            return None
        return str(v).strip()[:80]

    @field_validator("confidence", mode="before")
    @classmethod
    def _coerce_confidence(cls, v):
        c = str(v).strip().lower() if v else "medium"
        return c if c in ("high", "medium", "low") else "medium"

    @field_validator("page", mode="before")
    @classmethod
    def _coerce_page(cls, v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0

    @field_validator("end_page", mode="before")
    @classmethod
    def _coerce_end_page(cls, v):
        if v is None or v == "":
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            return None


class AIActionItemChunk(BaseModel):
    items: List[AIActionItem] = Field(default_factory=list)
    notes: str = Field(default="", max_length=500)

    model_config = {"extra": "ignore"}


def _action_item_dedupe_key(item: AIActionItem) -> tuple:
    return (item.page, item.item_type, item.item_text.casefold().strip())


def normalize_action_item_chunk(
    chunk: AIActionItemChunk, expected_pages: List[int],
) -> "tuple[AIActionItemChunk, NormalizationReport]":
    """Lenient, order-preserving cleanup for one chunk's own response —
    drops (never crashes on) items that are off-page, blank, or an
    exact-duplicate reported twice in the SAME response. Cross-chunk
    duplicate suppression (the same real item re-reported because
    overlap_pages re-sends tail pages to the next chunk) is a separate
    concern, handled by ai_action_items.py's own dedupe over the whole
    job — this function only ever sees one chunk at a time, so it cannot
    and does not attempt that. Every drop is counted in the returned
    report, never silently swallowed."""
    report = NormalizationReport()
    expected = set(expected_pages)
    seen: set = set()
    kept: List[AIActionItem] = []

    for item in chunk.items:
        if item.page not in expected:
            report.dropped_offpage += 1
            continue
        if not item.item_text.strip():
            report.dropped_empty += 1
            continue
        if not item.evidence_quote or not item.evidence_quote.strip():
            # No verbatim quote at all means grounding has nothing to work
            # with later — drop here rather than let an ungroundable item
            # waste a grounding attempt downstream.
            report.dropped_empty += 1
            continue
        dk = _action_item_dedupe_key(item)
        if dk in seen:
            report.dropped_duplicate += 1
            continue
        seen.add(dk)
        kept.append(item)

    kept.sort(key=lambda item: item.page)

    if len(kept) > config.AI_ACTION_ITEM_MAX_PER_CHUNK:
        report.dropped_overflow = len(kept) - config.AI_ACTION_ITEM_MAX_PER_CHUNK
        kept = kept[: config.AI_ACTION_ITEM_MAX_PER_CHUNK]

    return AIActionItemChunk(items=kept, notes=chunk.notes), report


VALID_MENTION_ACTIONS = {"confirm", "reject", "unsuppress"}


class AIMentionReview(BaseModel):
    mention_id: str = Field(default="")
    action: str = Field(default="confirm")
    evidence_quote: str = Field(default="")

    model_config = {"extra": "ignore"}

    @field_validator("mention_id", mode="before")
    @classmethod
    def _coerce_mention_id(cls, v):
        return str(v).strip() if v else ""

    @field_validator("action", mode="before")
    @classmethod
    def _coerce_action(cls, v):
        a = str(v).strip().lower() if v else "confirm"
        return a if a in VALID_MENTION_ACTIONS else "confirm"

    @field_validator("evidence_quote", mode="before")
    @classmethod
    def _truncate_quote(cls, v):
        return str(v).strip()[:120] if v else ""


class AIMentionChunk(BaseModel):
    reviews: List[AIMentionReview] = Field(default_factory=list)

    model_config = {"extra": "ignore"}


class AIDocrefVerdict(BaseModel):
    key: str = Field(default="")
    definition_occurrence_id: str = Field(default="none")
    evidence_quote: str = Field(default="")

    model_config = {"extra": "ignore"}

    @field_validator("key", mode="before")
    @classmethod
    def _coerce_key(cls, v):
        return str(v).strip() if v else ""

    @field_validator("definition_occurrence_id", mode="before")
    @classmethod
    def _coerce_occ_id(cls, v):
        return str(v).strip() if v else "none"

    @field_validator("evidence_quote", mode="before")
    @classmethod
    def _truncate_quote(cls, v):
        return str(v).strip()[:120] if v else ""


class AIDocrefReview(BaseModel):
    reviews: List[AIDocrefVerdict] = Field(default_factory=list)

    model_config = {"extra": "ignore"}

