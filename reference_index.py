"""Cross-reference navigation — Phase A: building the per-job reference index.

Detects internal targets (clause/section/article/annexure/... definitions) in a
page's OCR'd Markdown, tracks Article > Section > Clause scope plus bound-in
sub-document boundaries across a whole job, and produces candidate rows that
`database.py` persists into `ocr_reference_targets`.

This module is intentionally split into a pure, synchronous algorithm
(`build_targets_for_page`) with no DB/async dependency — fully unit-testable on
its own — and a thin orchestration layer (`update_reference_index_for_page`)
that handles persistence, the `reference_index_version` bump, and the
concurrency-safe out-of-order/retry ("frontier + cascade") mechanism.

See ~/.claude/plans/this-is-a-project-eventual-lamport.md for the full design
history and rationale (nine rounds of review across four architecture phases).
"""

import asyncio
import difflib
import re
from typing import Any, Dict, List, Optional, Tuple

import mistune

import database

# ─── Vocabulary & pattern definitions ──────────────────────────────────────────

_KEYWORD_ALTS = r"clause|section|sec\.?|article|art\.?|para(?:graph)?|annexure|schedule|appendix|page|ground"

CORE_RE = re.compile(
    rf"^\s*(?P<keyword>{_KEYWORD_ALTS})\b\.?\s+(?:no\.?\s*)?(?P<prefix>[A-Z]-?)?(?P<number>[ivxlcdm]+|\d+(?:\.\d+)*)\b",
    re.IGNORECASE,
)

# Mention-side: same core shape, but not anchored to block/line start, plus the
# optional trailing line modifier and range/list continuation (frontend-only
# concerns in the approved design, kept here too for the collapsed/roman helpers
# to stay in one place and for potential future server-side reuse/testing).
MENTION_RE = re.compile(
    rf"\b(?P<keyword>{_KEYWORD_ALTS})\b\.?\s+(?:no\.?\s*)?(?P<prefix>[A-Z]-?)?(?P<number>[ivxlcdm]+|\d+(?:\.\d+)*)\b",
    re.IGNORECASE,
)

GROUND_DIGIT_RE = re.compile(r"^\s*\(?(?P<number>\d+(?:\.\d+)*)(?:\)|\.)\s+[A-Z]")
GROUND_ROMAN_RE = re.compile(r"^\s*\(?(?P<number>[IVXLCDM]+)(?:\)|\.)\s+[A-Z]")

_GROUND_SCOPE_HEADINGS = re.compile(r"\b(GROUNDS?\s+OF\s+APPEAL|GROUNDS|PRAYER)\b", re.IGNORECASE)

EXTERNAL_LAW_A_RE = re.compile(
    r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s+(?:Act|Code|Rules|Regulations)\s+(?:19|20)\d{2}\b"
)
EXTERNAL_LAW_B_RE = re.compile(
    r"\bof\s+the\s+(?:[A-Z][\w\s]{0,20})?(?:Act|Code|Constitution|Rules)\b", re.IGNORECASE
)
EXTERNAL_LAW_C_RE = re.compile(r"\b(?:CPC|IPC|CrPC)\b")
# Case-law discussion markers — a block quoting/discussing a judicial precedent
# (e.g. "(2015) 3 SCC 49" / "... (supra)") is never a document-internal clause
# definition, even when it happens to open with "Section N"/"Clause N".
EXTERNAL_LAW_D_RE = re.compile(r"\bsupra\b|\(\d{4}\)\s*\d+\s*SCC\b", re.IGNORECASE)

# "INDEX" is deliberately NOT in this list — see _WEAK_SUBDOC_TRIGGER_RE
# below, which handles it as a conditional trigger instead.
SUBDOC_TRIGGER_RE = re.compile(
    r"(ANNEXURE|SCHEDULE|APPENDIX|GROUNDS\s+(?:OF|OR)\s+APPEAL|AFFIDAVIT|MEMO\s+OF\s+PARTIES|"
    r"JUDGE?MENT|IN\s+THE\s+(?:HIGH\s+)?COURT\s+OF|VERSUS|VS\.?|IMPUGNED\s+(?:ORDER|JUDGE?MENT))",
    re.IGNORECASE,
)
# "INDEX" legitimately means two different things: a bundle's own front
# index (a genuine new-document boundary) vs. a table of contents living
# inside an already-open Annexure/Schedule/Appendix (that container's own
# internal page, not a new document). Kept as a separate, weaker trigger —
# see detect_subdoc_trigger, which only lets it fire when nothing is
# currently open at rank 0.
_WEAK_SUBDOC_TRIGGER_RE = re.compile(r"\bINDEX\b", re.IGNORECASE)
_SUBDOC_NOISE_PREFIX = 15  # chars — how much leading OCR noise a trigger can tolerate

# A plain decimal-numbered heading of a document's own clause structure
# ("1. Article", "2.0 CONTRACT DOCUMENTS", "3.4 QUALITY PLANS") — a short
# label (up to 9 words) in either case style, not gated on all-caps (a
# real document mixes both). The `$` anchor (no re.MULTILINE) means this
# can only match a block that *is* the short label and nothing else — a
# numbered body paragraph ("2.1 The contract shall be performed strictly
# as per...") runs on far past 9 words and fails on length alone. See
# build_targets_for_page's decimal-heading branch for how this is nested
# (by the number's own segments, not by tag/position) and
# detect_subdoc_trigger for why it must never be treated as a generic
# sub-document boundary just because a trigger word appears in its label.
DECIMAL_HEADING_RE = re.compile(
    r"^\s*(?P<number>\d+(?:\.\d+)*)\.?\s+"
    r"(?P<label>[A-Z][\w'&/(),-]*(?:\s+[\w'&/(),-]+){0,8})\s*$"
)

# A single-letter section heading ("A. INTRODUCTION", "F. AWARD OF
# CONTRACT:") — same short-label shape as DECIMAL_HEADING_RE, plus an
# optional trailing colon (confirmed needed on real samples: "D. SUBMISSION
# OF BIDS:", "E. Observations & findings of Arbitral Tribunal:"). mistune
# never parses this shape as a list item (CommonMark ordered-list markers
# are digits only), so no list-item exclusion is needed here the way
# DECIMAL_HEADING_RE's caller needs one. See _letter_heading_trusted for why
# the regex alone isn't sufficient — a signature-block name with a middle
# initial ("H. G CHHABRA") matches this shape too.
LETTER_HEADING_RE = re.compile(
    r"^\s*(?P<letter>[A-Z])\.\s+"
    r"(?P<label>[A-Z][\w'&/(),-]*(?:\s+[\w'&/(),-]+){0,8})\s*:?\s*$"
)

_SENTENCE_END_CHARS = set(".!?:;’”\")]")

# One explicit table replaces what used to be two separate dicts
# (_FAMILY_ALIASES + _HIERARCHY_RANK) — rank (containment depth; None = never
# a container, always a leaf — "is a container" is just "rank is not None",
# so no separate boolean is needed), numbered (detected via CORE_RE's
# keyword+number shape vs. a generic unnumbered trigger like "AFFIDAVIT"),
# and aliases (spelling variants that fold within a family — families never
# merge across each other, a Schedule 2 and an Article 2 are unrelated) all
# live in one place. annexure/schedule/appendix are siblings at rank 0 (a
# Schedule does not nest inside an Annexure), matching a generic (unnumbered)
# sub-document trigger — affidavit/judgment/court-caption/etc. — synthesized
# under the same "subdoc" family (see detect_subdoc_trigger /
# build_targets_for_page). clause/para/page/ground are deliberately
# rank=None: always leaves, never containers, attach to whatever's currently
# on the stack — two sibling clauses must never show up in each other's
# scope_label, only their shared enclosing container should.
#
# Adding a genuinely deeper tier later is a one-line addition here (pick the
# next rank number, e.g. between existing ranks); the containment mechanism
# below is a plain integer comparison, not hard-coded to three levels. This
# does NOT cover nesting *within* a leaf tier (Clause 4 -> 4.1 -> 4.1.1) —
# that's decimal/sub-enumeration nesting, an explicitly separate, deferred
# follow-up, not something this table captures.
FAMILIES: Dict[str, Dict[str, Any]] = {
    "annexure": {"rank": 0, "numbered": True, "aliases": ["annexure"]},
    "schedule": {"rank": 0, "numbered": True, "aliases": ["schedule"]},
    "appendix": {"rank": 0, "numbered": True, "aliases": ["appendix"]},
    "subdoc":   {"rank": 0, "numbered": False, "aliases": []},
    "article":  {"rank": 1, "numbered": True, "aliases": ["article", "art"]},
    "section":  {"rank": 2, "numbered": True, "aliases": ["section", "sec"]},
    "para":     {"rank": None, "numbered": True, "aliases": ["para", "paragraph"]},
    "clause":   {"rank": None, "numbered": True, "aliases": ["clause"]},
    "ground":   {"rank": None, "numbered": True, "aliases": ["ground"]},
    "page":     {"rank": None, "numbered": True, "aliases": ["page"]},
}

# Thin derived views — every existing call site keeps working unchanged.
_HIERARCHY_RANK = {f: v["rank"] for f, v in FAMILIES.items() if v["rank"] is not None}
_FAMILY_ALIASES = {alias: f for f, v in FAMILIES.items() for alias in v["aliases"]}

# Numbered sub-document families — detected via CORE_RE like any other
# target, so they push onto nested_stack via the ordinary _HIERARCHY_RANK
# path below. Kept separate from unnumbered generic triggers (affidavit,
# judgment, court captions, ...), which detect_subdoc_trigger still handles.
_SUBDOC_NUMBERED_FAMILIES = {f for f, v in FAMILIES.items() if v["rank"] == 0 and v["numbered"]}

# ─── Heading-vs-reference confidence signals ───────────────────────────────
# These flag a match as possibly-a-citation-not-a-heading rather than
# excluding it outright (recall favored over precision — a human verifying
# the tree should see and judge these, not have them silently vanish). Only
# _is_backward_reference (below) is a hard exclude, matching the existing
# _is_external_law precedent for an unambiguous linguistic marker.

_BACKWARD_REF_RE = re.compile(
    r"^\s*(above|below|aforesaid|aforementioned|hereinabove|hereinbelow|supra)\b",
    re.IGNORECASE,
)


def _is_backward_reference(text: str, match_end: int) -> bool:
    """True if the text immediately following a matched keyword+number is a
    backward/forward-reference word ("Para 5.01 ABOVE that...") — an
    explicit linguistic marker that this occurrence cites something defined
    elsewhere, never a definition itself. Confirmed via a real sample
    (reference_targets_sample.md's para:5.01)."""
    return bool(_BACKWARD_REF_RE.match(text[match_end:]))


