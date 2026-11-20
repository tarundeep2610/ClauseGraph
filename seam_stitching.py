"""Global, post-hoc continuity pass over the *entire* assembled document — the actual
"don't lose the thread across page breaks" guarantee (the rolling context primer in
context_primer.py is a best-effort quality bonus, not this guarantee). Runs over
every consecutive page pair, not just chunk boundaries, since a table can split at
any page in the source document.

This is an annotation/readability layer, not a repair mechanism — it operates on
whatever text the model already produced, so it can't recover a page the model
outright misparsed. Catching that is confidence.py's `boundary_continuity_mismatch`
job, not this module's.

Two export modes, because they serve conflicting requirements:
  - default: every `## Page N` heading always preserved (citation, RAG chunking,
    human review) — a continuation renders as two correctly-headered fragments.
  - clean_tables=True: merges a detected continuation into one continuous table for
    structured-data consumers (pandas/SQL) who need table validity over page
    provenance — this is the one case where the heading at the seam is dropped,
    specifically because that's this mode's entire stated purpose.
"""
import re
from typing import Dict, List, Optional, TypedDict

from context_primer import continues_table, ends_mid_table, strip_engine_artifacts, table_header

_TABLE_SEP_ROW_RE = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?$")


class Page(TypedDict):
    page: int
    text: str


class Seam(TypedDict):
    from_page: int
    to_page: int
    table_header: Optional[str]


def _content_lines(text: str) -> List[str]:
    return [l for l in strip_engine_artifacts(text).splitlines() if l.strip()]


def _last_data_row(text: str) -> Optional[str]:
    lines = _content_lines(text)
    return lines[-1] if lines else None


def detect_seams(pages: List[Page]) -> List[Seam]:
    """Pages must already be in page-number order. Returns one entry per detected
    table continuation between consecutive pages."""
    seams: List[Seam] = []
    for i in range(len(pages) - 1):
        cur, nxt = pages[i], pages[i + 1]
        cur_text = cur.get("text", "") or ""
        if not ends_mid_table(cur_text):
            continue
        header = table_header(cur_text)
        last_row = _last_data_row(cur_text)
        matches, _reason = continues_table(header, last_row, nxt.get("text", "") or "")
        if matches:
            seams.append({"from_page": cur["page"], "to_page": nxt["page"], "table_header": header})
    return seams


def _header_line(seam_table_header: Optional[str]) -> Optional[str]:
    if not seam_table_header:
        return None
    lines = seam_table_header.splitlines()
    return lines[0].strip() if lines else None


def _next_page_already_has_header(next_text: str, seam_table_header: Optional[str]) -> bool:
    header_line = _header_line(seam_table_header)
    if not header_line:
        return False
    lines = _content_lines(next_text)
    return bool(lines) and lines[0].strip() == header_line


def _strip_leading_header_and_separator(text: str, seam_table_header: Optional[str]) -> str:
    header_line = _header_line(seam_table_header)
    if not header_line:
        return text
    lines = text.splitlines()
    out = []
    stripped_header = False
    stripped_sep = False
    for line in lines:
        s = line.strip()
        if not stripped_header and s == header_line:
            stripped_header = True
            continue
        if stripped_header and not stripped_sep and _TABLE_SEP_ROW_RE.match(s):
            stripped_sep = True
            continue
        out.append(line)
    return "\n".join(out).strip()


def render_export(pages: List[Page], clean_tables: bool = False, stitch: bool = True) -> str:
    """Assembles the final Markdown export. `pages` must be in page-number order —
    output assembly is always by page number, never completion order.

    stitch=False skips continuation detection entirely (used whenever the job had
    no page overlap to detect continuations across — single-page mode always, or
    batch mode when a job disabled overlap) — pages render exactly as OCR'd, no
    seam markers, no re-emitted headers. clean_tables has no effect when stitch is
    False (nothing to merge)."""
    if not stitch:
        return "\n\n---\n\n".join(
            f"## Page {p['page']}\n\n{p.get('text', '') or ''}" for p in pages
        )

    seams = detect_seams(pages)
    seam_by_from_page: Dict[int, Seam] = {s["from_page"]: s for s in seams}

    blocks: List[str] = []
    for idx, p in enumerate(pages):
        page_num = p["page"]
        text = p.get("text", "") or ""
        prev_page_num = pages[idx - 1]["page"] if idx > 0 else None
        seam = seam_by_from_page.get(prev_page_num) if prev_page_num is not None else None

        if seam and clean_tables:
            # This is the one place a heading is dropped — clean_tables mode exists
            # specifically to produce one continuous, structurally valid table.
            continuation = text
            if _next_page_already_has_header(text, seam["table_header"]):
                continuation = _strip_leading_header_and_separator(text, seam["table_header"])
            if blocks:
                blocks[-1] = blocks[-1].rstrip() + "\n" + continuation
                continue
            # No prior block to merge into (shouldn't happen — a seam always has a
            # predecessor block) — fall through to render normally.

        block = f"## Page {page_num}\n\n"
        if seam and not clean_tables:
            block += f"<!-- seam: pages {seam['from_page']}→{page_num}, table continues -->\n\n"
            if seam["table_header"] and not _next_page_already_has_header(text, seam["table_header"]):
                block += seam["table_header"] + "\n"
        block += text
        blocks.append(block)

    return "\n\n---\n\n".join(blocks)
