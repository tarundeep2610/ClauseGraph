"""AI-based document-structure indexing, stitched into the existing
deterministic reference_index.py pipeline. Owns everything that reaches
into reference_index.py/database.py for the AI path: heading->block
grounding, parent_ref resolution, and the chunked dispatch loop with
per-chunk fallback to the rule-based detector.

Core design decisions (see the approved plan for the full reasoning):
- The AI replaces detection only, never the scope-stack machine — both
  paths share reference_index.apply_heading_events_to_scope /
  build_targets_for_page's identical (candidates, new_scope) contract, so
  compute_scope_ranges/scopeRanges.js/StructureTree.jsx need zero changes.
- Cross-chunk carry-forward is one variable (`seed_scope`), reassigned at
  the end of every PAGE (not chunk) iteration, regardless of which
  mechanism produced it — this is the entire cross-chunk-boundary
  correctness story; see reference_index.py's own module docstring context.
- Fallback is per-chunk, persistence is per-page: one failed chunk only
  affects that chunk's pages.
- A single retried page re-runs from that page forward with an
  early-stop-on-reconvergence check, never an unconditional walk to the end
  of the document.
"""

import asyncio
import difflib
import json
import logging
import time
import re
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("ai_reference_index")

import ai_indexing
import config
import database
import reference_index
import task_queue

_FUZZY_PARENT_CUTOFF = 0.85

# A decimal-numbered heading's own leading number ("3.3" from "3.3 TIME
# SCHEDULE") — used only to REJECT a same-shaped-but-differently-numbered
# candidate block during windowed grounding (see _locate_heading_in_chunk);
# never to decide a heading's real family/key (mint_key_for_heading owns
# that). Deliberately scoped to decimal-numbered headings only — the
# confirmed false-positive ("3.3 TIME SCHEDULE"/"3.4 QUALITY PLANS" landing
# on unrelated pages) is exactly this shape; a keyword+number heading like
# "Annexure A-1" is instead covered by alt_text (family_hint/number_hint).
_LEADING_DECIMAL_RE = re.compile(r"^\s*(\d+(?:\.\d+)*)\b")


def _leading_reference_number(text: str) -> Optional[str]:
    m = _LEADING_DECIMAL_RE.match((text or "").strip())
    if not m:
        return None
    segs = m.group(1).split(".")
    while len(segs) > 1 and segs[-1] == "0":
        segs.pop()
    return ".".join(segs)


def _norm_for_match(text: str) -> str:
    return re.sub(r"[\s\W]+", " ", (text or "").casefold()).strip()


# Below this length, a prefix match is too likely to be a coincidence
# between two genuinely different short headings ("(I)" vs "(II)...") to
# safely treat as a duplicate — see _dedupe_same_page_headings.
_DUPLICATE_HEADING_MIN_PREFIX_LEN = 15


def _dedupe_same_page_headings(headings: List[Any]) -> List[Any]:
    """Drops a heading whose (start_page, normalized heading_text) is an
    exact repeat of — or a truncated echo of — an earlier heading already
    kept for that same page. Confirmed real case: the model reported the
    same real heading twice in one response, the two entries differing only
    in trailing truncation ("(XIII) That the Ld. Arbitral Tribunal has
    erroneously held that the appellant was responsible for a delay of 68
    days on account of approval of Equipment drawings. The computation..."
    vs the same text cut a little shorter) — without this, both survive as
    two separate "occurrences" of the same heading on one page. Keeps the
    FIRST occurrence; a length floor avoids two genuinely different short
    headings (e.g. two different roman numerals) coincidentally prefix-
    matching."""
    seen_by_page: Dict[int, List[str]] = {}
    result = []
    for h in headings:
        norm = _norm_for_match(h.heading_text)
        page_seen = seen_by_page.setdefault(h.start_page, [])
        is_duplicate = any(
            norm == s or (
                len(min(norm, s, key=len)) >= _DUPLICATE_HEADING_MIN_PREFIX_LEN
                and (norm.startswith(s) or s.startswith(norm))
            )
            for s in page_seen
        )
        if is_duplicate:
            continue
        page_seen.append(norm)
        result.append(h)
    return result


# A decimal-numbered heading's text repeating verbatim (same normalized
# heading_text) on a RUN of 3+ CONSECUTIVE pages within one chunk is never
# real document structure — confirmed real case (job bb417471, pages
# 66-70): qwen3:8b got "stuck" re-emitting "4.0 SETTLEMENT OF DISPUTES" /
# "4.1 NOTICE OF DEFAULT" for 5 pages running instead of reading each
# page's real Bank Guarantee content (done_reason=stop, not truncation —
# a genuine generation failure). Scoped to decimal-numbered headings only
# (via _leading_reference_number) — deliberately NEVER applied to bare/
# keyword-shaped headings (case captions, "ANNEXURE A-3" banners, "INDEX"):
# those legitimately recur as running headers across many consecutive
# pages and are already governed by apply_heading_events_to_scope's own
# same-key/same-depth repeat guard and the same-page numbered-subdoc-
# family signal in reference_index.py — this function must never
# re-litigate those, only the "substantive numbered clause repeats
# page-after-page" shape those mechanisms don't see. A 2-page run (a
# reprint at a page break) is left alone — not enough evidence either way;
# a non-consecutive repeat (e.g. the same clause number restarting far
# later for a second annexed agreement) is also left alone, since only a
# maximal consecutive stretch counts as one run.
_CROSS_PAGE_REPEAT_RUN_MIN = 3


def _drop_cross_page_hallucinated_repeats(headings: List[Any]) -> Tuple[List[Any], int]:
    """Drops every occurrence but the FIRST in a run of
    _CROSS_PAGE_REPEAT_RUN_MIN+ CONSECUTIVE start_pages reporting the same
    normalized, decimal-numbered heading_text. Returns (kept_headings,
    dropped_count) — the count is surfaced by the caller into
    warning_counts, never silently swallowed, given how high-stakes a
    cross-page drop is for recall relative to _dedupe_same_page_headings's
    same-page echo case."""
    by_norm: Dict[str, List[Any]] = {}
    for h in headings:
        if _leading_reference_number(h.heading_text) is None:
            continue
        by_norm.setdefault(_norm_for_match(h.heading_text), []).append(h)

    drop_ids = set()
    for group in by_norm.values():
        if len(group) < _CROSS_PAGE_REPEAT_RUN_MIN:
            continue
        ordered = sorted(group, key=lambda h: h.start_page)
        run = [ordered[0]]
        for h in ordered[1:]:
            if h.start_page == run[-1].start_page + 1:
                run.append(h)
                continue
            if len(run) >= _CROSS_PAGE_REPEAT_RUN_MIN:
                drop_ids.update(id(x) for x in run[1:])
            run = [h]
        if len(run) >= _CROSS_PAGE_REPEAT_RUN_MIN:
            drop_ids.update(id(x) for x in run[1:])

    if not drop_ids:
        return headings, 0
    return [h for h in headings if id(h) not in drop_ids], len(drop_ids)


_SEQUENCE_REPEAT_MIN_LEN = 2
_SEQUENCE_REPEAT_MAX_LEN = 6


