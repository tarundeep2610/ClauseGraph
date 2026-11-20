"""
layout_format.py
-----------------
Builds a layout-aware Markdown rendering of a page's OCR text directly from
baidu/Unlimited-OCR's raw `<|det|>type [x1,y1,x2,y2]<|/det|>content` tag
stream — only present when a job ran with grounding (`crop_mode=True`).

Deliberately a standalone module, not an addition to reference_index.py: it
shares no code with that module's heading-detection path, and its output is
written to a separate `formatted_text` column, never into `text` —
reference_index.py's (and the frontend's) mistune/marked-based block parsers
both yield zero blocks for a blockquote or a raw HTML wrapper (confirmed
directly against real data and the installed parsers), so writing this
output into `text` would silently delete content from the cross-reference
index and break the viewer's click-to-jump mention anchors. `text` must stay
exactly what it is today; this module's output is purely an additive,
separately-stored rendering.

Built and calibrated against real tagged pages pulled directly from a
production job (see tests/test_layout_format.py's embedded fixtures) rather
than synthetic examples — the same real-data-first discipline
OCR_INDEXING_AND_PERFORMANCE_DECISIONS.md's §7 describes for the
heading-nesting work, after an earlier draft of this exact module was found
(via that same discipline) to have 5 real bugs before any of this was
written. In particular: bbox coordinates are confirmed page-global and
0-999 normalized (verified across 3,513 real bboxes: min x1=37, min y1=10,
max x2=999, max y2=999) — not tile-local — so no per-tile translation is
needed and every rule below works in that same resolution-independent scale.
"""

import re
import statistics
from typing import List, NamedTuple, Optional, Tuple

_DET_TAG_RE = re.compile(r"<\|det\|>(?P<type>\w+)\s*\[(?P<bbox>[^\]]*)\]<\|/det\|>", re.DOTALL)

# Matches the exact footer unlimited_ocr_engine.run_ocr_with_raw()/
# run_ocr_multi() appends to the cleaned text it stores — this module is
# built from raw_text (which never has the footer, since it's added after
# clean_detection_tags() in the engine), but build_formatted_markdown()
# takes the already-stored `text` only to recover this one footer, which
# raw_text can never provide on its own.
_FOOTER_RE = re.compile(r"\n*---\n\*⏱️.*\Z", re.DOTALL)

# Block types the model is observed to emit (real data: text, title, header,
# footer, page_number, image, table). Only `title` gets heading markup —
# `header` tags repeated page letterhead on real documents (confirmed: pages
# 40-61 of a real job), not section headings, so it is deliberately excluded
# despite reference_index.py using "title"/"header" together as a *heading
# corroboration* signal elsewhere (a different, additive-only use that never
# unconditionally inserts a heading the way this module's formatting does).
_HEADING_TYPES = {"title"}

# Conservative, real-data-calibrated constants (see module docstring on the
# fixtures these came from). Deliberately erring toward under-triggering —
# "never force an uncertain layout call" is the same principle the project's
# own decision log already settled on for position-based signals.
_GAP_SECTION_BREAK_RATIO = 2.5  # gap must exceed this multiple of the page's own median gap
_INDENT_THRESHOLD = 150  # x1 must exceed the page's own median body-text x1 by this much (0-999 scale)
_COLUMN_MIN_GAP = 150  # minimum x1 gap (0-999 scale) to call two blocks separate columns
_COLUMN_RESET_THRESHOLD = 30  # how far y1 must drop to count as "back to the top of a new column"
# Column 2 must start within this fraction of column 1's own vertical span to
# count as a genuine side-by-side layout. Found via a real false positive: a
# full page of body paragraphs (y 130-849) got swept into "column 1" purely
# because none of them triggered a y1-reset, paired with a short signature
# block (y 766-865) that only overlaps the last ~14% of that range — not a
# real 2-column layout, just an ordinary single-column page ending in a
# place/date + signature flourish offset to the right. A real side-by-side
# pair (verified: a stamp-note header, column 2 starting ~36% into column
# 1's span) starts much earlier than this.
_COLUMN_Y_OVERLAP_START_RATIO = 0.5


class Block:
    __slots__ = ("type", "text", "bbox")

    def __init__(self, type_: str, text: str, bbox: Optional[Tuple[int, int, int, int]]):
        self.type = type_
        self.text = text
        self.bbox = bbox  # (x1, y1, x2, y2) in 0-999 page-normalized units, or None

    def __repr__(self):
        return f"Block({self.type!r}, {self.text[:30]!r}, bbox={self.bbox})"


