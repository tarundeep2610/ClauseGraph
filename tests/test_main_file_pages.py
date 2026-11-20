"""Tests for GET /api/jobs/{job_id}/file/pages (Part C3) — extracting a
contiguous page range from the original uploaded PDF into a small
standalone PDF, the "chunk" the Structure sidebar fetches instead of the
whole document."""

import uuid

import pypdfium2 as pdfium

import database


def _make_pdf_job(tmp_path, n_pages=10):
    job_id = str(uuid.uuid4())
    pdf_path = tmp_path / f"{job_id}.pdf"
    doc = pdfium.PdfDocument.new()
    for _ in range(n_pages):
        doc.new_page(200, 200)
    doc.save(str(pdf_path))
    doc.close()
    database.create_job(
        job_id=job_id, filename="test.pdf", file_path=str(pdf_path), file_type="pdf",
        engine="unlimited", total_pages=n_pages,
    )
    return job_id


def test_returns_a_pdf_with_exactly_the_requested_page_count(client, auth_headers, tmp_path):
    job_id = _make_pdf_job(tmp_path, n_pages=20)
    resp = client.get(f"/api/jobs/{job_id}/file/pages?start=5&end=9", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"

    chunk = pdfium.PdfDocument(resp.content)
    assert len(chunk) == 5  # pages 5,6,7,8,9


def test_a_single_page_range_is_valid(client, auth_headers, tmp_path):
    job_id = _make_pdf_job(tmp_path, n_pages=10)
    resp = client.get(f"/api/jobs/{job_id}/file/pages?start=3&end=3", headers=auth_headers)
    assert resp.status_code == 200
    chunk = pdfium.PdfDocument(resp.content)
    assert len(chunk) == 1


def test_end_before_start_is_rejected(client, auth_headers, tmp_path):
    job_id = _make_pdf_job(tmp_path, n_pages=10)
    resp = client.get(f"/api/jobs/{job_id}/file/pages?start=5&end=2", headers=auth_headers)
    assert resp.status_code == 400


def test_end_beyond_document_length_is_rejected(client, auth_headers, tmp_path):
    job_id = _make_pdf_job(tmp_path, n_pages=10)
    resp = client.get(f"/api/jobs/{job_id}/file/pages?start=1&end=11", headers=auth_headers)
    assert resp.status_code == 400


def test_missing_job_404s(client, auth_headers):
    resp = client.get("/api/jobs/does-not-exist/file/pages?start=1&end=2", headers=auth_headers)
    assert resp.status_code == 404


def test_rejects_a_non_pdf_job(client, auth_headers, tmp_path):
    job_id = str(uuid.uuid4())
    img_path = tmp_path / f"{job_id}.png"
    img_path.write_bytes(b"not a real png, but presence is all this endpoint checks before the type gate")
    database.create_job(
        job_id=job_id, filename="test.png", file_path=str(img_path), file_type="image",
        engine="unlimited", total_pages=1,
    )
    resp = client.get(f"/api/jobs/{job_id}/file/pages?start=1&end=1", headers=auth_headers)
    assert resp.status_code == 400
