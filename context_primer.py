"""Best-effort rolling context primer for sequential single-page OCR calls, plus the
shared "does this content plausibly continue that table" predicate used by both
confidence.py (boundary_continuity_mismatch) and seam_stitching.py (table merging).

This module is NOT the continuity guarantee — that's seam_stitching.py, which runs
uniformly over every page pair regardless of chunk/concurrency configuration. This
primer is a quality bonus for pages processed in true immediate sequence.
"""
import re
from typing import List, Optional, TypedDict

_UNLIMITED_FOOTER_RE = re.compile(r"\n*-{2,}\n\*⏱️.*$", re.DOTALL)
_GLM_TILING_HEADER_RE = re.compile(r"###\s*Document Section:[^\n]*\n*")
_LONE_SEPARATOR_RE = re.compile(r"\n-{2,}\n")
_TABLE_SEP_ROW_RE = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?$")

_SKIP_ROW_KEYWORDS = (
    "SUBTOTAL", "SUB-TOTAL", "SUB TOTAL", "SECTION", "CARRIED FORWARD",
    "BROUGHT FORWARD", "TOTAL",
)

_SENTENCE_ENDERS = ".!?:;।"  # Devanagari danda included


class Primer(TypedDict, total=False):
    tail: str
    ends_mid_table: bool
    ends_mid_sentence: bool
    table_header: Optional[str]


def strip_engine_artifacts(text: str) -> str:
    """Removes engine-specific formatting (Unlimited's timing footer, GLM's tiling
    section headers/separators) and error-sentinel pages, so a primer/continuation
    check never sees anything but real transcribed content."""
    if not text:
        return ""
    if text.lstrip().startswith("### ❌") or "Unlimited-OCR inference failed" in text[:200]:
        return ""
    cleaned = _UNLIMITED_FOOTER_RE.sub("", text)
    cleaned = _GLM_TILING_HEADER_RE.sub("", cleaned)
    cleaned = _LONE_SEPARATOR_RE.sub("\n", cleaned)
    return cleaned.strip()


def get_tail(text: str, max_chars: int = 300) -> str:
    """Last max_chars of cleaned text, cut back to a whitespace boundary."""
    cleaned = strip_engine_artifacts(text)
    if not cleaned:
        return ""
    if len(cleaned) <= max_chars:
        tail = cleaned
    else:
        tail = cleaned[-max_chars:]
        space_idx = tail.find(" ")
        if 0 <= space_idx < len(tail) - 1:
            tail = tail[space_idx + 1:]
    return re.sub(r"\n{3,}", "\n\n", tail).strip()


def _content_lines(text: str) -> List[str]:
    return [l for l in strip_engine_artifacts(text).splitlines() if l.strip()]


def looks_like_table_row(line: str) -> bool:
    line = line.strip()
    return line.startswith("|") and line.endswith("|") and line.count("|") >= 3


def ends_mid_table(text: str) -> bool:
    lines = _content_lines(text)
    if not lines:
        return False
    return looks_like_table_row(lines[-1])


def ends_mid_sentence(text: str) -> bool:
    cleaned = strip_engine_artifacts(text).rstrip()
    if not cleaned:
        return False
    return cleaned[-1] not in _SENTENCE_ENDERS


def table_header(text: str, max_chars: int = 200) -> Optional[str]:
    """Nearest preceding header row + separator row, scanning backward from the end."""
    lines = _content_lines(text)
    for i in range(len(lines) - 1, 0, -1):
        if _TABLE_SEP_ROW_RE.match(lines[i].strip()):
            combined = f"{lines[i - 1].strip()}\n{lines[i].strip()}"
            return combined[:max_chars]
    return None


def build_primer(prev_page_text: str, tail_chars: int = 300) -> Optional[Primer]:
    """Builds a primer from the previous page's text, or None if there's nothing
    usable (error sentinel, empty page)."""
    if not strip_engine_artifacts(prev_page_text):
        return None
    return {
        "tail": get_tail(prev_page_text, tail_chars),
        "ends_mid_table": ends_mid_table(prev_page_text),
        "ends_mid_sentence": ends_mid_sentence(prev_page_text),
        "table_header": table_header(prev_page_text),
    }