def _parse_bbox(raw: str) -> Optional[Tuple[int, int, int, int]]:
    try:
        parts = [int(p.strip()) for p in raw.split(",")]
    except (ValueError, AttributeError):
        return None
    return tuple(parts) if len(parts) == 4 else None


def parse_blocks(raw_text: str) -> List[Block]:
    """Parses the raw <|det|> tag stream into ordered Blocks, in document
    order. Unlike reference_index.parse_layout_blocks(), empty-content
    blocks are KEPT — an `image` tag carries no text but marks a real region
    of the page (confirmed: 102/102 real `image` tags in one job had empty
    content) — this module has no need to stay 1:1 with a separately-parsed
    clean-text block list (that's the exact correspondence
    reference_index.py's layout_aligned gate needs and often fails to get;
    this module sidesteps the problem entirely by building only from
    raw_text, never cross-referencing clean_text's own block count)."""
    if not raw_text:
        return []
    matches = list(_DET_TAG_RE.finditer(raw_text))
    blocks = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(raw_text)
        content = raw_text[start:end].strip()
        blocks.append(Block(m.group("type"), content, _parse_bbox(m.group("bbox"))))
    return blocks


def page_median_x1(blocks: List[Block]) -> Optional[float]:
    """The page's own left-margin baseline, from its `text`-type blocks'
    x1 — public (not underscore-prefixed) because reference_index.py's
    build_layout_annotated_text() needs it too, alongside is_indented()."""
    xs = [b.bbox[0] for b in blocks if b.bbox is not None and b.type == "text"]
    return statistics.median(xs) if xs else None


def is_indented(block: Block, median_x1: Optional[float]) -> bool:
    """Margin-relative indentation check, factored out so
    reference_index.build_layout_annotated_text() can reuse the exact same
    rule as this module's own blockquote formatting — one calibration, two
    consumers. `_INDENT_THRESHOLD` was tuned for a cosmetic Markdown
    decision (a false positive there just looks slightly off); reused as an
    AI-prompt signal, a false positive actively misinforms the model — if
    heading levels start coming back wrong, re-check this threshold first."""
    return (
        block.type == "text"
        and block.bbox is not None
        and median_x1 is not None
        and block.bbox[0] - median_x1 > _INDENT_THRESHOLD
    )


def _median_gap(blocks: List[Block]) -> Optional[float]:
    """Median vertical gap between consecutive blocks that both have a bbox
    and are in normal forward reading order (y1 non-decreasing) — a column
    reset's large *negative* jump must never pollute this baseline, or every
    gap on a two-column page would look abnormally large by comparison."""
    gaps = []
    prev = None
    for b in blocks:
        if b.bbox is None:
            continue
        if prev is not None and b.bbox[1] >= prev.bbox[1]:
            gap = b.bbox[1] - prev.bbox[3]
            if gap >= 0:
                gaps.append(gap)
        prev = b
    return statistics.median(gaps) if gaps else None


def _format_block_text(block: Block, html_context: bool = False) -> str:
    """`html_context=True` is for content going inside a raw HTML block (a
    column-run's <table>, see _render_column_run) — confirmed directly
    against the installed marked+DOMPurify pipeline that marked does NOT
    reprocess Markdown syntax inside a raw HTML block, so a `## ` prefix
    there would render as literal, visible hash marks instead of a heading.
    HTML markup is used instead in that case, since it passes through
    untouched either way."""
    text = block.text
    if block.type in _HEADING_TYPES:
        if not text:
            return text
        if html_context:
            return f"<strong>{text}</strong>"
        if not text.lstrip().startswith("#"):
            return f"## {text}"
        return text
    if block.type == "image":
        return "*[image]*" if not text else text
    return text


def _cluster_1d(values: List[int], min_gap: int) -> List[List[int]]:
    """Gap-based 1D clustering: sort, then start a new cluster whenever the
    gap to the next value exceeds min_gap."""
    if not values:
        return []
    ordered = sorted(values)
    clusters = [[ordered[0]]]
    for v in ordered[1:]:
        if v - clusters[-1][-1] > min_gap:
            clusters.append([v])
        else:
            clusters[-1].append(v)
    return clusters


class ColumnRun(NamedTuple):
    start: int          # index into blocks, inclusive
    end: int             # index into blocks, exclusive (the terminating full-width block, if any, is NOT included)
    left: List[Block]
    right: List[Block]


