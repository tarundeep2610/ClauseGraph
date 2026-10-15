import io

import pytest
from docx import Document
import pypdfium2 as pdfium

import export_service as es


def _job(**overrides):
    job = {
        "filename": "contract.pdf", "engine": "unlimited", "total_pages": 2,
        "status": "completed", "created_at": "2026-01-01T00:00:00",
        "processing_mode": "single", "enable_overlap": False,
    }
    job.update(overrides)
    return job


def _pages():
    return [
        {"page_num": 1, "text": "# Title\n\nSome **bold** and *italic* text.\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n", "needs_review": False},
        {"page_num": 2, "text": "## Section 2\n\n- item one\n- item two\n", "needs_review": True, "confidence": 0.4, "review_reason": "low_confidence"},
    ]


# ─── Page selection ──────────────────────────────────────────────────────────

def test_select_pages_full_returns_everything():
    pages = _pages()
    assert es.select_pages(pages, scope="full") == pages


def test_select_pages_page_scope_filters_to_one_page():
    selected = es.select_pages(_pages(), scope="page", page_num=2)
    assert [p["page_num"] for p in selected] == [2]


def test_select_pages_page_scope_missing_page_num_raises():
    with pytest.raises(es.ExportError):
        es.select_pages(_pages(), scope="page")


def test_select_pages_page_scope_out_of_range_raises():
    with pytest.raises(es.ExportError):
        es.select_pages(_pages(), scope="page", page_num=99)


def test_select_pages_range_scope_filters_correctly():
    pages = [{"page_num": n, "text": f"p{n}"} for n in range(1, 6)]
    selected = es.select_pages(pages, scope="range", page_range="1-2,5")
    assert [p["page_num"] for p in selected] == [1, 2, 5]


def test_select_pages_range_scope_missing_range_raises():
    with pytest.raises(es.ExportError):
        es.select_pages(_pages(), scope="range")


def test_select_pages_unknown_scope_raises():
    with pytest.raises(es.ExportError):
        es.select_pages(_pages(), scope="bogus")


# ─── Markdown content (shared with download-result) ─────────────────────────

def test_build_markdown_includes_metadata_header_by_default():
    md = es.build_markdown(_job(), _pages())
    assert "# OCR Extraction Report: contract.pdf" in md
    assert "**Engine**: UNLIMITED" in md


def test_build_markdown_omits_metadata_when_disabled():
    md = es.build_markdown(_job(), _pages(), include_metadata=False)
    assert "OCR Extraction Report" not in md
    assert "# Title" in md  # page content itself is untouched


def test_build_markdown_review_banner_only_on_flagged_pages():
    md = es.build_markdown(_job(), _pages(), include="all")
    assert "NEEDS HUMAN REVIEW" in md
    assert md.count("NEEDS HUMAN REVIEW") == 1  # only page 2 is flagged


def test_build_markdown_clean_excludes_flagged_pages():
    md = es.build_markdown(_job(), _pages(), include="clean")
    assert "Section 2" not in md
    assert "Title" in md


# ─── Format dispatch + round-trip fidelity ──────────────────────────────────

def test_export_document_md_returns_plain_markdown_bytes():
    data, content_type, ext = es.export_document(_job(), _pages(), fmt="md", scope="full")
    assert content_type == "text/markdown"
    assert ext == "md"
    assert b"# Title" in data


def test_export_document_rejects_unsupported_format():
    with pytest.raises(es.ExportError):
        es.export_document(_job(), _pages(), fmt="xlsx", scope="full")


def test_export_document_rejects_unsupported_scope():
    with pytest.raises(es.ExportError):
        es.export_document(_job(), _pages(), fmt="md", scope="bogus")


def test_docx_export_round_trips_heading_bold_and_table():
    data, content_type, ext = es.export_document(_job(), [_pages()[0]], fmt="docx", scope="full", include_metadata=False)
    assert content_type.endswith("wordprocessingml.document")
    assert ext == "docx"

    doc = Document(io.BytesIO(data))
    all_text = "\n".join(p.text for p in doc.paragraphs)
    assert "Title" in all_text
    assert "bold" in all_text and "italic" in all_text

    assert len(doc.tables) == 1
    table = doc.tables[0]
    assert len(table.rows) == 2
    assert [c.text for c in table.rows[0].cells] == ["A", "B"]
    assert [c.text for c in table.rows[1].cells] == ["1", "2"]


def test_docx_export_bold_run_is_actually_bold():
    data, _, _ = es.export_document(_job(), [_pages()[0]], fmt="docx", scope="full", include_metadata=False)
    doc = Document(io.BytesIO(data))
    bold_runs = [r for p in doc.paragraphs for r in p.runs if r.text == "bold"]
    assert bold_runs and bold_runs[0].bold is True


def test_docx_export_list_items_present():
    data, _, _ = es.export_document(_job(), [_pages()[1]], fmt="docx", scope="full", include_metadata=False)
    doc = Document(io.BytesIO(data))
    all_text = "\n".join(p.text for p in doc.paragraphs)
    assert "item one" in all_text
    assert "item two" in all_text


def test_pdf_export_contains_expected_marker_text():
    data, content_type, ext = es.export_document(_job(), [_pages()[0]], fmt="pdf", scope="full", include_metadata=False)
    assert content_type == "application/pdf"
    assert ext == "pdf"
    assert len(data) > 0

    pdf = pdfium.PdfDocument(data)
    text = pdf[0].get_textpage().get_text_range()
    assert "Title" in text
    assert "bold" in text
    assert "A" in text and "1" in text  # table content made it onto the page


def test_pdf_export_scope_page_only_includes_that_pages_content():
    data, _, _ = es.export_document(_job(), _pages(), fmt="pdf", scope="page", page_num=2, include_metadata=False)
    pdf = pdfium.PdfDocument(data)
    full_text = "".join(pdf[i].get_textpage().get_text_range() for i in range(len(pdf)))
    assert "Section 2" in full_text
    assert "Title" not in full_text


def test_docx_export_of_empty_page_list_does_not_crash():
    # scope=page with a valid, existing page whose text is empty — a
    # realistic "page OCR'd blank" case, not an exception path.
    pages = [{"page_num": 1, "text": "", "needs_review": False}]
    data, _, _ = es.export_document(_job(total_pages=1), pages, fmt="docx", scope="full", include_metadata=False)
    assert len(data) > 0
    Document(io.BytesIO(data))  # must still be a valid, openable docx