_OTHER_DOC_MARKER_RE = re.compile(r"\b(GCC|SCC|ITB|ITE|Vol\.?\s*-?\s*[IVXLCDM\d]+)\b", re.IGNORECASE)
_CHAINED_CITATION_RE = re.compile(
    r"\b(?:and|&)\b.{0,40}?\b(?:clause|cl\.?|section|sec\.?|article|art\.?|"
    r"annexure|ann\.?|schedule|sch\.?|appendix|app\.?)\b\.?\s*(?:no\.?\s*)?\d",
    re.IGNORECASE,
)
_RANGE_CONTINUATION_RE = re.compile(r"^\s*(?:to|thru|through)\s+\d", re.IGNORECASE)


def _citation_confidence_reasons(text: str, match_end: int, include_unbounded: bool = True) -> List[str]:
    """Reasons this match might be a reference rather than a heading — an
    empty list means no text-heuristic signal fired. Both patterns confirmed
    via real samples: the page 142/147 bid-compliance table ("Clause No.
    34.0, Sec. GCC, Vol.I and Cl. No. 9 SCC Vol-I.") for chained_citation/
    other_doc_marker; "SECTION 8 TO 15"/"SECTION 4 TO 16" for
    range_shaped_continuation.

    `chained_citation`/`other_doc_marker` both `.search()` the ENTIRE
    remainder - a safe, bounded check for a short, wholly-citation-shaped
    title (the real samples above), but a false-positive magnet once the
    remainder can be a full multi-sentence paragraph (a headerless numbered
    clause) that only incidentally mentions another volume/section deep
    inside otherwise-genuine clause text - confirmed via a real sample:
    "3.6 The contractor guarantees... as stipulated in the technical
    specifications (Vol-II)..." is a real clause of THIS document, not a
    citation to another one. `include_unbounded=False` (used only by the
    headerless call site, which already has its own, stronger structural
    trust basis - containment against an already-open parent number) skips
    both, keeping only `range_shaped_continuation` (`.match()`-anchored
    immediately after the number, so it stays meaningful regardless of the
    remainder's length)."""
    remainder = text[match_end:]
    reasons = []
    if include_unbounded:
        if _CHAINED_CITATION_RE.search(remainder):
            reasons.append("chained_citation")
        if _OTHER_DOC_MARKER_RE.search(remainder):
            reasons.append("other_doc_marker")
    if _RANGE_CONTINUATION_RE.match(remainder):
        reasons.append("range_shaped_continuation")
    return reasons


_DET_TAG_RE = re.compile(r"<\|det\|>(?P<type>\w+)\s*\[[^\]]*\]<\|/det\|>", re.DOTALL)


def parse_layout_blocks(raw_text: Optional[str]) -> List[Dict[str, str]]:
    """Parses baidu/Unlimited-OCR's raw `<|det|>type [bbox]<|/det|>content`
    output (captured before unlimited_ocr_engine.clean_detection_tags()
    strips it) into an ordered list of {"type", "text"} entries, one per
    tag, dropping any whose stripped content is empty — mirroring
    clean_detection_tags()'s own non-empty-line filter, so when the count
    matches, this lines up 1:1 with the cleaned page text's paragraph
    blocks. Returns [] for None/empty input, or input with no recognizable
    tags (e.g. the GLM engine, which never emits these)."""
    if not raw_text:
        return []
    matches = list(_DET_TAG_RE.finditer(raw_text))
    result = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(raw_text)
        content = raw_text[start:end].strip()
        if content:
            result.append({"type": m.group("type"), "text": content})
    return result

_ROMAN_VALUES = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}


def roman_to_arabic(s: str) -> int:
    s = s.lower()
    total = 0
    prev = 0
    for ch in reversed(s):
        val = _ROMAN_VALUES.get(ch, 0)
        if val < prev:
            total -= val
        else:
            total += val
            prev = val
    return total


def _is_roman(s: str) -> bool:
    return bool(re.fullmatch(r"[ivxlcdm]+", s, re.IGNORECASE))


# Only these families genuinely carry an exhibit-style letter prefix
# ("Annexure A-5", "Schedule R-9"). Elsewhere, CORE_RE's optional single-letter
# prefix group is a false split of a multi-letter roman numeral's leading
# character (e.g. "Article II" → prefix "I" + number "I") — reconciled below,
# not by making the regex itself family-aware.
_PREFIX_ELIGIBLE_FAMILIES = {"annexure", "schedule", "appendix"}


def reconcile_prefix_and_number(keyword: str, prefix: Optional[str], number_raw: str) -> Tuple[Optional[str], str]:
    family = _FAMILY_ALIASES.get(keyword.lower().rstrip("."), keyword.lower())
    if prefix and family not in _PREFIX_ELIGIBLE_FAMILIES:
        return None, f"{prefix.rstrip('-')}{number_raw}"
    return prefix, number_raw


def normalize_key(keyword: str, prefix: Optional[str], number_raw: str) -> str:
    prefix, number_raw = reconcile_prefix_and_number(keyword, prefix, number_raw)
    family = _FAMILY_ALIASES.get(keyword.lower().rstrip("."), keyword.lower())
    number = str(roman_to_arabic(number_raw)) if _is_roman(number_raw) else number_raw
    prefix_part = f"{prefix.rstrip('-').lower()}-" if prefix else ""
    return f"{family}:{prefix_part}{number}"


# ─── Fuzzy keyword recovery (OCR-garbled family keywords) ─────────────────
# Tries before giving up on a text match entirely — e.g. "AmneXureA-1" (a
# human reads "Annexure A-1," but the keyword is OCR-garbled and has no
# space before the number, so CORE_RE genuinely can't match it as written).

_FUZZY_PREFIX_RE = re.compile(r"^\s*([A-Za-z]{4,14})\s*([A-Z]?-?\d+(?:\.\d+)*)\b")
_FUZZY_TAIL_RE = re.compile(r"^(?P<prefix>[A-Z]-?)?(?P<number>[ivxlcdm]+|\d+(?:\.\d+)*)$", re.IGNORECASE)
_FUZZY_MATCH_CUTOFF = 0.75

# No-digit fallback: _FUZZY_PREFIX_RE above requires a literal digit
# character to even attempt a match — but OCR sometimes garbles the DIGIT
# itself into a letter, not just the family word (confirmed on a real
# sample: "AmexureAl" for "Annexure A-1" — the "1" was misread as a
# lowercase "l", leaving no digit anywhere in the string at all, unlike
# the already-handled "AmneXureA-1" case where the digit survives intact).
# This regex has no digit requirement — just a short trailing letter run —
# and is only ever tried once the primary, digit-anchored regex has
# already failed to match anything.
_FUZZY_PREFIX_NODIGIT_RE = re.compile(r"^\s*([A-Za-z]{4,14})([A-Za-z]{1,3})\s*$")

# OCR letter/digit confusions common enough to be worth a best-effort fix:
# l/I look like 1, O like 0, S like 5, Z like 2, B like 8, G like 6.
_DIGIT_CONFUSABLE_MAP = {"l": "1", "i": "1", "o": "0", "s": "5", "z": "2", "b": "8", "g": "6"}


def _denoise_digit_confusables(tail: str) -> str:
    return "".join(_DIGIT_CONFUSABLE_MAP.get(ch.lower(), ch) if ch.isalpha() else ch for ch in tail)


def _fuzzy_family_match(text: str) -> Optional[Tuple[str, Optional[str], str]]:
    """Best-effort recovery when CORE_RE's exact keyword fails - tries
    the leading alpha run (with and without its last 1-2 characters, to
    tolerate a trailing letter that's actually part of the prefix/number,
    e.g. "AmneXureA-1"'s trailing "A") against every FAMILIES alias.
    Returns (family, prefix_or_None, number_raw) or None.

    Falls back to _FUZZY_PREFIX_NODIGIT_RE when the primary, digit-anchored
    regex finds no digit at all — see that regex's comment. That fallback
    ALWAYS applies _denoise_digit_confusables to the tail before parsing it,
    never tries the raw tail first: _FUZZY_TAIL_RE's roman-numeral branch
    would otherwise happily (and wrongly) parse a bare "l" as the roman
    numeral L (=50) instead of a garbled "1". Since the fallback only ever
    runs when there is no digit anywhere in the input, it is structurally
    impossible for it to change behavior on any input the primary,
    already-verified path already resolves."""
    m = _FUZZY_PREFIX_RE.match(text)
    no_digit_fallback = False
    if not m:
        m = _FUZZY_PREFIX_NODIGIT_RE.match(text)
        no_digit_fallback = True
        if not m:
            return None
    candidates = [m.group(1)]
    if len(m.group(1)) > 4:
        candidates.append(m.group(1)[:-1])
    best_family, best_ratio, best_cand = None, 0.0, None
    for alias, family in _FAMILY_ALIASES.items():
        for cand in candidates:
            ratio = difflib.SequenceMatcher(None, alias, cand.lower()).ratio()
            if ratio > best_ratio:
                best_family, best_ratio, best_cand = family, ratio, cand
    if best_ratio < _FUZZY_MATCH_CUTOFF or best_family is None:
        return None
    # Whatever the *winning* (possibly-trimmed) candidate didn't consume
    # from the leading alpha run belongs back onto the prefix+number tail
    # - e.g. "AmneXureA-1": group(1)="AmneXureA", the trimmed candidate
    # "AmneXure" (8 chars) wins the ratio race against "annexure", so the
    # leftover "A" + group(2) "-1" reforms the real tail "A-1".
    tail = m.group(1)[len(best_cand):] + m.group(2)
    if no_digit_fallback:
        tail = _denoise_digit_confusables(tail)
    tail_m = _FUZZY_TAIL_RE.match(tail)
    if not tail_m:
        return None
    # reconcile_prefix_and_number takes a "keyword" string, but a family
    # name is already its own alias entry (_FAMILY_ALIASES["annexure"]
    # == "annexure"), so passing best_family directly resolves correctly.
    return best_family, *reconcile_prefix_and_number(best_family, tail_m.group("prefix"), tail_m.group("number"))


