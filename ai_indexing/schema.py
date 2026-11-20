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
from ai_indexing.guards import (
    _BARE_PAGE_OR_STAMP_RE,
    _GENERIC_LEGAL_NOISE_RE,
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


_REJECTION_CONSEQUENCE_RE = re.compile(
    r"\b(?:"
    r"shall\s+be\s+declared\s+as\s+ineligible|"
    r"shall\s+be\s+summarily\s+rejected|"
    r"shall\s+be\s+considered\s+ineligible|"
    r"shall\s+not\s+be\s+accepted\s+under\s+any\s+circumstances|"
    r"shall\s+not\s+be\s+entertained|"
    r"liable\s+for\s+(?:outright\s+)?rejection|"
    r"shall\s+be\s+rejected|"
    r"will\s+be\s+rejected|"
    r"lead\s+to\s+rejection"
    r")\b",
    re.I,
)


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
        if item.evidence_quote and _REJECTION_CONSEQUENCE_RE.search(item.evidence_quote):
            item.item_type = "condition"
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

