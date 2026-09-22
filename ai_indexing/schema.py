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


def sanitize_heading_text(text: str) -> str:
    if not text:
        return ""
    text = _MD_HEADING_PREFIX_RE.sub("", text)
    text = _MD_EMPHASIS_RE.sub("", text)
    text = _TRAILING_COLON_RE.sub("", text)
    return text.strip()


class AIHeading(BaseModel):
    heading_text: str = Field(default="")
    start_page: int = 0
    start_line_hint: Optional[int] = None
    end_page: Optional[int] = None
    still_open_at_chunk_end: bool = False
    parent_ref: Optional[str] = None
    level: int = 1
    # The AI's own best-guess resolution of a heading it recognizes despite
    # OCR garbling (e.g. heading_text="AmexureAl", family_hint="annexure",
    # number_hint="A-1") — heading_text itself must stay verbatim (it's
    # matched against the real OCR block for anchoring), so a corrected
    # reading can only ever be reported here, additively. Both null when
    # the model isn't confident, or the heading is genuinely unnumbered.
    # Resolved in reference_index.mint_key_for_heading via _resolve_ai_hint,
    # tried before blind regex fuzzy-matching.
    family_hint: Optional[str] = None
    number_hint: Optional[str] = None

    model_config = {"extra": "ignore"}  # a provider adding a field must not fail the chunk

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

    model_config = {"extra": "ignore"}


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
    chunk: AIStructureChunk, expected_pages: List[int],
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
        if not h.heading_text.strip():
            report.dropped_empty += 1
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
    # gave them, which is the only ordering signal available for those.
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

    return AIStructureChunk(headings=kept, notes=chunk.notes), report
