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
import hashlib
import html
from html.parser import HTMLParser
import itertools
import json
import re
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

import mistune

import database
import layout_format

# ─── Vocabulary & pattern definitions ──────────────────────────────────────────

_KEYWORD_ALTS = r"clause|section|sec\.?|article|art\.?|para(?:graph)?|annexure|schedule|appendix|page|ground"

CORE_RE = re.compile(
    rf"^\s*(?P<keyword>{_KEYWORD_ALTS})\b\.?\s+(?:no\.?\s*)?(?P<prefix>[A-Z]-?)?(?P<number>[ivxlcdm]+|\d+(?:\.\d+)*)\b",
    re.IGNORECASE,
)

MENTION_START_RE = re.compile(rf"\b(?:{_KEYWORD_ALTS})\b", re.IGNORECASE)
MENTION_RE = re.compile(
    rf"\b(?P<keyword>{_KEYWORD_ALTS})\b\.?[\s-]*(?:no\.?\s*)?(?P<prefix>[A-Za-z]-?)?(?P<number>[ivxlcdmIVXLCDM]+|\d+(?:\.\d+)*)\b",
    re.IGNORECASE,
)

# ─── Document Reference (docref) & Defined Terms (term) patterns ─────────────

_DOCREF_LABEL_ALTS = (
    r"technical specification|specification|tender enquiry|enquiry|tender|"
    r"bidding document|bid(?:ding)?|proposal|offer|quotation|purchase order|"
    r"work order|letter of award|letter|memo(?:randum)?|file|invoice|agreement|"
    r"contract|claim|arbitration case|case|award|order|misc(?:ellaneous)?"
)

_DOCREF_BARE_NUMERIC_LABELS = {"claim", "case", "arbitration case"}

DOCREF_START_RE = re.compile(rf"\b(?:{_DOCREF_LABEL_ALTS})\b", re.IGNORECASE)
DOCREF_MENTION_RE = re.compile(
    rf"\b(?P<label>{_DOCREF_LABEL_ALTS})\b\.?\s*Nos?\.?\s*:?\s*"
    r"(?P<code>[A-Za-z0-9](?:[A-Za-z0-9()\-]|/(?:\s*)?){0,49}(?:\s+\([A-Za-z0-9/()\-]+\))?)",
    re.IGNORECASE,
)

PAREN_ABBR_RE = re.compile(
    r"(?P<full>[A-Z][A-Za-z&/\-]*(?:\s+[A-Za-z&/\-]+){0,6})\s*\(\s*(?P<abbr>[A-Za-z][A-Za-z&/.\-]{1,9})\s*(?:,\s*for\s+short)?\s*\)"
)

HEREINAFTER_RE = re.compile(
    r'here\s*(?:in\s*)?after\s+(?:referred\s+to\s+as|called)\s+(?:the\s+)?["\'"“”‘’]?(?P<term>[A-Za-z][A-Za-z\s&/\-]{1,45}?)["\'"“”‘’]?\s*(?:\b(?:or|and)\b\s+["\'"“”‘’]?[A-Za-z][A-Za-z\s&/\-]{0,45}?["\'"“”‘’]?\s*)?(?:[.,;)]|which\s+expression)',
    re.IGNORECASE,
)

_TERM_STOP_WORDS = {"of", "the", "and", "for", "to", "in"}

# Front-matter INDEX/TOC pages describe later sections by their full name
# plus abbreviation ("Section-V: General Conditions of Contract (GCC)") or
# cite doc-ref-shaped numbers ("TENDER SPECIFICATION No. ..."). Unlike the
# AI heading path's own TOC suppression (is_inside_toc in
# ai_reference_index.py, scoped the same way: page <= 2), build_docref_index_for_job
# and build_term_index_for_job have no such awareness -- they walk raw text
# via _walk_blocks_from_tokens, which (unlike _flatten_blocks, used for
# heading grounding) DOES see inside a <table> block_html blob, so an
# INDEX's own row text mints a spurious baseline term/docref target right
# there instead of at the term's real, later first-use. Confirmed real:
# "Section-V: General Conditions of Contract (GCC)" in a tender-document's
# own page-2 INDEX table minted term:gcc from the INDEX row itself. Checked
# once per page (not per block) since a document could split an "INDEX"/
# "TABLE OF CONTENTS" heading and its row table across separate blocks.
_TOC_BLOCK_RE = re.compile(r"\b(?:INDEX|TABLE OF CONTENTS)\b", re.IGNORECASE)
_TOC_PAGE_MAX = 2


def _page_is_toc_like(page_num: int, page_text: str) -> bool:
    return page_num <= _TOC_PAGE_MAX and bool(_TOC_BLOCK_RE.search(page_text))


def normalize_docref_code(code: str) -> str:
    """Uppercase and strip all whitespace and punctuation from docref code."""
    clean = code.strip().rstrip(".,;:-/ ")
    return re.sub(r"[^A-Za-z0-9]", "", clean).upper()


def docref_key(code: str) -> str:
    return f"docref:{normalize_docref_code(code).lower()}"


def _is_plausible_docref_code(code: str, label: str) -> bool:
    clean_code = code.strip().rstrip(".,;:-/ ")
    if not clean_code:
        return False
    if "<|" in clean_code or "|>" in clean_code or "det" in clean_code.lower():
        return False
    # Must contain at least one digit
    if not any(c.isdigit() for c in clean_code):
        return False
    norm = normalize_docref_code(clean_code)
    if not norm:
        return False
    norm_label = label.lower().strip()
    if norm_label in {"page", "clause", "section", "article"}:
        return False
    # Reject bare short numerals unless label in _DOCREF_BARE_NUMERIC_LABELS
    if norm.isdigit():
        if norm_label in _DOCREF_BARE_NUMERIC_LABELS:
            return 1 <= len(norm) <= 4
        return False
    return True


def _is_acronym_of(abbr: str, phrase: str) -> bool:
    """Accept only when the abbreviation's letters match the initials of the trailing
    significant words of the preceding phrase (ignoring of/the/and/for/to/in)."""
    abbr_clean = re.sub(r"[^A-Za-z]", "", abbr).upper()
    if len(abbr_clean) < 2:
        return False
    words = re.findall(r"[A-Za-z]+", phrase)
    if not words:
        return False

    def matches(w_idx: int, a_idx: int) -> bool:
        if a_idx == len(abbr_clean):
            return w_idx == len(words)
        if w_idx == len(words):
            return False
        w = words[w_idx]
        w_init = w[0].upper()
        if w_init == abbr_clean[a_idx]:
            if matches(w_idx + 1, a_idx + 1):
                return True
        if w.lower() in _TERM_STOP_WORDS:
            if matches(w_idx + 1, a_idx):
                return True
        return False

    for start_idx in range(len(words)):
        if words[start_idx][0].upper() == abbr_clean[0]:
            if matches(start_idx, 0):
                return True
    return False


def normalize_term_key(term: str) -> str:
    clean = re.sub(r"^(?:the\s+)", "", term.strip(), flags=re.IGNORECASE)
    clean = re.sub(r"\s+", " ", clean).strip().lower()
    return clean


# Known OCR glyph confusables, letter -> its digit look-alike. Applied only
# to letters (digits already stand for themselves), so translating a code
# through this table yields a "confusable-canonical form": two codes that
# differ only by these specific glyph swaps collapse to the identical string.
# This exists because some confusions are SYSTEMATIC, not rare -- real case:
# "E1070151" (23 occurrences) vs "EI070151" (14 occurrences) across a
# 955-page job. The old frequency-asymmetry-only approach (below) requires
# one spelling to be ~5x rarer than the other to fold, which can never fire
# when an OCR engine makes the same substitution consistently and both
# spellings end up common. Grouping by this canonical form first, at ANY
# frequency, fixes that; the fuzzy fallback remains for one-off typos this
# exact-after-translation table doesn't cover.
_CONFUSABLE_LETTER_TO_DIGIT = str.maketrans({
    "I": "1", "L": "1",
    "O": "0",
    "S": "5",
    "B": "8",
    "Z": "2",
    "G": "6",
})


def _canonical_confusable_form(code: str) -> str:
    return code.translate(_CONFUSABLE_LETTER_TO_DIGIT)


def reconcile_docref_variants(
    raw_codes: List[str],
) -> Tuple[Dict[str, str], Dict[str, str]]:
    """
    Given raw docref codes across the job, computes frequency of each normalized code
    and folds variants into a dominant key in two stages:

    1. Confusable-canonical grouping, at ANY frequency: codes that become
       byte-identical after mapping known OCR-confusable letters to their
       digit look-alike (I/L->1, O->0, S->5, B->8, Z->2, G->6) are grouped
       together regardless of which spelling is more common -- a systematic
       glyph confusion produces both spellings in bulk, so a frequency
       threshold can never safely apply here.
    2. Residual fuzzy folding, 3x frequency threshold: for codes left alone
       by stage 1, fold a rare variant into a code that is both >=3x more
       frequent and near-identical (SequenceMatcher ratio >= 0.80, length
       within 1) -- this catches one-off typos the confusable map doesn't
       express (e.g. "XENPROJECTS" (17) vs "GENPROJECTS" (4), not a glyph
       confusion but a plausible single-character OCR slip at that
       frequency gap). 3x (not the original 5x) because stage 1 now absorbs
       the high-frequency systematic cases, leaving only rarer one-offs for
       this stage to catch.

    Returns (dominant_map, resolution_map) where resolution_map maps normalized code
    to 'exact' or 'approximate'.
    """
    from collections import Counter
    norm_codes = [normalize_docref_code(c) for c in raw_codes if normalize_docref_code(c)]
    counts = Counter(norm_codes)
    dominant_map: Dict[str, str] = {}
    resolution_map: Dict[str, str] = {}

    # Stage 1: confusable-canonical grouping, independent of frequency.
    by_confusable: Dict[str, List[str]] = {}
    for code in counts:
        by_confusable.setdefault(_canonical_confusable_form(code), []).append(code)

    remaining = dict(counts)
    for variants in by_confusable.values():
        if len(variants) < 2:
            continue
        dominant = max(variants, key=lambda c: (counts[c], -len(c)))
        for v in variants:
            dominant_map[v] = dominant
            resolution_map[v] = "exact" if v == dominant else "approximate"
            remaining.pop(v, None)

    # Stage 2: residual fuzzy folding for codes stage 1 left untouched.
    sorted_by_freq = sorted(remaining.items(), key=lambda x: (x[1], len(x[0])), reverse=True)

    for v_code, v_count in remaining.items():
        best_dominant = None
        best_ratio = 0.0
        for d_code, d_count in sorted_by_freq:
            if d_code == v_code:
                continue
            if d_count < 3 * v_count:
                continue
            if abs(len(d_code) - len(v_code)) > 1:
                continue
            ratio = difflib.SequenceMatcher(None, d_code, v_code).ratio()
            if ratio >= 0.80 and ratio > best_ratio:
                best_ratio = ratio
                best_dominant = d_code

        if best_dominant:
            dominant_map[v_code] = best_dominant
            resolution_map[v_code] = "approximate"
        else:
            dominant_map[v_code] = v_code
            resolution_map[v_code] = "exact"

    return dominant_map, resolution_map


PAGE_FOOTER_RE = re.compile(r"^\s*of\s+\d+\b", re.IGNORECASE)
EXTERNAL_LAW_SIGNAL_RE = re.compile(
    r"of\s+the\s+(?:[A-Za-z][\w\s]{0,30}?\s*)?\b(?:Act|Code|Constitution|Rules|Regulations)\b|\bCPC\b|\bIPC\b|\bCrPC\b|\b(?:19|20)\d{2}\b",
    re.IGNORECASE,
)
CASE_CITATION_RE = re.compile(
    r"\b(?:AIR|SCC|SCR|ILR|SCALE)\b|\(\d{4}\)\s*\d+|\b\d{4}\s*\(?\d*\)?\s*(?:SCC|SCR|AIR|SCALE)\b|\bSupp\.?\b|\bv\.?s?\.?\s+[A-Z]",
    re.IGNORECASE,
)
MENTION_LOOKAHEAD_CHARS = 60
EXTERNAL_LAW_FALLBACK_GUARD_FAMILIES = {"clause", "section", "article", "schedule", "appendix"}

FAMILY_FALLBACKS: Dict[str, List[str]] = {
    "clause": ["numheading"],
    "section": ["numheading"],
    "article": ["numheading"],
    "schedule": ["numheading"],
    "appendix": ["numheading"],
    "para": ["point", "numheading"],
}

RANGE_CONTINUATION_RE = re.compile(
    r"^\s*(to|and|&|,\s*(?:and|&)?)\s*([A-Za-z]-?)?(\d+(?:\.\d+)*)\b",
    re.IGNORECASE,
)
RANGE_CONTINUATION_YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")
MAX_RANGE_EXPANSION = 25

GROUND_DIGIT_RE = re.compile(r"^\s*\(?(?P<number>\d+(?:\.\d+)*)(?:\)\.?|\.)\s+[A-Z]")
GROUND_ROMAN_RE = re.compile(r"^\s*\(?(?P<number>[IVXLCDMivxlcdm]+)(?:\)\.?|\.)\s+[A-Z]")


def _is_date_shaped_number(number_raw: str) -> bool:
    """A GROUND_DIGIT_RE match whose segments look like a date ("22.10.2008",
    "19.02.2010") rather than a genuine enumeration — confirmed real bug: a
    bare date immediately followed by a capitalized word ("22.10.2008.
    Moreover, respondent...") is common in ordinary prose and matches this
    regex's \\d+(?:\\.\\d+)* shape exactly the same way a real numbered item
    does. A 4-digit segment is virtually never a genuine ground/point/clause
    number in any real numbering convention (which almost always uses 1-2
    digit segments) — every GROUND_DIGIT_RE call site must reject a match
    shaped this way."""
    return any(len(seg) == 4 for seg in number_raw.split("."))
# A SINGLE enclosed letter, any of A-Z (not just roman-valid ones) —
# GROUND_ROMAN_RE already only matches genuinely multi-character roman
# numerals unambiguously (II, III, IV, VII, XI, XXII, ...); the only real
# ambiguity is a single letter that's ALSO a valid roman numeral (I, V, X,
# L, C, D, M) appearing mid-sequence in an otherwise plain alphabetical
# list ("(A)(B)(C)(D)(E)" — confirmed real case: "(C)"/"(D)" were
# misparsed as roman 100/500 instead of the 3rd/4th list item). See
# _resolve_ground_or_point_match, which decides between the two using the
# sequence's own running state, never guessing from one item alone.
_GROUND_SINGLE_LETTER_RE = re.compile(r"^\s*\(?(?P<letter>[A-Za-z])(?:\)\.?|\.)\s+[A-Z]")
_ROMAN_VALID_LETTERS = set("IVXLCDM")

# A LOWERCASE roman-numeral sub-list, closing paren only ("i)", "ii)",
# "iii)", ...) — a distinct shape from GROUND_ROMAN_RE's uppercase,
# either-paren-or-period enumeration, confirmed real case: sub-items i)
# through vi) nested directly under a numbered clause ("2.1 The contract
# shall be performed... in the following documents... i) HVPNL bidding
# document... ii) HVPN Technical Specification..."), invisible entirely
# under the previous regex set. See the `subpoint` family/gate below.
_SUBPOINT_ROMAN_RE = re.compile(r"^\s*(?P<numeral>[ivxlcdm]+)\)\s+[A-Z]")
_SUBLIST_MARKER_RE = re.compile(r"^\s*\(?(?P<marker>[a-zA-Z]|[ivxlcdmIVXLCDM]+)(?:\)\.?|\.)\s+(?P<text>\S.*)$")


def _resolve_ground_or_point_match(
    text: str, last_ground_letter_ordinal: Optional[int],
) -> Optional[Tuple[str, int, bool]]:
    """Resolves a ground/point enumeration match against `text`, returning
    (number_raw, match_end, is_sequential_letter) or None. Tries, in
    order: a plain decimal number; a single enclosed letter, resolved as
    the next sequential letter (continuing an already-started run, or
    starting fresh when the letter isn't even a valid roman numeral so
    there's no ambiguity to resolve) rather than a roman numeral whenever
    the running state says so; otherwise a genuine (always
    unambiguous, since it's never a single character) roman numeral."""
    dm = GROUND_DIGIT_RE.match(text)
    if dm and not _is_date_shaped_number(dm.group("number")):
        return dm.group("number"), dm.end(), False
    lm = _GROUND_SINGLE_LETTER_RE.match(text)
    if lm:
        letter = lm.group("letter")
        ordinal = ord(letter.upper()) - ord("A") + 1
        continues_run = (
            last_ground_letter_ordinal is not None and ordinal == last_ground_letter_ordinal + 1
        )
        if continues_run or letter.upper() not in _ROMAN_VALID_LETTERS:
            # The letter itself, not its ordinal — "point:c" reads
            # naturally and, just as importantly, never collides with a
            # genuine decimal ground/point "1", "2", "3" in the same or an
            # overlapping scope the way a numeric ordinal string would.
            return letter, lm.end(), True
    rm = GROUND_ROMAN_RE.match(text)
    if rm:
        number_raw = rm.group("number")
        if _is_roman(number_raw):
            return number_raw, rm.end(), False
    return None

_GROUND_SCOPE_HEADINGS = re.compile(r"\b(GROUNDS?\s+OF\s+APPEAL|GROUNDS|PRAYER)\b", re.IGNORECASE)

# _pleading_scope_active's nearest-subdoc check needs the OPPOSITE shape
# from _GROUND_SCOPE_HEADINGS: pleading-type subdoc boundaries are
# open-vocabulary (an affidavit, "RESPECTFULLY SHOWETH", "FACTS", a
# judgment, a bare case caption — confirmed by this project's own test
# suite, which uses exactly these as canonical examples), so a fixed
# allow-list keyword match can't enumerate them and was tried first, then
# rejected (broke 13 existing tests whose generic subdoc labels, by
# design, match none of a small keyword set). Instead, default to active
# (preserving all existing behavior) and exclude only the specific,
# identifiable non-pleading document types actually responsible for a
# confirmed bug: an embedded commercial contract/correspondence annexure,
# where numbered paragraphs are contract clauses or distribution-list
# entries, never pleading points.
#
# Second confirmed case, same root cause, different document family: a
# tender/commercial specification's own top-level "Section-III: Instructions
# to Bidders" / "Section-IV: Scope of Work" style parts. Without this,
# EVERY numbered heading under such a part defaults to pleading-point
# treatment (a flat point/ground leaf, never pushed as a real container —
# see FAMILIES' rank=None comment), which silently breaks decimal
# sub-numbering (a document's own "1" -> "1.1"/"1.2" ends up flattened to
# three siblings instead of "1.1"/"1.2" nesting under "1") for any document
# whose numbered clauses sit directly under a "Section N" part instead of
# a recognized non-pleading label. Confirmed safe against both existing
# arbitration-judgment gold fixtures: neither has a subdoc CONTAINER (as
# opposed to plain body-text mentioning a law section, e.g. "under section
# 34 of Arbitration Act", always already inside a leaf point target) whose
# own label starts with "Section".
_NON_PLEADING_SUBDOC_LABELS_RE = re.compile(
    r"\b(CONTRACT\s+AGREEMENT|BANK\s+GUARANTEE|PURCHASE\s+ORDER|WORK\s+ORDER|"
    r"LETTER\s+OF\s+(?:AWARD|INTENT)|TENDER\s+(?:DOCUMENT|SPECIFICATION)|"
    r"SECTION[\s-]+[IVXLCDM0-9]+|DESPATCH\s+OF\s+MATERIAL|TECHNICAL|ENDORSEMENT)\b",
    re.IGNORECASE,
)

