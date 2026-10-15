"""Multi-format document export: Markdown (.md, the pre-existing format),
Word (.docx), and PDF — all three rendered from the exact same Markdown
content so none of them can drift out of sync with each other or with the
original GET /api/jobs/{id}/download-result endpoint.

Pipeline: build_markdown() (content/banners — this IS the logic that used to
live only in main.py's download_ocr_result, factored out here so every
format shares it) -> Python-Markdown converts that to HTML -> parse_html_blocks()
walks the HTML into a small structural IR (headings/paragraphs/tables/lists
with bold/italic/underline inline runs) -> render_docx()/render_pdf() each
consume that same IR.

Why this split instead of e.g. xhtml2pdf (HTML -> PDF directly): python-docx
has no HTML importer at all, so *some* HTML -> structured-document walk is
unavoidable for the .docx path. Reusing that same walk for PDF (via
reportlab, already present in this environment) means table/heading/list
handling is written and tested once, not twice per format.
"""

import io
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import List, Optional
from xml.sax.saxutils import escape

import markdown as md_lib
from docx import Document
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer, HRFlowable, ListFlowable, ListItem,
)

import seam_stitching
import task_queue

SUPPORTED_FORMATS = ("md", "docx", "pdf")
SUPPORTED_SCOPES = ("full", "page", "range")

_CONTENT_TYPES = {
    "md": "text/markdown",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
}


class ExportError(ValueError):
    """A bad export request (unknown format/scope, empty page selection, out-of-range page) — callers map this to a 4xx."""


# ─── Page selection ─────────────────────────────────────────────────────────

def select_pages(pages: List[dict], *, scope: str, page_num: Optional[int] = None, page_range: Optional[str] = None) -> List[dict]:
    if scope == "full":
        return pages
    if scope == "page":
        if not page_num:
            raise ExportError("page_num is required for scope=page")
        selected = [p for p in pages if p["page_num"] == page_num]
        if not selected:
            raise ExportError(f"Page {page_num} not found in this document")
        return selected
    if scope == "range":
        if not page_range or not page_range.strip():
            raise ExportError("page_range is required for scope=range")
        total_pages = max((p["page_num"] for p in pages), default=0)
        # Reuses task_queue's own range parser (the same "1-10,25,50-100"
        # syntax already used for upload-time page selection) rather than a
        # second, possibly-drifting implementation of the same grammar.
        wanted = task_queue._parse_page_range(page_range, total_pages)
        selected = [p for p in pages if p["page_num"] in wanted]
        if not selected:
            raise ExportError(f"No pages matched range '{page_range}'")
        return selected
    raise ExportError(f"Unknown scope '{scope}'")


# ─── Markdown content (shared with the pre-existing .md download) ──────────

def build_markdown(job: dict, pages: List[dict], *, include: str = "all", clean_tables: bool = False, include_metadata: bool = True) -> str:
    filename = job["filename"]
    flagged = [p for p in pages if p.get("needs_review")]
    if include == "clean":
        pages = [p for p in pages if not p.get("needs_review")]
    elif include == "review":
        pages = flagged

    stitch = job.get("processing_mode") == "batch" and bool(job.get("enable_overlap", True))
    export_pages = [{"page": p["page_num"], "text": p.get("formatted_text") or p.get("text", "")} for p in pages]
    body = seam_stitching.render_export(export_pages, clean_tables=clean_tables, stitch=stitch)

    if include == "all":
        for p in flagged:
            marker = f"## Page {p['page_num']}\n\n"
            banner = (
                f"## Page {p['page_num']}\n\n> ⚠️ **NEEDS HUMAN REVIEW** — confidence "
                f"{round((p.get('confidence') or 0) * 100)}% ({p.get('review_reason') or 'flagged'}). "
                f"This transcript may be incomplete.\n\n"
            )
            body = body.replace(marker, banner, 1)

    if not include_metadata:
        return body

    header_lines = [
        f"# OCR Extraction Report: {filename}",
        f"- **Engine**: {job['engine'].upper()}",
        f"- **Pages**: {job['total_pages']}",
        f"- **Status**: {job['status'].upper()}",
        f"- **Created At**: {job['created_at']}",
    ]
    if job["status"] == "escalating":
        header_lines.append("- **⚠️ Note**: escalation is still in progress for some pages — this snapshot may improve once it finishes.")
    if include == "all" and flagged:
        header_lines.append(f"\n## Review Summary ({len(flagged)} page(s) flagged)\n")
        for p in flagged:
            header_lines.append(f"- Page {p['page_num']}: confidence {round((p.get('confidence') or 0) * 100)}% — {p.get('review_reason') or 'flagged'}")

    return "\n".join(header_lines) + "\n\n---\n\n" + body