def _detect_column_runs(blocks: List[Block]) -> List[ColumnRun]:
    """Finds contiguous spans where the model emitted one column's blocks
    completely before the next — confirmed real pattern: a real page's raw
    tag stream lists every left-column block (increasing y1) immediately
    followed by every right-column block (increasing y1, starting back near
    the top), ending at a block whose own bbox bridges both columns (e.g. a
    full-width title). Bails out (no run recorded, or a run truncated
    early) on anything that doesn't cleanly match — never forces an
    uncertain split, matching the project's own established principle for
    position-based signals (OCR_INDEXING_AND_PERFORMANCE_DECISIONS.md §7.3)."""
    runs: List[ColumnRun] = []
    n = len(blocks)
    i = 0
    while i < n:
        b = blocks[i]
        if b.bbox is None:
            i += 1
            continue
        # Look for a later block whose y1 drops back notably below the max
        # y1 reached so far since i — the "reset to the top of a new column"
        # signal marking where column 2 starts. Everything from i up to
        # (not including) that block is column 1.
        max_y1 = b.bbox[1]
        reset_j = None
        j = i + 1
        while j < n:
            bj = blocks[j].bbox
            if bj is None:
                break
            if bj[1] < max_y1 - _COLUMN_RESET_THRESHOLD:
                reset_j = j
                break
            max_y1 = max(max_y1, bj[1])
            j += 1
        if reset_j is None:
            i += 1
            continue

        left = blocks[i:reset_j]
        left_x1 = [c.bbox[0] for c in left if c.bbox is not None]
        if not left_x1:
            i += 1
            continue
        # The blocks since i must themselves cluster as ONE tight x-group to
        # count as a genuine column-1 candidate — otherwise `left` can pick
        # up an unrelated earlier block (e.g. a corner page-number marker)
        # purely because it never triggered a y1-reset against what follows.
        if len(_cluster_1d(left_x1, _COLUMN_MIN_GAP)) != 1:
            i += 1
            continue
        left_max = max(left_x1)

        # Walk forward from reset_j collecting column-2 blocks, stopping at
        # the first block that either bridges both columns (terminates the
        # run, excluded from both cells) or doesn't fit as a plausible
        # continuation of column 2 at all (bail — truncate the run here
        # rather than guess).
        right: List[Block] = []
        right_min = right_max = right_prev_y1 = None
        end = reset_j
        k = reset_j
        while k < n:
            bk = blocks[k].bbox
            if bk is None:
                break
            if right_min is None:
                # First column-2 block: must sit clearly to the right of
                # column 1 (real gap between the two clusters), or this was
                # never a genuine 2-column split.
                if bk[0] - left_max <= _COLUMN_MIN_GAP:
                    break
                right_min, right_max, right_prev_y1 = bk[0], bk[2], bk[1]
                right.append(blocks[k])
                k += 1
                continue
            # Real false positive found: an unrelated block (a different
            # corner's own logo/header image, y1 near the top of the page)
            # passed the x-gap check below purely because right_max had
            # drifted wide from earlier, genuinely-wider blocks in this same
            # run, letting an unrelated later block "fit" by x-position alone.
            # Column 2 must keep flowing downward in y like column 1 does —
            # a block whose y1 drops back sharply is not a real continuation
            # of it, same reset logic used to find column 2 in the first
            # place.
            if bk[1] < right_prev_y1 - _COLUMN_RESET_THRESHOLD:
                end = k
                break
            gap_mid = (left_max + right_min) / 2.0
            bridges = bk[0] < gap_mid and bk[2] > gap_mid
            if bridges:
                end = k
                break
            fits_right = bk[0] - right_max > -_COLUMN_MIN_GAP or (right_min - _COLUMN_MIN_GAP) <= bk[0]
            if not fits_right:
                end = k
                break
            right_min = min(right_min, bk[0])
            right_max = max(right_max, bk[2])
            right_prev_y1 = bk[1]
            right.append(blocks[k])
            k += 1
        else:
            end = n

        # Real data turned up a false positive: a page whose only "column 2"
        # candidate was a genuinely empty-content block (an `image` tag with
        # no text, sitting at a column-like x-offset purely by coincidence)
        # paired with one harmless word on the left — wrapping that single
        # word in a near-empty 2-cell table reads worse than leaving it as
        # plain text. Require at least one non-empty block on BOTH sides —
        # real multi-page margin-stamp columns (also found in the same real
        # data) always have genuine content on both sides even when some
        # individual entries in the run are empty.
        if not right or not any(b.text for b in left) or not any(b.text for b in right):
            # No genuine second column found (first candidate block after
            # the reset didn't sit right of column 1 at all, or neither side
            # has real content) — not a real column split, move past just
            # the reset point.
            i = reset_j
            continue

        # A genuine side-by-side layout has column 2 starting not far below
        # where column 1 started — a long run of ordinary body paragraphs
        # ending in a short, offset signature/stamp block (real false
        # positive — see _COLUMN_Y_OVERLAP_START_RATIO's docstring) instead
        # has column 2 trailing in near the very end of column 1's span.
        left_y1 = min(b.bbox[1] for b in left if b.bbox is not None)
        left_y2 = max(b.bbox[3] for b in left if b.bbox is not None)
        right_y1 = min(b.bbox[1] for b in right if b.bbox is not None)
        left_span = left_y2 - left_y1
        if left_span > 0 and (right_y1 - left_y1) > _COLUMN_Y_OVERLAP_START_RATIO * left_span:
            i = reset_j
            continue

        runs.append(ColumnRun(start=i, end=end, left=list(left), right=right))
        i = end
    return runs