# Confirmed via live diagnostic (100-page gold fixture): numbered/lettered
# rows inside a technical spares/price-schedule table ("11. Relays, power
# contactors...", "A. 2000A", "4.10 Isolation valves") or a document INDEX
# listing ("10. Letter of M/s Areva T&D India Ltd. dated 27.12...") were
# being minted as real point/ground/numheading/letterheading targets —
# table/list rows, not document headings. INDEX and PRICE SCHEDULE are
# directly confirmed by that diagnostic; the rest are same-shape extensions
# that cleared the mandatory negative test against both gold fixtures (see
# _immediate_parent_is_listing_container's call sites).
_LISTING_CONTAINER_RE = re.compile(
    r"\b(?:INDEX|SCHEDULE|PRICE(?:S|\s+SCHEDULE)?|SPARES?|SPARE\s+PARTS|"
    r"LIST\s+OF\s+ANNEXURES?|BILL\s+OF\s+QUANTITIES|BOQ)\b", re.IGNORECASE)


_TABULAR_DATA_MAX_WORDS = 20  # a generous backstop against degenerate/garbled OCR noise, NOT a tight disqualifier —
# see _looks_like_tabular_data_shape: verb/modal absence is the real, primary signal.
_CLAUSE_VERB_RE = re.compile(
    r"\b(?:shall|should|must|is|are|was|were|will|would|may|can|could|has|have|had|does|did|needs?)\b",
    re.IGNORECASE,
)
_LEADING_MARKER_RE = re.compile(r"^\s*[\(\[]?[0-9A-Za-z]+[\.\)\]]\s*")


def _looks_like_tabular_data_shape(text: str) -> bool:
    """True when `text` reads as a short catalogue/field-value table cell
    (a part name, a date, a price, a document title) rather than a
    self-contained clause. Confirmed via real gold-fixture text from a
    second, structurally different document: finite-verb/modal absence is
    used as the PRIMARY, sufficient signal — NOT AND-combined with a tight
    word-count cap. An earlier design required BOTH "short" (<=8 words) AND
    verb-free, which broke an existing, confirmed-real case
    ("10. Letter of M/s Areva T&D India Ltd. dated 27.12.2007", 9 words,
    no verb, must stay suppressed under an INDEX parent) — the AND-combined
    design would have let it through just for being one word over the cap.
    Word count is kept only as a generous backstop against truly
    degenerate/garbled OCR noise, not a disqualifier for ordinary
    short-to-medium noun-phrase rows."""
    stripped = (text or "").strip()
    if not stripped:
        return False
    body = _LEADING_MARKER_RE.sub("", stripped)
    words = body.split()
    if len(words) > _TABULAR_DATA_MAX_WORDS:
        return False
    if _CLAUSE_VERB_RE.search(body):
        return False
    return True


def _immediate_parent_is_listing_container(nested_stack: List[Dict[str, Any]], row_text: Optional[str] = None) -> bool:
    """True when the nearest open container's own label marks it as a
    listing/schedule/index, not narrative prose, AND (when `row_text` is
    given) the row itself reads as tabular/field-value data rather than a
    real clause (see _looks_like_tabular_data_shape) — the gate for
    suppressing point/ground/numheading/letterheading rows that are really
    table/list rows (see _LISTING_CONTAINER_RE above). Deliberately checks
    only the single nearest container, not a full ancestor walk like
    _ground_scope_active — a listing table's own immediate parent is what
    matters, not whether some distant ancestor happens to also be named
    "Schedule". The row-shape check matters because a listing-labeled
    parent can genuinely contain BOTH real noise (price-schedule/spares
    data rows) AND real numbered clauses laid out as a table (confirmed on
    a second, structurally different document) — a real clause must
    survive even under a listing-labeled parent.

    `row_text=None` (the default) means "no single row to judge" (e.g. a
    whole-page gate deciding before scanning any individual line) — skips
    the shape check entirely and preserves this function's original,
    label-only behavior exactly. Passing `row_text=""` would otherwise
    silently disable the gate for every caller that always shape-rejects
    on empty input — None, not "", is the deliberate sentinel for
    "shape check not applicable here."""
    if not nested_stack:
        return False
    listing_entry = None
    for s in reversed(nested_stack):
        if _LISTING_CONTAINER_RE.search(s.get("label") or ""):
            listing_entry = s
            break
        if s.get("family") in ("section", "article", "annexure"):
            break
    if listing_entry is None:
        return False
    if row_text is None:
        return True
    return _looks_like_tabular_data_shape(row_text)

EXTERNAL_LAW_A_RE = re.compile(
    r"\b[A-Za-z]+(?:\s+(?:and|of|for|[A-Za-z]+))*\s+(?:Act|Code|Rules|Regulations)(?:,\s*|\s+)(?:19|20)\d{2}\b",
    re.IGNORECASE,
)
EXTERNAL_LAW_B_RE = re.compile(
    r"\bof\s+the\s+(?:[A-Za-z][\w\s]{0,30}?\s*)?\b(?:Act|Code|Constitution|Rules|Regulations)\b",
    re.IGNORECASE,
)
EXTERNAL_LAW_C_RE = re.compile(
    r"\b(?:CPC|IPC|CrPC)\b|(?i:\b(?:C\.P\.C\.|I\.P\.C\.|Cr\.P\.C\.)(?!\w))"
)
# Case-law discussion markers — a block quoting/discussing a judicial precedent
# (e.g. "(2015) 3 SCC 49" / "... (supra)") is never a document-internal clause
# definition, even when it happens to open with "Section N"/"Clause N".
EXTERNAL_LAW_D_RE = re.compile(
    r"\bsupra\b|\(\d{4}\)\s*\d+\s*SCC\b|\bAIR\s+(?:19|20)\d{2}\b|\b\d{4}\s+SCC\s+OnLine\b|\[\d{4}\]\s*\d+\s*SCR\b",
    re.IGNORECASE,
)

# "INDEX" is deliberately NOT in this list — see _WEAK_SUBDOC_TRIGGER_RE
# below, which handles it as a conditional trigger instead.
SUBDOC_TRIGGER_RE = re.compile(
    r"(ANNEXURE|SCHEDULE|APPENDIX|GROUNDS\s+(?:OF|OR)\s+APPEAL|AFFIDAVIT|MEMO\s+OF\s+PARTIES|"
    r"JUDGE?MENT|RESPECTFULLY\s+SHOWETH|IN\s+THE\s+(?:HIGH\s+)?COURT\s+OF|"
    r"IMPUGNED\s+(?:ORDER|JUDGE?MENT))",
    re.IGNORECASE,
)
# "VERSUS"/"VS." deliberately excluded — unlike the triggers above, it never
# indicates a real structural heading in ANY case-captioned document; it's
# always the party-separator inside a case caption (a general fact about the
# word, not something specific to one document). A bare "Versus" line used
# to fire this trigger and wipe out whatever real container was open —
# confirmed real bug: it repeatedly stomped "MEMO OF PARTIES" and a genuine
# case heading on adjacent pages of the same document.

# A bare, otherwise-unheralded "FACTS"/"PRAYER" line — pleading-section
# headings distinct enough (short, colon-optional, nothing else on the
# line) not to need the noise-prefix tolerance SUBDOC_TRIGGER_RE gets;
# unlike "GROUNDS OF APPEAL"/"PRAYER" above, a bare "FACTS" alone isn't in
# SUBDOC_TRIGGER_RE since "facts" also appears constantly in ordinary
# prose ("facts of the case") — this pattern's full-line anchoring is what
# keeps it safe.
_SHORT_PLEADING_HEADING_RE = re.compile(r"^\s*(FACTS|PRAYER)\s*:?\s*$", re.IGNORECASE)

# The case-caption trigger alone ("In the High Court of...") carries no
# distinguishing text of its own — the SAME caption legitimately repeats
# before several different pleadings (an application, an affidavit,
# another application) in one paper-book. When it fires, detect_subdoc_
# trigger looks ahead a few blocks for whichever of these actually
# distinguishes THIS occurrence, and labels the container with that
# instead of the generic caption phrase.
_CASE_CAPTION_TRIGGER_RE = re.compile(r"IN\s+THE\s+(?:HIGH\s+)?COURT\s+OF", re.IGNORECASE)
_CASE_CAPTION_LABEL_HINT_RE = re.compile(
    r"(CIVIL\s+MISC\.?\s*NO\.?\s*[\w./-]*|RESPECTFULLY\s+SHOWETH\s*:?|AFFIDAVIT\s+OF\s+[^\n.]*|"
    r"IN\s+F\.?A\.?O\.?[^\n]*)",
    re.IGNORECASE,
)
_CASE_CAPTION_LOOKAHEAD_BLOCKS = 5
# "INDEX" legitimately means two different things: a bundle's own front
# index (a genuine new-document boundary) vs. a table of contents living
# inside an already-open Annexure/Schedule/Appendix (that container's own
# internal page, not a new document). Kept as a separate, weaker trigger —
# see detect_subdoc_trigger, which only lets it fire when nothing is
# currently open at rank 0.
_WEAK_SUBDOC_TRIGGER_RE = re.compile(r"\bINDEX\b", re.IGNORECASE)
_SUBDOC_NOISE_PREFIX = 15  # chars — how much leading OCR noise a trigger can tolerate

# A single real qualifying word (4+ alphabetic chars, nothing else)
# immediately preceding a bare trigger keyword — "Price " before
# "Schedule", "Work " before "Schedule:" — signals the trigger names a
# MINOR sub-label of something else ("Price Schedule", a supply order's
# own internal heading), not a top-level document division, even though
# it's well within the noise-prefix tolerance above. General,
# document-agnostic distinction, not tied to "Schedule" specifically:
# real OCR noise (`ll`, `Q IN`, `4x4L`, `3S Dated ...` — confirmed real
# samples _SUBDOC_NOISE_PREFIX exists to tolerate) never matches this
# shape (too short, contains digits, or is more than one word), so the
# existing noise tolerance for a genuine heading is unaffected.
_QUALIFYING_WORD_BEFORE_TRIGGER_RE = re.compile(r"^\s*[A-Za-z]{4,}\s*$")

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
    "point":    {"rank": None, "numbered": True, "aliases": ["point"]},
    "subpoint": {"rank": None, "numbered": True, "aliases": ["subpoint"]},
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


# General, document-agnostic phrasing that introduces a QUOTED/REPRODUCED
# clause from elsewhere — never tied to a specific clause number or this
# document's own wording. Confirmed real bug: "...as per clause 9.0 ITB,
# Vol-II, Contract, which reads as under:" immediately precedes "9.0
# Local/Site conditions" reproduced verbatim inside a Ground of Appeal's
# own argument — the regex has no way to know this isn't a real new
# heading of THIS document without a signal like this one.
_REPRODUCED_TEXT_INTRO_RE = re.compile(
    r"(which\s+reads?\s+as\s+(?:under|follows)|reads?\s+as\s+(?:under|follows)|"
    r"as\s+per\s+clause|which\s+read[s]?\s+as|reproduced\s+below|extracted\s+below)"
    r"\s*:?\s*$",
    re.IGNORECASE,
)


def _preceded_by_reproduced_text_intro(preceding_text: Optional[str]) -> bool:
    """Soft signal only — never a hard exclude (see this module's design
    principles): the caller must still persist the match as its own
    low-confidence candidate, just prevented from becoming a stack-
    mutating ancestor (the existing push_blocking gate), so a human
    reviewer can still see and judge it rather than have it silently
    vanish."""
    if not preceding_text:
        return False
    return bool(_REPRODUCED_TEXT_INTRO_RE.search(preceding_text.strip()))


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
    if prefix and not prefix.endswith("-") and _is_roman(prefix + number_raw):
        # Confirmed real bug ("Annexure XI:"): CORE_RE's optional prefix
        # group is greedy with no hyphen requirement, so on a multi-char
        # roman numeral like "XI" it wrongly peels off the leading letter
        # ("X") as if it were a one-letter exhibit prefix, leaving "I" as
        # the number — garbled into annexure:x-1 instead of annexure:xi.
        # A real exhibit prefix ("A-1", "R-9") always keeps its hyphen, so
        # this guard only fires on the wrong split, never a genuine prefix.
        return None, prefix + number_raw
    return prefix, number_raw


def normalize_key(keyword: str, prefix: Optional[str], number_raw: str, suffix: Optional[str] = None) -> str:
    prefix, number_raw = reconcile_prefix_and_number(keyword, prefix, number_raw)
    family = _FAMILY_ALIASES.get(keyword.lower().rstrip("."), keyword.lower())
    if family in _PREFIX_ELIGIBLE_FAMILIES and _is_roman(number_raw):
        # Keep the roman numeral literal (not arabic-converted) for these
        # families — it's the document's own real numbering scheme
        # ("Annexure XI", not "Annexure 11"); Article/Section etc. still
        # convert to arabic below, unaffected.
        number = number_raw.lower()
    else:
        number = str(roman_to_arabic(number_raw)) if _is_roman(number_raw) else number_raw
    prefix_part = f"{prefix.rstrip('-').lower()}-" if prefix else ""
    suffix_part = f"-{suffix.lower()}" if suffix else ""
    return f"{family}:{prefix_part}{number}{suffix_part}"


# Hyphen-separator variant of CORE_RE, scoped to the four prefix/numbered
# families confirmed to actually appear in this hyphenated spelling
# ("Annexure-XI:" instead of "Annexure XI:") — confirmed real bug: CORE_RE's
# keyword separator hard-requires literal whitespace, never a hyphen, so
# this spelling previously failed family parsing entirely and fell through
# to an unrelated generic fallback bucket. "section" was confirmed via a
# 135-page tender fixture: "Section-I:".."Section-VI:" (hyphen) mint as a
# generic subdoc fallback while "Section VII:" (space) correctly mints as
# `section:7` — the same root cause as the annexure case, just for a
# different keyword. MENTION_RE already tolerates the hyphen for citation
# detection, so citations to "Section-I" were resolving to an expected
# `section:1` key that no target ever minted. Deliberately NOT a change to
# CORE_RE itself (that would affect every keyword's whitespace handling at
# once), and deliberately NOT yet extended to clause/para/page/ground
# (unconfirmed whether their hyphenated forms carry the same meaning — a
# separate, still-open question). Same group names as CORE_RE so
# `CORE_RE.match(t) or _SUBDOC_HYPHEN_RE.match(t)` is a drop-in everywhere.
#
# `prefix` requires a LITERAL trailing hyphen (`[A-Z]-`, not `[A-Z]-?`) —
# confirmed real false-positive this closes: a 7-page contract's real OCR
# text has "SECTION-GCC" (an acronym, General Conditions of Contract, with
# no number at all) immediately followed by "A. INTRODUCTION". With the
# prefix hyphen optional, "G" silently absorbed as a bare prefix letter and
# "CC" parsed as a roman numeral (100+100), wrongly minting a real
# `section:*` container that then swallowed "A. INTRODUCTION" as its child.
# Every confirmed real prefix case (`"Annexure A-1"`/`"AmneXureA-1"`) always
# has the hyphen between the prefix letter and the number, so this is a
# pure precision tightening, not a loss of coverage.
_SUBDOC_HYPHEN_RE = re.compile(
    r"^\s*(?P<keyword>annexure|schedule|appendix|section)\b\.?-\s*(?:no\.?\s*)?"
    r"(?P<prefix>[A-Z]-)?(?P<number>[ivxlcdm]+(?:\.\d+)*|\d+(?:\.\d+)*)\b",
    re.IGNORECASE,
)

_SUBDOC_TRAILING_SUFFIX_RE = re.compile(r"^-(?P<suffix>[A-Za-z])\b")


def _match_subdoc_trailing_suffix(family: str, trailing_text: str) -> Optional[str]:
    """Captures a lettered sub-annexure suffix after the main number
    ("Annexure XIII-A" -> "A"). Confirmed necessary: the tender-spec
    fixture has both a plain "Annexure XIII" and a distinct "Annexure
    XIII-A" as real, different headings — without this, fixing the
    roman-split bug above would make BOTH collapse onto the identical key
    "annexure:xiii"."""
    if family not in _PREFIX_ELIGIBLE_FAMILIES:
        return None
    sm = _SUBDOC_TRAILING_SUFFIX_RE.match(trailing_text)
    return sm.group("suffix") if sm else None


_SLUG_NON_ALNUM_RE = re.compile(r"[^a-z0-9\s]+")
_SLUG_MAX_WORDS = 6
_SLUG_MAX_CHARS = 40
_SLUG_DISAMBIG_SUFFIX_RE = re.compile(r"\s*\((\d+)\)\s*$")


def _slugify_subdoc_label(text: str) -> str:
    """Content-addressed key for the generic `subdoc` fallback family — every
    other family already derives its key from parsed content (normalize_key
    above); `subdoc` was the one family still minted from a positional
    counter (boundary_ordinal), which drifts whenever one run detects a
    different number of generic boundaries than another (see call sites).
    Slugging the heading's own text instead makes the key depend only on
    that heading, not on how many others were counted before it.

    Should be called on the DISAMBIGUATED label (_disambiguate_label's
    output), not the raw pre-disambiguation trigger text — two genuinely
    distinct sub-documents sharing identical heading text (two separate
    "AFFIDAVIT" headings) must still get distinct keys, or their children's
    scope_key chains collide into the same string; the label's own "(2)"/
    "(3)" suffix is exactly the per-text occurrence count needed to tell
    them apart. That suffix is extracted and re-appended AFTER the
    word/char cap below, never subject to it — otherwise a long base heading
    ("I, the above named deponent do hereby solemnly affirm and st...")
    would truncate the "(2)" away before it ever reached the slug, silently
    colliding with the unsuffixed first occurrence (confirmed on the real
    100-page gold fixture while regenerating its keys for this change).
    Same-page collisions between two genuinely different headings that
    happen to slug identically regardless are left to `occurrence_index` to
    disambiguate, same as every other family."""
    text = text or ""
    suffix = ""
    m = _SLUG_DISAMBIG_SUFFIX_RE.search(text)
    if m:
        suffix = f"-{m.group(1)}"
        text = text[:m.start()]
    cleaned = _SLUG_NON_ALNUM_RE.sub(" ", text.lower()).strip()
    words = cleaned.split()[:_SLUG_MAX_WORDS]
    slug = "-".join(words)[:_SLUG_MAX_CHARS].rstrip("-")
    if not slug:
        digest = hashlib.md5((text or "").encode("utf-8")).hexdigest()[:8]
        slug = f"unlabeled-{digest}"
    return f"subdoc:{slug}{suffix}"


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
        if tail.lower() in ("s", "es"):
            return None
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


_TABLE_ROOT_RE = re.compile(r"^\s*<table\b", re.IGNORECASE)