def _resolve_ai_hint(
    family_hint: Optional[str], number_hint: Optional[str],
) -> Optional[Tuple[str, Optional[str], str]]:
    """Resolves an AI-reported best-guess (family_hint, number_hint) pair —
    e.g. ("annexure", "A-1") for a garbled heading like "AmexureAl" — into
    the same (family, prefix, number_raw) shape _fuzzy_family_match returns,
    so it plugs into normalize_key unchanged. This is deliberately trusted
    OVER blind regex fuzzy-matching when present: recognizing a garbled
    heading's true family/number from context is exactly the judgement call
    AI-indexing exists to make, more general than any hand-coded
    confusable-character table could anticipate — see mint_key_for_heading's
    resolution order.

    Validated defensively, never raises: an unrecognized/hallucinated
    family_hint (not in _FAMILY_ALIASES) or an unparseable number_hint
    returns None, silently falling through to the existing cascade rather
    than propagating a bogus family into the key/scope machinery."""
    if not family_hint or not number_hint:
        return None
    family = _FAMILY_ALIASES.get(str(family_hint).strip().lower())
    if not family:
        return None
    tail_m = _FUZZY_TAIL_RE.match(str(number_hint).strip())
    if not tail_m:
        return None
    return family, *reconcile_prefix_and_number(family, tail_m.group("prefix"), tail_m.group("number"))


def collapse_key(key: str) -> str:
    """Strips internal periods from a key's number component only (never the
    family or prefix) — the OCR-tolerant fallback lookup value. Always computed,
    even when identical to `key` (e.g. integer-only clause numbers)."""
    family, _, number = key.partition(":")
    return f"{family}:{number.replace('.', '')}"


def _format_label_from_key(key: str) -> str:
    """Human label for a container's own key, e.g. "annexure:a-1" ->
    "Annexure A-1", "article:2" -> "Article 2" — reuses the already-
    normalized (roman-converted, prefix-reconciled) number embedded in `key`
    itself, rather than re-deriving from the raw regex match (which would
    require separately threading `prefix` through and silently drops it if
    it isn't)."""
    family, _, number = key.partition(":")
    return f"{family.capitalize()} {number.upper()}"