# ─── HTML -> structural IR ──────────────────────────────────────────────────

@dataclass
class Run:
    text: str
    bold: bool = False
    italic: bool = False
    underline: bool = False


@dataclass
class Block:
    kind: str  # 'heading' | 'paragraph' | 'blockquote' | 'table' | 'list' | 'hr'
    level: int = 1                         # heading level
    runs: List[Run] = field(default_factory=list)       # heading / paragraph / blockquote
    rows: List[List[List[Run]]] = field(default_factory=list)  # table: rows of cells of runs
    header_rows: int = 0                   # table: how many leading rows are <thead>
    items: List[List[Run]] = field(default_factory=list)  # list: one run-list per <li>
    ordered: bool = False                  # list: <ol> vs <ul>


_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_BOLD_TAGS = {"strong", "b"}
_ITALIC_TAGS = {"em", "i"}


class _BlockHTMLParser(HTMLParser):
    """Walks the narrow, predictable HTML surface produced by
    `markdown.markdown(..., extensions=["tables", "fenced_code", "nl2br"])` —
    not a general-purpose HTML parser. Unrecognized tags (e.g. <code>, <a>)
    are structurally ignored; their text content still comes through via
    handle_data."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks: List[Block] = []
        self._style_stack: List[str] = []
        self._current_block: Optional[Block] = None
        self._current_runs: Optional[List[Run]] = None
        self._table_current_row: Optional[List[List[Run]]] = None
        self._in_thead = False

    def handle_starttag(self, tag, attrs):
        if tag in _HEADING_TAGS:
            self._current_block = Block(kind="heading", level=int(tag[1]))
            self._current_runs = self._current_block.runs
        elif tag == "p":
            self._current_block = Block(kind="paragraph")
            self._current_runs = self._current_block.runs
        elif tag == "blockquote":
            self._current_block = Block(kind="blockquote")
            self._current_runs = self._current_block.runs
        elif tag == "hr":
            self.blocks.append(Block(kind="hr"))
        elif tag == "table":
            self._current_block = Block(kind="table")
        elif tag == "thead":
            self._in_thead = True
        elif tag == "tbody":
            self._in_thead = False
        elif tag == "tr":
            self._table_current_row = []
        elif tag in ("td", "th"):
            self._current_runs = []
        elif tag in ("ul", "ol"):
            self._current_block = Block(kind="list", ordered=(tag == "ol"))
        elif tag == "li":
            self._current_runs = []
        elif tag in _BOLD_TAGS:
            self._style_stack.append("bold")
        elif tag in _ITALIC_TAGS:
            self._style_stack.append("italic")
        elif tag == "u":
            self._style_stack.append("underline")
        elif tag == "br":
            if self._current_runs is not None:
                self._current_runs.append(Run(text="\n"))

    def handle_endtag(self, tag):
        if tag in _HEADING_TAGS or tag in ("p", "blockquote"):
            if self._current_block is not None:
                self.blocks.append(self._current_block)
            self._current_block = None
            self._current_runs = None
        elif tag == "table":
            if self._current_block is not None:
                self.blocks.append(self._current_block)
            self._current_block = None
        elif tag == "tr":
            if self._current_block is not None and self._table_current_row is not None:
                self._current_block.rows.append(self._table_current_row)
                if self._in_thead:
                    self._current_block.header_rows += 1
            self._table_current_row = None
        elif tag in ("td", "th"):
            if self._table_current_row is not None:
                self._table_current_row.append(self._current_runs or [])
            self._current_runs = None
        elif tag in ("ul", "ol"):
            if self._current_block is not None:
                self.blocks.append(self._current_block)
            self._current_block = None
        elif tag == "li":
            if self._current_block is not None:
                self._current_block.items.append(self._current_runs or [])
            self._current_runs = None
        elif tag in _BOLD_TAGS or tag in _ITALIC_TAGS or tag == "u":
            if self._style_stack:
                self._style_stack.pop()

    def handle_data(self, data):
        if self._current_runs is None or not data:
            return
        self._current_runs.append(Run(
            text=data,
            bold="bold" in self._style_stack,
            italic="italic" in self._style_stack,
            underline="underline" in self._style_stack,
        ))


def parse_html_blocks(html: str) -> List[Block]:
    parser = _BlockHTMLParser()
    parser.feed(html)
    return parser.blocks


def markdown_to_html(markdown_text: str) -> str:
    return md_lib.markdown(markdown_text, extensions=["tables", "fenced_code", "nl2br"])


def _runs_to_plain(runs: List[Run]) -> str:
    return "".join(r.text for r in runs)


# ─── IR -> .docx (python-docx) ──────────────────────────────────────────────

def _add_runs(paragraph, runs: List[Run], bold_override: bool = False):
    if not runs:
        return
    for r in runs:
        if r.text == "\n":
            paragraph.add_run().add_break()
            continue
        run = paragraph.add_run(r.text)
        run.bold = r.bold or bold_override
        run.italic = r.italic
        run.underline = r.underline


def render_docx(blocks: List[Block], title: str) -> bytes:
    doc = Document()
    doc.core_properties.title = title
    for block in blocks:
        if block.kind == "heading":
            doc.add_heading(_runs_to_plain(block.runs), level=min(max(block.level, 1), 9))
        elif block.kind == "paragraph":
            _add_runs(doc.add_paragraph(), block.runs)
        elif block.kind == "blockquote":
            try:
                p = doc.add_paragraph(style="Intense Quote")
            except KeyError:
                p = doc.add_paragraph()  # style unavailable in a bare/base template — plain paragraph is a safe fallback
            _add_runs(p, block.runs)
        elif block.kind == "hr":
            doc.add_paragraph("─" * 40)
        elif block.kind == "list":
            style = "List Number" if block.ordered else "List Bullet"
            for item_runs in block.items:
                try:
                    p = doc.add_paragraph(style=style)
                except KeyError:
                    p = doc.add_paragraph("• ")
                _add_runs(p, item_runs)
        elif block.kind == "table" and block.rows:
            n_cols = max((len(r) for r in block.rows), default=0)
            if n_cols == 0:
                continue
            table = doc.add_table(rows=0, cols=n_cols)
            try:
                table.style = "Light Grid Accent 1"
            except KeyError:
                pass
            for r_idx, row in enumerate(block.rows):
                cells = table.add_row().cells
                for c_idx in range(n_cols):
                    cell_runs = row[c_idx] if c_idx < len(row) else []
                    _add_runs(cells[c_idx].paragraphs[0], cell_runs, bold_override=(r_idx < block.header_rows))
            doc.add_paragraph()
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ─── IR -> .pdf (reportlab) ──────────────────────────────────────────────────

_styles = getSampleStyleSheet()
_quote_style = ParagraphStyle("ExportQuote", parent=_styles["BodyText"], leftIndent=18, textColor=colors.grey)
_heading_styles = {1: _styles["Heading1"], 2: _styles["Heading2"], 3: _styles["Heading3"], 4: _styles["Heading4"]}


def _runs_to_reportlab_markup(runs: List[Run]) -> str:
    parts = []
    for r in runs:
        if r.text == "\n":
            parts.append("<br/>")
            continue
        text = escape(r.text)
        if r.bold:
            text = f"<b>{text}</b>"
        if r.italic:
            text = f"<i>{text}</i>"
        if r.underline:
            text = f"<u>{text}</u>"
        parts.append(text)
    return "".join(parts) or "&nbsp;"


def render_pdf(blocks: List[Block], title: str) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, title=title,
        leftMargin=2 * cm, rightMargin=2 * cm, topMargin=2 * cm, bottomMargin=2 * cm,
    )
    flow = []
    for block in blocks:
        if block.kind == "heading":
            style = _heading_styles.get(block.level, _styles["Heading4"])
            flow.append(Paragraph(_runs_to_reportlab_markup(block.runs), style))
        elif block.kind == "paragraph":
            flow.append(Paragraph(_runs_to_reportlab_markup(block.runs), _styles["BodyText"]))
        elif block.kind == "blockquote":
            flow.append(Paragraph(_runs_to_reportlab_markup(block.runs), _quote_style))
        elif block.kind == "hr":
            flow.append(HRFlowable(width="100%", color=colors.grey))
        elif block.kind == "list":
            items = [ListItem(Paragraph(_runs_to_reportlab_markup(r), _styles["BodyText"])) for r in block.items]
            if items:
                flow.append(ListFlowable(items, bulletType="1" if block.ordered else "bullet"))
        elif block.kind == "table" and block.rows:
            n_cols = max((len(r) for r in block.rows), default=0)
            if n_cols == 0:
                continue
            data = [
                [Paragraph(_runs_to_reportlab_markup(row[c] if c < len(row) else []), _styles["BodyText"]) for c in range(n_cols)]
                for row in block.rows
            ]
            table = Table(data, repeatRows=block.header_rows or 0)
            style_cmds = [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
            if block.header_rows:
                style_cmds.append(("BACKGROUND", (0, 0), (-1, block.header_rows - 1), colors.whitesmoke))
                style_cmds.append(("FONTNAME", (0, 0), (-1, block.header_rows - 1), "Helvetica-Bold"))
            table.setStyle(TableStyle(style_cmds))
            flow.append(table)
        flow.append(Spacer(1, 6))
    if not flow:
        flow = [Paragraph("(empty document)", _styles["BodyText"])]
    doc.build(flow)
    return buf.getvalue()


# ─── Orchestration ───────────────────────────────────────────────────────────

def export_document(
    job: dict, pages: List[dict], *, fmt: str,
    scope: str = "full", page_num: Optional[int] = None, page_range: Optional[str] = None,
    include: str = "all", clean_tables: bool = False, include_metadata: bool = True,
):
    """Returns (bytes, content_type, file_extension)."""
    if fmt not in SUPPORTED_FORMATS:
        raise ExportError(f"Unsupported format '{fmt}' (expected one of {SUPPORTED_FORMATS})")
    if scope not in SUPPORTED_SCOPES:
        raise ExportError(f"Unsupported scope '{scope}' (expected one of {SUPPORTED_SCOPES})")

    selected = select_pages(pages, scope=scope, page_num=page_num, page_range=page_range)
    markdown_text = build_markdown(job, selected, include=include, clean_tables=clean_tables, include_metadata=include_metadata)

    if fmt == "md":
        return markdown_text.encode("utf-8"), _CONTENT_TYPES["md"], "md"

    html = markdown_to_html(markdown_text)
    blocks = parse_html_blocks(html)
    title = job.get("filename", "Document")

    if fmt == "docx":
        return render_docx(blocks, title), _CONTENT_TYPES["docx"], "docx"
    return render_pdf(blocks, title), _CONTENT_TYPES["pdf"], "pdf"