def _render_column_run(run: ColumnRun) -> str:
    left_html = "<br>".join(_format_block_text(b, html_context=True) for b in run.left if b.text)
    right_html = "<br>".join(_format_block_text(b, html_context=True) for b in run.right if b.text)
    return f"<table><tr><td>{left_html}</td><td>{right_html}</td></tr></table>"


def build_formatted_markdown(raw_text: Optional[str], stored_text: Optional[str] = None) -> Optional[str]:
    """Builds the layout-aware `formatted_text` value for a page.

    Returns None when there's no usable tag data (raw_text absent, or no
    recognizable <|det|> tags — e.g. the GLM engine, or any page OCR'd
    before raw_text capture existed) — callers store None, and every
    consumer (viewer, export) already falls back to the plain `text` column
    on None.

    `stored_text`, if given, is only consulted to recover the `⏱️ Execution
    Time` footer unlimited_ocr_engine.py appends to the *cleaned* text after
    this module's own input (raw_text) was already captured — raw_text
    itself never contains that footer, so there's nothing to parse out of
    it; it's simply re-appended verbatim here so formatted_text carries the
    same footer the plain text does.
    """
    blocks = parse_blocks(raw_text) if raw_text else []
    if not blocks:
        return None

    column_runs = _detect_column_runs(blocks)
    run_by_start = {r.start: r for r in column_runs}
    in_run = set()
    for r in column_runs:
        in_run.update(range(r.start, r.end))

    median_gap = _median_gap(blocks)
    median_x1 = page_median_x1(blocks)

    def gap_needs_extra_blank(leading_block: Block) -> bool:
        if (
            leading_block.bbox is not None
            and prev_block is not None
            and prev_block.bbox is not None
            and median_gap is not None
            and median_gap > 0
        ):
            gap = leading_block.bbox[1] - prev_block.bbox[3]
            return gap > 0 and gap > _GAP_SECTION_BREAK_RATIO * median_gap
        return False

    def emit(rendered: str, extra_blank: bool) -> None:
        if parts:
            parts.append("\n\n" + ("\n\n" if extra_blank else "") + rendered)
        else:
            parts.append(rendered)

    parts: List[str] = []
    i = 0
    n = len(blocks)
    prev_block: Optional[Block] = None
    while i < n:
        if i in run_by_start:
            run = run_by_start[i]
            emit(_render_column_run(run), gap_needs_extra_blank(run.left[0] if run.left else blocks[i]))
            prev_block = blocks[run.end - 1] if run.end > run.start else prev_block
            i = run.end
            continue
        if i in in_run:
            # Should not happen (run ranges are contiguous from their own
            # start), but never silently swallow a block if it does.
            i += 1
            continue

        block = blocks[i]
        if not block.text and block.type != "image":
            i += 1
            continue

        rendered = _format_block_text(block)
        extra_blank = gap_needs_extra_blank(block)

        if is_indented(block, median_x1):
            rendered = f"> {rendered}"

        emit(rendered, extra_blank)
        prev_block = block
        i += 1

    body = "".join(parts).strip()
    if not body:
        return None

    if stored_text:
        footer_match = _FOOTER_RE.search(stored_text)
        if footer_match:
            body += footer_match.group(0)

    return body