def _sanitize_for_id(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", key.lower()).strip("-")


# ─── mistune AST helpers ────────────────────────────────────────────────────────
# mistune's structured AST already separates markdown syntax (heading level,
# bold/italic wrappers) from leaf text content, so reconstructing a block's own
# plain text this way is *already* decoration-free by construction — no separate
# "strip `#`/`**`" regex step is needed, and it naturally matches what a
# rendered DOM element's textContent would be on the frontend side.

_CONTAINER_TYPES = {"list", "table", "table_head", "table_body", "table_row"}
_BLOCK_TYPES = {"paragraph", "heading", "list_item", "table_cell"}


def _get_parser():
    return mistune.create_markdown(renderer=None, plugins=["table"])


def _leaf_text(token: Dict[str, Any]) -> str:
    t = token.get("type")
    if t == "text":
        return token.get("raw", "")
    if t in ("softbreak", "linebreak"):
        return " "
    children = token.get("children")
    if children:
        return "".join(_leaf_text(c) for c in children)
    return ""


def _block_own_text(block: Dict[str, Any]) -> str:
    """A block's own text, excluding any nested sub-list's items (those are
    walked as separate blocks by `_flatten_blocks`). mistune strips an ordered
    list's own "N." marker out of its list_items entirely — `_flatten_blocks`
    re-attaches it as `_ordinal` so bare-numbered targets (the `ground`
    exception) can still be recognized from list-shaped OCR text."""
    t = block.get("type")
    if t == "list_item":
        parts = [
            _leaf_text(c) for c in block.get("children", [])
            if c.get("type") in ("block_text", "paragraph")
        ]
        text = " ".join(parts).strip()
        ordinal = block.get("_ordinal")
        return f"{ordinal}. {text}" if ordinal is not None else text
    return _leaf_text(block).strip()


def _flatten_blocks(tokens: List[Dict[str, Any]]):
    """Yields paragraph/heading/list_item/table_cell blocks in document order,
    recursing through list/table/table_head/table_body/table_row containers
    (which are never themselves yielded).

    Each list_item is also tagged with `_list_size`, the number of items in
    its own enclosing list - a real document heading shaped exactly like
    "N. Title" (e.g. "1. Article") parses as a mistune list of exactly ONE
    item, indistinguishable from a genuine multi-item list (a distribution
    list, a numbered-paragraph enumeration) by block type alone. Confirmed
    directly: both a lone real heading and a real 22-item addressee list on
    a page in a 955-page document parse as `list_item` - only the sibling
    count actually tells them apart."""
    for tok in tokens:
        t = tok.get("type")
        if t == "list":
            attrs = tok.get("attrs", {}) or {}
            ordered = attrs.get("ordered")
            start = attrs.get("start", 1) or 1
            children = tok.get("children", [])
            for idx, item in enumerate(children):
                if ordered:
                    item["_ordinal"] = start + idx
                item["_list_size"] = len(children)
                yield from _flatten_blocks([item])
            continue
        if t in _BLOCK_TYPES:
            yield tok
            if t == "list_item":
                for child in tok.get("children", []):
                    if child.get("type") != "block_text":
                        yield from _flatten_blocks([child])
        elif t in _CONTAINER_TYPES:
            yield from _flatten_blocks(tok.get("children", []))


# ─── Sub-document boundary & external-law exclusion ────────────────────────────

def detect_subdoc_trigger(block_text: str, nested_stack: List[Dict[str, str]]) -> Optional[str]:
    """Returns a formatted label for a GENERIC (unnumbered) sub-document
    trigger only — e.g. "Affidavit", "Judgment", a title-cased case-caption
    phrase — within a small bounded noise-prefix of the block's own
    beginning. Deliberately not anchored to the exact first character — real
    scanned pages routinely open a block with a stamp/docket-mark fragment
    before the real heading (confirmed: `ll`, `Q IN`, `4x4L`, `3S Dated ...`
    in real samples).

    Returns None when no trigger fires, OR when the trigger is one of the
    NUMBERED sub-document families (annexure/schedule/appendix): those are
    handled uniformly by the same _HIERARCHY_RANK push/pop machinery as
    article/section, via the ordinary core_m-driven candidate path in
    build_targets_for_page — no separate handling needed here. Also None
    for any block that's itself a decimal-numbered heading of this
    document's own clause structure (e.g. "3.3 TIME SCHEDULE") — that's
    handled by its own nesting path in build_targets_for_page, never a new
    sub-document boundary just because a trigger word (here "SCHEDULE")
    happens to appear inside its label.

    `nested_stack` is only consulted for the weak "INDEX" trigger — see
    _WEAK_SUBDOC_TRIGGER_RE."""
    if DECIMAL_HEADING_RE.match(block_text.strip()):
        return None
    m = SUBDOC_TRIGGER_RE.search(block_text)
    weak = False
    if not m or m.start() > _SUBDOC_NOISE_PREFIX:
        m = _WEAK_SUBDOC_TRIGGER_RE.search(block_text)
        weak = True
        if not m or m.start() > _SUBDOC_NOISE_PREFIX:
            return None
    if weak and any(_HIERARCHY_RANK.get(s["family"]) == 0 for s in nested_stack):
        return None  # a bare "INDEX" heading while an Annexure/Schedule/
        # Appendix is already open is that container's own internal table
        # of contents, not a new document - only trust it starting fresh.
    core_m = CORE_RE.match(block_text)
    if core_m and core_m.group("keyword").lower() in _SUBDOC_NUMBERED_FAMILIES:
        return None  # handled natively via _HIERARCHY_RANK below, not here
    return " ".join(w.capitalize() for w in re.split(r"\s+", m.group(0).strip()))


def _alpha_ratio_ok(text: str) -> bool:
    """Content-quality guard for the layout-only fallback (no textual/
    numeric match at all) — a title/header tag doesn't reliably mean "real
    structural heading" (a stamp/seal artifact like "\\( \\xi a \\)" can get
    the same tag). Validated directly against two real same-page samples:
    "AmneXureA-1" -> 9/11 alpha chars = 0.82 (passes); "\\( \\xi a \\)" ->
    3/11 alpha chars = 0.27 (correctly fails)."""
    alpha = sum(1 for c in text if c.isalpha())
    return alpha >= 3 and (alpha / max(1, len(text))) >= 0.5


def _numseg(number: str) -> List[str]:
    """A number's own dot-separated segments, a trailing ".0" segment
    dropped so "2.0" behaves as top-level "2" the same as a bare "2"
    would."""
    segments = number.split(".")
    if len(segments) > 1 and segments[-1] == "0":
        segments = segments[:-1]
    return segments


def _bare_integer_trusted(segments: List[str], last_bare_integer: Optional[int]) -> bool:
    """A bare/undotted single-segment number (no literal "." anywhere in the
    original matched text - "2.0"/"3.4" don't count, even though a trailing
    ".0" is later stripped from `segments`) is far too easily confused with
    an ordinary numbered list item, a stamped date, or an incidental
    quantity in running text to trust on its own. Confirmed directly
    against a real 955-page document: undotted numbers matched a running
    page header ("400" from "400 KV SUBSTATION KIRORI (HISAR)"), a stamped
    date ("27" from "27 MAR 2019"), a numbered judgment paragraph ("39"
    from "39. Consequently, the objection petition is hereby dismissed with
    costs"), and a stray equipment-schedule line ("1" from "1 Ph Welding
    Transformer (Air Cooled)") - all short enough to otherwise pass the
    label-length check. A bare integer is only trusted when it's the first
    heading of a fresh numbered outline ("1", with no bare-integer heading
    already open) or when it's a plausible next-in-sequence continuation of
    one that is (matches the 6-page job's real "1. Article" opening, which
    has nothing numbered open yet).

    `last_bare_integer` is a dedicated tracker threaded through
    build_targets_for_page's scope (not a nested_stack scan) precisely so
    it survives a letter-heading transition even though nested_stack's own
    numheading entries get cleared there for correct nesting - confirmed
    necessary on a real document where clause 12 continues clause 11's
    numbering straight across a new "B." section opening in between."""
    if last_bare_integer is None:
        return segments == ["1"]
    try:
        return int(segments[0]) in (last_bare_integer, last_bare_integer + 1)
    except ValueError:
        return False


def _letter_heading_trusted(ordinal: int, last_letter_ordinal: Optional[int]) -> bool:
    """Mirrors `_bare_integer_trusted`'s fresh-or-sequential discipline for
    single-letter section headings ("A. INTRODUCTION", ..., "F. AWARD OF
    CONTRACT"). Confirmed necessary against the same real document: a
    signature-block name with a middle initial ("H. G CHHABRA") matches
    LETTER_HEADING_RE's shape just as well as a genuine section heading -
    only trusted when nothing lettered is open yet and it's "A" (ordinal 1),
    or it's the immediately-following letter after what's already open."""
    if last_letter_ordinal is None:
        return ordinal == 1
    return ordinal in (last_letter_ordinal, last_letter_ordinal + 1)


def _decimal_heading_match(
    text: str,
    ltype: Optional[str],
    layout_aligned: bool,
    last_bare_integer: Optional[int],
    is_list_item: bool = False,
) -> Optional[Dict[str, Any]]:
    """Matches DECIMAL_HEADING_RE against a block's own (stripped) text and,
    when layout data is available for this block, additionally requires the
    model's own tag to be "title" or "header" - strictly additive
    corroboration on top of the length/shape check, not a replacement (when
    layout data isn't available at all, the regex alone still catches an
    obviously heading-shaped short numbered label). Returns a dict with the
    number's own dot-separated segments (see `_numseg`), the 0-indexed rank
    (segment count - 1), the label text, and match_end (end of the matched
    number, for _citation_confidence_reasons) - or None.

    Never matches a mistune `list_item` block - a real document heading is
    never itself a numbered list item; confirmed directly against a real
    955-page document where a 22-item addressee list ("1. Sr. PS to
    Chairman...", ..., "22. Supdt. (Purchase)/MM...") would otherwise be
    mistaken for numbered clause headings (one item, short enough to pass
    the label-length check, actually was).

    A bare/undotted number additionally requires `_bare_integer_trusted` -
    see that function for why. Numbers containing a literal "." are exempt
    and keep the original, more permissive behavior unchanged."""
    if is_list_item:
        return None
    m = DECIMAL_HEADING_RE.match(text.strip())
    if not m:
        return None
    if layout_aligned and ltype not in ("title", "header"):
        return None
    number = m.group("number")
    segments = _numseg(number)
    if "." not in number and not _bare_integer_trusted(segments, last_bare_integer):
        return None
    return {
        "number": number,
        "segments": segments,
        "rank": len(segments) - 1,
        "label": m.group("label"),
        "match_end": m.end("number"),
    }


_DECIMAL_NUMBER_HEAD_RE = re.compile(r"^\s*(?P<number>\d+(?:\.\d+)*)\.?(?:\s+|$)")


def _headerless_decimal_match(
    text: str, nested_stack: List[Dict[str, Any]], is_list_item: bool
) -> Optional[Dict[str, Any]]:
    """A decimal-numbered clause with no short-label title at all - e.g. a
    bare "2.1", or "2.1 The contract shall be performed strictly as per the
    terms and conditions..." running well past DECIMAL_HEADING_RE's 9-word
    cap.

    A bare/undotted number (no literal "." anywhere in the matched text) is
    never promoted this way: with no title AND no dot, there is no
    author-asserted evidence left that it's a heading at all - see
    `_bare_integer_trusted`'s docstring for why that exact shape is almost
    always ordinary numbered prose instead.

    A dotted number that still canonicalizes to a single segment after
    `_numseg` strips a trailing ".0" (e.g. "3.0" -> ["3"]) is trusted
    unconditionally, the same as a titled "3.0 ..." heading already is -
    confirmed necessary against a real document, where a genuine top-level
    heading ("3.0 - WELDING AND GAS CUTTING EQUIPMENTS...") failed
    DECIMAL_HEADING_RE's label shape only because of a stray leading "-",
    not because it was any less a real heading.

    A dotted number with more than one segment (e.g. "2.1", "3.4.1") is
    trusted only when its immediate parent segments are already open on
    `nested_stack` - the same containment evidence a titled decimal
    heading's own nesting already relies on (see the pop-loop in
    build_targets_for_page)."""
    if is_list_item:
        return None
    m = _DECIMAL_NUMBER_HEAD_RE.match(text.strip())
    if not m:
        return None
    number = m.group("number")
    if "." not in number:
        return None
    segments = _numseg(number)
    if len(segments) > 1:
        parent_segments = segments[:-1]
        if not any(s.get("num_segments") == parent_segments for s in nested_stack):
            return None
    return {
        "number": number,
        "segments": segments,
        "rank": len(segments) - 1,
        "label": None,
        "match_end": m.end("number"),
    }


def _is_external_law(block_text: str) -> bool:
    return bool(
        EXTERNAL_LAW_A_RE.search(block_text)
        or EXTERNAL_LAW_B_RE.search(block_text)
        or EXTERNAL_LAW_C_RE.search(block_text)
        or EXTERNAL_LAW_D_RE.search(block_text)
    )


def _ground_scope_active(nested_stack: List[Dict[str, str]]) -> bool:
    """The narrow, curated exception allowing bare digit/roman-numeral targets —
    only within a recognized enumeration heading's scope, never reopened
    generally (would otherwise flood the index with ordinary numbered prose).
    Checking only the outermost entry (index 0), not every stack entry, is
    intentional: the "subdoc" family — which is what a "GROUNDS OF APPEAL"
    trigger becomes — is hardcoded to rank 0, and the pop/push invariant
    guarantees a rank-0 entry is always at index 0. No other index could ever
    hold a Grounds-matching label under this rank table."""
    if nested_stack and _GROUND_SCOPE_HEADINGS.search(nested_stack[0]["label"]):
        return True
    return any(s.get("family") == "ground" for s in nested_stack)


def _disambiguate_label(label: str, counts: Dict[str, int]) -> str:
    """First occurrence of a generic (unnumbered) sub-document label is left
    unchanged; the 2nd, 3rd, ... get a distinguishing suffix — a numbered
    family (Annexure A-1 vs A-2) never needs this, it's already unique."""
    n = counts.get(label, 0)
    counts[label] = n + 1
    return label if n == 0 else f"{label} ({n + 1})"


# ─── Phase A core algorithm (pure — no DB/async) ───────────────────────────────

def build_targets_for_page(
    page_text: str, seed_scope: Optional[Dict[str, Any]], has_next_page: bool,
    raw_text: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Detects targets on one page's Markdown text.

    Returns (candidates, new_end_of_page_scope). Each candidate dict has:
    key, occurrence_index, anchor_id_suffix (backend fills in job/page when
    persisting), snippet, truncated, continues_on_page, scope_key, scope_label,
    collapsed_key, fingerprint, heading_confidence, heading_confidence_reasons.

    `seed_scope` is the previous page's saved end-of-page scope (or None to
    start fresh — used both for page 1 and when a sub-document boundary clears
    it). `has_next_page` controls whether an unclosed accumulator at the end of
    the page is eligible for page-boundary truncation at all.

    `raw_text` is the OCR engine's raw, pre-clean_detection_tags() output for
    this page (baidu/Unlimited-OCR only — None for any other engine, or for
    pages OCR'd before this capture existed). When present and its parsed
    layout-block count lines up 1:1 with this page's parsed markdown blocks,
    each candidate's confidence_reasons additionally reflects the model's own
    per-block layout type (header/title = positive signal, text = negative)
    — additive only, never removing a reason a text heuristic already added,
    and never applied at all on a count mismatch (never guess which block a
    layout type belongs to).
    """
    tokens, _ = _get_parser().parse(page_text or "")
    blocks = list(_flatten_blocks(tokens))

    layout_blocks = parse_layout_blocks(raw_text)
    layout_aligned = bool(layout_blocks) and len(layout_blocks) == len(blocks)

    scope = seed_scope or {"stack": [], "boundary_ordinal": 0, "label_occurrence_counts": {}}
    nested_stack: List[Dict[str, str]] = list(scope.get("stack", []))
    boundary_ordinal = scope.get("boundary_ordinal", 0)
    label_occurrence_counts: Dict[str, int] = dict(scope.get("label_occurrence_counts", {}))
    # Tracked independently of nested_stack's own containment/nesting so a
    # letter-heading transition (which clears nested_stack's numheading
    # entries for correct nesting) doesn't also break the sequential-
    # continuation evidence _bare_integer_trusted/_letter_heading_trusted
    # rely on - see their docstrings.
    last_bare_integer: Optional[int] = scope.get("last_bare_integer")
    last_letter_ordinal: Optional[int] = scope.get("last_letter_ordinal")

    candidates: List[Dict[str, Any]] = []
    occurrence_counts: Dict[str, int] = {}
    open_acc: Optional[Dict[str, Any]] = None
    # Same-page-repeat detection (Signal #4) — scoped to the SAME numbered
    # subdoc family repeating (e.g. two "annexure" triggers), not any rank-0
    # trigger in general: a real document can legitimately bind in an
    # Annexure *and* a Schedule on one page (different families, both
    # confirmed by an existing test) — only a same-family repeat matches the
    # confirmed real bug (annexure:a-5 then annexure:p-4 on one page).
    # Deliberately NOT applied to generic/unnumbered triggers (AFFIDAVIT,
    # JUDGMENT, ...) either — two different real people's affidavits on one
    # page are a legitimate, already-tested case, distinguishable by their
    # own label text in a way a repeated numbered citation isn't.
    seen_numbered_subdoc_families: set = set()

    def finalize(closed_by_new_target: bool):
        nonlocal open_acc
        if open_acc is None:
            return
        body_text = "\n\n".join(_block_own_text(b) for b in open_acc["blocks"])
        truncated = False
        if not closed_by_new_target:
            trimmed = body_text.rstrip()
            if trimmed and trimmed[-1] not in _SENTENCE_END_CHARS:
                truncated = True

        confidence_reasons = open_acc["confidence_reasons"]

        candidates.append({
            "key": open_acc["key"],
            "occurrence_index": open_acc["occurrence_index"],
            "snippet": body_text[:400],
            "truncated": truncated and has_next_page,
            "continues_on_page": None,  # caller fills in page+1 when truncated
            "scope_key": open_acc["scope_key"],
            "scope_label": open_acc["scope_label"],
            "scope_chain": open_acc["scope_chain"],
            "collapsed_key": collapse_key(open_acc["key"]),
            "fingerprint": open_acc["fingerprint"],
            "heading_confidence": "low" if confidence_reasons else "high",
            "heading_confidence_reasons": confidence_reasons,
            "own_label": open_acc.get("own_label"),
        })
        open_acc = None

    for block_idx, block in enumerate(blocks):
        text = _block_own_text(block)
        if not text:
            continue

        trigger_label = detect_subdoc_trigger(text, nested_stack)
        if trigger_label:
            # A generic boundary closes whatever was open before it, exactly
            # like a keyword-matched target does — without this, an
            # AFFIDAVIT/JUDGMENT heading with no accompanying core_m match
            # would fall through to the `open_acc["blocks"].append(block)`
            # tail below and get silently swallowed into the previous
            # clause's snippet instead of ending it.
            finalize(closed_by_new_target=True)
            boundary_ordinal += 1
            label = _disambiguate_label(trigger_label, label_occurrence_counts)
            # A brand new sub-document boundary obviously starts fresh
            # numbering too — numbered-heading entries (pushed below) have
            # no independent identity to survive it.
            nested_stack = [
                s for s in nested_stack
                if "num_segments" not in s and _HIERARCHY_RANK.get(s["family"], 99) < 0  # rank 0 = pop everything
            ]
            nested_stack.append({"family": "subdoc", "key": f"subdoc:{boundary_ordinal}", "label": label})
            last_bare_integer = None
            last_letter_ordinal = None

        core_m = CORE_RE.match(text)
        family = None
        number_raw = None
        prefix = None
        is_ground = False
        is_fuzzy = False
        match_end = None

        if core_m:
            keyword = core_m.group("keyword")
            family = _FAMILY_ALIASES.get(keyword.lower().rstrip("."), keyword.lower())
            prefix, number_raw = reconcile_prefix_and_number(keyword, core_m.group("prefix"), core_m.group("number"))
            match_end = core_m.end()
        else:
            fuzzy_m = _fuzzy_family_match(text)
            if fuzzy_m:
                family, prefix, number_raw = fuzzy_m
                is_fuzzy = True
                match_end = _FUZZY_PREFIX_RE.match(text).end()
            elif _ground_scope_active(nested_stack):
                gm = GROUND_DIGIT_RE.match(text) or GROUND_ROMAN_RE.match(text)
                if gm:
                    family = "ground"
                    number_raw = gm.group("number")
                    is_ground = True
                    match_end = gm.end()

        if family and number_raw:
            key = normalize_key(family if (is_ground or is_fuzzy) else core_m.group("keyword"), prefix, number_raw)
            excluded = _is_backward_reference(text, match_end) or ((not is_ground) and _is_external_law(text))

            finalize(closed_by_new_target=True)

            if not excluded:
                occ = occurrence_counts.get(key, 0)
                occurrence_counts[key] = occ + 1

                confidence_reasons = _citation_confidence_reasons(text, match_end)
                if family in _SUBDOC_NUMBERED_FAMILIES:
                    if family in seen_numbered_subdoc_families:
                        confidence_reasons.append("same_page_repeat")
                    else:
                        seen_numbered_subdoc_families.add(family)
                if layout_aligned:
                    ltype = layout_blocks[block_idx]["type"]
                    if ltype in ("header", "title"):
                        confidence_reasons.append(f"layout_{ltype}")
                    elif ltype == "text":
                        confidence_reasons.append("non_heading_layout_type")

                # Captured BEFORE the fuzzy reason below is appended: fuzzy
                # spelling-uncertainty is a genuinely different kind of
                # doubt than a citation-shaped false positive or a
                # layout-says-body-text signal - it's still shown to a
                # reviewer (low confidence), but it must not additionally
                # block this match from becoming a real container the way
                # those other reasons correctly do (an OCR-garbled but
                # otherwise clean "AmneXureA-1" still needs to anchor
                # everything nested under it).
                push_blocking = bool(confidence_reasons)
                if is_fuzzy:
                    confidence_reasons.append("fuzzy_keyword_match")

                # If this match is itself a container AND fully confident,
                # pop same-or-higher-rank siblings BEFORE computing this
                # row's own scope, then push itself as the new ancestor —
                # otherwise a just-replaced sibling (e.g. a second Article,
                # or a Schedule following an Annexure at the same rank 0)
                # would incorrectly appear as this row's own ancestor,
                # computed from the stale pre-pop stack. Leaves (clause/para/
                # ground/page — not in _HIERARCHY_RANK) never pop anything,
                # so they're unaffected: they just read whatever's currently
                # on the stack, as before.
                #
                # A container match with any confidence_reasons does NEITHER
                # pop nor push — full stack-neutrality, not just a skipped
                # push. A flagged match (a citation-shaped false positive, a
                # repeated running header, a same-page-repeat sub-document
                # trigger) is noise *within* whatever section is genuinely
                # still active, not proof that section has ended — popping
                # the real ancestor on a match we don't even trust would
                # orphan every genuine clause that follows it until the next
                # real container. (An earlier draft of this logic popped
                # unconditionally and only skipped the push — reconsidered
                # after checking it against the confirmed P-4-after-A-5 case:
                # that would have made clause rows *following* the low-
                # confidence P-4 match lose Annexure A-5 as their ancestor
                # entirely, which is wrong — content after a noisy match is
                # still part of whichever real sub-document was already
                # open.)
                if family in _HIERARCHY_RANK and not push_blocking:
                    nested_stack = [
                        s for s in nested_stack
                        if "num_segments" not in s and s.get("family") != "letterheading"
                        and _HIERARCHY_RANK.get(s["family"], -1) < _HIERARCHY_RANK[family]
                    ]
                    # A genuine new/real container obviously starts fresh
                    # numbering AND fresh lettering too - unlike a letter
                    # transition, this is a real sub-document boundary.
                    last_bare_integer = None
                    last_letter_ordinal = None

                # Full ancestor chain (including the sub-document tier, now
                # unified into the same nested_stack), not just the deepest
                # container — an earlier version keyed/labeled only by
                # nested_stack[-1], so two clauses under different Articles'
                # identically-numbered Sections (e.g. Article 2 > Section 3
                # vs Article 5 > Section 3) produced the same
                # scope_key/scope_label and were indistinguishable. No
                # separate leading counter is needed for disambiguation
                # anymore — a sub-document's own key (Annexure A-1 vs A-2, or
                # a synthetic subdoc:N for a generic trigger) is already
                # unique, so two different sub-documents naturally produce
                # different joined strings without one.
                scope_key = "/".join(s["key"] for s in nested_stack)
                scope_label = " › ".join(s["label"] for s in nested_stack)

                open_acc = {
                    "key": key,
                    "occurrence_index": occ,
                    "blocks": [block],
                    "scope_key": scope_key,
                    "scope_label": scope_label,
                    "scope_chain": [dict(s) for s in nested_stack],
                    "fingerprint": text[:30],
                    "confidence_reasons": confidence_reasons,
                }

                if family in _HIERARCHY_RANK and not push_blocking:
                    nested_stack.append({"family": family, "key": key, "label": _format_label_from_key(key)})
            continue

        # A lone "N. Title" heading (e.g. "1. Article") parses as mistune's
        # own one-item ordered list, structurally identical in block `type`
        # to a genuine multi-item numbered list (an addressee list, a
        # numbered-paragraph enumeration) - only the sibling count tells
        # them apart. See _flatten_blocks for where _list_size is set.
        is_list_item = block.get("type") == "list_item" and block.get("_list_size", 1) > 1
        ltype = layout_blocks[block_idx]["type"] if layout_aligned else None
        dm = _decimal_heading_match(text, ltype, layout_aligned, last_bare_integer, is_list_item)
        headerless = False
        if not dm:
            dm = _headerless_decimal_match(text, nested_stack, is_list_item)
            headerless = dm is not None
        if dm:
            finalize(closed_by_new_target=True)

            key = f"numheading:{dm['number']}"
            occ = occurrence_counts.get(key, 0)
            occurrence_counts[key] = occ + 1

            own_label = f"{dm['number']} {dm['label']}" if dm["label"] else dm["number"]

            # Same pipeline as a real core_m hit (citation heuristics,
            # layout_{ltype} reason when aligned) - this is a genuinely
            # numbered, author-asserted heading, not a guess, so it isn't
            # forced low the way the residual layout-only fallback below is.
            # A headerless match's `text` can be a full body paragraph, not
            # a short title - the two unbounded (whole-remainder-scanning)
            # heuristics are skipped for it; see _citation_confidence_reasons.
            confidence_reasons = _citation_confidence_reasons(text, dm["match_end"], include_unbounded=not headerless)
            if layout_aligned:
                if ltype in ("header", "title"):
                    confidence_reasons.append(f"layout_{ltype}")
                elif ltype == "text":
                    confidence_reasons.append("non_heading_layout_type")

            # Pop from the top only while the top entry is itself a
            # numbered-heading entry whose own segments are NOT a proper
            # prefix of this number's segments up to that entry's own
            # length - i.e. keep an ancestor only if this number actually
            # continues it. Stops the instant it reaches a stack entry
            # that isn't a numbered-heading entry (a real Annexure/
            # Schedule/Article/Section/subdoc container) - those are never
            # touched by this walk, so a numbered clause can never
            # dislodge the real container it lives inside. Skipped
            # entirely (like a flagged container match) when this match
            # isn't trusted enough to itself become an ancestor.
            segs = dm["segments"]
            if not confidence_reasons:
                while nested_stack and "num_segments" in nested_stack[-1]:
                    top_segs = nested_stack[-1]["num_segments"]
                    if top_segs != segs[: len(top_segs)]:
                        nested_stack.pop()
                    else:
                        break

            scope_key = "/".join(s["key"] for s in nested_stack)
            scope_label = " › ".join(s["label"] for s in nested_stack)

            can_anchor = not confidence_reasons

            open_acc = {
                "key": key,
                "occurrence_index": occ,
                "blocks": [block],
                "scope_key": scope_key,
                "scope_label": scope_label,
                "scope_chain": [dict(s) for s in nested_stack],
                "fingerprint": text[:30],
                "confidence_reasons": confidence_reasons,
                "own_label": own_label,
            }

            if headerless:
                # Display-only doubt, deliberately excluded from the gate
                # above (same precedent already set for fuzzy_keyword_match)
                # - a headerless clause still needs to anchor whatever's
                # nested under it (e.g. a hypothetical "2.1.1").
                confidence_reasons.append("headerless_numbered_clause")

            if can_anchor:
                nested_stack.append({
                    "family": "numheading", "key": key, "label": own_label, "num_segments": segs,
                })
                if len(segs) == 1:
                    last_bare_integer = int(segs[0])
            continue

        letter_m = LETTER_HEADING_RE.match(text.strip())
        if letter_m:
            ordinal = ord(letter_m.group("letter")) - ord("A") + 1
            if _letter_heading_trusted(ordinal, last_letter_ordinal):
                finalize(closed_by_new_target=True)

                key = f"letterheading:{letter_m.group('letter').lower()}"
                occ = occurrence_counts.get(key, 0)
                occurrence_counts[key] = occ + 1

                own_label = f"{letter_m.group('letter')} {letter_m.group('label')}"

                confidence_reasons = _citation_confidence_reasons(text, letter_m.end("letter"))
                if layout_aligned:
                    ltype = layout_blocks[block_idx]["type"]
                    if ltype in ("header", "title"):
                        confidence_reasons.append(f"layout_{ltype}")
                    elif ltype == "text":
                        confidence_reasons.append("non_heading_layout_type")

                can_anchor = not confidence_reasons

                # A new letter section is a sibling of the previous one, not
                # its child - pop any existing letterheading entry. Numheading
                # entries are ALSO cleared here (so the new letter's own
                # children nest under IT, not under a numbered clause left
                # over from the previous letter) - but this is a nested_stack
                # -only cleanup: last_bare_integer is a separate tracker,
                # untouched by it, because the document's numbered-clause
                # sequence (11, 12, ...) runs CONTINUOUSLY across letter
                # boundaries - confirmed on a real document, where clause 12
                # directly continues clause 11 despite "B." opening in
                # between. Only a genuine new sub-document (below) resets
                # last_bare_integer/last_letter_ordinal themselves.
                if can_anchor:
                    nested_stack = [s for s in nested_stack if "num_segments" not in s and s.get("family") != "letterheading"]

                scope_key = "/".join(s["key"] for s in nested_stack)
                scope_label = " › ".join(s["label"] for s in nested_stack)

                open_acc = {
                    "key": key,
                    "occurrence_index": occ,
                    "blocks": [block],
                    "scope_key": scope_key,
                    "scope_label": scope_label,
                    "scope_chain": [dict(s) for s in nested_stack],
                    "fingerprint": text[:30],
                    "confidence_reasons": confidence_reasons,
                    "own_label": own_label,
                }

                if can_anchor:
                    nested_stack.append({"family": "letterheading", "key": key, "label": own_label})
                    last_letter_ordinal = ordinal
                continue

        # Residual fallback: a heading-shaped block (model-tagged "title" or
        # "header") with no number and no keyword match at all. No textual
        # or numeric evidence exists for where this belongs, so - honestly,
        # not "concretely" - it's nested simply as a child of whatever's
        # currently open, always low confidence.
        if layout_aligned:
            ltype = layout_blocks[block_idx]["type"]
            if ltype in ("title", "header") and _alpha_ratio_ok(text):
                finalize(closed_by_new_target=True)
                boundary_ordinal += 1
                key = f"subdoc:{boundary_ordinal}"

                scope_key = "/".join(s["key"] for s in nested_stack)
                scope_label = " › ".join(s["label"] for s in nested_stack)

                open_acc = {
                    "key": key,
                    "occurrence_index": 0,
                    "blocks": [block],
                    "scope_key": scope_key,
                    "scope_label": scope_label,
                    "scope_chain": [dict(s) for s in nested_stack],
                    "fingerprint": text[:30],
                    "confidence_reasons": ["layout_only_no_text_match"],
                }
                nested_stack.append({"family": "subdoc", "key": key, "label": text[:60]})
                continue

        if open_acc is not None:
            open_acc["blocks"].append(block)

    finalize(closed_by_new_target=False)

    new_scope = {
        "stack": nested_stack, "boundary_ordinal": boundary_ordinal, "label_occurrence_counts": label_occurrence_counts,
        "last_bare_integer": last_bare_integer, "last_letter_ordinal": last_letter_ordinal,
    }
    return candidates, new_scope


def make_anchor_id(key: str, page: int, occurrence_index: int) -> str:
    return f"xref-target-{_sanitize_for_id(key)}-p{page}-{occurrence_index}"


def detect_stamped_page_number(page_text: str) -> Optional[int]:
    """Best-effort, opportunistic scan of a page's leading/trailing lines for a
    plausible printed/stamped page number (scanned legal paper books commonly
    carry dual pagination distinct from the physical PDF index). Frequently a
    no-op on degraded scans (confirmed: real running headers OCR'd as `ll`,
    `l3`, `M`) — the physical-page self-reference (§ orchestration layer) is
    the real, always-available safety net, not this heuristic."""
    lines = [ln.strip() for ln in (page_text or "").splitlines() if ln.strip()]
    for ln in (lines[:2] + lines[-2:]) if lines else []:
        if re.fullmatch(r"\d{1,4}", ln):
            return int(ln)
    return None


# ─── Orchestration layer (DB + concurrency) ────────────────────────────────────

_frontier_locks: Dict[str, asyncio.Lock] = {}
_CASCADE_PAGE_CAP = 100


def _lock_for_job(job_id: str) -> asyncio.Lock:
    lock = _frontier_locks.get(job_id)
    if lock is None:
        lock = asyncio.Lock()
        _frontier_locks[job_id] = lock
    return lock


async def update_reference_index_for_page(
    job_id: str, page_num: int, page_text: str, total_pages: int, raw_text: Optional[str] = None,
):
    """Call this after a page's OCR text is persisted (i.e. right after
    `database.upsert_page(..., text=...)`). Handles the frontier/cascade
    out-of-order-safety mechanism; safe to call for pages completing in any
    order, including retries of an already-processed page.

    `raw_text` (optional) is this page's raw, pre-clean_detection_tags() OCR
    output, when the caller captured one — see build_targets_for_page."""
    frontier = database.get_reference_index_frontier(job_id)
    if page_num == frontier + 1:
        async with _lock_for_job(job_id):
            # Re-check after acquiring the lock — another completion may have
            # already advanced the frontier while we were waiting.
            frontier = database.get_reference_index_frontier(job_id)
            if page_num != frontier + 1:
                await _process_single_page(job_id, page_num, page_text, total_pages, provisional=True, raw_text=raw_text)
                return
            await _process_single_page(job_id, page_num, page_text, total_pages, provisional=False, raw_text=raw_text)
            database.set_reference_index_frontier(job_id, page_num)
            # Already holding the per-job lock here — cascade in-line rather
            # than through the lock-acquiring entry point (asyncio.Lock isn't
            # reentrant; re-acquiring it from the same task would deadlock).
            await _cascade_locked(job_id, page_num, total_pages)
    else:
        await _process_single_page(job_id, page_num, page_text, total_pages, provisional=True, raw_text=raw_text)


async def _cascade_entry(job_id: str, from_page: int, total_pages: int):
    """Lock-acquiring entry point — used only by a cascade's own follow-up
    continuation task, which is a fresh call, not nested inside an
    already-held lock."""
    async with _lock_for_job(job_id):
        await _cascade_locked(job_id, from_page, total_pages)


async def _cascade_locked(job_id: str, from_page: int, total_pages: int):
    """Advances the frontier through any already-processed (provisional) pages
    immediately following `from_page`, bounded to `_CASCADE_PAGE_CAP` pages per
    run — a longer cascade continues via another asyncio task instead of
    looping unboundedly inside this call. Caller must already hold this job's
    per-job lock."""
    frontier = from_page
    advanced = 0
    while advanced < _CASCADE_PAGE_CAP:
        next_page = frontier + 1
        if next_page > total_pages:
            break
        page_row = database.get_page(job_id, next_page)
        if not page_row or not database.has_provisional_reference_targets(job_id, next_page):
            break
        await _process_single_page(job_id, next_page, page_row.get("text") or "", total_pages, provisional=False)
        frontier = next_page
        database.set_reference_index_frontier(job_id, frontier)
        advanced += 1
    else:
        # Cascade capped, more may remain — continue in a follow-up task
        # carrying just enough state to resume, per the approved plan's
        # bounded-cascade design.
        asyncio.create_task(_cascade_entry(job_id, frontier, total_pages))


def parse_page_blocks(page_text: str) -> List[str]:
    """Public wrapper over this module's own block parser (_get_parser +
    _flatten_blocks + _block_own_text) — used by ai_reference_index.py to
    ground an AI-reported heading against a page's real text blocks,
    without reaching into three separate private helpers itself."""
    tokens, _ = _get_parser().parse(page_text or "")
    return [_block_own_text(b) for b in _flatten_blocks(tokens)]


def mint_key_for_heading(
    text: str, boundary_ordinal: int, label_occurrence_counts: Dict[str, int],
    family_hint: Optional[str] = None, number_hint: Optional[str] = None,
) -> Tuple[str, str, str, Optional[List[str]], int]:
    """Reuses the rule detector's own PARSERS for key shape — never its
    trust gates (_bare_integer_trusted, _letter_heading_trusted, the
    layout_aligned gate, the headerless-decimal containment gate) — since
    deciding "is this a heading at all" is exactly the judgement being
    handed to the AI here; only the shape-to-key mapping is reused, so an
    AI-minted key stays compatible with referenceStore.js's mention linking
    and StructureTree.formatKeyLabel instead of an incompatible namespace.

    `family_hint`/`number_hint` (optional — always None from the rule-based
    path, which never calls this function) are the AI's own best-guess
    resolution of a garbled heading it couldn't read cleanly (e.g.
    heading_text="AmexureAl", family_hint="annexure", number_hint="A-1").

    Tried ONLY AS A LAST RESORT, after every other parser has already
    failed to match — never as a pre-emptive override. Confirmed necessary
    by a real run: the model volunteers a hint far more liberally than the
    prompt asks for, including on headings that already parse perfectly
    well on their own (e.g. hinting family_hint="section" on a plain
    "3.4 QUALITY PLANS", which the decimal-number parser below already
    resolves correctly to "numheading:3.4"). Trying the hint first meant a
    volunteered-but-unnecessary hint could silently re-key an
    already-correct match into a different family namespace. Since this
    function's whole point is "the AI-detected version of what
    CORE_RE/fuzzy/decimal/letter already resolve for the rule-based path,"
    the hint may only ever catch what NONE of those parsers could — which
    is exactly the genuinely-garbled case ("AmexureAl") it exists for,
    since nothing else can match that at all.

    Returns (family, key, label, num_segments_or_None, new_boundary_ordinal).
    """
    stripped = (text or "").strip()

    core_m = CORE_RE.match(stripped)
    if core_m:
        key = normalize_key(core_m.group("keyword"), core_m.group("prefix"), core_m.group("number"))
        return key.partition(":")[0], key, _format_label_from_key(key), None, boundary_ordinal

    fuzzy = _fuzzy_family_match(stripped)
    if fuzzy:
        family, prefix, number_raw = fuzzy
        key = normalize_key(family, prefix, number_raw)
        return family, key, _format_label_from_key(key), None, boundary_ordinal

    # Decimal-numbered heading — the number-extraction shape only, not
    # DECIMAL_HEADING_RE's stricter <=9-word label cap: the AI already
    # asserted this line is a heading, so a longer label must not be
    # silently rejected the way an unproven text heuristic would need to.
    dm = _DECIMAL_NUMBER_HEAD_RE.match(stripped)
    if dm:
        number = dm.group("number")
        segments = _numseg(number)
        key = f"numheading:{number}"
        label_part = stripped[dm.end():].strip()
        own_label = f"{number} {label_part}" if label_part else number
        return "numheading", key, own_label, segments, boundary_ordinal

    lm = re.match(r"^\s*(?P<letter>[A-Z])\.\s+(?P<label>.+)$", stripped)
    if lm:
        letter = lm.group("letter")
        key = f"letterheading:{letter}"
        return "letterheading", key, f"{letter}. {lm.group('label').strip()}", None, boundary_ordinal

    # Last resort before the generic subdoc bucket: the AI's own hint for
    # a heading nothing above could parse at all — see this function's
    # docstring for why this must stay last, not first.
    hinted = _resolve_ai_hint(family_hint, number_hint)
    if hinted:
        family, prefix, number_raw = hinted
        key = normalize_key(family, prefix, number_raw)
        return family, key, _format_label_from_key(key), None, boundary_ordinal

    # Otherwise: the keyword-less/number-less case AI exists for. Minted
    # exactly like the number a plain new boundary_ordinal always gets
    # (matching the existing residual layout-only fallback's own key
    # shape, build_targets_for_page:1071-1097) — but note this function
    # NEVER decides how much of nested_stack to pop; that's driven solely
    # by the caller's explicitly-resolved `depth` in
    # apply_heading_events_to_scope, never by this family's rank. That is
    # the deliberate fix for a real bug found in review: FAMILIES["subdoc"]
    # is rank 0, and BOTH existing rank-0 push paths in this module
    # (detect_subdoc_trigger's boundary trigger, and the generic
    # rank-based container push) unconditionally clear the ENTIRE
    # nested_stack when pushing any rank-0 entry — correct for a genuine
    # new sub-document boundary, but would silently evict an AI-detected
    # heading's real Article/Section ancestor if this module's rank table
    # were consulted for it at all. It never is.
    boundary_ordinal += 1
    key = f"subdoc:{boundary_ordinal}"
    label = _disambiguate_label(stripped[:60], label_occurrence_counts)
    return "subdoc", key, label, None, boundary_ordinal


def validate_scope_shape(scope: Dict[str, Any]) -> None:
    """Raises ValueError unless `scope` is structurally compatible with what
    build_targets_for_page emits — the contract both compute_scope_ranges
    and a later rule-based fallback chunk silently depend on (e.g.
    _ground_scope_active indexes nested_stack[0]["label"] without .get(),
    and _bare_integer_trusted/_letter_heading_trusted read
    last_bare_integer/last_letter_ordinal expecting them to always be
    present, even if None)."""
    if not isinstance(scope, dict):
        raise ValueError("scope must be a dict")
    for field_name in ("stack", "boundary_ordinal", "label_occurrence_counts",
                        "last_bare_integer", "last_letter_ordinal"):
        if field_name not in scope:
            raise ValueError(f"scope is missing required field {field_name!r}")
    stack = scope["stack"]
    if not isinstance(stack, list):
        raise ValueError("scope['stack'] must be a list")
    for i, entry in enumerate(stack):
        if not isinstance(entry, dict):
            raise ValueError(f"stack[{i}] must be a dict")
        for field_name in ("family", "key", "label"):
            if field_name not in entry:
                raise ValueError(f"stack[{i}] is missing required field {field_name!r}")
        if entry.get("family") == "numheading" and "num_segments" not in entry:
            raise ValueError(f"stack[{i}] is a numheading entry missing num_segments")


def apply_heading_events_to_scope(
    events: List[Dict[str, Any]], seed_scope: Optional[Dict[str, Any]], page_num: int, has_next_page: bool,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """AI-side analogue of build_targets_for_page's stack machine — same
    (candidates, new_scope) contract and the same 5-field scope shape (see
    validate_scope_shape), so compute_scope_ranges/scopeRanges.js/
    StructureTree.jsx can consume an AI-detected page with zero changes:
    they only ever look at stack/scope_key/scope_chain, never at how a page
    was detected.

    Each event in `events` (already resolved/grounded by ai_reference_index.py,
    in document order) is a dict:
      heading_text: str (already sanitized)
      depth: Optional[int] — target stack depth (0-indexed) to truncate to
        before pushing, resolved from parent_ref (falling back to level) —
        NEVER a family/rank lookup (see mint_key_for_heading's docstring for
        why that distinction is load-bearing). None means "no resolved
        parent at all" -> treated as a child of whatever's currently open.
      end_page: Optional[int] — page on which the AI asserts this container
        closes; still_open_at_chunk_end headings simply omit this.
      fingerprint, snippet, truncated, heading_confidence,
      heading_confidence_reasons, own_label_override, detection_model,
      start_line_hint: passed through to the candidate row.
    """
    scope = seed_scope or {
        "stack": [], "boundary_ordinal": 0, "label_occurrence_counts": {},
        "last_bare_integer": None, "last_letter_ordinal": None,
    }
    nested_stack: List[Dict[str, Any]] = [dict(s) for s in scope.get("stack", [])]
    boundary_ordinal = scope.get("boundary_ordinal", 0)
    label_occurrence_counts: Dict[str, int] = dict(scope.get("label_occurrence_counts", {}))
    last_bare_integer = scope.get("last_bare_integer")
    last_letter_ordinal = scope.get("last_letter_ordinal")

    candidates: List[Dict[str, Any]] = []
    occurrence_counts: Dict[str, int] = {}
    # Everything superseded WITHIN this page's own processing — either a
    # same-page sibling overwritten before this page is ever persisted, or a
    # container carried in from an earlier page that gets replaced partway
    # through this one — that compute_scope_ranges' end-of-page-snapshot diff
    # can otherwise never see at all (see compute_scope_ranges' own docstring
    # and _compute_scope_ranges_uncached's consumption of this field for why).
    closed_this_page: List[Dict[str, Any]] = []

    for ev in events:
        text = ev["heading_text"]
        family, key, label, num_segments, boundary_ordinal = mint_key_for_heading(
            text, boundary_ordinal, label_occurrence_counts,
            family_hint=ev.get("family_hint"), number_hint=ev.get("number_hint"),
        )

        depth = ev.get("depth")
        if depth is None:
            depth = len(nested_stack)
        depth = max(0, min(depth, len(nested_stack)))

        # Repeat guard: a running header re-detected on a later page,
        # resolving to the SAME depth+key as what's already open there, is
        # a no-op (not a truncate+push) — without this, a running
        # "ANNEXURE A-3" header re-seen on a later page would truncate the
        # stack to `depth` and close every genuine descendant range
        # mid-section.
        if depth < len(nested_stack) and nested_stack[depth]["key"] == key:
            continue

        # Record every entry this truncate is about to drop — nested_stack[depth:],
        # not just the one at `depth` — each with its OWN scope_key computed
        # against the pre-truncation stack (a multi-level pop removes entries
        # whose ancestor chains genuinely differ from one another).
        for d, removed in enumerate(nested_stack[depth:], start=depth):
            closed_this_page.append({
                "family": removed["family"], "key": removed["key"], "label": removed["label"],
                "depth": d, "scope_key": "/".join(s["key"] for s in nested_stack[:d]),
                "start_page": page_num, "end_page": page_num,
            })

        nested_stack = nested_stack[:depth]

        occ = occurrence_counts.get(key, 0)
        occurrence_counts[key] = occ + 1

        scope_key = "/".join(s["key"] for s in nested_stack)
        scope_label = " › ".join(s["label"] for s in nested_stack)

        # Must match frontend/src/components/StructureTree.jsx's
        # OWN_LABEL_FAMILIES exactly — those are the synthetic-key families
        # (no real document vocabulary) whose generic "Capitalize family +
        # uppercase number" formatting would be wrong ("Subdoc 1" is a
        # meaningless label; the real heading text is what must show).
        # Confirmed via a real run: without "subdoc" here, an AI-minted
        # keyword-less heading persisted with own_label=None, and the
        # frontend fell back to rendering the bare key fragment ("1") for a
        # `subdoc:1` node instead of its real heading text.
        own_label = ev.get("own_label_override")
        if own_label is None and family in ("numheading", "letterheading", "subdoc"):
            own_label = label

        candidates.append({
            "key": key,
            "occurrence_index": occ,
            "snippet": (ev.get("snippet") or "")[:400],
            "truncated": bool(ev.get("truncated")) and has_next_page,
            "continues_on_page": None,
            "scope_key": scope_key,
            "scope_label": scope_label,
            "scope_chain": [dict(s) for s in nested_stack],
            "collapsed_key": collapse_key(key),
            "fingerprint": ev.get("fingerprint", ""),
            "heading_confidence": ev.get("heading_confidence", "high"),
            "heading_confidence_reasons": ev.get("heading_confidence_reasons") or [],
            "own_label": own_label,
            "detection_source": "ai",
            "detection_model": ev.get("detection_model"),
            "start_line_hint": ev.get("start_line_hint"),
        })

        stack_entry: Dict[str, Any] = {"family": family, "key": key, "label": label, "source": "ai"}
        if num_segments is not None:
            stack_entry["num_segments"] = num_segments
        if ev.get("end_page") is not None:
            stack_entry["end_page"] = ev["end_page"]
        nested_stack.append(stack_entry)

        if family == "numheading" and num_segments and len(num_segments) == 1:
            try:
                last_bare_integer = int(num_segments[0])
            except (TypeError, ValueError):
                pass
        if family == "letterheading":
            last_letter_ordinal = ord(key.rsplit(":", 1)[-1].upper()) - ord("A") + 1

    # Close, now that this page's own events have had a chance to use it as
    # ancestor context, any entry whose AI-asserted end_page is THIS page —
    # so it does not persist into page_num + 1's seed. Cascading truncation
    # from the SHALLOWEST matching depth, not a blanket value-filter: a
    # value-filter would remove a matching parent while leaving a
    # non-matching child in place, and since list-comprehension filtering
    # compacts indices, that child's position — and therefore its effective
    # depth — would silently shift. A child is by definition nested inside a
    # closing parent and must close with it regardless of its own end_page.
    close_from = next((d for d, s in enumerate(nested_stack) if s.get("end_page") == page_num), None)
    if close_from is not None:
        for d, removed in enumerate(nested_stack[close_from:], start=close_from):
            closed_this_page.append({
                "family": removed["family"], "key": removed["key"], "label": removed["label"],
                "depth": d, "scope_key": "/".join(s["key"] for s in nested_stack[:d]),
                "start_page": page_num, "end_page": page_num,
            })
        nested_stack = nested_stack[:close_from]

    new_scope = {
        "stack": nested_stack, "boundary_ordinal": boundary_ordinal,
        "label_occurrence_counts": label_occurrence_counts,
        "last_bare_integer": last_bare_integer, "last_letter_ordinal": last_letter_ordinal,
        "closed_this_page": closed_this_page,
    }
    return candidates, new_scope


def _persist_page_index(
    job_id: str, page_num: int, page_text: str,
    candidates: List[Dict[str, Any]], new_scope: Dict[str, Any],
    provisional: bool, detection_source: str = "rule", detection_model: Optional[str] = None,
) -> int:
    """Everything the live/rule path and the AI path share once each has its
    own (candidates, new_scope) pair — version bump, anchor_id, the stamped-
    page row, and the upsert + end_of_page_scope write. Split out of what
    used to be _process_single_page's own body (behavior-preserving refactor
    — the rule-based call site below is unchanged) specifically so
    ai_reference_index.py's AI-sourced pages persist through the exact same
    path, never a parallel one that could drift out of sync."""
    version = database.bump_reference_index_version(job_id)
    rows = []
    for c in candidates:
        row = dict(c)
        row["anchor_id"] = make_anchor_id(row["key"], page_num, row["occurrence_index"])
        if row["truncated"]:
            row["continues_on_page"] = page_num + 1
        row["scope_provisional"] = provisional
        row.setdefault("detection_source", detection_source)
        row.setdefault("detection_model", detection_model)
        rows.append(row)

    stamped = detect_stamped_page_number(page_text)
    if stamped is not None and stamped != page_num:
        rows.append({
            "key": f"page:{stamped}",
            "occurrence_index": 0,
            "anchor_id": f"page-block-{page_num}",
            "snippet": (page_text or "")[:300],
            "truncated": False,
            "continues_on_page": None,
            "scope_key": None,
            "scope_label": f"Printed page {stamped} (found on physical page {page_num})",
            "scope_chain": [],
            "collapsed_key": f"page:{stamped}",
            "fingerprint": "",
            "scope_provisional": provisional,
            "heading_confidence": "high",
            "heading_confidence_reasons": [],
            "own_label": None,
            "detection_source": "rule",
            "detection_model": None,
        })

    database.upsert_reference_targets_for_page(job_id, page_num, rows, version)
    database.set_end_of_page_scope(job_id, page_num, new_scope)
    return version


async def _process_single_page(
    job_id: str, page_num: int, page_text: str, total_pages: int, provisional: bool, raw_text: Optional[str] = None,
):
    seed_scope = database.get_end_of_page_scope(job_id, page_num - 1) if page_num > 1 else None
    candidates, new_scope = build_targets_for_page(
        page_text, seed_scope, has_next_page=page_num < total_pages, raw_text=raw_text,
    )
    _persist_page_index(job_id, page_num, page_text, candidates, new_scope, provisional)


# ─── Backfill / reindex for an already-OCR'd job ───────────────────────────────
# Covers jobs whose pages were OCR'd before this feature existed (or via any
# path that predates the incremental task_queue.py hooks) — such a job has
# zero rows in ocr_reference_targets even though its page text is already
# sitting in ocr_pages. Also the tool for rebuilding a job's index from
# scratch after an algorithm change.

_active_reindex_jobs: set = set()


def is_reindex_active(job_id: str) -> bool:
    return job_id in _active_reindex_jobs


# ─── Part B: container scope ranges (page spans) ───────────────────────────

_scope_ranges_cache: Dict[str, Tuple[int, List[Dict[str, Any]]]] = {}


def compute_scope_ranges(job_id: str, total_pages: int) -> List[Dict[str, Any]]:
    """Derives, for every container that was ever the active structural
    ancestor on some page, the contiguous physical-page range(s) it covers —
    diffed page-to-page from each page's already-persisted end_of_page_scope
    stack (get_end_of_page_scope_bulk), not a new incrementally-maintained
    table (matches the existing "recompute a full pass" philosophy already
    used by reindex_job, and avoids a harder concurrency problem an
    incrementally-updated range table would have under the frontier+cascade
    out-of-order/retry design).

    A node's start_page/end_page means *the contiguous physical pages for
    which that node is the active structural ancestor* — not "the pages its
    own heading text happens to appear on." An Annexure's range covers
    everything nested under it, including pages where no target of its own
    was ever detected (pure prose). Two disjoint occurrences of the same key
    (it closes, then a later page re-opens it) become two separate range
    entries, never merged.

    Returns a flat list of
    {family, key, label, depth, scope_key, start_page, end_page, synthetic?}.
    depth is the 0-indexed stack position (0 = a rank-0 sub-document) — used
    by the frontend to prefer the *deepest* range covering a given page.
    `synthetic: True` entries (family/key "unclassified") gap-fill any
    processed page with no active container at all (front matter, or
    between two containers) — a third, distinct category from a
    low-confidence detection: "we detected nothing here," not "we detected
    something, unsure if it's real." Cached per job, invalidated whenever
    reference_index_version changes.
    """
    current_version = database.get_reference_index_version(job_id)
    cached = _scope_ranges_cache.get(job_id)
    if cached and cached[0] == current_version:
        return cached[1]

    ranges = _compute_scope_ranges_uncached(job_id, total_pages)
    _scope_ranges_cache[job_id] = (current_version, ranges)
    return ranges


def _compute_scope_ranges_uncached(job_id: str, total_pages: int) -> List[Dict[str, Any]]:
    scopes = database.get_end_of_page_scope_bulk(job_id)
    ranges: List[Dict[str, Any]] = []
    open_by_depth: Dict[Any, Dict[str, Any]] = {}
    last_processed_page = 0

    def close(slot_key, end_page):
        entry = open_by_depth.pop(slot_key)
        entry["end_page"] = end_page
        ranges.append(entry)

    for page_num in range(1, total_pages + 1):
        scope = scopes.get(page_num)
        if scope is None:
            # Not yet processed — invisible to range computation, so a
            # temporary out-of-order/retry gap never fragments an otherwise-
            # continuous range. Neither closes nor extends anything.
            continue
        stack = scope.get("stack") or []

        # Same-page closures the plain end-of-page snapshot can never show on
        # its own — a container superseded partway through THIS page (closes
        # here, not on the previous page) or a container that was pushed and
        # removed entirely within this one page (never appears in any
        # persisted snapshot at all). Processed IN ORDER, before the normal
        # cross-page diff below: when several same-depth siblings share one
        # page, the first record matches whatever's tracked from an earlier
        # page (if any) and closes it properly; each later record for the
        # same depth then correctly finds nothing left to match and falls
        # through to the degenerate single-page-range case.
        for rec in scope.get("closed_this_page") or []:
            depth = rec["depth"]
            tracked = open_by_depth.get(depth)
            if tracked is not None and tracked["key"] == rec["key"]:
                close(depth, page_num)
            else:
                ranges.append({
                    "family": rec["family"], "key": rec["key"], "label": rec["label"],
                    "depth": depth, "scope_key": rec.get("scope_key", ""),
                    "start_page": rec["start_page"], "end_page": rec["end_page"],
                })

        # Close any real depth slot whose key no longer matches (or no
        # longer exists) at that depth — deepest first, though order has no
        # correctness effect here (each close is independent). This is the
        # fallback for pages with no closed_this_page records at all (the
        # rule-based path never produces any yet) — by the time this runs on
        # an AI-produced page, the loop above has already resolved every real
        # mismatch, so it's a safe no-op there.
        for depth in sorted((d for d in open_by_depth if d != "unclassified"), reverse=True):
            current = stack[depth] if depth < len(stack) else None
            if current is None or current["key"] != open_by_depth[depth]["key"]:
                close(depth, last_processed_page)

        # Open any real depth slot not already open.
        for depth, entry in enumerate(stack):
            if depth not in open_by_depth:
                open_by_depth[depth] = {
                    "family": entry["family"], "key": entry["key"], "label": entry["label"],
                    "depth": depth, "scope_key": "/".join(s["key"] for s in stack[:depth]),
                    "start_page": page_num,
                }

        # Gap-filling: a processed page with an empty stack gets a synthetic
        # "unclassified" span; closes the moment a real container reopens.
        if not stack:
            if "unclassified" not in open_by_depth:
                open_by_depth["unclassified"] = {
                    "family": "unclassified", "key": "unclassified", "label": "Main Document",
                    "depth": 0, "scope_key": "", "synthetic": True, "start_page": page_num,
                }
        elif "unclassified" in open_by_depth:
            close("unclassified", last_processed_page)

        last_processed_page = page_num

    for slot_key in list(open_by_depth.keys()):
        close(slot_key, last_processed_page)

    return ranges


async def reindex_job(job_id: str, force_mode: Optional[str] = None, start_page: Optional[int] = None):
    """Dispatches to the rule-based or AI-based full reindex depending on
    the job's frozen engine_params.ai_indexing_enabled (or force_mode, for
    a manual ?mode= override on the "Rebuild Cross-References" endpoint).

    `start_page`, when given, is only meaningful for the AI path (a targeted
    re-run starting from one retried page, per ai_reference_index.py) — the
    rule-based path ignores it and always rebuilds from page 1, since it's
    cheap enough that a partial-rebuild optimization isn't worth the extra
    code path."""
    if job_id in _active_reindex_jobs:
        return
    _active_reindex_jobs.add(job_id)
    try:
        job = database.get_job(job_id, include_pages=False)
        if not job:
            return
        engine_params = job.get("engine_params") or {}
        mode = force_mode or ("ai" if engine_params.get("ai_indexing_enabled") else "rule")
        if mode == "ai":
            import ai_reference_index
            await ai_reference_index.reindex_job_with_ai(job_id, start_page=start_page)
        else:
            await _reindex_job_rule_based(job_id)
    finally:
        _active_reindex_jobs.discard(job_id)


async def _reindex_job_rule_based(job_id: str):
    """Rebuilds the entire reference index for one job from its already-stored
    page text, processing pages strictly in physical order so every call lands
    on the simple in-order path (the out-of-order provisional/cascade
    machinery in update_reference_index_for_page is for genuinely concurrent
    live completion and is never exercised here by construction)."""
    job = database.get_job(job_id, include_pages=False)
    if not job:
        return
    total_pages = job.get("total_pages") or 0
    database.set_reference_index_frontier(job_id, 0)
    for page in database.get_pages(job_id):
        await update_reference_index_for_page(job_id, page["page_num"], page.get("text") or "", total_pages)