def render_primer(prompt: str, primer: Optional[Primer], enabled: bool = True) -> str:
    """Byte-identical to `prompt` when disabled or there's no usable primer — this is
    the zero-regression guarantee for pages that don't have one."""
    if not enabled or not primer or not primer.get("tail"):
        return prompt
    lines = [
        "",
        "CONTEXT FROM THE PREVIOUS PAGE — for continuity only. Do NOT transcribe,",
        "repeat, or continue this text. Transcribe ONLY what is visible in the",
        "current image.",
    ]
    if primer.get("ends_mid_table"):
        lines.append("- Previous page ended mid-table: yes")
        header = primer.get("table_header")
        if header:
            lines.append(f"- Table columns in progress: {header.splitlines()[0]}")
    if primer.get("ends_mid_sentence"):
        lines.append("- Previous page ended mid-sentence: yes")
    lines.append(f"- Last lines of previous page: \"…{primer['tail']}…\"")
    return prompt + "\n" + "\n".join(lines)


# ─── Shared table-continuation predicate (confidence.py + seam_stitching.py) ──────

def _is_skippable_non_data_row(line: str) -> bool:
    """Section dividers / subtotal rows that can legitimately interrupt a continued
    table without meaning the continuation itself is broken."""
    stripped = line.strip()
    inner = stripped.strip("|").strip()
    if not inner:
        return True
    if any(k in inner.upper() for k in _SKIP_ROW_KEYWORDS):
        return True
    cells = [c.strip() for c in inner.split("|")]
    non_empty = [c for c in cells if c]
    return len(non_empty) <= 1


def _leading_cell_pattern(cell: str) -> str:
    cell = cell.strip()
    if not cell:
        return "empty"
    if re.match(r"^\d{4}$", cell):
        return "year"
    if re.match(r"^\d+(\.\d+)*$", cell):
        return "numeric"
    if re.match(r"^[A-Za-z]$", cell):
        return "letter"
    return "text"


def continues_table(
    prior_header: Optional[str],
    prior_last_data_row: Optional[str],
    following_text: str,
    max_lines: int = 15,
) -> tuple:
    """Does `following_text` plausibly continue the table described by
    prior_header/prior_last_data_row? Scans up to max_lines, skipping recognizable
    non-data rows (headers, captions, section dividers, subtotals) along the way —
    but the first genuine table-row candidate found must match on column count and
    leading-column pattern, or this returns False immediately (it does NOT keep
    scanning for some later, coincidentally-matching row — that reopens the
    window-overshoot false-negative this predicate exists to prevent).

    Returns (matches: bool, reason: str).
    """
    lines = _content_lines(following_text)[:max_lines]

    prior_col_count = None
    if prior_header:
        header_lines = prior_header.splitlines()
        if header_lines:
            prior_col_count = header_lines[0].count("|")

    prior_leading_pattern = None
    if prior_last_data_row:
        cells = [c.strip() for c in prior_last_data_row.strip().strip("|").split("|")]
        if cells:
            prior_leading_pattern = _leading_cell_pattern(cells[0])

    for line in lines:
        stripped = line.strip()
        if not looks_like_table_row(stripped):
            continue
        if _is_skippable_non_data_row(stripped):
            continue

        cells = [c.strip() for c in stripped.strip("|").split("|")]
        col_count = stripped.count("|")

        if prior_col_count is not None and col_count != prior_col_count:
            return False, "column_count_mismatch"

        if prior_leading_pattern is not None and cells:
            this_pattern = _leading_cell_pattern(cells[0])
            if this_pattern != prior_leading_pattern and "empty" not in (this_pattern, prior_leading_pattern):
                return False, "leading_column_pattern_mismatch"

        return True, "match"

    return False, "no_table_marker_found"