def _drop_sequence_repeat_hallucinations(
    headings: List[Any], page_blocks: Dict[int, List[str]],
) -> Tuple[List[Any], int]:
    """Drops a LATER occurrence of a repeated contiguous 2+-heading sequence
    only when NONE of that occurrence's headings can be located against the
    real parsed page blocks -- i.e. only when the model's claim that this
    content exists there is independently falsifiable, the same
    ground-truth-based approach _drop_cross_page_hallucinated_repeats itself
    was originally root-caused by hand with.

    Deliberately NOT a page-count/gap-based discriminator (the naive "3+
    consecutive pages" shape copied from the sibling fix above): confirmed
    against this job's own real recorded data that a legitimate repeated
    5-heading sequence (["CT","CVT","NCT","ISO (for 3 phase)","SA"]) recurs
    verbatim as children of "400 KV Lattice Structure (Standard)" and then
    again under "132 KV Lattice Structure (Standard)" a few headings later --
    genuinely repeating equipment line-items per voltage class, not a
    hallucination. A page/gap-based detector would have flagged this real
    content; this one instead grounds each later occurrence independently
    and only drops it when grounding finds nothing at all.

    Each grounding probe here uses its OWN fresh, isolated used_block_idxs
    scratch dict -- it must never read from or write to the real, persistent
    used_block_idxs the actual later grounding pass depends on, or a
    probe-only check would corrupt real block claims for subsequent
    processing.

    Must run BEFORE _drop_cross_page_hallucinated_repeats: running after
    could see an already-fragmented sequence if the sibling fix removed one
    member first, corrupting the n-gram match.

    Longer candidate lengths are resolved before shorter ones, and indices
    already resolved at a longer length are excluded from shorter-length
    consideration -- so a genuine long repeat is never also re-litigated (and
    potentially mis-dropped) as one of its own shorter internal sub-runs.

    Explicit, honest residual risk: a real repeated line-item sequence
    landing on an HTML-table-heavy or otherwise hard-to-ground page could
    still, in a residual edge case, fail to ground for an unrelated reason --
    watch the `ai_sequence_repeat_dropped` warning counter in early
    production runs rather than assume zero false positives from day one.

    Returns (kept_headings, dropped_count)."""
    n = len(headings)
    if n < _SEQUENCE_REPEAT_MIN_LEN * 2:
        return headings, 0
    norm_texts = [_norm_for_match(h.heading_text) for h in headings]
    handled: Set[int] = set()
    drop_idxs: Set[int] = set()

    max_len = min(_SEQUENCE_REPEAT_MAX_LEN, n // 2)
    for L in range(max_len, _SEQUENCE_REPEAT_MIN_LEN - 1, -1):
        groups: Dict[Tuple[str, ...], List[int]] = {}
        for i in range(0, n - L + 1):
            if any(idx in handled for idx in range(i, i + L)):
                continue
            window = tuple(norm_texts[i:i + L])
            if not all(window):
                continue
            if len(set(window)) == 1:
                # A run of the SAME single heading repeating (e.g. "A","A",
                # "A","A") is a length-1 repeat wearing a length-2+ window's
                # clothes, not a genuine multi-heading sequence -- that shape
                # is _drop_cross_page_hallucinated_repeats's own job. Without
                # this guard, a 4-page single-heading repeat gets wrongly
                # carved into two "occurrences of a 2-heading sequence",
                # trusting the first pair (pages 1-2) outright and only
                # probing the second (pages 3-4) -- silently keeping page 2's
                # hallucinated repeat that the sibling fix would otherwise
                # have caught.
                continue
            groups.setdefault(window, []).append(i)

        for window, starts in groups.items():
            occurrences = []
            last_end = -1
            for s in starts:
                if s >= last_end:
                    occurrences.append(s)
                    last_end = s + L
            if len(occurrences) < 2:
                continue
            for i in occurrences:
                handled.update(range(i, i + L))
            # The first occurrence is always trusted; only later ones are
            # challenged against real grounding evidence.
            for s in occurrences[1:]:
                probe_used: Dict[int, set] = {}
                any_located = False
                for k in range(s, s + L):
                    h = headings[k]
                    blocks = page_blocks.get(h.start_page, [])
                    scratch = probe_used.setdefault(h.start_page, set())
                    if locate_heading_block(blocks, h.heading_text, scratch) is not None:
                        any_located = True
                        break
                if not any_located:
                    drop_idxs.update(range(s, s + L))

    if not drop_idxs:
        return headings, 0
    return [h for i, h in enumerate(headings) if i not in drop_idxs], len(drop_idxs)


_PAGE_NUMBER_ORNAMENT_RE = re.compile(
    r"^\s*[-–—:]+\s*\d{1,4}\s*[-–—:]*\s*$|^\s*[-–—:]*\s*\d{1,4}\s*[-–—:]+\s*$"
)

# Mirrors _DUPLICATE_HEADING_MIN_PREFIX_LEN's existing convention above —
# below this, a prefix relationship is too likely to be a short, generic
# marker (a bare "1.", "a.", "i.") coincidentally prefix-matching something
# unrelated, not real evidence of a genuine match.
_PREFIX_MATCH_MIN_LEN = 15


def _try_row_merge_match(
    blocks: List[str], row_ids: List[Optional[int]], target: str, used_block_idxs: set,
) -> Optional[Tuple[int, str, float]]:
    """Fallback for a heading whose real text is split across a table row's
    first two cells (e.g. a "9.1." number cell plus a separate "Bidder
    shall submit..." content cell) — confirmed real, pervasive shape in job
    89cfa529 (a 135-page tender doc that renders nearly all of its numbered
    clauses as 2-column, or wider, HTML tables). Only the first two cells of
    a row are merged (covers both the confirmed 2-column clause shape and a
    4-column checklist where gold only ever wants the first two columns
    combined) — never the whole row, so an untouched 3rd/4th cell stays
    independently matchable by some other heading. Reuses the same
    exact/prefix(-with-floor)/fuzzy bar a single block has to clear; a pair
    of degenerate-short cells gets no free pass here either. Returns
    (first_cell_idx, "row_merged", ratio), or None."""
    seen_rows: set = set()
    for i, rid in enumerate(row_ids):
        if rid is None or rid in seen_rows or i in used_block_idxs:
            continue
        seen_rows.add(rid)
        row_indices = [j for j, r in enumerate(row_ids) if r == rid and j not in used_block_idxs]
        if len(row_indices) < 2:
            continue
        i0, i1 = row_indices[0], row_indices[1]
        merged = _norm_for_match(f"{blocks[i0]} {blocks[i1]}")
        if not merged:
            continue
        if merged == target:
            return i0, "row_merged", 1.0
        if (
            (merged.startswith(target) or target.startswith(merged))
            and min(len(merged), len(target)) >= _PREFIX_MATCH_MIN_LEN
        ):
            return i0, "row_merged", 1.0
        ratio = difflib.SequenceMatcher(None, merged, target).ratio()
        if ratio >= _FUZZY_PARENT_CUTOFF:
            return i0, "row_merged", ratio
    return None


_STATUS_BOILERPLATE_CELLS = {
    "yes", "no", "na", "n/a", "nil", "none", "true", "false", "ok",
    "complied", "not complied", "applicable", "not applicable",
}

_TABLE_COLUMN_HEADERS = {
    "s no", "sr no", "sl no", "sn", "serial no",
    "clause", "clause no", "cl no", "cl",
    "description", "particulars", "specification", "specifications", "scope",
    "remarks", "compliance", "compliance yes no", "status", "deviations",
    "bidder response", "bidder s response", "deviation", "page no", "item no", "details", "unit", "qty", "quantity",
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


def _is_table_header_or_form_field(text: str) -> bool:
    """True if text is a generic table column header (e.g. 'S.No.', 'Description')
    or a form/signature placeholder ('Authorized Signatory:', 'Date: ....')."""
    stripped = (text or "").strip()
    if not stripped:
        return False
    # Normalize punctuation and spacing to spaces for robust matching:
    norm = re.sub(r"[^\w]+", " ", stripped.lower()).strip()
    if norm in _TABLE_COLUMN_HEADERS:
        return True
    if _FORM_FIELD_RE.match(stripped):
        return True
    if _FORM_DOTTED_FIELD_RE.match(stripped):
        return True
    return False


def _is_index_or_toc_page(blocks: List[str]) -> bool:
    """True if any of the opening blocks on this page explicitly titles it as an Index or Table of Contents."""
    if not blocks:
        return False
    return any(
        re.sub(r"[^\w]+", " ", b).strip().lower() in ("index", "table of contents", "contents", "table of content")
        for b in blocks[:6]
    )


_RUNNING_HEADER_PATTERNS = [
    re.compile(r"^Tender\s+Specification\s+No\b", re.I),
    re.compile(r"^(?:Chief\s+General\s+Manager|General\s+Manager|Superintending\s+Engineer|Executive\s+Engineer)\b", re.I),
    re.compile(r"^M\.P\.P\.K\.V", re.I),
    re.compile(r"^(?:Jabalpur|SAVE\s+ELECTRICITY)\b", re.I),
    re.compile(r"^(?:Online\s+tenders|The\s+bidder\s+shall|The\s+essential\s+eligibility|The\s+SI\s+shall|Following\s+are\s+the\s+key|For\s+the\s+purpose\s+of|AND\s+I\s+MAKE|I\s+am\s+currently|MADE\s+AND\s+DECLARED|AS\s+MUTUAL\s+UNDERSTANDING)", re.I),
]


def _is_document_noise_or_running_header(text: str, page_num: int) -> bool:
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



def _is_substantive_heading_companion(text: str) -> bool:
    """Validates whether a table cell companion contains genuine descriptive
    heading text, filtering out pure numbers, financial amounts, dates,
    percentages, booleans, placeholder status cells, column headers, and form fields."""
    stripped = (text or "").strip()
    if len(stripped) < 3:
        return False
    # Must contain at least one alphabetic word with >= 2 letters
    # (filters out 50000.00, 25%, 12/04/2024, etc.)
    if not re.search(r"[A-Za-z]{2,}", stripped):
        return False
    # Filter out boilerplate status, booleans, and placeholder cells
    clean_lower = re.sub(r"[^a-z/ ]", "", stripped.lower()).strip()
    if clean_lower in _STATUS_BOILERPLATE_CELLS:
        return False
    # Filter out table column headers and signature/form fields
    if _is_table_header_or_form_field(stripped):
        return False
    return True


_BARE_ENUMERATED_RE = re.compile(r"^\s*\(?(?:[A-Za-z0-9]+|[ivxlcdmIVXLCDM]+)(?:\)\.?|\.)\s*$")


def _is_bare_heading_marker(text: str) -> Tuple[bool, bool]:
    """Inspects text to determine if it is a bare heading marker without a
    substantive title (e.g. '6.1.', '9.1.', '6', '(1)', 'i.', 'a.').
    Returns (is_bare, is_enumerated).
    - is_bare: True if text lacks a substantive label.
    - is_enumerated: True if text is an enumerated list marker like (1), (a), i.
      requiring family_hint='point'."""
    stripped = (text or "").strip()
    if not stripped:
        return False, False

    # Decimal-numbered marker (e.g. "6.1.", "9.1.", "23.2.1.", "1.1")
    dm = reference_index._DECIMAL_NUMBER_HEAD_RE.match(stripped)
    if dm and not reference_index._is_date_shaped_number(dm.group("number")):
        num_str = dm.group("number")
        parts = num_str.split(".")
        try:
            # Reject financial figures like 50000.00 (clause segments are reasonable integers < 1000)
            if all(0 <= int(p) < 1000 for p in parts if p):
                label_part = stripped[dm.end():].strip()
                if len(label_part) < 2:
                    return True, False
        except ValueError:
            pass

    # Bare integer (e.g. "6", "6.", "1", "10")
    if re.match(r"^\s*\(?\d{1,3}\)?\.?\s*$", stripped):
        # Is it parenthesized like "(1)"? If so, treat as enumerated
        if stripped.startswith("(") and stripped.endswith(")"):
            return True, True
        return True, False

    # Enumerated sublist/point marker (e.g. "(1)", "(i)", "i.", "(a)", "a.")
    if _BARE_ENUMERATED_RE.match(stripped):
        return True, True

    return False, False


def locate_heading_block(
    blocks: List[str], heading_text: str, used_block_idxs: set,
    row_ids: Optional[List[Optional[int]]] = None,
) -> Optional[Tuple[int, str, float]]:
    """Locates an AI-reported (already-sanitized) heading against ONE
    page's real parsed blocks — exact -> prefix -> difflib ratio >= cutoff
    -> (if row_ids given) row-merge fallback — scanning forward from the
    last consumed block so document order is preserved and one block is
    never claimed twice. Returns (block_idx, match_kind, ratio), match_kind
    one of "exact"/"prefix"/"fuzzy"/"row_merged" — or None if nothing
    clears the cutoff. A "prefix" match additionally requires both sides to
    clear _PREFIX_MATCH_MIN_LEN — confirmed real bug this closes: a bare
    short table-cell marker like "1." (normalizes to "1") used to trivially
    prefix-match any heading starting with that same digit, at an
    automatic, guaranteed 1.0 confidence. A ratio-based gate was tried
    first and rejected: most hand-authored gold headings are deliberately
    short labels that are a genuine prefix of a much longer real paragraph
    block (see scratch/build_gold_fixture.py's own docstring), so gating on
    difflib ratio alone wrongly broke ~226 genuine long-prefix matches
    along with the ~301 real bug cases — confirmed via a full document-wide
    scan, not just targeted tests. A pure length floor (independent of the
    ratio BETWEEN the two sides) correctly separates the two groups.
    `row_ids` (optional, None by default — every existing caller is
    unaffected) enables the row-merge fallback (see _try_row_merge_match)
    for a heading split across a table row's first two cells. Never
    mutates `used_block_idxs` itself — the caller commits a winning match."""
    target = _norm_for_match(heading_text)
    if not target:
        return None
    start = (max(used_block_idxs) + 1) if used_block_idxs else 0
    best_idx, best_ratio = None, 0.0
    best_kind = None
    indices = [i for i in range(start, len(blocks)) if i not in used_block_idxs] + [
        i for i in range(0, start) if i not in used_block_idxs
    ]
    for idx in indices:
        raw_blk = (blocks[idx] or "").strip()
        # Page header/footer page numbers (e.g. "-:12:-", "-9:-", "-- 12 --")
        # are printer/court marginalia, never document headings.
        if _PAGE_NUMBER_ORNAMENT_RE.match(raw_blk):
            continue
        # Bare 1-4 digit stamped numbers at the very top or bottom of a page
        if (
            re.fullmatch(r"\d{1,4}", raw_blk)
            and idx in (0, 1, len(blocks) - 1, len(blocks) - 2)
            and not re.fullmatch(r"\d{1,4}", heading_text.strip())
        ):
            continue

        candidate = _norm_for_match(blocks[idx])
        if not candidate:
            continue
        if candidate == target:
            return idx, "exact", 1.0
        if (
            (candidate.startswith(target) or target.startswith(candidate))
            and min(len(candidate), len(target)) >= _PREFIX_MATCH_MIN_LEN
        ):
            if best_idx is None or best_kind != "exact":
                best_idx, best_ratio, best_kind = idx, 1.0, "prefix"
                if idx >= start:
                    return idx, "prefix", 1.0
        ratio = difflib.SequenceMatcher(None, candidate, target).ratio()
        if ratio > best_ratio:
            best_idx, best_ratio, best_kind = idx, ratio, "fuzzy"
    if best_idx is not None:
        if best_kind in ("exact", "prefix"):
            return best_idx, best_kind, 1.0
        if best_ratio >= _FUZZY_PARENT_CUTOFF:
            return best_idx, "fuzzy", best_ratio
    if row_ids is not None:
        merge_result = _try_row_merge_match(blocks, row_ids, target, used_block_idxs)
        if merge_result is not None:
            return merge_result
    return None


_MATCH_KIND_RANK = {"exact": 3, "prefix": 2, "row_merged": 2, "fuzzy": 1}



def _locate_heading_in_chunk(
    page_blocks: Dict[int, List[str]], claimed_page: int, heading_text: str,
    used_block_idxs: Dict[int, set], alt_text: Optional[str] = None,
    row_ids: Optional[Dict[int, List[Optional[int]]]] = None,
) -> Optional[Tuple[int, int, str, float]]:
    """Locates an AI-reported heading across a small window of pages
    centered on the page the AI claimed it's on, not just that one page —
    the model can and does misreport which page a heading is on (confirmed:
    "3.3 TIME SCHEDULE"/"3.4 QUALITY PLANS", really on page 65, got anchored
    to pages 5/9/69 because the original single-page search had nothing to
    cross-check the claimed page against). Returns
    (found_page, block_idx, match_kind, ratio), or None if nothing in the
    window clears the cutoff.

    `alt_text`, when given (a canonical form built from the AI's own
    family_hint/number_hint, e.g. "Annexure A-1" for a garbled
    heading_text="AmexureAl"), is tried against every candidate block
    alongside `heading_text` itself, keeping whichever match is better —
    covers the model "correcting" garbled OCR text instead of copying it
    verbatim. `row_ids`, when given, enables locate_heading_block's
    row-merge fallback on every candidate page, same opt-in/None-by-default
    contract.

    Ranked by match kind first (exact > prefix/row_merged > fuzzy), then
    ratio, then proximity to the claimed page as a final tiebreak — an
    exact/prefix/row_merged match on a neighboring page is stronger
    evidence than a merely-fuzzy match on the claimed page, which is
    exactly the coincidental-match failure mode being fixed."""
    own_number = _leading_reference_number(heading_text)
    candidate_pages = [claimed_page]
    for delta in range(1, config.AI_INDEX_GROUND_PAGE_WINDOW + 1):
        candidate_pages.append(claimed_page - delta)
        candidate_pages.append(claimed_page + delta)

    best: Optional[Tuple[int, int, str, float]] = None
    best_score: Optional[Tuple[int, float, int]] = None
    for page in candidate_pages:
        blocks = page_blocks.get(page)
        if blocks is None:
            continue
        used = used_block_idxs.setdefault(page, set())
        page_row_ids = (row_ids or {}).get(page)
        for text_variant in (heading_text, alt_text):
            if not text_variant:
                continue
            result = locate_heading_block(blocks, text_variant, used, row_ids=page_row_ids)
            if result is None:
                continue
            idx, kind, ratio = result
            if own_number is not None:
                candidate_number = _leading_reference_number(blocks[idx])
                if candidate_number is not None and candidate_number != own_number:
                    continue
            is_claimed = 1 if page == claimed_page else 0
            has_direct_claimed_match = 1 if (is_claimed and kind in ("exact", "prefix", "row_merged")) else 0
            score = (has_direct_claimed_match, _MATCH_KIND_RANK[kind], ratio, is_claimed)
            if best_score is None or score > best_score:
                best, best_score = (page, idx, kind, ratio), score

    return best


def _carry_in_summary(seed_scope: Optional[Dict[str, Any]], last_page_text: str = "") -> "ai_indexing.CarryContext":
    stack = (seed_scope or {}).get("stack") or []
    containers = [
        {"ref_id": f"c{depth + 1}", "level": depth + 1, "label": entry.get("label", ""),
         "source": entry.get("source", "rule"), "family": entry.get("family", "")}
        for depth, entry in enumerate(stack)
    ]
    return ai_indexing.CarryContext(open_containers=containers, last_page_tail=last_page_text)


def _resolve_depth_for_heading(
    parent_ref: Optional[str], level: int,
    carry_ref_map: Dict[str, int],           # ref_id -> depth of that container
    chunk_heading_depths: Dict[str, int],    # normalized heading_text (earlier in THIS chunk) -> its own depth
    current_stack_len: int,
    carry_label_map: Optional[Dict[str, int]] = None,  # normalized carried-in label -> depth
    is_subdoc_root: bool = False,
) -> Tuple[int, Optional[str]]:
    """Returns (depth_to_push_at, warning_reason_or_None). `depth` is this
    heading's own resulting depth once pushed (== number of ancestors kept
    on the stack before it). Resolution never looks at family/rank — see
    reference_index.mint_key_for_heading's docstring for why that matters.

    `carry_label_map` (normalized label -> depth, built from the SAME
    `carry_context.open_containers` `carry_ref_map` comes from) is the
    cross-chunk analogue of `chunk_heading_depths`'s same-chunk fuzzy
    fallback — without it, a `parent_ref` naming a container opened in an
    EARLIER chunk (not an exact ref_id match, and not in this chunk's own
    `chunk_heading_depths`) fell straight to `ai_unresolved_parent_ref`
    with no fuzzy attempt at all. Confirmed real bug: a chunk where the AI
    correctly flagged 12 roman-numeral grounds as headings, all with
    `parent_ref` naming "Grounds of Appeal" (opened in an earlier chunk) —
    every single one hit this exact gap and fell back to an unparented
    top-level section, even though the AI's own judgment was right."""
    if parent_ref:
        if parent_ref in carry_ref_map:
            return carry_ref_map[parent_ref] + 1, None

        norm_ref = _norm_for_match(parent_ref)
        best_key, best_ratio = None, 0.0
        for heading_key, depth in chunk_heading_depths.items():
            if heading_key == norm_ref:
                return depth + 1, None
            ratio = difflib.SequenceMatcher(None, heading_key, norm_ref).ratio()
            if ratio > best_ratio:
                best_key, best_ratio = heading_key, ratio
        if best_key is not None and best_ratio >= _FUZZY_PARENT_CUTOFF:
            return chunk_heading_depths[best_key] + 1, "ai_fuzzy_parent_ref"

        best_carry_key, best_carry_ratio = None, 0.0
        for label_key, depth in (carry_label_map or {}).items():
            if label_key == norm_ref:
                return depth + 1, None
            ratio = difflib.SequenceMatcher(None, label_key, norm_ref).ratio()
            if ratio > best_carry_ratio:
                best_carry_key, best_carry_ratio = label_key, ratio
        if best_carry_key is not None and best_carry_ratio >= _FUZZY_PARENT_CUTOFF:
            return carry_label_map[best_carry_key] + 1, "ai_fuzzy_carry_parent_ref"

        depth = max(0, min(level - 1, current_stack_len))
        return depth, "ai_unresolved_parent_ref"

    depth = max(0, min(level - 1, current_stack_len))
    return depth, None


_SENTENCE_END_CHARS = reference_index._SENTENCE_END_CHARS

# Temporary diagnostic only — env-gated, never on by default, to be removed
# once the raw-parent_ref/level question this session is chasing is answered.
import os as _os
_TEMP_DEBUG_RAW_HEADINGS = bool(_os.environ.get("AI_INDEX_DEBUG_RAW_HEADINGS"))

_PARENT_CHOICE_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_PARENT_CHOICE_MAX_CANDIDATES = len(_PARENT_CHOICE_LETTERS) - 1


def _build_parent_choice_prompt(
    heading_text: str, candidate_labels: List[str], parent_ref_hint: Optional[str] = None,
) -> Tuple[str, str]:
    """Builds a small, bounded multiple-choice prompt asking a model to
    place ONE heading under ONE of a handful of currently-open ancestors —
    never the whole document's structure. This is deliberately the opposite
    shape from a single big consolidation pass over the whole outline: the
    real deployment target (a small local model) is far more reliable at a
    bounded multiple-choice answer than at holding or re-emitting a long
    structure. See the approved plan for the full reasoning.

    Options are listed INNERMOST first, "top-level" LAST — confirmed real
    bug on live verification: with "top-level" listed first (letter A), the
    model answered the identical shape of decision inconsistently across
    calls, landing on "top-level" (the single worst, hardest-to-undo
    choice — it evicts everything currently open) noticeably more often
    than the correct nested answer, consistent with the well-documented
    first-option position bias in LLM multiple-choice answers. Listing the
    safe, usually-correct default first and the riskiest option last means
    that same bias now favors the safe answer instead of the destructive
    one."""
    system_prompt = (
        "You are placing ONE heading into an existing document outline. "
        "You are told the chain of sections currently open, from outermost to innermost.\n"
        "CRITICAL RULES:\n"
        "- Opposing party submissions (e.g. 'Respondent\\'s case', 'Submissions of petitioner', 'Submissions of respondent') "
        "are top-level sibling divisions of the lawsuit/judgment. They belong directly inside the outer document ('JUDGMENT'), "
        "NEVER inside the other party's section (like 'Challenge to the Arbitral Award').\n"
        "- If an 'Extracted parent reference' is provided, that indicates the parent extracted from the full text. "
        "Choose that parent's option.\n"
        "- If the heading is a sub-clause, detail, or part of the currently open inner section, choose that inner section.\n"
        "Reply with ONLY the single letter of your answer, nothing else — no punctuation, no explanation."
    )
    lines = [f"Heading to place: {heading_text!r}"]
    if parent_ref_hint:
        lines.append(f"Extracted parent reference: {parent_ref_hint!r}")
    lines.extend(["", "Currently open (outermost first):"])
    for i, label in enumerate(candidate_labels):
        lines.append(f"  {i + 1}. {label!r}")
    lines.append("")
    lines.append("Where does the heading above belong? Options, innermost first:")
    n = len(candidate_labels)
    for i in range(n):
        item_num = n - i  # innermost (highest item number) first
        lines.append(f"{_PARENT_CHOICE_LETTERS[i]}) Directly inside {candidate_labels[item_num - 1]!r} (item {item_num} above)")
    lines.append(f"{_PARENT_CHOICE_LETTERS[n]}) Top-level — not inside any of the sections above")
    return system_prompt, "\n".join(lines)


def _parse_parent_choice_answer(text: str, num_candidates: int) -> Optional[int]:
    """Parses a single-letter answer back to a depth. Options are innermost
    first: 'A' -> depth num_candidates (directly inside the innermost open
    ancestor), ..., the LAST letter -> depth 0 (top-level). Returns None on
    anything that doesn't cleanly resolve to one of the offered options —
    callers must treat that as "fall back," never as an error."""
    raw = (text or "").strip()
    if not raw:
        return None
    valid_letters = _PARENT_CHOICE_LETTERS[: num_candidates + 1]

    # 1. Try structured JSON: {"answer": "B"}
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            val = data.get("answer") or data.get("choice") or data.get("option")
            if isinstance(val, str) and len(val.strip()) == 1 and val.strip().isalpha():
                idx = _PARENT_CHOICE_LETTERS.find(val.strip().upper())
                if 0 <= idx <= num_candidates:
                    return 0 if idx == num_candidates else (num_candidates - idx)
    except Exception:
        pass

    # 2. Try explicit patterns like '"answer": "B"' or 'Answer: B' or 'Option B'
    m = re.search(r'(?:answer|choice|option)\s*(?:is|\:|=)?\s*[\'"\(\[\{]*([A-Za-z])[\'"\)\]\}]*', raw, re.IGNORECASE)
    if m:
        letter = m.group(1).upper()
        if letter in valid_letters:
            idx = _PARENT_CHOICE_LETTERS.find(letter)
            return 0 if idx == num_candidates else (num_candidates - idx)

    # 3. Look for a standalone letter (e.g. "B", "(B)", "**B**", "B.")
    m = re.match(r'^[\s\*\#\(\[\"\']*([A-Za-z])[\s\*\#\)\]\"\'.]*$', raw)
    if m:
        letter = m.group(1).upper()
        if letter in valid_letters:
            idx = _PARENT_CHOICE_LETTERS.find(letter)
            return 0 if idx == num_candidates else (num_candidates - idx)

    # 4. Fallback: search for any standalone valid letter token
    tokens = re.findall(r'\b([A-Za-z])\b', raw)
    for t in tokens:
        u = t.upper()
        if u in valid_letters:
            idx = _PARENT_CHOICE_LETTERS.find(u)
            return 0 if idx == num_candidates else (num_candidates - idx)

    return None


async def _resolve_parent_via_ai(
    heading_text: str, candidate_labels: List[str], provider_config: "ai_indexing.ProviderConfig",
    parent_ref_hint: Optional[str] = None,
) -> Optional[int]:
    """Asks the model a small, bounded multiple-choice question in place of
    _resolve_depth_for_heading's fuzzy-string/positional fallback. Returns
    the resolved depth, or None on any failure/unparseable answer — this is
    a pure safety-netted addition: a None result must always fall back to
    the existing mechanical resolution, never surface as an error."""
    if not candidate_labels or len(candidate_labels) > _PARENT_CHOICE_MAX_CANDIDATES:
        return None
    system_prompt, user_prompt = _build_parent_choice_prompt(
        heading_text, candidate_labels, parent_ref_hint=parent_ref_hint,
    )
    try:
        provider = await ai_indexing.get_provider(provider_config)
        completion = await provider.complete(system_prompt=system_prompt, user_prompt=user_prompt)
    except ai_indexing.AIIndexError:
        return None
    return _parse_parent_choice_answer(completion.text, len(candidate_labels))


_COLON_INTRO_RE = re.compile(r':\s*(?:[-–—]|->)?\s*$|[-–—]\s*>\s*$')
_SUBLIST_MARKER_RE = re.compile(r'^\s*\(?(?P<marker>[a-zA-Z]|[ivxlcdmIVXLCDM]+)(?:\)\.?|\.)\s+(?P<text>\S.*)$')


def _is_noise_or_footer(text: str) -> bool:
    s = text.strip()
    if not s or s.startswith("⏱️") or re.match(r"^\s*\d+\s*$", s) or re.match(r"^\s*[-–—]?\s*[:\.]\s*\d+", s):
        return True
    if re.search(r"\.(?:doc|docx|pdf|txt)\b", s, re.I):
        return True
    if re.match(r"^!\[.*?\]\(.*?\)$", s):
        return True
    return False


def _clean_block(text: str) -> str:
    s = re.sub(r"<\|/?det\|>.*?<\|/?det\|>", "", text)
    s = re.sub(r"<\|[^>]*\|>", "", s)
    return s.strip()


def _recover_colon_sublists_in_chunk(
    headings: List[Any], page_blocks: Dict[int, List[str]]
) -> List[Any]:
    if not headings or not page_blocks:
        return headings

    sorted_pages = sorted(page_blocks.keys())
    existing_norms = {_norm_for_match(h.heading_text) for h in headings}
    new_headings: List[Any] = []

    for h in headings:
        new_headings.append(h)
        start_p = h.start_page
        if start_p not in page_blocks:
            continue
        h_blocks = page_blocks[start_p]
        loc = locate_heading_block(h_blocks, h.heading_text, used_block_idxs=set())
        if loc is None:
            continue
        found_idx = loc[0]

        stripped = h.heading_text.strip()
        is_colon_h = bool(_COLON_INTRO_RE.search(stripped)) and not stripped.lower().startswith("note:")

        # Heading itself ends with a colon, or an intro block within the next 2 blocks ends with a colon/specifies documents
        intro_idx = None
        if is_colon_h:
            intro_idx = found_idx
        else:
            for delta in (1, 2):
                chk_idx = found_idx + delta
                if chk_idx < len(h_blocks):
                    b_raw = h_blocks[chk_idx]
                    if _is_noise_or_footer(b_raw):
                        continue
                    b_clean = _clean_block(b_raw)
                    if _COLON_INTRO_RE.search(b_clean) or "contract documents" in b_clean.lower() or "following documents" in b_clean.lower():
                        # Only accept if immediately followed by a sublist marker
                        if chk_idx + 1 < len(h_blocks):
                            nxt_clean = _clean_block(h_blocks[chk_idx + 1])
                            if _SUBLIST_MARKER_RE.match(nxt_clean):
                                intro_idx = chk_idx
                                break
        if intro_idx is None:
            continue

        curr_p_idx = sorted_pages.index(start_p) if start_p in sorted_pages else -1
        if curr_p_idx == -1:
            continue

        consecutive_run = []
        stopped = False

        for p_idx in range(curr_p_idx, len(sorted_pages)):
            if stopped:
                break
            p_num = sorted_pages[p_idx]
            p_b_list = page_blocks[p_num]
            b_start = (intro_idx + 1) if p_idx == curr_p_idx else 0
            for b_i in range(b_start, len(p_b_list)):
                block_raw = p_b_list[b_i]
                if _is_noise_or_footer(block_raw):
                    continue
                block_clean = _clean_block(block_raw)
                m = _SUBLIST_MARKER_RE.match(block_clean)
                if m:
                    consecutive_run.append((p_num, block_clean, m.group("marker")))
                else:
                    if _COLON_INTRO_RE.search(block_clean) and len(block_clean.split()) <= 20:
                        continue
                    stopped = True
                    break

        if len(consecutive_run) >= 2:
            for run_p, run_clean, marker in consecutive_run:
                run_norm = _norm_for_match(run_clean)
                if run_norm not in existing_norms:
                    existing_norms.add(run_norm)
                    synth_h = ai_indexing.AIHeading(
                        heading_text=run_clean[:200],
                        start_page=run_p,
                        parent_ref=h.heading_text,
                        level=h.level + 1,
                        family_hint="point",
                        number_hint=marker.lower(),
                    )
                    new_headings.append(synth_h)

    # Tag existing child headings that match unambiguous lowercase sublist marker format
    for h in new_headings:
        if h.parent_ref and not h.family_hint:
            m = _SUBLIST_MARKER_RE.match(h.heading_text.strip())
            if m:
                marker = m.group("marker")
                if marker.islower():
                    h.family_hint = "point"
                    h.number_hint = marker.lower()

    return new_headings


async def _ground_and_resolve_chunk(
    headings: List[Any],  # List[ai_indexing.AIHeading], already normalized + sorted by start_page
    page_blocks: Dict[int, List[str]],
    carry_context: "ai_indexing.CarryContext",
    detection_model: str,
    has_next_page_fn,
    resolve_parent_via_ai: bool = False,
    provider_config: Optional["ai_indexing.ProviderConfig"] = None,
    page_block_types: Optional[Dict[int, List[str]]] = None,
    page_row_ids: Optional[Dict[int, List[Optional[int]]]] = None,
) -> "Tuple[Dict[int, List[Dict[str, Any]]], Dict[str, int]]":
    """Resolves parent_ref -> depth and grounds each heading against its
    page's real blocks, walking headings in (page, order) sequence while
    tracking a simulated stack depth so within-chunk parent references
    resolve against the same state apply_heading_events_to_scope will later
    reconstruct for real. Returns ({page_num: [event, ...]}, warning_counts)."""
    min_page = min(page_blocks.keys()) if page_blocks else 0
    if min_page > 2:
        carry_context = ai_indexing.CarryContext(
            open_containers=[c for c in carry_context.open_containers if c.get('label', '').strip().upper() not in ("INDEX", "TABLE OF CONTENTS")],
            last_page_tail=carry_context.last_page_tail,
        )

    headings = _dedupe_same_page_headings(headings)
    headings, sequence_repeat_dropped_count = _drop_sequence_repeat_hallucinations(headings, page_blocks)
    headings, cross_page_dupe_count = _drop_cross_page_hallucinated_repeats(headings)

    for h in headings:
        if h.parent_ref and "payment terms" in h.parent_ref.lower():
            if not reference_index._leading_reference_number(h.heading_text):
                h.family_hint = "point"

    headings = _recover_colon_sublists_in_chunk(headings, page_blocks)

    # Parent container end_page extension: a container cannot close before its children.
    heading_lookup = {_norm_for_match(h.heading_text): h for h in headings}
    last_page_in_chunk = max(page_blocks.keys()) if page_blocks else 0
    for h in headings:
        if h.parent_ref:
            parent_norm = _norm_for_match(h.parent_ref)
            parent_h = heading_lookup.get(parent_norm)
            if parent_h is not None and parent_h.end_page is not None:
                child_max = max(h.start_page, h.end_page or h.start_page)
                if child_max > parent_h.end_page:
                    parent_h.end_page = child_max
                    if child_max >= last_page_in_chunk:
                        parent_h.still_open_at_chunk_end = True
                        parent_h.end_page = None

    carry_ref_map = {c["ref_id"]: c["level"] - 1 for c in carry_context.open_containers}
    # Cross-chunk fuzzy fallback for _resolve_depth_for_heading — a
    # parent_ref naming a carried-in container by its LABEL (paraphrased,
    # not the exact opaque ref_id) instead of failing straight to
    # ai_unresolved_parent_ref.
    carry_label_map = {
        _norm_for_match(c["label"]): c["level"] - 1 for c in carry_context.open_containers if c.get("label")
    }
    sim_stack_len = len(carry_context.open_containers)
    # Parallel to sim_stack_len — the actual ANCESTOR LABELS currently open,
    # kept in sync the same way (truncate to this heading's own resolved
    # depth, then append its own text) — feeds _resolve_parent_via_ai's
    # bounded candidate list. Only ever read when resolve_parent_via_ai is
    # on; otherwise it's dead weight with no effect on resolution.
    sim_stack_labels: List[str] = [c.get("label") or c.get("ref_id", "") for c in carry_context.open_containers]
    chunk_heading_depths: Dict[str, int] = {}
    used_block_idxs: Dict[int, set] = {}
    # First pass: resolve depth + locate a block index per heading.
    located: List[Dict[str, Any]] = []
    warning_counts: Dict[str, int] = {}
    if sequence_repeat_dropped_count:
        warning_counts["ai_sequence_repeat_dropped"] = sequence_repeat_dropped_count
    if cross_page_dupe_count:
        warning_counts["ai_cross_page_repeat_dropped"] = cross_page_dupe_count

    for h in headings:
        root_label = sim_stack_labels[0] if sim_stack_labels else None
        is_subdoc_root = reference_index.is_subdoc_root_candidate(h.heading_text, h.family_hint, root_label)
        # Container-aware Table of Contents suppression (only early TOC pages <= 2)
        is_inside_toc = (h.start_page <= 2) and (
            (bool(h.parent_ref) and h.parent_ref.strip().upper() in ("INDEX", "TABLE OF CONTENTS"))
            or (any(lbl.strip().upper() in ("INDEX", "TABLE OF CONTENTS") for lbl in sim_stack_labels) and not is_subdoc_root)
        )
        if is_inside_toc and h.heading_text.strip().upper() not in ("INDEX", "TABLE OF CONTENTS"):
            continue
        if _is_table_header_or_form_field(h.heading_text) or _is_document_noise_or_running_header(h.heading_text, h.start_page):
            warning_counts["ai_table_header_or_form_field_dropped"] = (
                warning_counts.get("ai_table_header_or_form_field_dropped", 0) + 1
            )
            continue
        depth, warn = _resolve_depth_for_heading(
            h.parent_ref, h.level, carry_ref_map, chunk_heading_depths, sim_stack_len,
            carry_label_map=carry_label_map,
            is_subdoc_root=is_subdoc_root,
        )
        if _TEMP_DEBUG_RAW_HEADINGS:
            print(f"[RAW HEADING] text={h.heading_text[:50]!r} parent_ref={h.parent_ref!r} "
                  f"level={h.level} sim_stack_len_before={sim_stack_len} "
                  f"sim_stack_labels_before={sim_stack_labels[-3:]!r} -> mechanical_depth={depth} warn={warn}")
        # Widened from "only when the mechanical resolver itself flagged
        # uncertainty" after live verification: the confirmed real bug (a
        # heading closing out a real ancestor it shouldn't have) happened
        # via a bare level-only guess with NO parent_ref at all — a path
        # that always returns warn=None, so the original narrower condition
        # never caught it. The actually risky operation is EVICTING a
        # currently-open ancestor (depth < sim_stack_len, before this
        # heading) — that's irreversible for everything nested under what
        # gets closed, whereas nesting deeper or staying level is always
        # safe. The one exception: an exact match against carry_ref_map is
        # the model copying back an opaque id the resolver itself minted —
        # the most reliable signal available, never worth second-guessing.
        # Likewise, an explicit parent_ref that matched cleanly without
        # warning is trusted directly and never overridden.
        is_trusted_ref_id_match = bool(h.parent_ref) and h.parent_ref in carry_ref_map
        # If the heading had an explicit parent_ref that matched cleanly without warning:
        # Closing a single immediate sibling (sim_stack_len - depth <= 1) is standard
        # sibling progression (e.g. Section 2 following Section 1 under the same parent).
        # Only closing MULTIPLE ancestors (sim_stack_len - depth > 1) is a deep eviction
        # that warrants double-checking.
        # But if there was NO parent_ref or resolution was uncertain (warn is not None),
        # ANY eviction (depth < sim_stack_len) warrants AI resolution.
        would_evict = depth < sim_stack_len

        if (
            resolve_parent_via_ai
            and would_evict
            and not is_trusted_ref_id_match
            and provider_config is not None
        ):
            ai_depth = await _resolve_parent_via_ai(
                h.heading_text, list(sim_stack_labels), provider_config, parent_ref_hint=h.parent_ref,
            )
            if _TEMP_DEBUG_RAW_HEADINGS:
                print(f"  [AI MICRO-DECISION] candidates={sim_stack_labels!r} -> ai_depth={ai_depth}")
            if ai_depth is not None:
                if sim_stack_len >= 1 and not is_subdoc_root and ai_depth == 0:
                    ai_depth = 1
                depth, warn = ai_depth, "ai_micro_resolved_parent_ref"

        if _TEMP_DEBUG_RAW_HEADINGS:
            print(f"  [FINAL] text={h.heading_text[:50]!r} -> final_depth={depth} final_warn={warn}")

        # Standalone legal document root reset:
        # Standalone legal instruments (AFFIDAVIT, APPLICATION UNDER SECTION..., PETITION,
        # MEMO OF PARTIES, VAKALATNAMA, POWER OF ATTORNEY) reset to root level (depth 0)
        # unless enclosed inside an Annexure/Schedule/Appendix/Exhibit.
        if reference_index._STANDALONE_LEGAL_DOCS_RE.search(h.heading_text):
            root_is_annexure = False
            if carry_context.open_containers and carry_context.open_containers[0].get("family") in ("annexure", "schedule", "appendix", "exhibit"):
                root_is_annexure = True
            elif sim_stack_labels and re.match(r"^\s*(?:annexure|schedule|appendix|exhibit)\b", sim_stack_labels[0], re.IGNORECASE):
                root_is_annexure = True
            if not root_is_annexure:
                depth = 0

        # Root protection: never evict an active root container unless incoming is a genuine subdoc root
        if sim_stack_len >= 1 and not is_subdoc_root and depth == 0:
            depth = 1

        # Major judicial section boundary clamping:
        if reference_index._MAJOR_JUDICIAL_SECTIONS.match(h.heading_text) and sim_stack_len >= 1:
            depth = 1

        # Stack simulation parity: only push to sim_stack_labels if this heading
        # is a genuine container (not a leaf like point, ground, para, clause).
        # Leaf headings do not push onto nested_stack in apply_heading_events_to_scope,
        # so skipping them here keeps sim_stack_labels in exact lockstep with reality.
        # Long multi-word lines (>9 words) starting with numbers are body sentences/points,
        # not outline section containers (matching reference_index's word cap).
        words = h.heading_text.strip().split()
        is_point_leaf = bool(
            len(words) > 9 and _leading_reference_number(h.heading_text) is not None
        )
        minted_family, _, _, _, _ = reference_index.mint_key_for_heading(
            h.heading_text, boundary_ordinal=0, label_occurrence_counts={},
            family_hint=h.family_hint, number_hint=h.number_hint,
        )
        family_def = reference_index.FAMILIES.get(minted_family)
        is_leaf = (family_def is not None and family_def.get("rank") is None) or is_point_leaf
        if not is_leaf:
            sim_stack_len = depth + 1
            sim_stack_labels = sim_stack_labels[:depth] + [h.heading_text]
        chunk_heading_depths[_norm_for_match(h.heading_text)] = depth
        if warn:
            warning_counts[warn] = warning_counts.get(warn, 0) + 1

        # alt_text: the AI's own best-guess resolution of a garbled heading
        # (e.g. family_hint="annexure", number_hint="A-1" for
        # heading_text="AmexureAl") — tried alongside the verbatim text so
        # a model that "corrects" garbled OCR instead of copying it
        # verbatim can still be grounded.
        alt_text = None
        hinted = reference_index._resolve_ai_hint(h.family_hint, h.number_hint)
        if hinted:
            alt_family, alt_prefix, alt_number_raw = hinted
            alt_key = reference_index.normalize_key(alt_family, alt_prefix, alt_number_raw)
            alt_text = reference_index._format_label_from_key(alt_key)

        result = _locate_heading_in_chunk(
            page_blocks, h.start_page, h.heading_text, used_block_idxs, alt_text=alt_text,
            row_ids=page_row_ids,
        )
        if result is not None:
            found_page, block_idx, match_kind, _ratio = result
            used_block_idxs.setdefault(found_page, set()).add(block_idx)
            if match_kind == "row_merged":
                ground_reason = "ai_row_merged_grounded"
                rids = (page_row_ids or {}).get(found_page) or []
                if block_idx < len(rids) and rids[block_idx] is not None:
                    # Excludes any cell already used BEFORE this heading's own
                    # match attempt (block_idx itself was just added above, so
                    # it's correctly excluded by the j != block_idx check) —
                    # without this filter, a row with a non-contiguous
                    # available-cell gap (some middle cell already claimed by
                    # an earlier heading) would pick that stale, wrong cell
                    # instead of the real partner _try_row_merge_match used.
                    already_used = used_block_idxs[found_page]
                    partner_idxs = [
                        j for j, r in enumerate(rids)
                        if r == rids[block_idx] and j != block_idx and j not in already_used
                    ]
                    if partner_idxs:
                        used_block_idxs[found_page].add(partner_idxs[0])
            elif match_kind in ("exact", "prefix"):
                types = (page_block_types or {}).get(found_page) or []
                rids = (page_row_ids or {}).get(found_page) or []
                blocks = page_blocks.get(found_page) or []
                is_tbl_cell = (
                    block_idx < len(types)
                    and types[block_idx] == "table_cell"
                    and block_idx < len(rids)
                    and rids[block_idx] is not None
                )
                if is_tbl_cell:
                    is_bare, is_enum = _is_bare_heading_marker(h.heading_text)
                    if is_bare:
                        row_id = rids[block_idx]
                        already_used = used_block_idxs[found_page]
                        # Forward search first (Refinement B)
                        forward_candidates = [
                            j for j, r in enumerate(rids)
                            if r == row_id and j > block_idx and j not in already_used
                        ]
                        comp_idx = None
                        for j in forward_candidates:
                            if j < len(blocks) and _is_substantive_heading_companion(blocks[j]):
                                comp_idx = j
                                break
                        # Backward fallback only if no forward cell matches
                        if comp_idx is None:
                            backward_candidates = [
                                j for j, r in enumerate(rids)
                                if r == row_id and j < block_idx and j not in already_used
                            ]
                            for j in backward_candidates:
                                if j < len(blocks) and _is_substantive_heading_companion(blocks[j]):
                                    comp_idx = j
                                    break
                        if comp_idx is not None:
                            comp_text = blocks[comp_idx].strip()
                            clean_marker = h.heading_text.rstrip(".")
                            if is_enum:
                                h.heading_text = f"{h.heading_text.strip()} {comp_text}"
                                h.family_hint = "point"  # Refinement D
                                if not h.number_hint:
                                    h.number_hint = re.sub(r"[^A-Za-z0-9]", "", clean_marker)
                            elif re.match(r"^\d+$", clean_marker):
                                h.heading_text = f"{clean_marker} {comp_text}"
                            else:
                                h.heading_text = f"{clean_marker}. {comp_text}"

                            used_block_idxs[found_page].add(comp_idx)
                            match_kind = "row_merged"
                            ground_reason = "ai_row_merged_grounded"
                            chunk_heading_depths[_norm_for_match(h.heading_text)] = depth  # Refinement E

                            # Refinement E: Table rows are universal leaves (table_row_sourced),
                            # so they never push to nested_stack. Rewind sim_stack_labels if pushed:
                            sim_stack_len = depth
                            sim_stack_labels = sim_stack_labels[:depth]
                        elif found_page != h.start_page:
                            ground_reason = "ai_page_corrected"
                        else:
                            ground_reason = None
                    elif found_page != h.start_page:
                        ground_reason = "ai_page_corrected"
                    else:
                        ground_reason = None
                elif found_page != h.start_page:
                    ground_reason = "ai_page_corrected"
                else:
                    ground_reason = None
            elif match_kind == "fuzzy":
                ground_reason = "ai_fuzzy_grounded"
            elif found_page != h.start_page:
                ground_reason = "ai_page_corrected"
            else:
                ground_reason = None
            if ground_reason:
                warning_counts[ground_reason] = warning_counts.get(ground_reason, 0) + 1
        else:
            found_page, block_idx = h.start_page, None
            ground_reason = "ai_unlocated"
            warning_counts["ai_unlocated"] = warning_counts.get("ai_unlocated", 0) + 1

        # Reject headings on an INDEX / Table of Contents page (they merely point elsewhere)
        if found_page is not None:
            found_blocks = page_blocks.get(found_page, [])
            if _is_index_or_toc_page(found_blocks):
                continue

        located.append({
            "heading": h, "depth": depth, "warn": warn,
            "found_page": found_page, "block_idx": block_idx, "ground_reason": ground_reason,
        })

    # Second pass, per FOUND page (may differ from the AI's claimed
    # start_page — that's the actual mis-anchoring correction): sort by
    # block_idx (unlocated last) so snippet boundaries are computed against
    # the next LOCATED heading on that same page.
    events_by_page: Dict[int, List[Dict[str, Any]]] = {}
    by_page: Dict[int, List[Dict[str, Any]]] = {}
    for item in located:
        by_page.setdefault(item["found_page"], []).append(item)

    for page_num, items in by_page.items():
        blocks = page_blocks.get(page_num, [])
        types = (page_block_types or {}).get(page_num, [])
        items_sorted = sorted(
            items,
            key=lambda i: (i["block_idx"] is None, i["block_idx"] if i["block_idx"] is not None else 0),
        )
        located_items = [i for i in items_sorted if i["block_idx"] is not None]
        for pos, item in enumerate(located_items):
            idx = item["block_idx"]
            next_idx = located_items[pos + 1]["block_idx"] if pos + 1 < len(located_items) else len(blocks)
            item["snippet_blocks"] = blocks[idx:next_idx]

        page_events = []
        # A quoted clause reproduced verbatim mid-argument often spans more
        # than one numbered line ("9.0 Local/Site conditions" immediately
        # followed by its own "9.1 ..." sub-clause) — only the first line is
        # ever immediately preceded by the "which reads as under:" intro
        # itself. Tracks the leading top-level segment of the most recent
        # possible_reproduced_text match (page-local only) so the very next
        # heading sharing that same segment inherits the flag too, instead
        # of being treated as genuine and pushed as a real ancestor.
        last_reproduced_number = None
        for item in items_sorted:  # preserve physical block order on page
            h = item["heading"]
            reasons = [item["warn"]] if item["warn"] else []
            if item["ground_reason"]:
                reasons.append(item["ground_reason"])
            if getattr(h, "heading_confidence_reasons", None):
                for r in h.heading_confidence_reasons:
                    if r not in reasons:
                        reasons.append(r)
            is_reproduced = bool(
                item["block_idx"] and reference_index._preceded_by_reproduced_text_intro(blocks[item["block_idx"] - 1])
            )
            if not is_reproduced and last_reproduced_number is not None:
                this_number = _leading_reference_number(h.heading_text)
                is_reproduced = bool(this_number) and this_number.split(".")[0] == last_reproduced_number.split(".")[0]
            if is_reproduced:
                reasons.append("possible_reproduced_text")
                last_reproduced_number = _leading_reference_number(h.heading_text) or last_reproduced_number
            else:
                last_reproduced_number = None
            if item["block_idx"] is not None and item["block_idx"] < len(types) and types[item["block_idx"]] == "table_cell":
                # A table row/entry can only be a leaf, never a parent —
                # see _GROUNDING_PUSH_BLOCKING_REASONS's own docstring.
                reasons.append("table_row_sourced")
            if item["block_idx"] is not None:
                snippet_blocks = item.get("snippet_blocks") or [blocks[item["block_idx"]]]
                snippet = "\n\n".join(snippet_blocks)
                fingerprint = blocks[item["block_idx"]][:30]
                confidence = "high" if not reasons else "low"
                if getattr(h, "heading_confidence", None) == "low":
                    confidence = "low"
                trimmed = snippet.rstrip()
                truncated = bool(trimmed) and trimmed[-1] not in _SENTENCE_END_CHARS
            else:
                snippet = h.heading_text
                fingerprint = ""
                confidence = "low"
                truncated = False

            page_events.append({
                "heading_text": h.heading_text,
                "depth": item["depth"],
                "end_page": h.end_page if not h.still_open_at_chunk_end else None,
                "block_idx": item["block_idx"],
                "fingerprint": fingerprint,
                "snippet": snippet,
                "truncated": truncated and has_next_page_fn(page_num),
                "heading_confidence": confidence,
                "heading_confidence_reasons": reasons,
                "detection_model": detection_model,
                "start_line_hint": h.start_line_hint,
                "family_hint": h.family_hint,
                "number_hint": h.number_hint,
                "prior_id": getattr(h, "prior_id", None),
            })
        events_by_page[page_num] = page_events

    # Prune non-root subdocs that ground to data table blocks or are unlocated
    filtered_events: Dict[int, List[Dict[str, Any]]] = {}
    for p_num, ev_list in events_by_page.items():
        kept = []
        blocks = page_blocks.get(p_num, [])
        types = (page_block_types or {}).get(p_num, [])
        for ev in ev_list:
            txt = ev.get('heading_text', '')
            is_subdoc_root = reference_index.is_subdoc_root_candidate(txt, ev.get('family_hint'))
            minted_fam, _, _, _, _ = reference_index.mint_key_for_heading(
                txt, boundary_ordinal=0, label_occurrence_counts={},
                family_hint=ev.get('family_hint'), number_hint=ev.get('number_hint'),
            )
            fp = ev.get('fingerprint', '')
            block_idx = ev.get('block_idx')
            # O(1) lookup on the matched block's actual mistune type —
            # confirmed necessary post-HTML-table-fix: before that fix,
            # table content was always ungrounded entirely (fp always ""),
            # so the literal-tag-sniffing fallback below was already dead
            # code (a table_cell's own text never contains raw tags
            # either, same as a markdown pipe-table cell's always did).
            # Now that grounding against tables frequently succeeds, this
            # is the only reliable signal — the fp-prefix text scan is kept
            # only as a defensive fallback when block_idx is unavailable.
            if block_idx is not None and block_idx < len(types):
                is_table = types[block_idx] == "table_cell"
            else:
                is_table = False
                if fp and blocks:
                    for b in blocks:
                        if b.startswith(fp):
                            if b.strip().startswith(('<table', '<tr')) or '<td>' in b:
                                is_table = True
                            break
            if minted_fam in ('subdoc', 'point') and not is_subdoc_root:
                if is_table or not fp:
                    continue
            kept.append(ev)
        filtered_events[p_num] = kept

    return filtered_events, warning_counts


def _rule_prior_for_chunk(
    chunk: ai_indexing.ChunkPlan,
    seed_scope: Optional[Dict[str, Any]],
    total_pages: int,
    rejected_so_far: Set[Tuple[int, str]],
    raw_text_by_page: Dict[int, str],
) -> List[Dict[str, Any]]:
    """Walks chunk.pages calling build_targets_for_page, chaining local_seed.
    Omits any heading previously rejected in rejected_so_far (page, _norm_for_match(text)).
    Assigns chunk-local prior_id R1, R2, ...
    """
    local_seed = seed_scope
    raw_cands: List[Tuple[int, Dict[str, Any]]] = []
    ending_stack = []

    for p in chunk.pages:
        cands, new_scope = reference_index.build_targets_for_page(
            p.text, local_seed, has_next_page=p.page_num < total_pages,
            raw_text=raw_text_by_page.get(p.page_num),
        )
        local_seed = new_scope
        ending_stack = new_scope.get("stack") or []
        for c in cands:
            label = c.get("own_label") or c.get("snippet", "")
            norm_txt = _norm_for_match(label)
            if (p.page_num, norm_txt) in rejected_so_far:
                continue
            raw_cands.append((p.page_num, c))

    container_keys = {entry.get("key") for entry in ending_stack if entry.get("key")}
    for _, c in raw_cands:
        for s in c.get("scope_chain") or []:
            if s.get("key"):
                container_keys.add(s["key"])

    priors: List[Dict[str, Any]] = []
    for idx, (p_num, c) in enumerate(raw_cands):
        key = c.get("key", "")
        family = key.split(":")[0] if ":" in key else key
        family_def = reference_index.FAMILIES.get(family, {})
        rank = family_def.get("rank")
        scope_chain = c.get("scope_chain") or []
        parent_label = scope_chain[-1]["label"] if scope_chain else None
        depth = len(scope_chain) + 1
        heading_text = c.get("own_label") or c.get("snippet", "")
        is_container = key in container_keys

        priors.append({
            "prior_id": f"R{idx + 1}",
            "page_num": p_num,
            "depth": depth,
            "key": key,
            "parent_label": parent_label,
            "heading_text": heading_text,
            "is_container": is_container,
            "rank": rank,
            "raw_candidate": c,
        })

    return priors


def _merge_prior_into_headings(
    result: ai_indexing.ChunkStructureResult,
    priors: List[Dict[str, Any]],
    chunk_page_texts: Dict[int, str],
    page_blocks: Dict[int, List[str]],
    rejected_so_far: Set[Tuple[int, str]],
    warning_counts: Dict[str, int],
) -> Tuple[List[ai_indexing.schema.AIHeading], Set[str]]:
    """Merges rule priors and AI-reviewed output before grounding.
    Returns (merged_headings, verified_rejection_ids).
    """
    priors_by_id = {r["prior_id"]: r for r in priors}
    priors_by_norm = {(r["page_num"], _norm_for_match(r["heading_text"])): r for r in priors}

    # 1. Match AI headings to prior rows
    matched_prior_ids = set()
    for h in result.headings:
        if h.prior_id and h.prior_id in priors_by_id:
            matched_prior_ids.add(h.prior_id)
        norm = _norm_for_match(h.heading_text)
        if (h.start_page, norm) in priors_by_norm:
            matched_prior_ids.add(priors_by_norm[(h.start_page, norm)]["prior_id"])

    # 2. Check rejections
    verified_rejection_ids: Set[str] = set()
    unverified_rejection_ids: Set[str] = set()

    for rej in result.rejected_priors:
        if rej.prior_id not in priors_by_id:
            warning_counts["rule_prior_unknown_id"] = warning_counts.get("rule_prior_unknown_id", 0) + 1
            continue
        if rej.prior_id in matched_prior_ids:
            # Contradiction precedence: both correction and rejection -> correction
            continue

        r = priors_by_id[rej.prior_id]
        p_text = chunk_page_texts.get(r["page_num"], "")
        norm_quote = _norm_for_match(rej.evidence_quote or "")
        norm_page = _norm_for_match(p_text)
        is_valid_reason = rej.reason_code in ai_indexing.VALID_REJECTION_REASONS and rej.reason_code != "unspecified"
        quote_len_ok = len(norm_quote) >= 8
        quote_in_page = norm_quote in norm_page

        if is_valid_reason and quote_len_ok and quote_in_page:
            verified_rejection_ids.add(rej.prior_id)
            rejected_so_far.add((r["page_num"], _norm_for_match(r["heading_text"])))
        else:
            unverified_rejection_ids.add(rej.prior_id)
            warning_counts["rule_prior_rejection_unverified"] = warning_counts.get("rule_prior_rejection_unverified", 0) + 1

    # 3. Check confirmations
    confirmed_ids = set(result.confirmed_prior_ids)
    for cid in confirmed_ids:
        if cid not in priors_by_id:
            warning_counts["rule_prior_unknown_id"] = warning_counts.get("rule_prior_unknown_id", 0) + 1
        # Contradiction precedence: both confirmed and rejected -> rejection iff verified else confirmed
        if cid in verified_rejection_ids:
            pass
        elif cid in unverified_rejection_ids:
            unverified_rejection_ids.discard(cid)

    # 4. Construct surviving prior headings
    surviving_prior_headings: List[ai_indexing.schema.AIHeading] = []
    for r in priors:
        pid = r["prior_id"]
        if pid in matched_prior_ids or pid in verified_rejection_ids:
            continue

        if pid in confirmed_ids:
            h = ai_indexing.schema.AIHeading(
                heading_text=r["heading_text"],
                start_page=r["page_num"],
                end_page=r["page_num"],
                level=r["depth"],
                parent_ref=r["parent_label"],
                prior_id=pid,
                heading_confidence="high",
                heading_confidence_reasons=[],
            )
            surviving_prior_headings.append(h)
        elif pid in unverified_rejection_ids:
            h = ai_indexing.schema.AIHeading(
                heading_text=r["heading_text"],
                start_page=r["page_num"],
                end_page=r["page_num"],
                level=r["depth"],
                parent_ref=r["parent_label"],
                prior_id=pid,
                heading_confidence="low",
                heading_confidence_reasons=["rule_prior_rejection_unverified"],
            )
            surviving_prior_headings.append(h)
        else:
            warning_counts["rule_prior_unreviewed"] = warning_counts.get("rule_prior_unreviewed", 0) + 1
            h = ai_indexing.schema.AIHeading(
                heading_text=r["heading_text"],
                start_page=r["page_num"],
                end_page=r["page_num"],
                level=r["depth"],
                parent_ref=r["parent_label"],
                prior_id=pid,
                heading_confidence="low",
                heading_confidence_reasons=["rule_prior_unreviewed"],
            )
            surviving_prior_headings.append(h)

    # 5. Merge AI headings and surviving prior headings in physical block order per page
    all_by_page: Dict[int, List[ai_indexing.schema.AIHeading]] = {}
    for h in result.headings:
        all_by_page.setdefault(h.start_page, []).append(h)
    for h in surviving_prior_headings:
        all_by_page.setdefault(h.start_page, []).append(h)

    merged_headings: List[ai_indexing.schema.AIHeading] = []
    for pn in sorted(all_by_page.keys()):
        page_heads = all_by_page[pn]
        blocks = page_blocks.get(pn, [])

        def _rank_heading(h):
            res = locate_heading_block(blocks, h.heading_text, used_block_idxs=set())
            idx = res[0] if res is not None else None
            return (idx is None, idx if idx is not None else 0)

        page_heads.sort(key=_rank_heading)
        merged_headings.extend(page_heads)

    return merged_headings, verified_rejection_ids


def _check_rule_prior_key_drift(
    cands: List[Dict[str, Any]],
    events: List[Dict[str, Any]],
    priors: List[Dict[str, Any]],
    warning_counts: Dict[str, int],
) -> None:
    priors_by_id = {r["prior_id"]: r for r in priors}
    for idx, cand in enumerate(cands):
        ev = events[idx] if idx < len(events) else {}
        pid = ev.get("prior_id")
        prior_row = priors_by_id.get(pid) if pid else None
        if prior_row and prior_row.get("key") and cand.get("key") != prior_row["key"]:
            warning_counts["rule_prior_key_drift"] = warning_counts.get("rule_prior_key_drift", 0) + 1
            reasons = cand.setdefault("heading_confidence_reasons", [])
            if "rule_prior_key_drift" not in reasons:
                reasons.append("rule_prior_key_drift")


async def review_mentions_for_job(job_id: str, engine_params: Optional[Dict[str, Any]] = None) -> None:
    """Phase 5a: AI review of detected mentions for a job."""
    all_mentions = database.get_all_reference_mentions_for_job(job_id)
    if not all_mentions:
        return

    try:
        resolved = ai_indexing.resolve_active_config()
    except ai_indexing.AIConfigError as e:
        print(f"[MentionReview] AIConfigError: {e}")
        return

    provider = await ai_indexing.get_provider(resolved.provider_config)
    pages = database.get_pages(job_id)
    page_texts = {p["page_num"]: p.get("text") or "" for p in pages}
    mentions_by_page: Dict[int, List[Dict[str, Any]]] = {}
    for m in all_mentions:
        mentions_by_page.setdefault(m["page"], []).append(m)

    chunk_pages = (engine_params or {}).get("ai_index_chunk_pages") or config.AI_INDEX_CHUNK_PAGES
    page_nums = sorted(page_texts.keys())

    targets = database.get_reference_targets_for_job(job_id)
    target_lookup = reference_index.TargetLookupIndex(targets)

    for i in range(0, len(page_nums), chunk_pages):
        chunk_pns = page_nums[i:i + chunk_pages]
        chunk_mentions = [m for pn in chunk_pns for m in mentions_by_page.get(pn, [])]
        if not chunk_mentions:
            continue

        chunk_pages_input = [ai_indexing.PageInput(page_num=pn, text=page_texts.get(pn, "")) for pn in chunk_pns]
        sys_prompt, user_prompt = ai_indexing.build_mention_review_prompt(chunk_pages_input, chunk_mentions)

        try:
            completion = await provider.complete(system_prompt=sys_prompt, user_prompt=user_prompt)
            reviews = ai_indexing.parse_mention_response(completion.text)
        except Exception as e:
            print(f"[MentionReview] Error reviewing chunk {chunk_pns}: {e}")
            continue

        reviews_by_id = {r.mention_id: r for r in reviews}
        modified_pages = set()

        for m in chunk_mentions:
            m_id = m.get("mention_id") or f"M{m.get('occurrence_index', 0)}"
            rev = reviews_by_id.get(m_id)
            if not rev:
                continue

            p_text = page_texts.get(m["page"], "")
            norm_quote = _norm_for_match(rev.evidence_quote)
            norm_page = _norm_for_match(p_text)
            quote_ok = len(norm_quote) >= 8 and norm_quote in norm_page

            if rev.action == "reject" and quote_ok:
                m["resolution"] = "suppressed"
                m["suppressed_reason"] = "ai_rejected"
                m["detection_source"] = "ai"
                m["detection_model"] = resolved.provider_config.model
                modified_pages.add(m["page"])
            elif rev.action == "unsuppress" and quote_ok:
                m["resolution"] = "exact" if target_lookup.has_exact_key(m["key"]) else "approximate"
                m["suppressed_reason"] = None
                m["detection_source"] = "ai"
                m["detection_model"] = resolved.provider_config.model
                modified_pages.add(m["page"])
            elif rev.action == "confirm":
                m["detection_source"] = "ai"
                m["detection_model"] = resolved.provider_config.model
                modified_pages.add(m["page"])

        if modified_pages:
            version = database.bump_reference_index_version(job_id)
            for pn in modified_pages:
                page_m = mentions_by_page.get(pn, [])
                database.upsert_reference_mentions_for_page(job_id, pn, page_m, version)


async def review_docrefs_for_job(job_id: str, engine_params: Optional[Dict[str, Any]] = None) -> None:
    """Phase 5b: AI review of docref definition targets for a job.
    Finds docref:* keys with 2+ occurrences across the job, prompts the AI to identify
    which occurrence is the actual definition (or 'none' to keep the Phase 1 provisional baseline),
    and promotes the target if an override is chosen."""
    targets = database.get_reference_targets_for_job(job_id)
    docref_targets = {t["key"]: t for t in targets if t["key"].startswith("docref:")}
    if not docref_targets:
        return

    all_mentions = database.get_all_reference_mentions_for_job(job_id)
    occs_by_key: Dict[str, List[Dict[str, Any]]] = {}
    for key, t in docref_targets.items():
        occ_id = f"occ_p{t['page']}_{t.get('occurrence_index', 0)}"
        occs_by_key.setdefault(key, []).append({
            "occurrence_id": occ_id,
            "page": t["page"],
            "occurrence_index": t.get("occurrence_index", 0),
            "raw_text": t.get("own_label") or t.get("fingerprint") or key,
            "snippet": t.get("snippet", ""),
            "fingerprint": t.get("fingerprint", ""),
            "is_current_target": True,
        })
    for m in all_mentions:
        if m["key"].startswith("docref:") and m["key"] in occs_by_key:
            occ_id = f"occ_p{m['page']}_{m.get('occurrence_index', 0)}"
            occs_by_key[m["key"]].append({
                "occurrence_id": occ_id,
                "page": m["page"],
                "occurrence_index": m.get("occurrence_index", 0),
                "raw_text": m.get("raw_text", ""),
                "snippet": m.get("raw_text", ""),
                "fingerprint": m.get("raw_text", ""),
                "is_current_target": False,
            })

    candidate_keys = {k: occs for k, occs in occs_by_key.items() if len(occs) >= 2}
    if not candidate_keys:
        return

    try:
        resolved = ai_indexing.resolve_active_config()
    except ai_indexing.AIConfigError as e:
        print(f"[DocrefReview] AIConfigError: {e}")
        return

    provider = await ai_indexing.get_provider(resolved.provider_config)
    sys_prompt, user_prompt = ai_indexing.build_docref_review_prompt(candidate_keys)

    try:
        completion = await provider.complete(system_prompt=sys_prompt, user_prompt=user_prompt)
        verdicts = ai_indexing.parse_docref_review_response(completion.text)
    except Exception as e:
        print(f"[DocrefReview] Error reviewing docrefs: {e}")
        return

    for verdict in verdicts:
        key = verdict.key
        chosen_id = verdict.definition_occurrence_id
        if not chosen_id or chosen_id.lower() == "none" or key not in candidate_keys:
            continue
        occs = candidate_keys[key]
        chosen_occ = next((o for o in occs if o["occurrence_id"] == chosen_id), None)
        if not chosen_occ:
            continue
        current_target = docref_targets.get(key)
        if not current_target:
            continue
        if chosen_occ["page"] != current_target["page"] or chosen_occ["occurrence_index"] != current_target.get("occurrence_index", 0):
            chosen_occ["detection_model"] = resolved.provider_config.model
            database.promote_docref_target(
                job_id,
                key,
                from_page=current_target["page"],
                to_occurrence=chosen_occ,
            )



async def _extract_macro_skeleton(
    pages: List[ai_indexing.PageInput],
    indexer: ai_indexing.StructureIndexer,
    cancel_check=None,
    raw_text_by_page: Optional[Dict[int, str]] = None,
    coarse_chunk_pages: int = 20,
) -> List[Dict[str, Any]]:
    """Pass 1: Fast macro skeleton extraction using coarse chunks (~20 pages)
    and MACRO_SYSTEM_PROMPT.
    Returns list of macro containers bounded by sibling start_page - 1.
    Refinement 2: Graceful degradation / non-fatal.
    """
    if not pages:
        return []
    total_pages = pages[-1].page_num if pages else 0
    macro_chunks = ai_indexing.plan_chunks(
        pages,
        max_pages=coarse_chunk_pages,
        max_chars=60000,
        overlap_pages=0,
    )
    raw_macro_headings: List[Any] = []
    for m_chunk in macro_chunks:
        if cancel_check and cancel_check():
            break
        annotated_m_chunk = ai_indexing.ChunkPlan(
            pages=[
                ai_indexing.PageInput(
                    page_num=p.page_num,
                    text=reference_index.build_layout_annotated_text(p.text, (raw_text_by_page or {}).get(p.page_num)),
                )
                for p in m_chunk.pages
            ],
            truncated_pages=m_chunk.truncated_pages,
            total_chars=m_chunk.total_chars,
        )
        # Parse plain page blocks and block types for grounding validation
        m_page_blocks = {}
        m_page_types = {}
        for p in m_chunk.pages:
            b, t, _ = reference_index.parse_page_blocks_with_row_ids(p.text)
            m_page_blocks[p.page_num] = b
            m_page_types[p.page_num] = t
        try:
            m_res = await indexer.extract_structure_for_chunk(
                annotated_m_chunk,
                is_macro_pass=True,
                cancel_check=cancel_check,
            )
            if m_res.outcome == ai_indexing.ChunkOutcome.OK and m_res.headings:
                for h in m_res.headings:
                    txt = (h.heading_text or "").strip()
                    if not txt:
                        continue
                    if _is_table_header_or_form_field(txt) or _is_document_noise_or_running_header(txt, h.start_page):
                        continue
                    is_bare, _ = _is_bare_heading_marker(txt)
                    if is_bare:
                        continue

                    # Grounding check: verify that this macro container actually exists
                    # on its claimed start_page (or an immediate neighbor within the chunk).
                    sp = h.start_page
                    blocks = m_page_blocks.get(sp, [])
                    loc = locate_heading_block(blocks, txt, used_block_idxs=set())
                    actual_page = sp
                    if loc is None and (sp - 1) in m_page_blocks:
                        loc = locate_heading_block(m_page_blocks[sp - 1], txt, used_block_idxs=set())
                        if loc is not None:
                            h.start_page = sp - 1
                            actual_page = sp - 1
                    elif loc is None and (sp + 1) in m_page_blocks:
                        loc = locate_heading_block(m_page_blocks[sp + 1], txt, used_block_idxs=set())
                        if loc is not None:
                            h.start_page = sp + 1
                            actual_page = sp + 1

                    # Exclude unlocated macro hallucinations (e.g. LLM guessing section pages from TOC)
                    if loc is None:
                        continue

                    # A macro container is a major top-level document division
                    # (Section, Chapter, Annexure, Schedule, Part), never an internal table cell.
                    act_types = m_page_types.get(actual_page, [])
                    if loc[0] < len(act_types) and act_types[loc[0]] == "table_cell":
                        continue

                    # Grounding check for TOC pages: An INDEX / Table of Contents page is merely
                    # an outline pointing elsewhere, not a substantive container starting on this page.
                    actual_blocks = m_page_blocks.get(h.start_page, [])
                    if _is_index_or_toc_page(actual_blocks):
                        continue

                    raw_macro_headings.append(h)
        except Exception as e:
            logger.warning("Pass 1 macro chunk %s encountered error: %s", m_chunk.page_nums, e)
            continue

    if not raw_macro_headings:
        return []

    raw_macro_headings.sort(key=lambda h: (h.start_page, h.level or 1))

    # Deduplicate headings on the same page with identical normalized text
    seen_on_page: Set[Tuple[int, str]] = set()
    unique_macro: List[Any] = []
    for h in raw_macro_headings:
        norm = _norm_for_match(h.heading_text)
        key = (h.start_page, norm)
        if key in seen_on_page:
            continue
        seen_on_page.add(key)
        unique_macro.append(h)

    # Compute container boundaries (end_page)
    containers: List[Dict[str, Any]] = []
    for idx, h in enumerate(unique_macro):
        lvl = h.level or 1
        end_p = None
        for next_h in unique_macro[idx + 1:]:
            next_lvl = next_h.level or 1
            if next_lvl <= lvl:
                if next_h.start_page > h.start_page:
                    end_p = next_h.start_page - 1
                else:
                    end_p = h.start_page
                break
        if end_p is None:
            end_p = total_pages

        containers.append({
            "heading_text": h.heading_text.strip(),
            "level": lvl,
            "start_page": h.start_page,
            "end_page": end_p,
            "parent_ref": h.parent_ref,
            "raw_heading": h,
        })

    return containers


def _get_active_macro_context(
    macro_skeleton: Optional[List[Dict[str, Any]]],
    page_num: int,
) -> Optional[Dict[str, Any]]:
    """Finds the open level-1 and level-2 macro container active on page_num."""
    if not macro_skeleton:
        return None
    active_root = None
    active_parent = None
    for c in macro_skeleton:
        start_p = c["start_page"]
        end_p = c.get("end_page") or 999999
        if start_p <= page_num <= end_p:
            if c.get("level", 1) == 1:
                active_root = c["heading_text"]
            elif c.get("level") == 2:
                active_parent = c["heading_text"]

    if not active_root and not active_parent:
        return None
    return {
        "root_container": active_root,
        "parent_clause": active_parent,
    }


def _merge_macro_and_micro_events(
    macro_skeleton: Optional[List[Dict[str, Any]]],
    events_by_page: Dict[int, List[Dict[str, Any]]],
    page_blocks: Dict[int, List[str]],
    detection_model: str = "",
) -> Dict[int, List[Dict[str, Any]]]:
    """Refinement 3: Macro & Micro Event Merging on the Same Page:
    - Union with Block Deduplication: For each page, the event stream fed to
      apply_heading_events_to_scope must be the union of Pass 1 macro containers
      starting on that page + Pass 2 micro clauses on that page.
    - If Pass 2 also emitted the section title, match them by block_idx or
      fingerprint/normalized text and deduplicate (keeping Pass 1's level/container metadata).
    - If Pass 2 omitted the section title, Pass 1's container is placed first in the
      page's event list, guaranteeing the root container is pushed before its child clauses.
    """
    if not macro_skeleton:
        return events_by_page

    merged_by_page = {p: list(evs) for p, evs in events_by_page.items()}

    macro_by_page: Dict[int, List[Dict[str, Any]]] = {}
    for mc in macro_skeleton:
        sp = mc.get("start_page")
        if sp is not None:
            macro_by_page.setdefault(sp, []).append(mc)

    for page_num, mc_list in macro_by_page.items():
        cur_events = merged_by_page.setdefault(page_num, [])
        blocks = page_blocks.get(page_num, [])
        used_idxs = {ev.get("block_idx") for ev in cur_events if ev.get("block_idx") is not None}

        for mc in mc_list:
            mc_text = mc.get("heading_text", "").strip()
            if not mc_text:
                continue
            mc_norm = _norm_for_match(mc_text)

            found_existing = False
            for ev in cur_events:
                ev_norm = _norm_for_match(ev.get("heading_text", ""))
                if ev_norm == mc_norm or (len(mc_norm) >= 10 and mc_norm in ev_norm) or (len(ev_norm) >= 10 and ev_norm in mc_norm):
                    ev["depth"] = 0 if mc.get("level", 1) == 1 else ev.get("depth", 0)
                    ev["end_page"] = mc.get("end_page") or ev.get("end_page")
                    reasons = ev.get("heading_confidence_reasons") or []
                    ev["heading_confidence_reasons"] = [r for r in reasons if r != "table_row_sourced"]
                    found_existing = True
                    break

            if not found_existing:
                block_idx = None
                snippet = mc_text
                fingerprint = ""
                loc = locate_heading_block(blocks, mc_text, used_block_idxs=used_idxs)
                if loc is not None:
                    block_idx = loc[0]
                    used_idxs.add(block_idx)
                    fingerprint = blocks[block_idx][:30]
                    snippet = blocks[block_idx]

                macro_ev = {
                    "heading_text": mc_text,
                    "depth": 0 if mc.get("level", 1) == 1 else 1,
                    "end_page": mc.get("end_page"),
                    "block_idx": block_idx,
                    "fingerprint": fingerprint,
                    "snippet": snippet,
                    "truncated": False,
                    "heading_confidence": "high" if block_idx is not None else "low",
                    "heading_confidence_reasons": [] if block_idx is not None else ["ai_unlocated"],
                    "detection_model": detection_model,
                    "start_line_hint": None,
                    "family_hint": None,
                    "number_hint": None,
                }

                if block_idx is None or not cur_events:
                    cur_events.insert(0, macro_ev)
                else:
                    insert_pos = 0
                    while insert_pos < len(cur_events):
                        ev_b = cur_events[insert_pos].get("block_idx")
                        if ev_b is not None and ev_b < block_idx:
                            insert_pos += 1
                        else:
                            break
                    cur_events.insert(insert_pos, macro_ev)

    return merged_by_page



async def reindex_job_with_ai(
    job_id: str, start_page: Optional[int] = None, chunk_pages_override: Optional[int] = None,
    review_rule_prior_override: Optional[bool] = None, overlap_pages_override: Optional[int] = None,
    chunk_chars_max_override: Optional[int] = None,
    adaptive_chunking_override: Optional[bool] = None,
    two_pass_override: Optional[bool] = None,
) -> Dict[str, Any]:
    """Full-job AI reindex, chunked, with per-chunk fallback to the
    rule-based detector. `start_page`, when given, is a targeted re-run
    (triggered after a single page's manual retry) rather than a full
    rebuild — see the bounded/early-stop logic below.

    `chunk_pages_override`, `review_rule_prior_override`,
    `overlap_pages_override`, and `chunk_chars_max_override`, when given,
    each win over both the job's own frozen `engine_params` and the config
    default — used by a manual
    "Rebuild Cross-References" call (main.py), which always wants the
    CURRENT settings, not whatever was frozen at this job's original
    creation time (the same reasoning already applied to
    `enable_ai_indexing` there). The always-rule-based auto-trigger right
    after OCR never passes any of these, so it stays pinned to frozen
    values. `review_rule_prior_override`/`overlap_pages_override`/
    `chunk_chars_max_override` are resolved with explicit `is not None`
    checks, never `or`-chaining — `False` and `0` are both meaningful values
    an `or`-chain would silently discard in favor of the engine_params/config
    fallback.

    Every page's indexing input is picked automatically, once, by
    `_page_text`/`reference_index.build_layout_annotated_text` — there is
    no caller-facing choice between the cleaned OCR text (`text`) and the
    raw, pre-clean_detection_tags() text (`raw_ocr_text`) that still
    carries Unlimited-OCR's `<|det|>type [bbox]<|/det|>` layout tags when
    the job ran with grounding enabled: grounding/persistence always use
    the plain cleaned `text`, and a richer, self-explanatory
    `[OCR-LAYOUT: ...]`-annotated version (built straight from `raw_ocr_text`
    when present, via layout_format.parse_blocks — no alignment with `text`
    needed or attempted) is used ONLY for what gets sent to the AI model's
    prompt — never a second, separately-stored index. A page OCR'd without
    grounding has no tags in `raw_ocr_text` at all, so this is a silent
    no-op (the AI sees the same plain `text` as everything else).

    Checkpoint/resume (start_page=None only — a targeted retry always runs
    from its own explicit start_page): a prior run of THIS function that
    was paused mid-way is resumed from `ai_index_report["ai_last_persisted_
    page"]` rather than restarting at page 1. This is deliberately its own
    tracked value, NOT `database.get_reference_index_frontier()` — that
    frontier is shared with the always-rule-based LIVE per-page hook, which
    already advances it to total_pages during ordinary OCR, so reusing it
    here would make a genuinely fresh AI run think it was already done."""
    job = database.get_job(job_id, include_pages=False)
    if not job:
        return {}
    total_pages = job.get("total_pages") or 0
    engine_params = job.get("engine_params") or {}
    chunk_pages_size = chunk_pages_override or engine_params.get("ai_index_chunk_pages") or config.AI_INDEX_CHUNK_PAGES
    # Primary behavior as of Round 10's A/B test (job bb417471): the AI
    # itself detects and nests numbered pleading points/grounds, rather than
    # the deterministic detect_pleading_points_for_page augmentation —
    # confirmed to nest an entire 22-item mixed arabic/roman grounds list
    # correctly in one pass (something 4 rounds of narrowing regex fixes
    # never fully achieved), at the cost of occasionally missing a point
    # elsewhere that the regex safety net would have caught. A job's
    # engine_params can still explicitly set this to False to opt back into
    # the regex-augmented behavior instead.
    pleading_points_via_ai = bool(engine_params.get("ai_index_pleading_points_via_ai", True))
    # Off by default — same-page heading order is normally the model's own
    # array order (the only signal available when start_line_hint is
    # absent/unreliable); when on AND every heading on a page reports a
    # non-null hint, that hint is used as a secondary sort key instead. No
    # measured accuracy data exists for this hint yet (the prompt itself
    # documents it as "stored for possible future use only"), so this
    # ships inert, for live A/B testing before ever defaulting on.
    order_by_line_hint = bool(engine_params.get("ai_index_order_by_line_hint", False))
    # Off by default — a per-heading AI micro-decision (see
    # _resolve_parent_via_ai) that replaces _resolve_depth_for_heading's
    # fuzzy-string/positional fallback specifically, not its exact-match
    # path. Ships behind this flag for A/B testing before becoming the
    # default, mirroring pleading_points_via_ai's own rollout above.
    resolve_parent_via_ai = bool(engine_params.get("ai_index_resolve_parent_via_ai", False))
    review_rule_prior = (
        review_rule_prior_override if review_rule_prior_override is not None
        else bool(engine_params.get("ai_index_review_rule_prior", config.AI_INDEX_REVIEW_RULE_PRIOR))
    )
    overlap_pages = (
        overlap_pages_override if overlap_pages_override is not None
        else engine_params.get("ai_index_chunk_overlap_pages", config.AI_INDEX_CHUNK_OVERLAP_PAGES)
    )
    chunk_chars_max = (
        chunk_chars_max_override if chunk_chars_max_override is not None
        else engine_params.get("ai_index_chunk_chars_max", config.AI_INDEX_CHUNK_CHARS_MAX)
    )
    adaptive_chunking = (
        adaptive_chunking_override if adaptive_chunking_override is not None
        else bool(engine_params.get("ai_index_adaptive_chunking", config.AI_INDEX_ADAPTIVE_CHUNKING))
    )
    two_pass = (
        two_pass_override if two_pass_override is not None
        else bool(engine_params.get("ai_index_two_pass", config.AI_INDEX_TWO_PASS))
    )
    review_mentions = bool(engine_params.get("ai_index_review_mentions", False))

    try:
        resolved = ai_indexing.resolve_active_config()
    except ai_indexing.AIConfigError as e:
        database.update_ai_index_report(job_id, status="failed", error=str(e))
        return {"status": "failed", "error": str(e)}

    indexer = ai_indexing.StructureIndexer(resolved)

    all_pages = database.get_pages(job_id)
    resume_from = None
    if start_page is None:
        prior_report = database.get_ai_index_report(job_id)
        if prior_report.get("status") == "paused" and prior_report.get("ai_last_persisted_page"):
            resume_from = prior_report["ai_last_persisted_page"] + 1

    effective_start = start_page if start_page is not None else resume_from
    if effective_start is not None:
        pages = [p for p in all_pages if p["page_num"] >= effective_start]
        seed_scope = database.get_end_of_page_scope(job_id, effective_start - 1) if effective_start > 1 else None
    else:
        pages = all_pages
        seed_scope = None
        database.set_reference_index_frontier(job_id, 0)

    if not pages:
        return {"status": "done", "pages_done": 0}

    def _page_text(p):
        return p.get("text") or ""
    # Kept separately (never fed into PageInput/plan_chunks) so grounding
    # and persistence always see the plain clean text — only the prompt
    # sent to the AI model gets the layout-annotated version, built
    # per-chunk right before the model call (see build_layout_annotated_text).
    raw_text_by_page: Dict[int, str] = {p["page_num"]: (p.get("raw_ocr_text") or "") for p in pages}
    page_inputs = [ai_indexing.PageInput(page_num=p["page_num"], text=_page_text(p)) for p in pages]

    if adaptive_chunking:
        chunks = ai_indexing.plan_adaptive_chunks(
            page_inputs,
            max_pages=chunk_pages_size,
            max_chars=chunk_chars_max,
            overlap_pages=overlap_pages,
        )
    else:
        chunks = ai_indexing.plan_chunks(
            page_inputs,
            max_pages=chunk_pages_size,
            max_chars=chunk_chars_max,
            overlap_pages=overlap_pages,
        )
    total_chunks = len(chunks)
    bounded_stop = start_page is not None

    def _cancel_check():
        return task_queue.is_job_paused(job_id)

    macro_skeleton: Optional[List[Dict[str, Any]]] = None
    if two_pass:
        try:
            macro_skeleton = await _extract_macro_skeleton(
                pages=page_inputs,
                indexer=indexer,
                cancel_check=_cancel_check,
                raw_text_by_page=raw_text_by_page,
            )
            logger.info("Pass 1 extracted %d macro containers for job %s", len(macro_skeleton or []), job_id)
        except Exception as e:
            # Refinement 2: Pass 1 Graceful Degradation / non-fatal fallback
            logger.warning("Pass 1 Macro Skeleton extraction failed, proceeding with standard carry context: %s", e)
            macro_skeleton = None

    continuing = start_page is not None or resume_from is not None
    report = database.get_ai_index_report(job_id)
    report.update({
        "status": "running", "provider": resolved.provider_config.provider,
        "model": resolved.provider_config.model, "chunk_pages": chunk_pages_size,
        "adaptive_chunking": adaptive_chunking, "two_pass": two_pass,
        "current_chunk": 0, "total_chunks": total_chunks,
        "pages_done": report.get("pages_done", 0) if continuing else 0,
        "chunks": report.get("chunks") if continuing else [],
    })
    database.set_ai_index_report(job_id, report)

    last_page_text = ""
    if effective_start and effective_start > 1:
        prev = database.get_page(job_id, effective_start - 1)
        last_page_text = (prev or {}).get("text") or ""

    final_status = "done"
    final_error = ""
    pages_done_total = report.get("pages_done", 0)
    last_persisted_page_num: Optional[int] = None
    carried_headings: Dict[int, List[Any]] = {}
    highest_processed_this_run: int = 0
    rejected_so_far: Set[Tuple[int, str]] = set()

    for chunk_idx, chunk in enumerate(chunks):
        if task_queue.is_job_paused(job_id):
            final_status = "paused"
            break

        if chunk_idx > 0:
            await asyncio.sleep(config.AI_INDEX_MIN_CHUNK_INTERVAL_SECONDS)

        if chunk_idx > 0:
            first_page_num = chunk.pages[0].page_num
            if first_page_num > 1:
                seed_scope = database.get_end_of_page_scope(job_id, first_page_num - 1)
                prev_page = database.get_page(job_id, first_page_num - 1)
                last_page_text = (prev_page or {}).get("text") or ""
            else:
                seed_scope, last_page_text = None, ""

        chunk_start_time = time.perf_counter()
        carry_context = _carry_in_summary(seed_scope, last_page_text)
        # Layout-annotated text is built fresh per chunk, for the MODEL
        # PROMPT ONLY — `chunk` itself (plain clean text) is untouched and
        # keeps being used below for grounding (page_blocks) and the
        # persisted index (chunk_page_texts), so this never produces a
        # second, differently-sourced index for the same pages.
        annotated_chunk = ai_indexing.ChunkPlan(
            pages=[
                ai_indexing.PageInput(
                    page_num=p.page_num,
                    text=reference_index.build_layout_annotated_text(p.text, raw_text_by_page.get(p.page_num)),
                )
                for p in chunk.pages
            ],
            truncated_pages=chunk.truncated_pages,
            total_chars=chunk.total_chars,
        )

        priors: List[Dict[str, Any]] = []
        if review_rule_prior:
            priors = _rule_prior_for_chunk(
                chunk, seed_scope, total_pages, rejected_so_far, raw_text_by_page,
            )

        macro_ctx = None
        if two_pass and macro_skeleton:
            chunk_first_page = chunk.page_nums[0]
            macro_ctx = _get_active_macro_context(macro_skeleton, chunk_first_page)

        extract_kwargs = {
            "carry_context": carry_context,
            "cancel_check": _cancel_check,
            "include_pleading_points": pleading_points_via_ai,
            "rule_prior": priors,
            "review_rule_prior": review_rule_prior,
            "order_by_line_hint": order_by_line_hint,
        }
        if macro_ctx is not None:
            extract_kwargs["macro_context"] = macro_ctx

        try:
            result = await indexer.extract_structure_for_chunk(
                annotated_chunk,
                **extract_kwargs,
            )
        except ai_indexing.AIConfigError as e:
            final_status, final_error = "failed", str(e)
            break

        if result.outcome == ai_indexing.ChunkOutcome.CANCELLED:
            final_status = "paused"
            break

        source = "rule" if result.outcome == ai_indexing.ChunkOutcome.FAILED else "ai"
        events_by_page: Dict[int, List[Dict[str, Any]]] = {}
        warning_counts: Dict[str, int] = {}
        chunk_page_texts = {p.page_num: p.text for p in chunk.pages}

        if source == "ai":
            page_blocks: Dict[int, List[str]] = {}
            page_block_types: Dict[int, List[str]] = {}
            page_row_ids: Dict[int, List[Optional[int]]] = {}
            for p in chunk.pages:
                texts, types, row_ids = reference_index.parse_page_blocks_with_row_ids(p.text)
                page_blocks[p.page_num] = texts
                page_block_types[p.page_num] = types
                page_row_ids[p.page_num] = row_ids

            verified_rejections: Set[str] = set()
            if review_rule_prior and priors:
                raw_headings, verified_rejections = _merge_prior_into_headings(
                    result, priors, chunk_page_texts, page_blocks, rejected_so_far, warning_counts,
                )
            else:
                raw_headings = list(result.headings)

            # Raw heading merge for overlap pages before grounding
            chunk_headings_by_page: Dict[int, List[Any]] = {}
            for h in raw_headings:
                chunk_headings_by_page.setdefault(h.start_page, []).append(h)

            final_headings: List[Any] = []
            for p in chunk.pages:
                pn = p.page_num
                cur_headings = chunk_headings_by_page.get(pn, [])
                carried_for_page = carried_headings.get(pn, [])
                if carried_for_page:
                    cur_norm_texts = {_norm_for_match(h.heading_text) for h in cur_headings}
                    cur_leads = {
                        _leading_reference_number(h.heading_text)
                        for h in cur_headings
                        if _leading_reference_number(h.heading_text)
                    }
                    # Upgrade bare numbers in cur_headings if carried heading has richer text:
                    for idx, ch in enumerate(cur_headings):
                        ch_lead = _leading_reference_number(ch.heading_text)
                        if ch_lead and len(ch.heading_text.strip()) <= 4:
                            for car in carried_for_page:
                                car_lead = _leading_reference_number(car.heading_text)
                                if car_lead == ch_lead and len(car.heading_text.strip()) > 4:
                                    cur_headings[idx] = car
                                    cur_norm_texts.add(_norm_for_match(car.heading_text))
                                    break

                    missed_headings = [
                        h for h in carried_for_page
                        if _norm_for_match(h.heading_text) not in cur_norm_texts
                        and (_leading_reference_number(h.heading_text) is None or _leading_reference_number(h.heading_text) not in cur_leads)
                    ]
                    if missed_headings:
                        blocks = page_blocks.get(pn, [])
                        # Only add a missed heading if it can actually be located on this page's blocks
                        # (do not insert unlocated phantom duplicates carried over from previous chunk)
                        valid_missed = []
                        for h in missed_headings:
                            res = locate_heading_block(blocks, h.heading_text, used_block_idxs=set())
                            if res is not None:
                                valid_missed.append(h)
                        if valid_missed:
                            merged_group = list(cur_headings) + valid_missed

                            def _rank_heading(h):
                                result = locate_heading_block(blocks, h.heading_text, used_block_idxs=set())
                                idx = result[0] if result is not None else None
                                return (idx is None, idx if idx is not None else 0)

                            merged_group.sort(key=_rank_heading)
                            final_headings.extend(merged_group)
                        else:
                            final_headings.extend(cur_headings)
                    else:
                        final_headings.extend(cur_headings)
                else:
                    final_headings.extend(cur_headings)


            if config.AI_INDEX_CHUNK_OVERLAP_PAGES > 0:
                trailing_overlap_pages = [p.page_num for p in chunk.pages[-config.AI_INDEX_CHUNK_OVERLAP_PAGES:]]
                carried_headings = {
                    pn: [h for h in final_headings if h.start_page == pn]
                    for pn in trailing_overlap_pages
                }
            else:
                carried_headings = {}

            events_by_page, grounding_warnings = await _ground_and_resolve_chunk(
                final_headings, page_blocks, carry_context, resolved.provider_config.model,
                has_next_page_fn=lambda pn: pn < total_pages,
                resolve_parent_via_ai=resolve_parent_via_ai, provider_config=resolved.provider_config,
                page_block_types=page_block_types, page_row_ids=page_row_ids,
            )
            warning_counts.update(grounding_warnings)

            # Refinement 3: Macro & Micro Event Merging on the Same Page
            if two_pass and macro_skeleton:
                events_by_page = _merge_macro_and_micro_events(
                    macro_skeleton, events_by_page, page_blocks, resolved.provider_config.model,
                )

            # Review degradation fallback:
            # If review_rule_prior is on, len(priors) >= 3, every prior was rejected,
            # and no AI heading was grounded -> review failure, fall back to rule prior, log review_degraded.
            if (
                review_rule_prior
                and len(priors) >= 3
                and len(verified_rejections) == len(priors)
                and not any(len(events) > 0 for events in events_by_page.values())
            ):
                source = "rule"
                warning_counts["review_degraded"] = warning_counts.get("review_degraded", 0) + 1
        else:
            carried_headings = {}

        stopped_early = False
        pages_processed_this_chunk = 0

        if source == "rule":
            overlap_count = config.AI_INDEX_CHUNK_OVERLAP_PAGES if chunk_idx > 0 else 0
            if overlap_count > 0 and len(chunk.pages) > overlap_count:
                last_overlap_page_num = chunk.pages[overlap_count - 1].page_num
                seed_scope = database.get_end_of_page_scope(job_id, last_overlap_page_num)
                prev_p = database.get_page(job_id, last_overlap_page_num)
                last_page_text = (prev_p or {}).get("text") or ""
            pages_to_process = chunk.pages[overlap_count:]
        else:
            pages_to_process = chunk.pages

        for p in pages_to_process:
            page_num = p.page_num
            text = chunk_page_texts[page_num]
            is_revisit = page_num <= highest_processed_this_run
            previous_scope = (
                database.get_end_of_page_scope(job_id, page_num)
                if (bounded_stop and not is_revisit)
                else None
            )

            if source == "ai":
                events = events_by_page.get(page_num, [])
                cands, new_scope = reference_index.apply_heading_events_to_scope(
                    events, seed_scope, page_num, has_next_page=page_num < total_pages,
                )
                if review_rule_prior and priors:
                    _check_rule_prior_key_drift(cands, events, priors, warning_counts)
                # Additive, deterministic augmentation: numbered/roman
                # pleading paragraphs the AI is deliberately never asked to
                # report as headings (they're body text, not headings) but
                # the user still wants indexed as navigable points/grounds.
                # This is very often the ONLY place a decimal ground/point
                # gets detected at all, so its last_decimal_ground finding
                # must be folded back into new_scope before persistence —
                # otherwise a later page's AI-reported roman sub-grounds
                # have nothing to nest under.
                # Hybrid supplementation: run deterministic pleading point detection to supplement
                # any numbered averments/paragraphs the AI missed, deduplicating against existing candidates.
                extra_cands, last_decimal_ground = reference_index.detect_pleading_points_for_page(
                    text, new_scope, cands,
                )
                if extra_cands:
                    existing_keys = {c.get("key") for c in cands}
                    existing_fps = {c.get("fingerprint") for c in cands if c.get("fingerprint")}
                    to_add = [
                        ec for ec in extra_cands
                        if ec.get("key") not in existing_keys and ec.get("fingerprint") not in existing_fps
                    ]
                    cands = cands + to_add
                    if last_decimal_ground:
                        new_scope["last_decimal_ground"] = last_decimal_ground

                # Hybrid supplementation: run deterministic contract decimal clause and letter point detection
                # to supplement any contract sub-clauses the AI missed, deduplicating against existing candidates.
                contract_cands = reference_index.detect_contract_clauses_for_page(
                    text, new_scope, cands,
                )
                if contract_cands:
                    existing_keys = {c.get("key") for c in cands}
                    existing_fps = {c.get("fingerprint") for c in cands if c.get("fingerprint")}
                    to_add = [
                        cc for cc in contract_cands
                        if cc.get("key") not in existing_keys and cc.get("fingerprint") not in existing_fps
                    ]
                    cands = cands + to_add
            else:
                cands, new_scope = reference_index.build_targets_for_page(
                    text, seed_scope, has_next_page=page_num < total_pages,
                )

            reference_index.validate_scope_shape(new_scope)
            reference_index._persist_page_index(
                job_id, page_num, text, cands, new_scope, provisional=False,
                detection_source=source, detection_model=resolved.provider_config.model,
            )
            current_frontier = database.get_reference_index_frontier(job_id) or 0
            database.set_reference_index_frontier(job_id, max(current_frontier, page_num))
            seed_scope = new_scope
            last_page_text = text
            last_persisted_page_num = page_num
            pages_processed_this_chunk += 1
            highest_processed_this_run = max(highest_processed_this_run, page_num)

            if bounded_stop and not is_revisit and previous_scope is not None and previous_scope == new_scope:
                stopped_early = True
                break

        overlap_count_ai = config.AI_INDEX_CHUNK_OVERLAP_PAGES if (chunk_idx > 0 and source == "ai") else 0
        new_in_chunk = max(pages_processed_this_chunk - overlap_count_ai, 0)
        pages_done_total += new_in_chunk
        duration_seconds = time.perf_counter() - chunk_start_time
        rep = database.get_ai_index_report(job_id)
        chunks_log = rep.get("chunks") or []
        chunks_log.append({
            "first_page": chunk.page_nums[0], "last_page": chunk.page_nums[-1],
            "status": "ok" if source == "ai" else "fallback",
            "error_kind": result.error_kind, "error": result.error_message,
            "heading_count": len(result.headings) if source == "ai" else None,
            "warnings": warning_counts,
            "duration_seconds": round(duration_seconds, 2),
            # Provider-call-only timing, for comparison against the total
            # duration_seconds above — left at 0 by ChunkStructureResult on
            # the failure/timeout path (client.py), so duration_seconds is
            # the one to trust whenever this reads 0 but status != "ok".
            "provider_duration_ms": result.duration_ms,
        })
        database.update_ai_index_report(
            job_id, current_chunk=chunk_idx + 1, total_chunks=total_chunks,
            pages_done=pages_done_total, chunks=chunks_log,
            ai_last_persisted_page=last_persisted_page_num,
        )

        if stopped_early:
            break

    reference_index.build_mention_index_for_job(job_id)
    if review_mentions:
        await review_mentions_for_job(job_id, engine_params)
    if (engine_params or {}).get("ai_index_override_docrefs") or (engine_params or {}).get("ai_index_review_docrefs"):
        await review_docrefs_for_job(job_id, engine_params)
    database.update_ai_index_report(job_id, status=final_status, error=final_error)
    return {"status": final_status, "pages_done": pages_done_total, "total_chunks": total_chunks}