class _HtmlTableCellParser(HTMLParser):
    """Lenient per-cell text extractor for a single raw `<table>...</table>`
    HTML blob (a mistune `block_html` token's own `raw` string). Scoped
    ONLY to <table>/<tr>/<td>/<th> structure. A new <tr>/<td>/<th> start
    tag always implicitly closes whatever cell/row is still open first —
    OCR'd HTML missing end tags (confirmed common in this corpus) degrades
    gracefully instead of losing structure, mirroring a browser's own
    lenient table parser. `close()` is overridden because stdlib
    HTMLParser.close() does NOT synthesize end-tag events for anything
    still open (confirmed directly) — without this override, a table
    truncated with no closing </tr>/</table> would silently lose its last
    row entirely."""

    _CELL_TAGS = {"td", "th"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows: List[List[str]] = []
        self._cur_row: Optional[List[str]] = None
        self._cur_cell: Optional[List[str]] = None

    def _close_cell(self):
        if self._cur_cell is not None:
            text = re.sub(r"\s+", " ", "".join(self._cur_cell)).strip()
            if self._cur_row is None:
                self._cur_row = []
            self._cur_row.append(text)
            self._cur_cell = None

    def _close_row(self):
        self._close_cell()
        if self._cur_row is not None:
            self.rows.append(self._cur_row)
            self._cur_row = None

    def close(self):
        super().close()
        self._close_row()

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._close_row()
            self._cur_row = []
        elif tag in self._CELL_TAGS:
            self._close_cell()
            if self._cur_row is None:
                self._cur_row = []
            self._cur_cell = []
        elif tag in ("br", "p", "div") and self._cur_cell is not None:
            self._cur_cell.append(" ")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag in self._CELL_TAGS:
            self._close_cell()
        elif tag in ("tr", "table"):
            self._close_row()

    def handle_data(self, data):
        if self._cur_cell is not None:
            self._cur_cell.append(data)


def _parse_html_table_blocks(
    raw: str, row_id_counter: Optional[Iterator[int]] = None,
) -> List[Dict[str, Any]]:
    """If `raw` (a mistune block_html token's raw text) is TABLE-rooted,
    parses it into one synthetic `table_cell` token per <td>/<th> cell, in
    row-major document order — the exact token shape _block_own_text
    already handles for a real mistune pipe-table cell. Returns [] (never
    None) when `raw` isn't table-rooted or parsing fails or yields no
    cells — the caller then falls through to today's unconditional drop,
    so non-table raw HTML (a bare <div>, <center>) stays exactly as
    invisible as before.

    `row_id_counter`, when given, tags each emitted cell dict with
    `"_row_id"` — one id per `<tr>`, shared by every cell in that row (reuses
    `_HtmlTableCellParser.rows`' already-correct row grouping, which is
    otherwise discarded by the flattening below). Lets a caller downstream
    reconstruct a split "number cell + content cell" row as one logical
    unit (see ai_reference_index.py's row-merge grounding fallback). `None`
    (the default, used by every existing caller) preserves today's exact
    output — zero risk to parse_page_blocks's existing contract."""
    if not _TABLE_ROOT_RE.match(raw or ""):
        return []
    parser = _HtmlTableCellParser()
    try:
        parser.feed(raw)
        parser.close()
    except Exception:
        return []
    out = []
    for row in parser.rows:
        row_id = next(row_id_counter) if row_id_counter is not None else None
        for cell_text in row:
            cell = {"type": "table_cell", "children": [{"type": "text", "raw": cell_text}]}
            if row_id is not None:
                cell["_row_id"] = row_id
            out.append(cell)
    return out


def _flatten_blocks(tokens: List[Dict[str, Any]], row_id_counter: Optional[Iterator[int]] = None):
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
    count actually tells them apart.

    `row_id_counter`, when given, is threaded through to `_parse_html_table_
    blocks` for every `block_html` token encountered (and through this
    function's own recursive self-calls) — a single shared counter, so row
    ids stay globally unique and monotonically increasing across multiple
    separate HTML tables on the same page. Native markdown pipe-table rows
    (the `table_row` container branch below) are deliberately NOT tagged —
    no confirmed case needs it yet; they keep `_row_id` absent, same as
    today. `None` (the default) preserves today's exact output."""
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
                yield from _flatten_blocks([item], row_id_counter=row_id_counter)
            continue
        if t == "block_html":
            yield from _parse_html_table_blocks(tok.get("raw", ""), row_id_counter=row_id_counter)
            continue
        if t in _BLOCK_TYPES:
            yield tok
            if t == "list_item":
                for child in tok.get("children", []):
                    if child.get("type") != "block_text":
                        yield from _flatten_blocks([child], row_id_counter=row_id_counter)
        elif t in _CONTAINER_TYPES:
            yield from _flatten_blocks(tok.get("children", []), row_id_counter=row_id_counter)


# ─── Sub-document boundary & external-law exclusion ────────────────────────────

def detect_subdoc_trigger(
    block_text: str, nested_stack: List[Dict[str, str]], lookahead_texts: Tuple[str, ...] = (),
) -> Optional[Tuple[str, bool]]:
    """Returns (label, is_strong) for a GENERIC (unnumbered) sub-document
    trigger only — e.g. "Affidavit", "Judgment", a title-cased case-caption
    phrase — within a small bounded noise-prefix of the block's own
    beginning. Deliberately not anchored to the exact first character — real
    scanned pages routinely open a block with a stamp/docket-mark fragment
    before the real heading (confirmed: `ll`, `Q IN`, `4x4L`, `3S Dated ...`
    in real samples). Returns None (not a tuple) when nothing fires at all.

    `is_strong` distinguishes a confident, real section boundary — the
    caller resets the whole stack, as always — from a keyword match
    qualified by a single real leading word in the same block ("Price
    Schedule", "Work Schedule:") that more likely names a minor sub-label
    of whatever's already open than a genuine new top-level division (see
    _QUALIFYING_WORD_BEFORE_TRIGGER_RE) — confirmed real bug: exactly this
    shape opened a container that stayed open for 20 subsequent pages,
    silently swallowing everything real in between. The caller persists a
    non-strong match as its own low-confidence candidate without touching
    the stack — flagged for a human to judge, never silently dropped.

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
    _WEAK_SUBDOC_TRIGGER_RE.

    `lookahead_texts` (the next few blocks' own text, caller-supplied) is
    only consulted when the fired trigger is the case-caption pattern (see
    _CASE_CAPTION_TRIGGER_RE) — that trigger's own matched text carries no
    distinguishing content (the SAME caption legitimately repeats before
    several different pleadings in one paper-book), so the label instead
    comes from whichever later block actually distinguishes this
    occurrence (a Civil Misc. No., "Respectfully Showeth", "Affidavit of
    ...", an "In F.A.O. ..." line)."""
    if DECIMAL_HEADING_RE.match(block_text.strip()):
        return None
    m = SUBDOC_TRIGGER_RE.search(block_text)
    weak = False
    if not m or m.start() > _SUBDOC_NOISE_PREFIX:
        m = _WEAK_SUBDOC_TRIGGER_RE.search(block_text)
        weak = True
        if not m or m.start() > _SUBDOC_NOISE_PREFIX:
            if _SHORT_PLEADING_HEADING_RE.match(block_text):
                return block_text.strip().rstrip(":").title(), True
            return None
    if weak and any(_HIERARCHY_RANK.get(s["family"]) == 0 for s in nested_stack):
        return None  # a bare "INDEX" heading while an Annexure/Schedule/
        # Appendix is already open is that container's own internal table
        # of contents, not a new document - only trust it starting fresh.
    core_m = CORE_RE.match(block_text) or _SUBDOC_HYPHEN_RE.match(block_text)
    if core_m and core_m.group("keyword").lower() in _SUBDOC_NUMBERED_FAMILIES:
        return None  # handled natively via _HIERARCHY_RANK below, not here
    if not weak and _CASE_CAPTION_TRIGGER_RE.search(m.group(0)):
        for candidate in lookahead_texts:
            hint_m = _CASE_CAPTION_LABEL_HINT_RE.search(candidate or "")
            if hint_m:
                return " ".join(w.capitalize() for w in re.split(r"\s+", hint_m.group(0).strip()))[:80], True
        return " ".join(w.capitalize() for w in re.split(r"\s+", m.group(0).strip())), True

    label = " ".join(w.capitalize() for w in re.split(r"\s+", m.group(0).strip()))
    if weak:
        return label, True
    is_strong = not _QUALIFYING_WORD_BEFORE_TRIGGER_RE.match(block_text[:m.start()])
    return label, is_strong


_IMPLICIT_KEYWORD_NUMBER_RE = re.compile(
    rf"^\s*(?P<keyword>{_KEYWORD_ALTS})\b[\s\-]*\.?[\s\-]*"
    rf"(?P<number>[ivxlcdm]+(?:\.\d+)*|\d+(?:\.\d+)*)\b",
    re.IGNORECASE,
)


def is_subdoc_root_candidate(text: str, family: Optional[str] = None, root_label: Optional[str] = None) -> bool:
    """Returns True if the heading represents a genuine top-level document boundary
    (an independent document, annexure, schedule, exhibit, or major root boundary)
    rather than a section heading within the active document.

    `root_label` (the currently-open root container's own label, when the
    caller has one) enables one more, general signal: a heading sharing
    the SAME explicit numbered-family keyword as the document's current
    root (e.g. both say "Section") is a SIBLING of that root, not its
    child — regardless of whether its label also happens to contain some
    unrelated trigger word. Confirmed real bug this closes: a tender-spec
    document's "Section-I"/"Section-II".."Section-VI" headings were
    wrongly clamped under whichever of them opened first, because this
    function has no concept of "Section" as a keyword at all — "Section-I"
    and "Section VII" only escaped the clamp by COINCIDENCE (their own
    labels also happened to contain "Tender"/"Annexures"), not because of
    their hyphen-vs-space spelling, which the investigation had
    (incorrectly) documented as the cause. Deliberately shape-only and
    keyword-matched (via _IMPLICIT_KEYWORD_NUMBER_RE, tolerant of
    hyphen/space/nothing between keyword and number) — never touches
    key-minting, so it can't re-key an already-scored target. A genuinely
    nested child sharing the root's own number (e.g. "Section-I.1" under
    root "Section-I") is excluded via the decimal-extension guard below."""
    if family in _SUBDOC_NUMBERED_FAMILIES:
        return True
    if not text:
        return False
    stripped = text.strip()
    if SUBDOC_TRIGGER_RE.search(stripped):
        return True
    if _WEAK_SUBDOC_TRIGGER_RE.search(stripped):
        return True
    if re.search(
        r"\b(?:EXHIBIT|ATTACHMENT|ADDENDUM|AMENDMENT|AGREEMENT|TENDER|SPECIFICATION|BILL\s+OF\s+QUANTITIES|BOQ)\b",
        stripped,
        re.IGNORECASE,
    ):
        return True
    if _STANDALONE_LEGAL_DOCS_RE.search(stripped):
        return True
    if root_label:
        m_this = _IMPLICIT_KEYWORD_NUMBER_RE.match(stripped)
        if m_this:
            m_root = _IMPLICIT_KEYWORD_NUMBER_RE.match(root_label.strip())
            if m_root:
                kw_this = _FAMILY_ALIASES.get(m_this.group("keyword").lower().rstrip("."), m_this.group("keyword").lower())
                kw_root = _FAMILY_ALIASES.get(m_root.group("keyword").lower().rstrip("."), m_root.group("keyword").lower())
                if kw_this == kw_root:
                    this_num = m_this.group("number").lower()
                    root_num = m_root.group("number").lower()
                    if this_num != root_num and not this_num.startswith(root_num + "."):
                        return True
    return False


_STANDALONE_LEGAL_DOCS_RE = re.compile(
    r"^\s*(?:AFFIDAVIT|APPLICATION\s+UNDER|PETITION|MEMO\s+OF\s+PARTIES|VAKALATNAMA|POWER\s+OF\s+ATTORNEY)\b",
    re.IGNORECASE,
)


_MAJOR_JUDICIAL_SECTIONS = re.compile(
    r"^\s*(?:"
    r"FACTS|"
    r"PETITIONER'?S?\s+(?:VERSION|CASE|SUBMISSIONS?)|"
    r"RESPONDENT'?S?\s+(?:VERSION|CASE|SUBMISSIONS?)|"
    r"CHALLENGE\s+TO\s+.*|"
    r"ISSUES?|"
    r"FINDINGS?|"
    r"ANALYSIS|"
    r"SUBMISSIONS?\s+OF\s+.*|"
    r"CONCLUSION|"
    r"DISPOSAL|"
    r"ORDER"
    r")\s*[:\.\-]?\s*$",
    re.IGNORECASE,
)


_GENERIC_HEADING_MAX_WORDS = 6
_GENERIC_HEADING_CONNECTORS = {"of", "for", "and", "the", "in", "to", "on", "at", "&", "or"}
# Same general fact as SUBDOC_TRIGGER_RE's exclusion of "Versus"/"Vs." —
# case-caption furniture, never a real heading in any document — applied
# here too so the shape-only detector doesn't independently reintroduce
# exactly the bug the trigger-list exclusion was meant to fix.
_GENERIC_HEADING_EXCLUDED_WORDS = {"versus", "vs", "vs."}

# A bare OCR/editorial placeholder tag — the whole stripped text is one
# bracket pair with nothing outside it ("[NO TEXT]", "[Non-Text]",
# "[illegible]", "[IMAGE]"). See _looks_like_generic_heading_shape's use.
_BRACKETED_PLACEHOLDER_RE = re.compile(r"^\[[^\[\]]{1,40}\]$")

# A line introducing a mailing/circulation distribution list ("Copy of the
# above is forwarded to the following for information...", bare "Copy to:")
# — the numbered addressee lines that follow (office designation + place
# name, e.g. "Chief Engineer/MM, HVPNL, Panchkula.") satisfy
# _looks_like_generic_heading_shape's shape test exactly, with nothing in
# that detector able to tell a real open-vocabulary heading apart from a
# CC-list entry. See build_targets_for_page's in_distribution_list usage.
_DISTRIBUTION_LIST_INTRO_RE = re.compile(
    r"\bcopy\b.{0,60}\bforwarded\b.{0,30}\bfollowing\b|\bcopy\s+to\s*:?\s*$",
    re.IGNORECASE,
)
_DISTRIBUTION_INTRO_RE = re.compile(
    r"(?:endst\.?\s*(?:no\.?|memo)|copy\s+forwarded\s+(?:for|to)|distribution\s*:|copies\s+to\b|copy\s+to\b|\bcopy\b.{0,60}\bforwarded\b)",
    re.IGNORECASE,
)
_DISTRIBUTION_RECIPIENT_RE = re.compile(
    r"^\s*(?:\(?\d+[\.\)]|\(?[a-z][\.\)]|\(?[ivxlcdm]+[\.\)])\s*(?:the\s+)?(?:sr\.?\s*ps|ps\s+to|pa\s+to|chief\s+engineer|superintending\s+engineer|executive\s+engineer|supdt|secretary|director|manager|accounts\s+officer|ao\b|ca\b|fa\b|notice\s+board|guard\s+file)",
    re.IGNORECASE,
)


def _looks_like_generic_heading_shape(text: str) -> bool:
    """General, document-agnostic SHAPE signal for an open-vocabulary
    heading no fixed keyword list could ever enumerate in advance
    ("Insurance:", "Contract Agreement:", "Taxes & Duties", "Bank
    Guarantee for Contract Performance", "Summary Price Proposal" — real
    confirmed misses, none sharing a common keyword). Requires either a
    trailing colon, or every significant word capitalized/ALL-CAPS
    throughout (short connector words exempted) — deliberately
    conservative (<= 6 words, first word always capitalized) since this
    signal has no numbering/keyword evidence behind it at all, shape
    alone. Purely a detection signal — callers must still treat a match
    as low-confidence and never let it become a stack-mutating ancestor
    (see build_targets_for_page's residual-fallback usage)."""
    stripped = text.strip()
    if not stripped:
        return False
    if _BRACKETED_PLACEHOLDER_RE.match(stripped):
        # A bare OCR/editorial placeholder tag ("[NO TEXT]", "[Non-Text]",
        # "[illegible]", "[IMAGE]") — confirmed real bug (job bb417471,
        # page 91): the word-shape check below strips non-word characters
        # before testing capitalization, so the bracket itself contributes
        # no signal and a short Title-Case placeholder like this slips
        # through as if it were a real heading. Scoped to the WHOLE
        # stripped text being one bracket pair with nothing outside it, so
        # a real heading that merely contains a bracket ("Annexure A
        # (Revised)") is unaffected.
        return False
    if stripped.rstrip(":.- ").lower() in _GENERIC_HEADING_EXCLUDED_WORDS:
        return False
    has_colon = stripped.endswith(":") or stripped.endswith(":-")
    if stripped.endswith(":-"):
        body = stripped[:-2]
    elif has_colon:
        body = stripped[:-1]
    else:
        body = stripped
    words = body.strip().split()
    if not words or len(words) > _GENERIC_HEADING_MAX_WORDS:
        return False
    first_core = re.sub(r"[^\w]", "", words[0])
    if not first_core or not first_core[0].isupper():
        return False
    if has_colon:
        return True
    for w in words[1:]:
        core = re.sub(r"[^\w]", "", w)
        if not core:
            continue
        if core.lower() in _GENERIC_HEADING_CONNECTORS:
            continue
        if not core[0].isupper():
            return False
    return True


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


def _leading_reference_number(text: str) -> Optional[str]:
    m = _DECIMAL_NUMBER_HEAD_RE.match((text or "").strip())
    if not m:
        return None
    segs = m.group("number").split(".")
    while len(segs) > 1 and segs[-1] == "0":
        segs.pop()
    return ".".join(segs)


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

    Walks inward-to-outward and stops at the NEAREST subdoc boundary — never
    scans past it. Originally this checked ONLY nested_stack[0] against
    _GROUND_SCOPE_HEADINGS (on the assumption a "GROUNDS OF APPEAL" trigger is
    always the outermost entry) plus a separate, unbounded "any entry anywhere
    has family=='ground'" scan. Both assumptions broke in practice: depth is
    resolved from parent_ref/level, not family rank (see
    apply_heading_events_to_scope's own docstring), so a `subdoc` entry is not
    guaranteed to sit only at index 0 — and confirmed on real data, "GROUNDS OF
    APPEAL" itself can legitimately BE nested_stack[0] (the AI's own top-level
    read of the document), with many genuinely distinct sub-documents (e.g. a
    separate "Respondent's case", a "CONTRACT AGREEMENT BETWEEN" annexure)
    nested beneath it. The old unconditional index-0 check made every one of
    those unrelated sub-documents' own numbered/lettered children inherit
    ground-scope merely because the document's root happened to be titled
    "Grounds of Appeal" — for as long as that root stayed open, i.e. most of
    the document. The fix: treat the NEAREST enclosing subdoc boundary as the
    one that matters, whether or not it happens to be the Grounds trigger
    itself. A subdoc boundary whose own label does NOT match
    _GROUND_SCOPE_HEADINGS is a neutral container (an unrelated sub-document)
    and stops ground-scope inheritance from anything further out — it is NOT
    itself evidence that ground-scope should apply."""
    for s in reversed(nested_stack):
        if s.get("family") == "ground":
            return True
        if s.get("family") == "subdoc":
            label = s.get("label") or ""
            if _GROUND_SCOPE_HEADINGS.search(label):
                return True
            # If nearest subdoc is an averment continuation (e.g. "(I) That the learned court below..."),
            # do not treat it as an unrelated subdocument that evicts ground scope:
            if _PLEADING_AVERMENT_RE.search(label) or re.match(r"^\s*\(?[IVXLCDMivxlcdm0-9]+\)?\s+That\b", label, re.IGNORECASE):
                continue
            return False
    return False


def _pleading_scope_active(nested_stack: List[Dict[str, str]]) -> bool:
    """Numbered/roman pleading paragraphs ("1. That the appellant is
    filing...") are indexed as leaf `point` entries when they sit directly
    under a generic, unnumbered sub-document boundary (a case-caption
    block, an affidavit, a judgment, "Grounds of Appeal" itself before
    Ground 10's own roman-numeral sub-grounds take over via
    _ground_scope_active, checked first by every caller) — never inside a
    numbered contract container (Annexure/Article/Section), where a "2.1"
    is a real numbered clause, not a pleading point.

    Walks inward-to-outward and stops at the NEAREST subdoc boundary — same
    fix already applied to _ground_scope_active for the identical bug: the
    original unconditionally checked nested_stack[0] (the document's root),
    which is "subdoc" family for nearly every document (e.g. "JUDGMENT"),
    so this returned True almost everywhere, 90+ pages deep into an
    unrelated embedded annexure included (confirmed live: a 22-item
    distribution/CC list on an unrelated Contract Agreement annexure page
    minted as point:1..point:22, because the TRUE root was "JUDGMENT" and
    nothing stopped inheritance at the nearer, unrelated subdoc boundaries
    in between). A nearest subdoc boundary matching
    _NON_PLEADING_SUBDOC_LABELS_RE is a neutral/excluded container and
    stops inheritance from anything further out, same mechanism as
    _ground_scope_active, inverted (exclusion list, not allow-list — see
    that constant's docstring for why)."""
    # If any container in the ancestor chain is an explicit non-pleading document
    # (e.g. Contract Agreement, Purchase Order, Section-I through Section-XX, Annexures, Schedules),
    # then child subdocs inside it are commercial clauses, never court pleadings.
    if any(
        s.get("family") in ("annexure", "section", "article", "contract", "schedule", "appendix")
        or s.get("key", "").startswith(("annexure:", "section:", "article:", "contract:", "schedule:", "appendix:"))
        or _NON_PLEADING_SUBDOC_LABELS_RE.search(s.get("label") or "")
        for s in nested_stack
    ):
        return False
    for s in reversed(nested_stack):
        if s.get("family") == "point":
            return True
        if s.get("family") == "subdoc":
            return not bool(_NON_PLEADING_SUBDOC_LABELS_RE.search(s.get("label") or ""))
    return False



def _disambiguate_label(label: str, counts: Dict[str, int]) -> str:
    """First occurrence of a generic (unnumbered) sub-document label is left
    unchanged; the 2nd, 3rd, ... get a distinguishing suffix — a numbered
    family (Annexure A-1 vs A-2) never needs this, it's already unique.

    Counts by a case/whitespace-normalized form of the label, not the raw
    string: confirmed on the real gold fixture that the same heading can
    recur with only a case difference ("RESPECTFULLY SHOWETH" vs
    "Respectfully Showeth") — exact-string counting treated both as
    unrelated "first occurrences" and suffixed neither, which used to be
    harmless (boundary_ordinal kept their keys unique regardless) but, now
    that _slugify_subdoc_label derives the key from this label, would
    otherwise mint the identical key for two genuinely different
    sub-documents. The returned label still preserves the caller's original
    casing — only the counting key is normalized."""
    norm = (label or "").strip().lower()
    n = counts.get(norm, 0)
    counts[norm] = n + 1
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
    layout type belongs to). Note: decomposing a table into N cell blocks
    means len(blocks) no longer lines up with the OCR engine's one-det-tag-per-
    table-region count on table pages — this mismatch already existed pre-fix
    (0 blocks vs 1 det-tag region), so alignment simply degrades gracefully
    to False on table-bearing pages without corrupting candidates.
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
    # A quoted clause reproduced verbatim mid-argument often spans more than
    # one numbered line ("9.0 Local/Site conditions" immediately followed by
    # its own "9.1 ..." sub-clause, both part of the SAME quote) — only the
    # first line is ever immediately preceded by the "which reads as under:"
    # intro itself. Tracks the leading top-level segment of the most recent
    # possible_reproduced_text match, page-local only (not carried in
    # `scope`), so the very next numbered heading sharing that same
    # top-level segment inherits the flag too, instead of being treated as a
    # genuine new heading that pushes a real ancestor and resets
    # last_decimal_ground.
    last_reproduced_number: Optional[str] = None
    # Separate from last_letter_ordinal above (that one's for the
    # unrelated "A. INTRODUCTION"-style letterheading family) — tracks a
    # running (A)(B)(C)... sequential-letter enumeration inside an active
    # ground/point scope specifically, so a later roman-valid letter
    # (C=100, D=500, ...) reached mid-run resolves as the next list item,
    # not a wildly out-of-place roman numeral. See
    # _resolve_ground_or_point_match.
    last_ground_letter_ordinal: Optional[int] = scope.get("last_ground_letter_ordinal")
    # The most recent DECIMAL ground/point seen in the currently active
    # scope ("ground:10") — when a roman-numeral ground/point follows it
    # (the document's own numbering-scheme transition: grounds 1-10 given
    # as plain decimals, then continuing as roman numerals "(XI)",
    # "(XII)", ...), the roman items nest as ITS children instead of
    # flat siblings continuing the decimal sequence. Reset alongside
    # last_bare_integer/last_letter_ordinal whenever a genuine new
    # container opens (see both reset sites below) — a numbering-scheme
    # transition inside one still-open scope is one thing; a real new
    # section boundary is a different, unrelated event.
    last_decimal_ground: Optional[Dict[str, str]] = scope.get("last_decimal_ground")
    # Mirrors apply_heading_events_to_scope's outer_decimal_ground — a
    # decimal ground/point that opened a NESTED decimal sub-list (e.g.
    # "19. ... summarized under following heads: 1. ... 2. ..."), so a
    # later bare integer can resolve either as a child continuing that
    # sub-list, or as a resumption of the OUTER sequence once the sub-list
    # ends (e.g. "20." resuming as point:19's sibling, not its child). This
    # tracking previously existed only in the AI path — ported here so the
    # rule-based detector (both the live per-page path and the per-chunk AI
    # fallback) agrees with the AI path on nested-decimal documents instead
    # of leaving every such item flat.
    outer_decimal_ground: Optional[Dict[str, Any]] = scope.get("outer_decimal_ground")

    candidates: List[Dict[str, Any]] = []
    occurrence_counts: Dict[str, int] = {}
    open_acc: Optional[Dict[str, Any]] = None
    # Page-local only (not carried in scope) — a "Copy ... forwarded to the
    # following"/"Copy to:" distribution-list intro line, followed by a run
    # of numbered addressee lines ("Chief Engineer/MM, HVPNL, Panchkula."),
    # is structurally indistinguishable from genuine open-vocabulary
    # headings to _looks_like_generic_heading_shape (title-cased office
    # names + place names satisfy its shape test exactly) — confirmed on
    # the 100-page fixture's page 91 (19 of 22 CC-list lines minted as
    # spurious subdoc: candidates). Suppresses only residual-fallback #2
    # below; every other, more-evidenced detection path is untouched.
    # Deliberately never cleared once set (stays True for the rest of the
    # page): mistune flattens each "N. addressee" list item into TWO blocks
    # (a list_item carrying the leading number, then a nested paragraph
    # with it stripped) — a per-block "has this line exited the list"
    # check kept false-triggering on that stripped-number paragraph after
    # just the first item. A real heading appearing on the same page after
    # a distribution-list intro is rare enough that this is an acceptable
    # trade.
    in_distribution_list = False
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

        if _DISTRIBUTION_LIST_INTRO_RE.search(text):
            in_distribution_list = True

        lookahead_texts = tuple(
            _block_own_text(b) for b in blocks[block_idx + 1:block_idx + 1 + _CASE_CAPTION_LOOKAHEAD_BLOCKS]
        )
        trigger_result = detect_subdoc_trigger(text, nested_stack, lookahead_texts)
        if trigger_result:
            trigger_label, trigger_is_strong = trigger_result
            if not trigger_is_strong:
                # Qualified match ("Price Schedule", "Work Schedule:") — a
                # minor sub-label of whatever's already open, not a real
                # top-level division (see detect_subdoc_trigger's
                # is_strong docstring). Recall-biased: still persisted as
                # its own visible, low-confidence candidate for a human to
                # keep or delete — never silently dropped — but it must
                # NOT finalize/replace the genuinely open accumulator or
                # itself become an ancestor anything nests under (that's
                # the confirmed real bug: this exact shape opened a
                # container that stayed open for 20 subsequent pages).
                boundary_ordinal += 1
                weak_key = _slugify_subdoc_label(trigger_label)
                weak_occ = occurrence_counts.get(weak_key, 0)
                occurrence_counts[weak_key] = weak_occ + 1
                scope_key = "/".join(s["key"] for s in nested_stack)
                scope_label = " › ".join(s["label"] for s in nested_stack)
                candidates.append({
                    "key": weak_key,
                    "occurrence_index": weak_occ,
                    "snippet": text[:400],
                    "truncated": False,
                    "continues_on_page": None,
                    "scope_key": scope_key,
                    "scope_label": scope_label,
                    "scope_chain": [dict(s) for s in nested_stack],
                    "collapsed_key": collapse_key(weak_key),
                    "fingerprint": text[:30],
                    "heading_confidence": "low",
                    "heading_confidence_reasons": ["qualified_generic_trigger"],
                    "own_label": trigger_label,
                })
                continue

            # A generic boundary closes whatever was open before it, exactly
            # like a keyword-matched target does — without this, an
            # AFFIDAVIT/JUDGMENT heading with no accompanying core_m match
            # would fall through to the `open_acc["blocks"].append(block)`
            # tail below and get silently swallowed into the previous
            # clause's snippet instead of ending it.
            finalize(closed_by_new_target=True)
            boundary_ordinal += 1
            # Slug the DISAMBIGUATED label, not the raw trigger text: two
            # genuinely distinct sub-documents sharing identical heading text
            # (e.g. two separate "AFFIDAVIT" headings) must still get distinct
            # keys, or their children's scope_key chains collide into the
            # same string — label's own "(2)"/"(3)" suffix is exactly the
            # per-text occurrence count needed to tell them apart, and slugs
            # cleanly since digits survive _slugify_subdoc_label's cleanup.
            label = _disambiguate_label(trigger_label, label_occurrence_counts)
            key = _slugify_subdoc_label(label)
            # A brand new sub-document boundary obviously starts fresh
            # numbering too — numbered-heading entries (pushed below) have
            # no independent identity to survive it.
            nested_stack = [
                s for s in nested_stack
                if "num_segments" not in s and _HIERARCHY_RANK.get(s["family"], 99) < 0  # rank 0 = pop everything
            ]
            nested_stack.append({"family": "subdoc", "key": key, "label": label})
            last_bare_integer = None
            last_letter_ordinal = None
            last_ground_letter_ordinal = None
            last_decimal_ground = None
            outer_decimal_ground = None
            continue

        core_m = CORE_RE.match(text) or _SUBDOC_HYPHEN_RE.match(text)
        family = None
        number_raw = None
        prefix = None
        suffix = None
        is_ground = False
        is_point = False
        is_seq_letter = False
        is_fuzzy = False
        match_end = None

        if core_m:
            keyword = core_m.group("keyword")
            family = _FAMILY_ALIASES.get(keyword.lower().rstrip("."), keyword.lower())
            prefix, number_raw = reconcile_prefix_and_number(keyword, core_m.group("prefix"), core_m.group("number"))
            match_end = core_m.end()
            suffix = _match_subdoc_trailing_suffix(family, text[match_end:])
        else:
            fuzzy_m = _fuzzy_family_match(text)
            if fuzzy_m:
                family, prefix, number_raw = fuzzy_m
                is_fuzzy = True
                m_fuzzy = _FUZZY_PREFIX_RE.match(text) or _FUZZY_PREFIX_NODIGIT_RE.match(text)
                match_end = m_fuzzy.end() if m_fuzzy else len(text)
            elif _ground_scope_active(nested_stack):
                gm = _resolve_ground_or_point_match(text, last_ground_letter_ordinal)
                if gm:
                    number_raw, match_end, is_seq_letter = gm
                    family = "ground"
                    is_ground = True
            elif _pleading_scope_active(nested_stack):
                gm = _resolve_ground_or_point_match(text, last_ground_letter_ordinal)
                if gm:
                    number_raw, match_end, is_seq_letter = gm
                    family = "point"
                    is_point = True
            elif nested_stack and nested_stack[-1].get("family") == "numheading":
                sm = _SUBPOINT_ROMAN_RE.match(text)
                if sm:
                    family = "subpoint"
                    number_raw = sm.group("numeral").lower()
                    match_end = sm.end()

        if is_seq_letter:
            last_ground_letter_ordinal = ord(number_raw) - ord("A") + 1

        if family and number_raw:
            if is_seq_letter:
                # Bypass normalize_key entirely for this case: it treats
                # ANY roman-shaped string as a roman numeral (a single "C"
                # would become 100), which is exactly the misinterpretation
                # _resolve_ground_or_point_match already decided against —
                # the key must carry the letter itself, not a numeral.
                key = f"{family}:{number_raw.lower()}"
            elif (is_ground or is_point) and _is_roman(number_raw):
                # Bypass normalize_key's decimal conversion here too: a real
                # document can have decimal grounds/points 1-10 AND roman
                # grounds/points reaching the same values (I=1, ..., X=10) in
                # the SAME scope (confirmed real case — decimal background
                # facts 1-10, then roman sub-grounds (I)-(X) nested under
                # ground 10) — converting the roman item to its arabic value
                # would collide with the real decimal item of the same
                # number, corrupting both. The roman string itself is always
                # unique against a decimal key.
                key = f"{family}:{number_raw.lower()}"
            elif family == "subpoint":
                key = normalize_key(family, prefix, number_raw)
            else:
                key = normalize_key(
                    family if (is_ground or is_point or is_fuzzy) else core_m.group("keyword"),
                    prefix, number_raw, suffix,
                )
            is_subpoint = family == "subpoint"
            excluded = _is_backward_reference(text, match_end) or (
                (not (is_ground or is_point or is_subpoint)) and _is_external_law(text)
            )

            # A numbered/lettered row that's really a table/list row (a
            # spares-list part, a price-schedule line item, a document-index
            # entry), not a real ground/point — see _LISTING_CONTAINER_RE.
            # point/ground never anchor anything (FAMILIES gives both
            # rank=None), so skipping entirely here can't orphan any real
            # nested content.
            if (is_ground or is_point) and _immediate_parent_is_listing_container(nested_stack, text):
                continue

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
                    last_ground_letter_ordinal = None
                    last_decimal_ground = None
                    outer_decimal_ground = None

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
                # Mirrors apply_heading_events_to_scope's incoming_int — a
                # bare single-segment decimal ground/point number, used
                # below to detect a nested decimal sub-list opening (e.g.
                # "19. ... following heads: 1. Delay...") or an outer
                # sequence resuming after one closes (e.g. "20." resuming
                # as 19's sibling). Only ever set for a genuine decimal
                # match — roman numerals and sequential letters don't carry
                # an outer/inner-sequence meaning.
                incoming_int = None
                if (is_ground or is_point) and not is_seq_letter and not _is_roman(number_raw) and "." not in number_raw:
                    try:
                        incoming_int = int(number_raw)
                    except (TypeError, ValueError):
                        pass

                effective_stack = nested_stack
                if (is_ground or is_point) and (is_seq_letter or _is_roman(number_raw)) and last_decimal_ground:
                    # A roman-numeral OR sequential-letter ground/point
                    # continuing a decimal one in the SAME scope — nest
                    # under that specific decimal item (a synthetic entry,
                    # local to this row only, never pushed onto the real
                    # nested_stack: ground/point stay leaves, this doesn't
                    # make them containers). Both are numbering-style
                    # transitions from the same decimal parent; there's no
                    # principled reason to nest one but not the other.
                    base_stack = last_decimal_ground.get("stack") or nested_stack
                    effective_stack = [dict(s) for s in base_stack] + [
                        {"family": family, "key": last_decimal_ground["key"], "label": last_decimal_ground["label"]}
                    ]
                elif (is_ground or is_point) and incoming_int is not None:
                    # Ported from apply_heading_events_to_scope — a decimal
                    # ground/point can itself nest under ANOTHER decimal
                    # ground/point (e.g. "19. ... summarized under following
                    # heads: 1. Delay in issuing the GELO. 2. ... 5. Award
                    # of amounts..."), and once that nested sub-list ends, a
                    # later integer resumes the OUTER sequence as a sibling
                    # of the item that opened the sub-list (e.g. "20."
                    # resumes as 19's sibling, not as a child of "5.").
                    if outer_decimal_ground is not None:
                        outer_p = outer_decimal_ground.get("int_val")
                        if outer_p is not None and outer_p < incoming_int <= outer_p + 3:
                            # Resumes outer sequence (e.g. 20 resumes 19).
                            outer_decimal_ground = None
                            effective_stack = nested_stack
                        else:
                            base_stack = outer_decimal_ground.get("stack") or nested_stack
                            effective_stack = [dict(s) for s in base_stack] + [
                                {"family": family, "key": outer_decimal_ground["key"], "label": outer_decimal_ground["label"]}
                            ]
                    elif last_decimal_ground is not None:
                        prev_p = last_decimal_ground.get("int_val")
                        if prev_p is not None and prev_p >= 2 and incoming_int == 1:
                            # Starts a nested decimal sub-list (1. follows 19.).
                            outer_decimal_ground = last_decimal_ground
                            base_stack = outer_decimal_ground.get("stack") or nested_stack
                            effective_stack = [dict(s) for s in base_stack] + [
                                {"family": family, "key": outer_decimal_ground["key"], "label": outer_decimal_ground["label"]}
                            ]

                scope_key = "/".join(s["key"] for s in effective_stack)
                scope_label = " › ".join(s["label"] for s in effective_stack)

                open_acc = {
                    "key": key,
                    "occurrence_index": occ,
                    "blocks": [block],
                    "scope_key": scope_key,
                    "scope_label": scope_label,
                    "scope_chain": [dict(s) for s in effective_stack],
                    "fingerprint": text[:30],
                    "confidence_reasons": confidence_reasons,
                }
                if family in ("point", "subpoint"):
                    open_acc["own_label"] = text[:80].strip()

                if (is_ground or is_point) and not is_seq_letter:
                    if _is_roman(number_raw):
                        pass  # a roman continuation never itself becomes the new "last decimal"
                    else:
                        # `effective_stack` is always THIS heading's own
                        # ancestor chain (never includes its own key — see
                        # the matching note in apply_heading_events_to_scope);
                        # store it as-is so a later roman/sequential-letter
                        # or nested-decimal continuation of THIS heading can
                        # append this heading's own key on top of it without
                        # silently dropping an ancestor.
                        last_decimal_ground = {
                            "key": key,
                            "label": _format_label_from_key(key),
                            "stack": [dict(s) for s in effective_stack],
                            "int_val": incoming_int,
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
            is_reproduced = block_idx > 0 and _preceded_by_reproduced_text_intro(_block_own_text(blocks[block_idx - 1]))
            if not is_reproduced and last_reproduced_number is not None:
                is_reproduced = dm["number"].split(".")[0] == last_reproduced_number.split(".")[0]
            if is_reproduced:
                confidence_reasons.append("possible_reproduced_text")
                last_reproduced_number = dm["number"]
            else:
                last_reproduced_number = None

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

            # A numbered row that's really a table/list row (a price-schedule
            # line item, a spares-list part), not a real numbered clause —
            # see _LISTING_CONTAINER_RE. Checked AFTER the pop-loop above, not
            # before: confirmed real bug otherwise — a genuine sibling like
            # "3.4 QUALITY PLANS" was wrongly suppressed because the STALE
            # pre-pop top-of-stack ("3.3 TIME SCHEDULE") matched the gate,
            # even though 3.4's true resolved parent ("3.0 CONDITIONS AND
            # COVENANTS") does not. Must use the same post-resolution
            # nested_stack state scope_key itself is about to be built from.
            if _immediate_parent_is_listing_container(nested_stack, text):
                continue

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

                # A lettered row that's really a table/list row (e.g. "A.
                # 2000A", "B. 220 KV SF-6 Circuit Breaker" in a price-schedule
                # table), not a real lettered section — see
                # _LISTING_CONTAINER_RE. Checked AFTER the prune above (same
                # reasoning/bug as the numheading site: the stale pre-prune
                # top-of-stack is not necessarily this heading's true
                # resolved parent).
                if _immediate_parent_is_listing_container(nested_stack, text):
                    continue

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
                # Disambiguate the label first (same reasoning as the real
                # boundary trigger above): this site also pushes onto
                # nested_stack as a real ancestor, so two identical-text
                # layout-tagged headings must not collide into one key.
                fallback_label = _disambiguate_label(text[:60], label_occurrence_counts)
                key = _slugify_subdoc_label(fallback_label)
                occ = occurrence_counts.get(key, 0)
                occurrence_counts[key] = occ + 1

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
                    "confidence_reasons": ["layout_only_no_text_match"],
                }
                nested_stack.append({"family": "subdoc", "key": key, "label": fallback_label})
                continue

        # Residual fallback #2: a short, isolated, generic-shaped line
        # (colon-terminated or title-cased/ALL-CAPS) with no number, no
        # keyword match, and no visual layout signal either — general
        # TEXT-SHAPE detector for Root Cause B's open vocabulary, so it
        # works on any job regardless of whether OCR grounding was
        # enabled. Deliberately a pure leaf (never pushed onto
        # nested_stack, unlike the layout-tag fallback above, which has
        # real visual evidence behind it) — purely additive and
        # recall-biased: a false positive here is just an extra
        # low-confidence row a human can delete; it can never itself
        # become a false ancestor other content gets wrongly nested
        # under.
        if (
            _looks_like_generic_heading_shape(text)
            and not _is_external_law(text)
            and not _is_backward_reference(text, 0)
            and not in_distribution_list
            and block.get("type") != "table_cell"
        ):
            finalize(closed_by_new_target=True)
            boundary_ordinal += 1
            key = _slugify_subdoc_label(text)
            occ = occurrence_counts.get(key, 0)
            occurrence_counts[key] = occ + 1
            scope_key = "/".join(s["key"] for s in nested_stack)
            scope_label = " › ".join(s["label"] for s in nested_stack)
            candidates.append({
                "key": key,
                "occurrence_index": occ,
                "snippet": text[:400],
                "truncated": False,
                "continues_on_page": None,
                "scope_key": scope_key,
                "scope_label": scope_label,
                "scope_chain": [dict(s) for s in nested_stack],
                "collapsed_key": collapse_key(key),
                "fingerprint": text[:30],
                "heading_confidence": "low",
                "heading_confidence_reasons": ["generic_heading_shape"],
                "own_label": text.strip().rstrip(":").rstrip("-").strip()[:80],
            })
            continue

        if open_acc is not None:
            open_acc["blocks"].append(block)

    finalize(closed_by_new_target=False)

    new_scope = {
        "stack": nested_stack, "boundary_ordinal": boundary_ordinal, "label_occurrence_counts": label_occurrence_counts,
        "last_bare_integer": last_bare_integer, "last_letter_ordinal": last_letter_ordinal,
        "last_ground_letter_ordinal": last_ground_letter_ordinal, "last_decimal_ground": last_decimal_ground,
        "outer_decimal_ground": outer_decimal_ground,
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
        await _process_single_page(
            job_id, next_page, page_row.get("text") or "", total_pages, provisional=False,
            raw_text=page_row.get("raw_ocr_text"),
        )
        frontier = next_page
        database.set_reference_index_frontier(job_id, frontier)
        advanced += 1
    else:
        # Cascade capped, more may remain — continue in a follow-up task
        # carrying just enough state to resume, per the approved plan's
        # bounded-cascade design.
        asyncio.create_task(_cascade_entry(job_id, frontier, total_pages))


def parse_page_blocks_with_types(page_text: str) -> Tuple[List[str], List[str]]:
    """Like parse_page_blocks, but also returns each block's own mistune
    `type` (e.g. "table_cell", "paragraph", "list_item") alongside its
    text, same order, same length — lets a caller tell a table-cell-sourced
    block apart from ordinary prose without re-deriving that from the
    block's TEXT (which, post-HTML-table-fix, no longer carries any raw
    tag markup to sniff — see _ground_and_resolve_chunk's table-cell
    pruning, which needed exactly this)."""
    tokens, _ = _get_parser().parse(page_text or "")
    flat = list(_flatten_blocks(tokens))
    return [_block_own_text(b) for b in flat], [b.get("type") for b in flat]


def parse_page_blocks(page_text: str) -> List[str]:
    """Public wrapper over this module's own block parser (_get_parser +
    _flatten_blocks + _block_own_text) — used by ai_reference_index.py to
    ground an AI-reported heading against a page's real text blocks,
    without reaching into three separate private helpers itself."""
    texts, _ = parse_page_blocks_with_types(page_text)
    return texts


def parse_page_blocks_with_row_ids(page_text: str) -> Tuple[List[str], List[str], List[Optional[int]]]:
    """Like parse_page_blocks_with_types, but also returns each block's
    HTML-table row id (None for every non-HTML-table-cell block; a shared
    integer for every cell belonging to the same <tr>, globally unique and
    increasing across multiple separate tables on the page) — lets a caller
    reconstruct a split "number cell + content cell" row as one logical
    unit for matching purposes (see ai_reference_index.py's row-merge
    grounding fallback). A separate, new, opt-in function rather than a
    parameter on parse_page_blocks_with_types, so nothing that already
    calls that function needs to change — this one is used exclusively by
    the AI-grounding path, never by build_targets_for_page's rule-based
    detector, which has no need for it."""
    tokens, _ = _get_parser().parse(page_text or "")
    row_id_counter = itertools.count()
    flat = list(_flatten_blocks(tokens, row_id_counter=row_id_counter))
    texts = [_block_own_text(b) for b in flat]
    types = [b.get("type") for b in flat]
    row_ids = [b.get("_row_id") for b in flat]
    return texts, types, row_ids


def build_layout_annotated_text(clean_text: str, raw_text: Optional[str]) -> str:
    """Builds a page's text annotated with the OCR engine's own per-block
    layout classification — for the AI-assisted indexer's PROMPT INPUT ONLY
    (ai_reference_index.py). Grounding and persistence must keep using the
    plain `clean_text` unchanged, never this annotated form, so a heading
    string the AI echoes back can still be located against real page blocks.

    Sourced entirely from layout_format.parse_blocks(raw_text) — type,
    position, and content all come from that one parse, so there is no
    cross-referencing against clean_text's separately-parsed blocks and
    therefore no alignment to fail. The previous implementation required
    parse_layout_blocks(raw_text)'s block count to match
    parse_page_blocks(clean_text)'s mistune-parsed block count 1:1, falling
    back silently to plain clean_text otherwise — the same alignment
    fragility measured elsewhere at ~38% success on real pages (empty-content
    `image` tags dropped on one side, table-cell splitting mismatched on the
    other). Falls back to `clean_text` only when there's genuinely no tag
    data to work with (raw_text absent, or no parseable blocks at all).

    Each block also gets an `INDENTED` marker when it sits notably right of
    the page's own text margin (layout_format.is_indented) — an additional,
    weighted signal for the model's own judgment, documented in
    ai_indexing/prompt.py's OCR-LAYOUT section as corroborating, never
    overriding."""
    if not raw_text:
        return clean_text
    blocks = layout_format.parse_blocks(raw_text)
    if not blocks:
        return clean_text
    median_x1 = layout_format.page_median_x1(blocks)
    parts = []
    for b in blocks:
        if not b.text:
            continue
        ltype = (b.type or "").strip().upper() or "TEXT"
        if layout_format.is_indented(b, median_x1):
            ltype = f"{ltype}, INDENTED"
        parts.append(f"[OCR-LAYOUT: {ltype}]\n{b.text}")
    return "\n\n".join(parts)


# ─── Mention detection & resolution (server-side badge index) ─────────────────

def collapse_key_fallback(key: str) -> str:
    """Mirrors the frontend collapseKeyFallback / target row collapsed_key:
    strips dots from the numeric part for period-tolerant matching."""
    if ":" not in key:
        return key
    family, _, number = key.partition(":")
    return f"{family}:{number.replace('.', '')}"


def _number_dual_forms(number: str) -> List[str]:
    """Tries integer/`.0` dual forms for numeric cross-family fallback lookup."""
    if number.endswith(".0"):
        return [number, number[:-2]]
    if "." not in number:
        return [number, f"{number}.0"]
    return [number]


class TargetLookupIndex:
    """In-memory index for resolving mention candidate keys against target rows."""

    def __init__(self, targets: Optional[List[Dict[str, Any]]] = None):
        self.by_exact_key: Dict[str, List[Dict[str, Any]]] = {}
        self.by_collapsed_key: Dict[str, Set[str]] = {}
        if targets:
            for t in targets:
                key = t["key"]
                self.by_exact_key.setdefault(key, []).append(t)
                ck = t.get("collapsed_key") or collapse_key_fallback(key)
                self.by_collapsed_key.setdefault(ck, set()).add(key)

    @classmethod
    def from_store_like(cls, data: Any) -> "TargetLookupIndex":
        if isinstance(data, TargetLookupIndex):
            return data
        if isinstance(data, list):
            return cls(data)
        if isinstance(data, dict):
            idx = cls()
            if not data:
                return idx
            first_val = next(iter(data.values()))
            if isinstance(first_val, dict):
                for by_key in data.values():
                    for k, cands in by_key.items():
                        idx.by_exact_key.setdefault(k, []).extend(cands)
                        for c in cands:
                            ck = c.get("collapsed_key") or collapse_key_fallback(k)
                            idx.by_collapsed_key.setdefault(ck, set()).add(k)
            elif isinstance(first_val, list):
                for k, cands in data.items():
                    idx.by_exact_key.setdefault(k, []).extend(cands)
                    for c in cands:
                        ck = c.get("collapsed_key") or collapse_key_fallback(k)
                        idx.by_collapsed_key.setdefault(ck, set()).add(k)
            return idx
        return cls([])

    def has_exact_key(self, key: str) -> bool:
        return key in self.by_exact_key

    def resolve(self, key: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        exact = self.by_exact_key.get(key, [])
        approximate: List[Dict[str, Any]] = []

        candidate_key = key if key in self.by_exact_key else None
        if candidate_key and self.by_exact_key[candidate_key]:
            ck = self.by_exact_key[candidate_key][0].get("collapsed_key") or collapse_key_fallback(key)
        else:
            ck = collapse_key_fallback(key)

        sibling_keys = self.by_collapsed_key.get(ck, set())
        for k in sibling_keys:
            if k != key:
                approximate.extend(self.by_exact_key.get(k, []))

        if ":" in key:
            family, _, number = key.partition(":")
            fallback_families = FAMILY_FALLBACKS.get(family)
            if fallback_families:
                for fam in fallback_families:
                    for n in _number_dual_forms(number):
                        fb_key = f"{fam}:{n}"
                        approximate.extend(self.by_exact_key.get(fb_key, []))

        deduped: List[Dict[str, Any]] = []
        seen = set()
        for c in approximate:
            cid = (
                c.get("key"),
                c.get("page"),
                c.get("anchor_id") if c.get("anchor_id") is not None else c.get("occurrence_index"),
            )
            if cid not in seen:
                seen.add(cid)
                deduped.append(c)

        return exact, deduped


def _strip_html(text: str) -> str:
    if not text:
        return ""
    s = re.sub(r"&nbsp;|&#160;", " ", text, flags=re.IGNORECASE)
    s = html.unescape(s)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _expand_range_continuations(
    keyword: str,
    base_prefix: Optional[str],
    base_number: str,
    sibling_text: str,
) -> Tuple[List[Dict[str, Any]], int]:
    """Port of markdown.js expandRangeContinuations. Decimal-only restriction,
    Oxford comma support, year guard, MAX_RANGE_EXPANSION cap.
    Returns (expanded_list, total_chars_consumed)."""
    if not base_number or _is_roman(base_number):
        return [], 0
    rest = sibling_text
    prev_number = base_number
    prev_prefix = base_prefix
    count = 0
    out = []
    total_consumed = 0

    while count < MAX_RANGE_EXPANSION:
        m = RANGE_CONTINUATION_RE.match(rest)
        if not m:
            break
        matched = m.group(0)
        connector_raw = m.group(1)
        prefix_raw = m.group(2)
        number_raw = m.group(3)
        connector = connector_raw.strip().lower()
        is_bare_comma = connector.startswith(",") and not prefix_raw

        if is_bare_comma and RANGE_CONTINUATION_YEAR_RE.match(number_raw):
            break

        prefix = prefix_raw or prev_prefix or None
        is_to_range = (
            connector == "to"
            and prev_number.isdigit()
            and number_raw.isdigit()
            and prefix == prev_prefix
        )

        if is_to_range:
            start = int(prev_number) + 1
            end = int(number_raw)
            for n in range(start, end + 1):
                if count >= MAX_RANGE_EXPANSION:
                    break
                key = normalize_key(keyword, prefix, str(n))
                raw = f"{keyword} {prefix}{n}" if prefix else f"{keyword} {n}"
                out.append({
                    "keyword": keyword,
                    "prefix": prefix,
                    "number": str(n),
                    "key": key,
                    "raw_text": raw,
                })
                count += 1
        else:
            key = normalize_key(keyword, prefix, number_raw)
            raw = f"{keyword} {prefix}{number_raw}" if prefix else f"{keyword} {number_raw}"
            out.append({
                "keyword": keyword,
                "prefix": prefix,
                "number": number_raw,
                "key": key,
                "raw_text": raw,
            })
            count += 1

        prev_number = number_raw
        prev_prefix = prefix
        total_consumed += len(matched)
        rest = rest[len(matched):]

    return out, total_consumed


def _extract_mention_clean_text(token: Dict[str, Any]) -> str:
    """Extracts text while blanking out codespan, link, image, and block_code
    with spaces to preserve character offsets while enforcing the safety boundary."""
    t = token.get("type")
    if t in ("codespan", "link", "image", "block_code"):
        raw = token.get("raw")
        if raw is not None:
            return " " * len(raw)
        children = token.get("children", [])
        child_len = sum(len(_extract_mention_clean_text(c)) for c in children)
        return " " * max(1, child_len)
    if t == "text":
        return token.get("raw", "")
    if t in ("softbreak", "linebreak"):
        return " "
    children = token.get("children")
    if children:
        return "".join(_extract_mention_clean_text(c) for c in children)
    return ""


def _scan_text_for_mentions(
    text: str,
    target_lookup: TargetLookupIndex,
    block_index: int,
    out: List[Dict[str, Any]],
) -> None:
    pos = 0
    while pos < len(text):
        start_m = MENTION_START_RE.search(text[pos:])
        if not start_m:
            break
        match_index = pos + start_m.start()
        slice_text = text[match_index:]
        m = MENTION_RE.match(slice_text)
        if m:
            raw = m.group(0)
            keyword = m.group("keyword")
            prefix_raw = m.group("prefix")
            number_raw = m.group("number")
            prefix, number = reconcile_prefix_and_number(keyword, prefix_raw, number_raw)
            key = normalize_key(keyword, prefix_raw, number_raw)
            family = _FAMILY_ALIASES.get(keyword.lower().rstrip("."), keyword.lower())
            lookahead = slice_text[len(raw):len(raw) + MENTION_LOOKAHEAD_CHARS]
            before = text[max(0, match_index - 150):match_index]

            suppressed_reason = None
            if family == "page" and PAGE_FOOTER_RE.search(lookahead):
                suppressed_reason = "page_footer"
            elif not target_lookup.has_exact_key(key):
                if family in EXTERNAL_LAW_FALLBACK_GUARD_FAMILIES and EXTERNAL_LAW_SIGNAL_RE.search(lookahead):
                    suppressed_reason = "external_law"
                elif family == "para" and CASE_CITATION_RE.search(lookahead):
                    suppressed_reason = "case_citation"
                elif family == "para" and CASE_CITATION_RE.search(before):
                    suppressed_reason = "case_citation"

            if suppressed_reason is not None:
                resolution = "suppressed"
            else:
                exact, approximate = target_lookup.resolve(key)
                if exact:
                    resolution = "exact"
                elif approximate:
                    resolution = "approximate"
                else:
                    resolution = "suppressed"
                    suppressed_reason = "unresolved"

            occ_idx = len(out)
            out.append({
                "occurrence_index": occ_idx,
                "key": key,
                "raw_text": raw,
                "block_index": block_index,
                "resolution": resolution,
                "suppressed_reason": suppressed_reason,
                "detection_source": "rule",
                "detection_model": None,
            })

            total_consumed = 0
            if suppressed_reason not in ("case_citation", "page_footer", "external_law"):
                cont_text = slice_text[len(raw):len(raw) + 200]
                expanded, total_consumed = _expand_range_continuations(keyword, prefix, number, cont_text)
                for exp in expanded:
                    exp_key = exp["key"]
                    exp_exact, exp_approx = target_lookup.resolve(exp_key)
                    if exp_exact:
                        exp_res = "exact"
                        exp_supp = None
                    elif exp_approx:
                        exp_res = "approximate"
                        exp_supp = None
                    else:
                        exp_res = "suppressed"
                        exp_supp = "unresolved"
                    out.append({
                        "occurrence_index": len(out),
                        "key": exp_key,
                        "raw_text": exp["raw_text"],
                        "block_index": block_index,
                        "resolution": exp_res,
                        "suppressed_reason": exp_supp,
                        "detection_source": "rule",
                        "detection_model": None,
                    })

            pos = match_index + len(raw) + total_consumed
        else:
            pos = match_index + len(start_m.group(0))


def _walk_blocks_from_tokens(tok_list):
    for tok in tok_list:
        t = tok.get("type")
        if t == "block_code":
            continue
        if t == "block_html":
            clean = _strip_html(tok.get("raw", ""))
            if clean:
                yield ("html", clean)
            continue
        if t == "list":
            for child in tok.get("children", []):
                yield from _walk_blocks_from_tokens([child])
            continue
        if t in _CONTAINER_TYPES:
            yield from _walk_blocks_from_tokens(tok.get("children", []))
            continue
        if t in _BLOCK_TYPES:
            clean = _extract_mention_clean_text(tok)
            if clean:
                yield ("text", clean)


def build_docref_index_for_job(
    job_id: str, version: int
) -> Tuple[List[Dict[str, Any]], Dict[int, List[Dict[str, Any]]], Set[Tuple[int, int, int]]]:
    """Whole-job docref scanning, fuzzy-variant reconciliation, and target creation pass.
    The lowest (page, block_index, start) occurrence of a code spanning 2+ pages becomes
    its provisional baseline target; all other occurrences become mentions.
    Single-page codes do not create targets, landing in resolution='undefined'.
    Returns (targets, occurrences_by_page, definition_positions).
    """
    pages = database.get_pages(job_id)
    raw_occurrences: List[Dict[str, Any]] = []

    for page in pages:
        page_num = page["page_num"]
        page_text = page.get("text") or ""
        if _page_is_toc_like(page_num, page_text):
            continue
        tokens, _ = _get_parser().parse(page_text)
        block_idx = 0
        for b_type, b_text in _walk_blocks_from_tokens(tokens):
            clean_b_text = re.sub(r"<\|/?det\|>.*?<\|/?det\|>", "", b_text)
            clean_b_text = re.sub(r"<\|[^>]*\|>", "", clean_b_text)
            for m in DOCREF_MENTION_RE.finditer(clean_b_text):
                label = m.group("label")
                code_raw = m.group("code")
                code = code_raw.strip().rstrip(".,;:-/ ")
                if not _is_plausible_docref_code(code, label):
                    continue
                start_char = m.start()
                raw_match = m.group(0)
                snippet_start = max(0, start_char - 40)
                snippet_end = min(len(clean_b_text), start_char + len(raw_match) + 60)
                snippet = clean_b_text[snippet_start:snippet_end].replace("\n", " ").strip()
                raw_occurrences.append({
                    "page": page_num,
                    "block_index": block_idx,
                    "start": start_char,
                    "label": label,
                    "code": code,
                    "raw_text": raw_match,
                    "snippet": snippet,
                })
            block_idx += 1

    if not raw_occurrences:
        database.upsert_docref_targets_for_job(job_id, [], version)
        return [], {}, set()

    dominant_map, resolution_map = reconcile_docref_variants([occ["code"] for occ in raw_occurrences])

    by_key: Dict[str, List[Dict[str, Any]]] = {}
    for occ in raw_occurrences:
        norm = normalize_docref_code(occ["code"])
        canonical_norm = dominant_map.get(norm, norm)
        canonical_key = f"docref:{canonical_norm.lower()}"
        occ["key"] = canonical_key
        occ["canonical_norm"] = canonical_norm
        occ["resolution"] = resolution_map.get(norm, "exact")
        by_key.setdefault(canonical_key, []).append(occ)

    targets: List[Dict[str, Any]] = []
    definition_positions: Set[Tuple[int, int, int]] = set()

    for key, occs in by_key.items():
        unique_pages = {o["page"] for o in occs}
        if len(unique_pages) >= 2:
            occs.sort(key=lambda o: (o["page"], o["block_index"], o["start"]))
            first = occs[0]
            definition_positions.add((first["page"], first["block_index"], first["start"]))
            targets.append({
                "key": key,
                "page": first["page"],
                "occurrence_index": 0,
                "anchor_id": f"target-{key.replace(':', '-')}-{first['page']}",
                "snippet": first["snippet"],
                "truncated": False,
                "continues_on_page": None,
                "scope_key": None,
                "scope_label": None,
                "scope_provisional": 0,
                "collapsed_key": key,
                "fingerprint": first["raw_text"],
                "scope_chain": [],
                "heading_confidence": "high",
                "heading_confidence_reasons": [],
                "own_label": first["raw_text"],
                "detection_source": "rule",
                "detection_model": None,
                "start_line_hint": None,
            })

    database.upsert_docref_targets_for_job(job_id, targets, version)

    occurrences_by_page: Dict[int, List[Dict[str, Any]]] = {}
    for occ in raw_occurrences:
        occurrences_by_page.setdefault(occ["page"], []).append(occ)

    return targets, occurrences_by_page, definition_positions


# A term/abbreviation whose definition+mention page coverage exceeds this
# share of the job is dropped entirely -- real case: a 955-page arbitration
# paper book's repeated bank-guarantee proforma defines "Equipment",
# "Contract", and "Contractor" via "hereinafter referred to as", each
# spanning 30-45% of the document. Technically a correct definition, but a
# badge on a third of every page carries no navigational value. Abbreviations
# like GCC/SCC/NIT/ITB all sit at 6-8% coverage in the same document and are
# unaffected by this cap.
TERM_MAX_PAGE_COVERAGE = 0.30
# Only enforced once the job has at least this many pages -- on a short job,
# 30% is one or two pages, and a term legitimately used throughout a 5-page
# document would be wrongly suppressed.
TERM_MAX_PAGE_COVERAGE_MIN_JOB_SIZE = 20


def build_term_index_for_job(
    job_id: str, version: int
) -> Tuple[List[Dict[str, Any]], Dict[int, List[Dict[str, Any]]], Set[Tuple[int, int, int]]]:
    """Whole-job defined term scanning and target creation pass.
    Discovers (ABBR) definitions validated via _is_acronym_of, plus 'hereinafter referred to as'.
    Lowest (page, block_index, start) becomes target.
    Terms whose page coverage exceeds TERM_MAX_PAGE_COVERAGE (see above) are
    dropped entirely -- no target, no mentions.
    Returns (targets, occurrences_by_page, definition_positions).
    """
    pages = database.get_pages(job_id)
    total_pages = len(pages)
    definitions: List[Dict[str, Any]] = []

    for page in pages:
        page_num = page["page_num"]
        page_text = page.get("text") or ""
        if _page_is_toc_like(page_num, page_text):
            continue
        tokens, _ = _get_parser().parse(page_text)
        block_idx = 0
        for b_type, b_text in _walk_blocks_from_tokens(tokens):
            clean_b_text = re.sub(r"<\|/?det\|>.*?<\|/?det\|>", "", b_text)
            clean_b_text = re.sub(r"<\|[^>]*\|>", "", clean_b_text)

            # 1. Parenthetical abbreviations
            for m in PAREN_ABBR_RE.finditer(clean_b_text):
                full = m.group("full").strip()
                abbr = m.group("abbr").strip()
                if _is_acronym_of(abbr, full):
                    key = f"term:{re.sub(r'[^A-Za-z0-9]', '', abbr).lower()}"
                    start_char = m.start()
                    snippet = clean_b_text[max(0, start_char - 40):min(len(clean_b_text), start_char + len(m.group(0)) + 60)].replace("\n", " ").strip()
                    definitions.append({
                        "key": key,
                        "term": abbr,
                        "page": page_num,
                        "block_index": block_idx,
                        "start": start_char,
                        "raw_text": m.group(0),
                        "snippet": snippet,
                        "is_abbr": True,
                    })

            # 2. Hereinafter definitions
            for m in HEREINAFTER_RE.finditer(clean_b_text):
                term = m.group("term").strip().strip("\"'“”‘’")
                if term:
                    norm = normalize_term_key(term)
                    key = f"term:{norm}"
                    start_char = m.start()
                    snippet = clean_b_text[max(0, start_char - 40):min(len(clean_b_text), start_char + len(m.group(0)) + 60)].replace("\n", " ").strip()
                    definitions.append({
                        "key": key,
                        "term": term,
                        "page": page_num,
                        "block_index": block_idx,
                        "start": start_char,
                        "raw_text": m.group(0),
                        "snippet": snippet,
                        "is_abbr": False,
                    })
            block_idx += 1

    if not definitions:
        database.upsert_term_targets_for_job(job_id, [], version)
        return [], {}, set()

    by_key: Dict[str, List[Dict[str, Any]]] = {}
    for d in definitions:
        by_key.setdefault(d["key"], []).append(d)

    definition_positions: Set[Tuple[int, int, int]] = set()
    first_by_key: Dict[str, Dict[str, Any]] = {}
    for key, def_list in by_key.items():
        def_list.sort(key=lambda d: (d["page"], d["block_index"], d["start"]))
        first = def_list[0]
        first_by_key[key] = first
        definition_positions.add((first["page"], first["block_index"], first["start"]))

    # Bare mention scanning across pages -- done BEFORE targets are built so
    # that total (definition + mention) page coverage per key is known up
    # front and can gate which keys actually survive (see
    # TERM_MAX_PAGE_COVERAGE above).
    term_specs = []
    for key, first in first_by_key.items():
        term_text = first["term"]
        if first["is_abbr"]:
            pat = re.compile(rf"\b{re.escape(term_text)}\b")
        else:
            pat = re.compile(rf"\b{re.escape(term_text)}\b", re.IGNORECASE)
        term_specs.append((key, term_text, pat))

    raw_occurrences_by_key: Dict[str, List[Dict[str, Any]]] = {}
    pages_seen_by_key: Dict[str, Set[int]] = {key: {d["page"] for d in def_list} for key, def_list in by_key.items()}

    for page in pages:
        page_num = page["page_num"]
        page_text = page.get("text") or ""
        if _page_is_toc_like(page_num, page_text):
            continue
        tokens, _ = _get_parser().parse(page_text)
        block_idx = 0
        for b_type, b_text in _walk_blocks_from_tokens(tokens):
            clean_b_text = re.sub(r"<\|/?det\|>.*?<\|/?det\|>", "", b_text)
            clean_b_text = re.sub(r"<\|[^>]*\|>", "", clean_b_text)
            for key, term_text, pat in term_specs:
                for m in pat.finditer(clean_b_text):
                    start_char = m.start()
                    pages_seen_by_key[key].add(page_num)
                    raw_occurrences_by_key.setdefault(key, []).append({
                        "key": key,
                        "page": page_num,
                        "block_index": block_idx,
                        "start": start_char,
                        "raw_text": m.group(0),
                        "resolution": "exact",
                    })
            block_idx += 1

    if total_pages >= TERM_MAX_PAGE_COVERAGE_MIN_JOB_SIZE:
        max_pages = TERM_MAX_PAGE_COVERAGE * total_pages
        survivors = {key for key, pg_set in pages_seen_by_key.items() if len(pg_set) <= max_pages}
    else:
        survivors = set(by_key.keys())

    targets: List[Dict[str, Any]] = []
    for key, first in first_by_key.items():
        if key not in survivors:
            definition_positions.discard((first["page"], first["block_index"], first["start"]))
            continue
        targets.append({
            "key": key,
            "page": first["page"],
            "occurrence_index": 0,
            "anchor_id": f"target-{key.replace(':', '-')}-{first['page']}",
            "snippet": first["snippet"],
            "truncated": False,
            "continues_on_page": None,
            "scope_key": None,
            "scope_label": None,
            "scope_provisional": 0,
            "collapsed_key": key,
            "fingerprint": first["raw_text"],
            "scope_chain": [],
            "heading_confidence": "high",
            "heading_confidence_reasons": [],
            "own_label": first["raw_text"],
            "detection_source": "rule",
            "detection_model": None,
            "start_line_hint": None,
        })

    database.upsert_term_targets_for_job(job_id, targets, version)

    occurrences_by_page: Dict[int, List[Dict[str, Any]]] = {}
    for key, occs in raw_occurrences_by_key.items():
        if key not in survivors:
            continue
        for occ in occs:
            if (occ["page"], occ["block_index"], occ["start"]) in definition_positions:
                continue
            occurrences_by_page.setdefault(occ["page"], []).append(occ)

    return targets, occurrences_by_page, definition_positions


def detect_mentions_for_page(
    page_text: str,
    page_num: int,
    target_index: Any,
    definition_positions: Optional[Set[Tuple[int, int, int]]] = None,
    docref_page_occurrences: Optional[List[Dict[str, Any]]] = None,
    term_page_occurrences: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Detects and resolves mentions on page_num against target_index.
    Returns list of mention dicts with page, occurrence_index, key, raw_text,
    block_index, resolution ('exact'|'approximate'|'suppressed'|'undefined'), suppressed_reason."""
    lookup = TargetLookupIndex.from_store_like(target_index)
    tokens, _ = _get_parser().parse(page_text or "")
    out: List[Dict[str, Any]] = []

    block_idx = 0
    for b_type, b_text in _walk_blocks_from_tokens(tokens):
        _scan_text_for_mentions(b_text, lookup, block_idx, out)
        block_idx += 1

    # 1. Process docref mentions
    if docref_page_occurrences is not None:
        for occ in docref_page_occurrences:
            pos_tuple = (page_num, occ["block_index"], occ["start"])
            if definition_positions and pos_tuple in definition_positions:
                continue
            key = occ["key"]
            exact, approx = lookup.resolve(key)
            if exact or approx:
                resolution = occ.get("resolution", "exact" if exact else "approximate")
                suppressed_reason = None
            else:
                resolution = "undefined"
                suppressed_reason = "unresolved"
            out.append({
                "occurrence_index": len(out),
                "key": key,
                "raw_text": occ["raw_text"],
                "block_index": occ["block_index"],
                "resolution": resolution,
                "suppressed_reason": suppressed_reason,
                "detection_source": "rule",
                "detection_model": None,
            })
    else:
        block_idx = 0
        for b_type, b_text in _walk_blocks_from_tokens(tokens):
            clean_b_text = re.sub(r"<\|/?det\|>.*?<\|/?det\|>", "", b_text)
            clean_b_text = re.sub(r"<\|[^>]*\|>", "", clean_b_text)
            for m in DOCREF_MENTION_RE.finditer(clean_b_text):
                label = m.group("label")
                code_raw = m.group("code")
                code = code_raw.strip().rstrip(".,;:-/ ")
                if not _is_plausible_docref_code(code, label):
                    continue
                start_char = m.start()
                pos_tuple = (page_num, block_idx, start_char)
                if definition_positions and pos_tuple in definition_positions:
                    continue
                key = docref_key(code)
                exact, approx = lookup.resolve(key)
                if exact:
                    resolution = "exact"
                    suppressed_reason = None
                elif approx:
                    resolution = "approximate"
                    suppressed_reason = None
                else:
                    resolution = "undefined"
                    suppressed_reason = "unresolved"
                out.append({
                    "occurrence_index": len(out),
                    "key": key,
                    "raw_text": m.group(0),
                    "block_index": block_idx,
                    "resolution": resolution,
                    "suppressed_reason": suppressed_reason,
                    "detection_source": "rule",
                    "detection_model": None,
                })
            block_idx += 1

    # 2. Process term mentions
    if term_page_occurrences is not None:
        for occ in term_page_occurrences:
            pos_tuple = (page_num, occ["block_index"], occ["start"])
            if definition_positions and pos_tuple in definition_positions:
                continue
            key = occ["key"]
            exact, approx = lookup.resolve(key)
            if exact or approx:
                resolution = "exact" if exact else "approximate"
                suppressed_reason = None
            else:
                resolution = "undefined"
                suppressed_reason = "unresolved"
            out.append({
                "occurrence_index": len(out),
                "key": key,
                "raw_text": occ["raw_text"],
                "block_index": occ["block_index"],
                "resolution": resolution,
                "suppressed_reason": suppressed_reason,
                "detection_source": "rule",
                "detection_model": None,
            })
    else:
        term_keys = [k for k in lookup.by_exact_key.keys() if k.startswith("term:")]
        if term_keys:
            block_idx = 0
            for b_type, b_text in _walk_blocks_from_tokens(tokens):
                clean_b_text = re.sub(r"<\|/?det\|>.*?<\|/?det\|>", "", b_text)
                clean_b_text = re.sub(r"<\|[^>]*\|>", "", clean_b_text)
                for tk in term_keys:
                    term_name = tk[5:]
                    if len(term_name) <= 4:
                        pat = re.compile(rf"\b{re.escape(term_name.upper())}\b")
                    else:
                        pat = re.compile(rf"\b{re.escape(term_name)}\b", re.IGNORECASE)
                    for m in pat.finditer(clean_b_text):
                        start_char = m.start()
                        pos_tuple = (page_num, block_idx, start_char)
                        if definition_positions and pos_tuple in definition_positions:
                            continue
                        out.append({
                            "occurrence_index": len(out),
                            "key": tk,
                            "raw_text": m.group(0),
                            "block_index": block_idx,
                            "resolution": "exact",
                            "suppressed_reason": None,
                            "detection_source": "rule",
                            "detection_model": None,
                        })
                block_idx += 1

    for m in out:
        m["page"] = page_num
    return out


def build_mention_index_for_job(job_id: str) -> None:
    """Whole-job mention detection pass: executes once target indexing is complete.
    Builds docref and defined-term targets across the job, then builds mentions
    for each page and persists them into ocr_reference_mentions.

    Registers itself in `_active_reindex_jobs` for the call's duration (reentrant-
    safe: only discards it if THIS call was the one that added it) so
    `is_reindex_active` — which the manual /references/revert-to-rule and
    /references/build-mentions endpoints check before running — sees this build
    as in-flight, guarding against two manual triggers (e.g. a rapid double
    click of "Build Index & Citations") racing each other. The AI-indexing path
    already calls this from inside reindex_job's own registration, so the
    self-guard must not clear that outer registration early."""
    already_active = job_id in _active_reindex_jobs
    if not already_active:
        _active_reindex_jobs.add(job_id)
    try:
        # Bumped, not just read: the frontend's "Rebuilding..." state only
        # clears once reference_index_version advances past the value it
        # captured at click time (see AppShell.jsx's reindexState), and the
        # incremental GET .../references?since_version= delta fetch only
        # returns mentions whose written_at_version is strictly greater than
        # what the client already has — both silently never fire if this
        # reused the current, un-bumped version instead (confirmed as a real
        # stuck-"Rebuilding…"-forever bug via Playwright, not theoretical).
        version = database.bump_reference_index_version(job_id)
        docref_targets, docref_occs_by_page, def_pos_docref = build_docref_index_for_job(job_id, version)
        term_targets, term_occs_by_page, def_pos_term = build_term_index_for_job(job_id, version)
        definition_positions = def_pos_docref | def_pos_term

        targets = database.get_reference_targets_for_job(job_id)
        target_lookup = TargetLookupIndex(targets)
        pages = database.get_pages(job_id)

        for page in pages:
            page_num = page["page_num"]
            page_text = page.get("text") or ""
            mentions = detect_mentions_for_page(
                page_text,
                page_num,
                target_lookup,
                definition_positions=definition_positions,
                docref_page_occurrences=docref_occs_by_page.get(page_num, []),
                term_page_occurrences=term_occs_by_page.get(page_num, []),
            )
            database.upsert_reference_mentions_for_page(job_id, page_num, mentions, version)
    finally:
        if not already_active:
            _active_reindex_jobs.discard(job_id)


_PLEADING_AVERMENT_RE = re.compile(
    r"^\s*(?:\(?\d+[\.\)]|\(?[a-z][\.\)]|\(?[ivxlcdm]+[\.\)])\s+"
    r"(?:That\s+|(?:The\s+)?(?:petitioner|appellant|respondent|applicant|plaintiff|defendant|deponent)\b|"
    r"Rejoinder\b|Arguments\s+have\s+been|It\s+has\s+been\s+argued)",
    re.IGNORECASE | re.MULTILINE,
)


def detect_pleading_points_for_page(
    page_text: str, scope: Dict[str, Any], existing_candidates: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, str]]]:
    """Augments an already-detected page (AI or rule) with numbered/roman
    pleading paragraphs ("1. That the appellant is filing...") as indexed
    leaf `point`/`ground` entries — purely additive, deterministic, reusing
    GROUND_DIGIT_RE/GROUND_ROMAN_RE exactly as build_targets_for_page's own
    ground-detection branch does. The AI prompt is deliberately never asked
    to report these itself (a numbered sentence with a finite verb is
    correctly excluded there as "not a heading"); this is the server-side
    mechanism that actually indexes them.

    Returns (results, last_decimal_ground) — the caller must fold the
    returned `last_decimal_ground` back into the persisted scope for the
    NEXT page. This function is very often the ONLY place a decimal ground/
    point ever gets detected at all (the AI, per its prompt, is never asked
    to report these itself) — confirmed real case: ground 10's own decimal
    heading was found here, not by the AI, so if this function's finding
    didn't propagate forward, page 19's AI-reported roman sub-grounds had
    nothing to nest under at all.

    `scope` is this page's own END-OF-PAGE scope (i.e. after whatever
    process built `existing_candidates` has already run) — a documented
    simplification: a container opened and closed entirely within this one
    page won't be reflected for blocks that came before it closed within
    that same page. Flagged here rather than silently assumed correct.

    `existing_candidates` are the candidate rows already produced for this
    page — used both to skip a block already classified as a real heading
    (fingerprint dedup) and to continue this page's own per-key occurrence
    numbering rather than restarting at 0 and colliding with an existing
    row for the same key."""
    nested_stack = (scope or {}).get("stack") or []
    if _immediate_parent_is_listing_container(nested_stack):
        return [], (scope or {}).get("last_decimal_ground")

    # Contract/outline containers never inherit pleading points
    if nested_stack and (
        nested_stack[0].get("family") in ("article", "section", "clause", "annexure", "contract", "schedule", "appendix")
        or any(
            s.get("family") in ("article", "section", "clause", "annexure", "contract", "schedule", "appendix")
            or s.get("key", "").startswith(("annexure:", "section:", "article:", "contract:", "schedule:", "appendix:"))
            for s in nested_stack
        )
    ):
        return [], (scope or {}).get("last_decimal_ground")

    if _ground_scope_active(nested_stack):
        family = "ground"
    elif _pleading_scope_active(nested_stack):
        family = "point"
    elif _PLEADING_AVERMENT_RE.search(page_text) and not any(
        s.get("family") in ("article", "section", "clause", "annexure", "contract", "schedule", "appendix")
        for s in nested_stack
    ):
        family = "point"
    else:
        return [], (scope or {}).get("last_decimal_ground")

    already_claimed_fingerprints = {c.get("fingerprint") for c in existing_candidates if c.get("fingerprint")}
    occurrence_counts: Dict[str, int] = {}
    for c in existing_candidates:
        k = c.get("key")
        if k:
            occurrence_counts[k] = occurrence_counts.get(k, 0) + 1

    # Inherited from apply_heading_events_to_scope's own tracking (updated
    # there when the AI itself reports a decimal ground/point) — a roman
    # match found here nests under it the same way build_targets_for_page's
    # main loop does, instead of landing flat as an unrelated sibling.
    last_decimal_ground: Optional[Dict[str, str]] = (scope or {}).get("last_decimal_ground")

    results: List[Dict[str, Any]] = []
    in_distribution_list = False
    for block_text in parse_page_blocks(page_text):
        text = (block_text or "").strip()
        if not text:
            continue
        if _DISTRIBUTION_INTRO_RE.search(text) or _DISTRIBUTION_LIST_INTRO_RE.search(text):
            in_distribution_list = True
            continue
        if in_distribution_list or _DISTRIBUTION_RECIPIENT_RE.match(text):
            continue
        fingerprint = text[:30]
        if fingerprint in already_claimed_fingerprints:
            continue
        dm = GROUND_DIGIT_RE.match(text)
        if dm and _is_date_shaped_number(dm.group("number")):
            dm = None
        gm = dm or GROUND_ROMAN_RE.match(text)
        if not gm:
            continue
        if _is_backward_reference(text, gm.end()):
            continue

        number_raw = gm.group("number")
        is_roman = _is_roman(number_raw)
        if is_roman:
            # Same collision build_targets_for_page's main loop guards
            # against: normalize_key would convert a roman ground/point to
            # its arabic value, colliding with a real decimal ground/point
            # of the same number in the same scope.
            key = f"{family}:{number_raw.lower()}"
        else:
            key = normalize_key(family, None, number_raw)
        occ = occurrence_counts.get(key, 0)
        occurrence_counts[key] = occ + 1
        confidence_reasons = _citation_confidence_reasons(text, gm.end())

        effective_stack = nested_stack
        if is_roman and last_decimal_ground:
            base_stack = last_decimal_ground.get("stack") or nested_stack
            effective_stack = [dict(s) for s in base_stack] + [
                {"family": family, "key": last_decimal_ground["key"], "label": last_decimal_ground["label"]}
            ]

        results.append({
            "key": key,
            "occurrence_index": occ,
            "snippet": text[:400],
            "truncated": False,
            "continues_on_page": None,
            "scope_key": "/".join(s["key"] for s in effective_stack),
            "scope_label": " › ".join(s["label"] for s in effective_stack),
            "scope_chain": [dict(s) for s in effective_stack],
            "collapsed_key": collapse_key(key),
            "fingerprint": fingerprint,
            "heading_confidence": "low" if confidence_reasons else "high",
            "heading_confidence_reasons": confidence_reasons,
            "own_label": text[:80] if family == "point" else None,
            "detection_source": "rule",
            "detection_model": None,
            "start_line_hint": None,
        })

        if not is_roman:
            last_decimal_ground = {"key": key, "label": _format_label_from_key(key)}
    return results, last_decimal_ground


_CLAUSE_NARRATIVE_VERB_RE = re.compile(
    r"\b(?:shall|agrees?|is|are|was|were|will|undertakes?|constitutes?|guarantees?|means?|placed|contained)\b",
    re.IGNORECASE,
)


def detect_contract_clauses_for_page(
    page_text: str, scope: Dict[str, Any], existing_candidates: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Augments an AI-indexed page with multi-segment decimal clauses (e.g. '1.11.8',
    '4.1', '24.3.2') and sequential letter points ('a.', 'b.') that the AI prompt
    deliberately excludes as running prose, but which form canonical structural
    clauses of commercial contracts and tenders.
    """
    nested_stack = (scope or {}).get("stack") or []
    if _ground_scope_active(nested_stack) or _pleading_scope_active(nested_stack):
        return []
    if any(_STANDALONE_LEGAL_DOCS_RE.search(s.get("label") or "") for s in nested_stack):
        return []

    is_contract = any(
        s.get("family") in ("section", "annexure", "schedule", "article", "clause", "subdoc")
        for s in nested_stack
    )
    if not is_contract and not _NON_PLEADING_SUBDOC_LABELS_RE.search(page_text):
        return []

    is_under_annexure = any(
        s.get("family") == "annexure" or "annexure" in s.get("key", "")
        for s in nested_stack
    )

    already_claimed_fps = {c.get("fingerprint") for c in existing_candidates if c.get("fingerprint")}
    existing_keys = {c.get("key") for c in existing_candidates if c.get("key")}

    occurrence_counts: Dict[str, int] = {}
    for c in existing_candidates:
        k = c.get("key")
        if k:
            occurrence_counts[k] = occurrence_counts.get(k, 0) + 1

    results: List[Dict[str, Any]] = []
    blocks = parse_page_blocks(page_text)

    # 1. Multi-segment decimal clauses (^\d+(\.\d+)+) and bare single-integer
    # clauses (^\d+\.?\s). A multi-segment decimal ("24.2.1") is already a
    # strong enough structural signal on its own; a bare single integer
    # ("1.", "5.") is NOT — that shape is indistinguishable from an ordinary
    # price-schedule/data-table row number, so it additionally requires a
    # genuine finite verb/modal (_looks_like_tabular_data_shape == False,
    # the same content-shape signal the D6/D7 listing-container gate below
    # uses) before being trusted as a real clause.
    for block_text in blocks:
        for line in (block_text or "").split("\n"):
            text = line.strip()
            if not text:
                continue
            if _immediate_parent_is_listing_container(nested_stack, text):
                continue
            fp = text[:30]
            if fp in already_claimed_fps:
                continue

            m = _DECIMAL_NUMBER_HEAD_RE.match(text)
            if not m:
                continue
            number = m.group("number")
            if _is_date_shaped_number(number):
                continue
            is_multiseg = "." in number
            if not is_multiseg and _looks_like_tabular_data_shape(text):
                continue
            if _is_backward_reference(text, m.end()):
                continue
            if _is_external_law(text):
                continue

            label_part = text[m.end():].strip()
            if (is_under_annexure or not is_contract) and is_multiseg:
                if len(label_part.split()) > 8 or len(label_part) > 60 or _CLAUSE_NARRATIVE_VERB_RE.search(label_part):
                    continue

            key = f"numheading:{number}"
            if key in existing_keys:
                continue

            occ = occurrence_counts.get(key, 0)
            occurrence_counts[key] = occ + 1
            existing_keys.add(key)
            already_claimed_fps.add(fp)

            own_label = f"{number} {label_part[:80]}".strip() if label_part else number

            results.append({
                "key": key,
                "occurrence_index": occ,
                "snippet": text[:400],
                "truncated": False,
                "continues_on_page": None,
                "scope_key": "/".join(s["key"] for s in nested_stack),
                "scope_label": " › ".join(s["label"] for s in nested_stack),
                "scope_chain": [dict(s) for s in nested_stack],
                "collapsed_key": collapse_key(key),
                "fingerprint": fp,
                "heading_confidence": "high",
                "heading_confidence_reasons": ["hybrid_decimal_supplemented"],
                "own_label": own_label,
                "detection_source": "rule",
                "detection_model": None,
                "start_line_hint": None,
            })

    # 2. Sequential letter points (a., b., c.) under active contract clauses
    for block_text in blocks:
        for line in (block_text or "").split("\n"):
            text = line.strip()
            if not text:
                continue
            if _immediate_parent_is_listing_container(nested_stack, text):
                continue
            fp = text[:30]
            if fp in already_claimed_fps:
                continue

            lm = re.match(r"^\s*(?P<marker>[a-z])[\.\)]\s+(?P<bodyText>[A-Z].*)", text)
            if not lm:
                continue

            marker = lm.group("marker").lower()
            key = f"point:{marker}"
            occ = occurrence_counts.get(key, 0)
            occurrence_counts[key] = occ + 1
            already_claimed_fps.add(fp)

            results.append({
                "key": key,
                "occurrence_index": occ,
                "snippet": text[:400],
                "truncated": False,
                "continues_on_page": None,
                "scope_key": "/".join(s["key"] for s in nested_stack),
                "scope_label": " › ".join(s["label"] for s in nested_stack),
                "scope_chain": [dict(s) for s in nested_stack],
                "collapsed_key": collapse_key(key),
                "fingerprint": fp,
                "heading_confidence": "high",
                "heading_confidence_reasons": ["hybrid_letter_point_supplemented"],
                "own_label": text[:80].strip(),
                "detection_source": "rule",
                "detection_model": None,
                "start_line_hint": None,
            })

    return results


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

    core_m = CORE_RE.match(stripped) or _SUBDOC_HYPHEN_RE.match(stripped)
    if core_m:
        keyword = core_m.group("keyword")
        family = _FAMILY_ALIASES.get(keyword.lower().rstrip("."), keyword.lower())
        suffix = _match_subdoc_trailing_suffix(family, stripped[core_m.end():])
        key = normalize_key(keyword, core_m.group("prefix"), core_m.group("number"), suffix)
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
    if dm and not _is_date_shaped_number(dm.group("number")):
        number = dm.group("number")
        label_part = stripped[dm.end():].strip()
        # Must have a substantive label (e.g. "1.1 SCOPE" or "2.0 CONTRACT DOCUMENTS").
        # A bare number ("1.2") without a label is rejected.
        if len(label_part) >= 2:
            segments = _numseg(number)
            key = f"numheading:{number}"
            own_label = f"{number} {label_part}"
            return "numheading", key, own_label, segments, boundary_ordinal

    lm = re.match(r"^\s*(?P<letter>[A-Z])\.\s+(?P<label>.+)$", stripped)
    if lm:
        letter = lm.group("letter")
        key = f"letterheading:{letter}"
        return "letterheading", key, f"{letter}. {lm.group('label').strip()}", None, boundary_ordinal

    # Gated on family_hint, not a bare shape match: _SUBLIST_MARKER_RE matches
    # ANY lettered/roman-prefixed line ("(A) ...", "(i) ...", "a. ..."), which
    # is also exactly the shape apply_heading_events_to_scope's forced_family
    # mechanism conservatively falls through to the generic subdoc bucket for
    # whenever _resolve_ground_or_point_match can't confidently place it (no
    # active ground/pleading scope, or the text doesn't fit that resolver's
    # shape). A bare `sm` match with no family_hint used to also fire this
    # branch — confirmed regression (tests/test_ai_reference_index_stack_machine.py,
    # 3 cases): it intercepted genuine real sub-headings outside any
    # grounds/pleading/colon-recovery context (e.g. "(i)" under a plain
    # "Article 1", or a sequential-letter run under "GROUNDS OF APPEAL" that
    # the ground/point resolver itself declined to resolve) and flattened them
    # to `point` leaves instead of letting them fall through to become real
    # containers — exactly the fallback the surrounding design already relies
    # on. family_hint is only ever set by the AI itself or by
    # _recover_colon_sublists_in_chunk's synthesized headings, so requiring it
    # restricts this branch to what it was actually built for.
    sm = _SUBPOINT_ROMAN_RE.match(stripped) or _SUBLIST_MARKER_RE.match(stripped)
    if family_hint in ("point", "subpoint") and (number_hint or sm):
        marker = number_hint.lower() if number_hint else (
            sm.group("numeral").lower() if ("numeral" in sm.groupdict() and sm.group("numeral")) else sm.group("marker").lower()
        )
        key = f"point:{marker}"
        own_label = stripped
        return "point", key, own_label, None, boundary_ordinal

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
    # Slug the disambiguated label (see build_targets_for_page's matching
    # comment) so two distinct AI-reported headings sharing identical text
    # don't collide into the same key.
    label = _disambiguate_label(stripped[:60], label_occurrence_counts)
    key = _slugify_subdoc_label(label)
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


# AI-reported events carrying any of these reasons (see
# ai_reference_index.locate_heading_block/_ground_and_resolve_chunk) either
# failed to ground convincingly against real page text, or ground cleanly
# but onto text immediately preceded by a "which reads as under:"-style
# intro (a clause quoted verbatim mid-argument, not a real heading — the
# AI-path analogue of build_targets_for_page's own possible_reproduced_text
# signal, since the model doesn't always apply its own quoted-text
# instruction correctly). Either way, this heading must never be trusted as
# a stack anchor (see apply_heading_events_to_scope's skip_stack_mutation
# gate, mirroring the rule engine's own "a flagged match does neither pop
# nor push" precedent at build_targets_for_page).
#
# Split into two tiers, not one flat set — confirmed real bug the split
# closes: "ai_unlocated"/"ai_fuzzy_grounded" only mean the GROUNDING step
# couldn't pin down exactly WHERE on the page the heading's text sits, not
# that the heading itself is suspect. A genuine, confirmed-real heading
# ("Annexure IV: Format for Price Schedule", "Annexure IX: Guidelines for
# E-Procurement System") can easily need fuzzy/unlocated grounding purely
# from markdown/OCR formatting noise — blocking it from ever becoming an
# ancestor left it permanently un-pushed, so every subsequent heading
# (including its own rightful children) kept accruing under whichever
# PREVIOUS container was last genuinely open, cascading for dozens of
# pages until a later sibling happened to ground cleanly again. Confirmed
# on two real documents: a 135-page tender spec (Annexure IV/IX, each
# swallowing everything up to the next cleanly-grounded Annexure) and a
# 100-page arbitration appeal (an Affidavit nested under the unrelated
# Grounds of Appeal, an Annexure nested under a Judgment that had already
# concluded). `_POSITIONAL_ONLY_PUSH_BLOCKING_REASONS` are exempted
# whenever `is_subdoc_root_candidate` independently confirms — from the
# heading's own TEXT SHAPE, never its grounding confidence — that it's a
# genuine document-boundary keyword/family (annexure/schedule/appendix,
# EXHIBIT/ATTACHMENT/etc., a standalone legal instrument, or a sibling of
# the open root sharing its own numbered keyword): that's independent,
# stronger evidence than positional-grounding confidence can ever provide.
_POSITIONAL_ONLY_PUSH_BLOCKING_REASONS = {"ai_unlocated", "ai_fuzzy_grounded"}
_GROUNDING_PUSH_BLOCKING_REASONS = _POSITIONAL_ONLY_PUSH_BLOCKING_REASONS | {
    # A clause/sub-clause quoted VERBATIM mid-argument (a pleading citing
    # a contract term "which reads as under: ...") is real text but not a
    # genuine boundary of THIS document's own outline — unconditional
    # regardless of family, since the family heuristic has no way to tell
    # "this is a real structural heading" from "this is a faithful quote
    # of one" (both look identical in text shape).
    "possible_reproduced_text",
    # A table row/entry must only ever be a leaf, never a parent — added
    # here (rather than relying solely on the D6/D7 content-shape
    # heuristic above, which can be imperfect) so this invariant is
    # UNIVERSAL and UNCONDITIONAL: any heading grounded to a table_cell
    # block (see _ground_and_resolve_chunk's event-building, which attaches
    # this reason) is structurally prevented from becoming a stack-mutating
    # ancestor, regardless of family or how the shape heuristic judges it.
    # This substantially de-risks that heuristic — even where it's wrong
    # (e.g. the Section-I short-field-row limitation), the failure mode is
    # now contained to "one extra/missing isolated leaf target," never
    # "real subsequent content wrongly absorbed as this row's children."
    "table_row_sourced",
}

_ENUM_MARKER_RE = re.compile(r"^\s*\(?(?P<marker>[0-9]+(?:\.[0-9]+)*|[A-Za-z]+)(?:\)\.?|\.)\s+")


def _enumeration_marker_style(text: str) -> Optional[str]:
    """Classifies a heading's leading enumeration marker into a coarse
    STYLE bucket only ("decimal", "lower_roman", "upper_letter_or_roman")
    — not its exact value, so "(i)" and "(ii)" match without needing to
    know they're 1 and 2. Used only as a safety net for events that already
    went through mint_key_for_heading's generic path (forced_family is
    None) — a real ground/point leaf already has its own correct,
    value-aware nesting via last_decimal_ground/effective_stack and never
    needs this coarser check."""
    m = _ENUM_MARKER_RE.match(text or "")
    if not m:
        return None
    marker = m.group("marker")
    if marker[0].isdigit():
        return "decimal"
    if not marker.isalpha():
        return None
    if marker.islower():
        return "lower_roman"
    return "upper_letter_or_roman"


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
    # AI-path analogue of build_targets_for_page's last_decimal_ground: the
    # most recent DECIMAL ground/point reported in the current scope — a
    # roman-numeral ground/point that follows nests under it (synthetically,
    # never pushed onto the real stack — ground/point stay leaves) instead
    # of landing flat as an unrelated sibling.
    last_decimal_ground: Optional[Dict[str, Any]] = scope.get("last_decimal_ground")
    outer_decimal_ground: Optional[Dict[str, Any]] = scope.get("outer_decimal_ground")
    # AI-path analogue of build_targets_for_page's own last_ground_letter_ordinal
    # — tracks a running (A)(B)(C)... sequential-letter enumeration inside an
    # active ground/point scope, so a later roman-valid letter (C=100, D=500,
    # I=1, L=50, M=1000, ...) reached mid-run resolves as the next list item,
    # not a wildly out-of-place roman numeral. See _resolve_ground_or_point_match.
    last_ground_letter_ordinal: Optional[int] = scope.get("last_ground_letter_ordinal")

    candidates: List[Dict[str, Any]] = []
    occurrence_counts: Dict[str, int] = {}
    # Everything superseded WITHIN this page's own processing — either a
    # same-page sibling overwritten before this page is ever persisted, or a
    # container carried in from an earlier page that gets replaced partway
    # through this one — that compute_scope_ranges' end-of-page-snapshot diff
    # can otherwise never see at all (see compute_scope_ranges' own docstring
    # and _compute_scope_ranges_uncached's consumption of this field for why).
    closed_this_page: List[Dict[str, Any]] = []
    # Page-local safety net for the sibling-chaining bug: consecutive
    # generic-path events (not forced_family) sharing the same coarse
    # enumeration marker STYLE (e.g. "(i)", "(ii)", "(iii)") are almost
    # always flat siblings of each other, never nested one under the
    # previous one — but the AI's own reported depth/parent_ref
    # occasionally chains them into a runaway deepening stack anyway.
    # Not persisted into new_scope: same accepted page-local limitation as
    # the reproduced-text-continuation fix.
    prev_enum_style: Optional[str] = None
    prev_enum_depth: Optional[int] = None
    prev_enum_num_segments: Optional[List[str]] = None

    for ev in events:
        text = ev["heading_text"]
        stripped_text = (text or "").strip()

        # Forced-family override, checked BEFORE mint_key_for_heading and
        # against nested_stack exactly as it stands prior to this event's
        # own mutation: a numbered/roman list item reported by the AI
        # inside an active Grounds-of-Appeal or generic-pleading scope is a
        # `ground`/`point` leaf, never a fresh top-level container — the
        # bug this closes: an AI-reported "(VII) That the tribunal..."
        # with no resolved parent otherwise fell through to
        # mint_key_for_heading's generic subdoc bucket and got minted as
        # its own top-level section, staying open for dozens of subsequent
        # pages. mint_key_for_heading itself is never given the chance to
        # re-interpret these — its own fuzzy/family-hint cascade has no
        # opinion on ground/point shapes anyway.
        #
        # Uses _resolve_ground_or_point_match (build_targets_for_page's own
        # resolver) rather than raw GROUND_DIGIT_RE/GROUND_ROMAN_RE matching
        # — confirmed real bug: a sequential-letter list ("(A)"..."(O)")
        # where some letters are also valid roman numerals (C, D, I, L, M)
        # had those specific letters misclassified as roman and forced into
        # this leaf family, while the non-roman-valid letters
        # (A, B, E, F, G, H, J, K, N, O) fell through to mint_key_for_heading's
        # generic "subdoc" container bucket and got pushed as real ancestors
        # — so a later roman-valid letter appeared to nest under whichever
        # non-roman letter was most recently pushed, every time.
        forced_family = None
        if ev.get("family_hint") in ("point", "ground"):
            forced_family = ev.get("family_hint")
        elif _ground_scope_active(nested_stack):
            forced_family = "ground"
        elif _pleading_scope_active(nested_stack):
            forced_family = "point"

        # Explicit container/family keyword matches always bypass forced_family
        is_explicit_keyword = bool(
            CORE_RE.match(stripped_text)
            or _fuzzy_family_match(stripped_text)
            or SUBDOC_TRIGGER_RE.search(stripped_text)
        )
        if is_explicit_keyword:
            forced_family = None

        is_roman_ground_or_point = False
        is_seq_letter = False
        if forced_family:
            resolved = _resolve_ground_or_point_match(stripped_text, last_ground_letter_ordinal)
            if resolved is None and forced_family in ("point", "ground"):
                # Bare integer paragraph without trailing period (e.g. "29 In M/s Navodaya..."):
                bare_m = re.match(r"^\s*\(?(?P<number>\d+)\s+[A-Z]", stripped_text)
                if bare_m and not _is_date_shaped_number(bare_m.group("number")):
                    resolved = (bare_m.group("number"), bare_m.end(), False)
                else:
                    # Bare integer string or integer with dot/paren alone (e.g. "16", "16.", "(16)")
                    bare_digit_m = re.match(r"^\s*\(?(?P<number>\d+)(?:\.|\))?\s*$", stripped_text)
                    if bare_digit_m and not _is_date_shaped_number(bare_digit_m.group("number")):
                        resolved = (bare_digit_m.group("number"), bare_digit_m.end(), False)
                    elif ev.get("number_hint"):
                        resolved = (ev["number_hint"], len(stripped_text), False)
            if resolved is None:
                forced_family = None

            else:
                number_raw, _match_end, is_seq_letter = resolved
                if is_seq_letter:
                    last_ground_letter_ordinal = ord(number_raw) - ord("A") + 1
                    # Bypass normalize_key entirely for this case: it treats
                    # ANY roman-shaped string as a roman numeral (a single
                    # "C" would become 100) — the key must carry the letter
                    # itself, not a numeral.
                    key = f"{forced_family}:{number_raw.lower()}"
                else:
                    is_roman_ground_or_point = _is_roman(number_raw)
                    if is_roman_ground_or_point:
                        # Same collision this family's build_targets_for_page
                        # counterpart guards against: normalize_key would
                        # convert a roman ground/point to its arabic value,
                        # colliding with a real decimal ground/point of the
                        # same number in the same scope. Keep the roman
                        # string itself as the key.
                        key = f"{forced_family}:{number_raw.lower()}"
                    else:
                        key = normalize_key(forced_family, None, number_raw)
                label = _format_label_from_key(key)
                family, num_segments = forced_family, None
        if not forced_family:
            family, key, label, num_segments, boundary_ordinal = mint_key_for_heading(
                text, boundary_ordinal, label_occurrence_counts,
                family_hint=ev.get("family_hint"), number_hint=ev.get("number_hint"),
            )

        depth = ev.get("depth")
        if depth is None:
            depth = len(nested_stack)
        depth = max(0, min(depth, len(nested_stack)))

        # Standalone legal document root reset:
        # Standalone legal instruments (AFFIDAVIT, APPLICATION UNDER SECTION..., PETITION,
        # MEMO OF PARTIES, VAKALATNAMA, POWER OF ATTORNEY) reset to root level (depth 0)
        # unless enclosed inside an Annexure/Schedule/Appendix/Exhibit.
        if _STANDALONE_LEGAL_DOCS_RE.search(text):
            if len(nested_stack) == 0 or nested_stack[0].get("family") not in ("annexure", "schedule", "appendix", "exhibit"):
                depth = 0

        # Rank-0 root container protection:
        # If an active root container is open (rank == 0, e.g. "subdoc", "annexure", "schedule", "appendix"),
        # an incoming event must NOT evict the root document by resolving to depth 0 unless the incoming
        # event is itself a genuine document boundary / root candidate.
        if depth == 0 and len(nested_stack) >= 1:
            root_family = nested_stack[0].get("family")
            root_is_subdoc = root_family in ("subdoc", "annexure", "schedule", "appendix")
            if root_is_subdoc and not is_subdoc_root_candidate(text, family, nested_stack[0].get("label")):
                depth = 1

        # Major judicial section boundary clamping:
        # Major sections (e.g. FACTS, ISSUES, ANALYSIS, FINDINGS, CONCLUSION) in judicial /
        # legal proceedings are primary top-level divisions directly under the active root
        # container (depth 1), never nested deep inside previous submissions or sub-issues.
        if _MAJOR_JUDICIAL_SECTIONS.match(text) and len(nested_stack) >= 1:
            depth = 1

        # Leaf-family / low-confidence gate — the AI-path analogue of the
        # rule engine's "a leaf (rank is None) just reads whatever's
        # currently on the stack" invariant (build_targets_for_page only
        # ever pushes/pops for a family in _HIERARCHY_RANK, i.e. rank is
        # not None). Without this, apply_heading_events_to_scope pushed
        # EVERY event unconditionally regardless of family, so a leaf
        # (ground/point/clause/para) — or any event whose own grounding
        # couldn't be trusted — could truncate the stack and evict a real
        # ancestor, or itself become a false top-level container that
        # everything after it gets mis-scoped underneath.
        family_def = FAMILIES.get(family)
        is_true_leaf = family_def is not None and family_def.get("rank") is None
        reasons = ev.get("heading_confidence_reasons") or []
        unconditional_block = any(
            r in _GROUNDING_PUSH_BLOCKING_REASONS and r not in _POSITIONAL_ONLY_PUSH_BLOCKING_REASONS
            for r in reasons
        )
        positional_only_block = any(r in _POSITIONAL_ONLY_PUSH_BLOCKING_REASONS for r in reasons)
        if positional_only_block and not unconditional_block:
            root_label = nested_stack[0].get("label") if nested_stack else None
            if is_subdoc_root_candidate(stripped_text, family, root_label):
                positional_only_block = False
        push_blocking = unconditional_block or positional_only_block
        skip_stack_mutation = is_true_leaf or push_blocking
        if skip_stack_mutation:
            depth = len(nested_stack)

        # Sibling-chaining safety net (generic path only — a real
        # ground/point leaf is untouched, it already nests correctly via
        # last_decimal_ground/effective_stack above). If this item's marker
        # style matches the immediately preceding same-style item's AND the
        # AI resolved it as exactly one level DEEPER than that item, flatten
        # it back to that item's own depth instead — a same-style run is
        # almost always a flat list, never a self-nesting chain.
        #
        # Confirmed real bug this safety net must NOT fire on: a genuine
        # decimal parent/child pair ("4.1" then "4.1.1") where both happen to
        # end with a trailing period before the label ("4.1. Introduction",
        # "4.1.1. Existing..."). _enumeration_marker_style buckets both as
        # the coarse "decimal" style (it only distinguishes decimal/roman/
        # letter, not depth), so a textbook one-level-deeper decimal child
        # was being misread as the exact same "(i) mis-nested one level
        # under (ii)" shape this net exists to catch, and flattened back to
        # its parent's own depth — breaking "1" -> "1.1"/"1.2" and
        # "4.1" -> "4.1.1" nesting for any document using period-terminated
        # decimal numbering (confirmed on a real tender-spec gold fixture).
        # A decimal child is distinguished from a mis-nested decimal sibling
        # by `num_segments`: a genuine child's segments extend the previous
        # item's by exactly one (["4","1","1"] extends ["4","1"]); two
        # siblings at the same conceptual level never do (["1"] vs ["2"],
        # ["4","1"] vs ["4","2"]).
        if not forced_family:
            enum_style = _enumeration_marker_style(stripped_text)
            is_decimal_child = (
                enum_style == "decimal"
                and num_segments and prev_enum_num_segments
                and len(num_segments) == len(prev_enum_num_segments) + 1
                and num_segments[:-1] == prev_enum_num_segments
            )
            if (
                enum_style is not None
                and enum_style == prev_enum_style
                and prev_enum_depth is not None
                and depth == prev_enum_depth + 1
                and not is_decimal_child
            ):
                depth = prev_enum_depth
            prev_enum_style = enum_style
            prev_enum_depth = depth if enum_style is not None else None
            prev_enum_num_segments = num_segments if enum_style is not None else None
        else:
            prev_enum_style = None
            prev_enum_depth = None
            prev_enum_num_segments = None

        # Integer sequence resumption:
        # If an incoming numbered heading is a single integer N (e.g. "20"), check if any open ancestor
        # was an integer item P that opened a sub-structure. If P < N <= P + 3 (e.g. P=19, N=20),
        # this heading is continuing the outer integer sequence and must close the sub-structure,
        # resuming at P's depth (as a sibling of P).
        incoming_int = None
        if num_segments and len(num_segments) == 1:
            try:
                incoming_int = int(num_segments[0])
            except (TypeError, ValueError):
                pass
        else:
            lead_num = _leading_reference_number(stripped_text)
            if lead_num and lead_num.isdigit():
                incoming_int = int(lead_num)

        if incoming_int is not None and len(nested_stack) > 1:
            for d in range(len(nested_stack) - 1, 0, -1):
                ancestor = nested_stack[d]
                anc_segs = ancestor.get("num_segments")
                anc_int = None
                if anc_segs and len(anc_segs) == 1:
                    try:
                        anc_int = int(anc_segs[0])
                    except (TypeError, ValueError):
                        pass
                else:
                    anc_lead = _leading_reference_number(ancestor.get("label", ""))
                    if anc_lead and anc_lead.isdigit():
                        anc_int = int(anc_lead)
                if anc_int is not None:
                    if anc_int < incoming_int <= anc_int + 3:
                        depth = d
                        break
                    elif incoming_int > anc_int and d == len(nested_stack) - 1:
                        # Cannot nest a higher integer inside a lower integer (e.g. 20 inside 5)
                        depth = min(depth, d)

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

        # Mirrors build_targets_for_page's effective_stack: a roman or
        # sequential-letter ground/point continuing a decimal one in the
        # SAME scope nests under that specific decimal item — a synthetic
        # entry, local to this event only, never pushed onto the real
        # nested_stack.
        effective_stack = nested_stack
        if forced_family and (is_roman_ground_or_point or is_seq_letter) and last_decimal_ground:
            base_stack = last_decimal_ground.get("stack")
            if base_stack is None:
                base_stack = nested_stack
            effective_stack = [dict(s) for s in base_stack] + [
                {"family": forced_family, "key": last_decimal_ground["key"], "label": last_decimal_ground["label"]}
            ]
        elif forced_family and not (is_roman_ground_or_point or is_seq_letter) and incoming_int is not None:
            if outer_decimal_ground is not None:
                outer_p = outer_decimal_ground.get("int_val")
                if outer_p is not None and outer_p < incoming_int <= outer_p + 3:
                    # Resumes outer sequence (e.g. 20 resumes 19)
                    outer_decimal_ground = None
                    effective_stack = nested_stack
                else:
                    base_stack = outer_decimal_ground.get("stack") or nested_stack
                    effective_stack = [dict(s) for s in base_stack] + [
                        {"family": forced_family, "key": outer_decimal_ground["key"], "label": outer_decimal_ground["label"]}
                    ]
            elif last_decimal_ground is not None:
                prev_p = last_decimal_ground.get("int_val")
                if prev_p is not None and prev_p >= 2 and incoming_int == 1:
                    # Starts decimal sub-list (1. follows 19.)
                    outer_decimal_ground = last_decimal_ground
                    base_stack = outer_decimal_ground.get("stack") or nested_stack
                    effective_stack = [dict(s) for s in base_stack] + [
                        {"family": forced_family, "key": outer_decimal_ground["key"], "label": outer_decimal_ground["label"]}
                    ]

        # A numbered/lettered row that's really a table/list row (a
        # price-schedule line item, a spares-list part, a document-index
        # entry), not a real heading — see _LISTING_CONTAINER_RE. Checked
        # against effective_stack (not nested_stack) now that it's fully
        # resolved — not earlier, right after family/key: confirmed via the
        # equivalent rule-path bug that checking a stale, about-to-be-popped/
        # truncated top-of-stack entry can gate on the WRONG parent (e.g. a
        # sibling heading briefly sees a doomed ancestor's label before
        # depth/truncation resolves the true one).
        if family in ("point", "ground", "numheading", "letterheading") and _immediate_parent_is_listing_container(effective_stack, text):
            continue

        scope_key = "/".join(s["key"] for s in effective_stack)
        scope_label = " › ".join(s["label"] for s in effective_stack)

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
        if own_label is None and family == "point":
            own_label = stripped_text[:80]

        candidates.append({
            "key": key,
            "occurrence_index": occ,
            "snippet": (ev.get("snippet") or "")[:400],
            "truncated": bool(ev.get("truncated")) and has_next_page,
            "continues_on_page": None,
            "scope_key": scope_key,
            "scope_label": scope_label,
            "scope_chain": [dict(s) for s in effective_stack],
            "collapsed_key": collapse_key(key),
            "fingerprint": ev.get("fingerprint", ""),
            "heading_confidence": ev.get("heading_confidence", "high"),
            "heading_confidence_reasons": ev.get("heading_confidence_reasons") or [],
            "own_label": own_label,
            "detection_source": "ai",
            "detection_model": ev.get("detection_model"),
            "start_line_hint": ev.get("start_line_hint"),
        })

        if forced_family:
            # A roman or sequential-letter continuation never itself becomes
            # the new "last decimal" — only a genuine decimal match does.
            if not is_roman_ground_or_point and not is_seq_letter:
                # `effective_stack` is always THIS heading's own ANCESTOR
                # chain (ground/point are leaves, never pushed onto
                # nested_stack, so effective_stack never includes the
                # heading's own key) — store it as-is. A later roman/
                # sequential-letter continuation of THIS heading appends
                # this heading's own key on top of whatever "stack" is
                # stored here (see the consumer above), so storing
                # anything less than the full chain silently drops an
                # ancestor. Confirmed live: storing `effective_stack[:-1]`
                # here (a previous version) dropped point:19 from point:5's
                # own stack whenever point:5 itself was nested under
                # point:19 via outer_decimal_ground — point:5's roman
                # sub-points (i)-(vii) then resolved to
                # "subdoc:.../point:5" instead of
                # "subdoc:.../point:19/point:5", producing a duplicate
                # "point:5" node with no real row once the frontend
                # materializes every scope_chain segment as a tree node.
                last_decimal_ground = {
                    "key": key,
                    "label": label,
                    "stack": [dict(s) for s in effective_stack],
                    "int_val": incoming_int,
                }
        elif not skip_stack_mutation:
            # A genuine new/real container obviously starts fresh numbering
            # (and lettering) too — a numbering-scheme transition inside one
            # still-open scope is one thing; a real new section boundary is
            # a different, unrelated event.
            last_decimal_ground = None
            outer_decimal_ground = None
            last_ground_letter_ordinal = None

        if not skip_stack_mutation:
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
        # If the container at close_from has an active sequential letter or roman enumeration in progress
        # (e.g. (A)-(E) at bottom of page continuing to next page), do not close it prematurely.
        if last_ground_letter_ordinal is not None and last_decimal_ground:
            anc_keys = {s["key"] for s in (last_decimal_ground.get("stack") or [])}
            anc_keys.add(last_decimal_ground.get("key"))
            if any(s.get("key") in anc_keys for s in nested_stack[close_from:]):
                close_from = None
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
        "last_decimal_ground": last_decimal_ground,
        "outer_decimal_ground": outer_decimal_ground,
        "last_ground_letter_ordinal": last_ground_letter_ordinal,
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
    if detection_source == "rule" and not provisional:
        snapshot = {
            "version": version,
            "candidates": candidates,
            "new_scope": new_scope,
        }
        database.set_page_rule_snapshot(job_id, page_num, snapshot)
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
        return [dict(r) for r in cached[1]]

    ranges = _compute_scope_ranges_uncached(job_id, total_pages)
    _scope_ranges_cache[job_id] = (current_version, ranges)
    return [dict(r) for r in ranges]


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


async def reindex_job(
    job_id: str, force_mode: Optional[str] = None, start_page: Optional[int] = None,
    chunk_pages_override: Optional[int] = None,
    review_rule_prior_override: Optional[bool] = None,
    overlap_pages_override: Optional[int] = None,
):
    """Dispatches to the rule-based or AI-based full reindex depending on
    the job's frozen engine_params.ai_indexing_enabled (or force_mode, for
    a manual ?mode= override on the "Rebuild Cross-References" endpoint).

    `start_page`, when given, is only meaningful for the AI path (a targeted
    re-run starting from one retried page, per ai_reference_index.py) — the
    rule-based path ignores it and always rebuilds from page 1, since it's
    cheap enough that a partial-rebuild optimization isn't worth the extra
    code path.

    `chunk_pages_override`, `review_rule_prior_override`, and
    `overlap_pages_override`, all AI-path-only, are passed straight through
    to ai_reference_index.reindex_job_with_ai — see its docstring.

    There is no caller-facing choice between Unlimited-OCR's raw `<|det|>`-
    tagged capture and the plain cleaned text as indexing input — the AI
    path (ai_reference_index._page_text / build_layout_annotated_text)
    picks automatically, per page, so a job only ever gets ONE reference
    index, never two built from different inputs to compare/keep. The
    rule-based path already has its own separate layout signal
    (parse_layout_blocks / layout_aligned)."""
    if job_id in _active_reindex_jobs:
        return
    _active_reindex_jobs.add(job_id)
    try:
        job = database.get_job(job_id, include_pages=False)
        if not job:
            return
        engine_params = job.get("engine_params") or {}
        mode = force_mode or ("ai" if engine_params.get("ai_indexing_enabled") else "rule")
        # Full reindex only (never a narrow AI single-page retry, which only
        # touches one page and leaves the rest of the job's keys/overrides
        # alone — nothing to remap there). The rule path always rebuilds
        # from page 1 regardless of start_page, so it's always "full" here.
        if mode == "rule" or start_page is None:
            database.remap_subdoc_keys_for_job(job_id)
        if mode == "ai":
            import ai_reference_index
            await ai_reference_index.reindex_job_with_ai(
                job_id, start_page=start_page, chunk_pages_override=chunk_pages_override,
                review_rule_prior_override=review_rule_prior_override,
                overlap_pages_override=overlap_pages_override,
            )
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
    build_mention_index_for_job(job_id)
    if (job.get("engine_params") or {}).get("ai_index_review_mentions"):
        import ai_reference_index
        await ai_reference_index.review_mentions_for_job(job_id, job.get("engine_params"))
    if (job.get("engine_params") or {}).get("ai_index_override_docrefs") or (job.get("engine_params") or {}).get("ai_index_review_docrefs"):
        import ai_reference_index
        await ai_reference_index.review_docrefs_for_job(job_id, job.get("engine_params"))


