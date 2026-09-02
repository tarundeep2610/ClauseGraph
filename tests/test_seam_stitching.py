import seam_stitching as ss


def _pages(*texts):
    return [{"page": i + 1, "text": t} for i, t in enumerate(texts)]


def test_detects_seam_across_non_chunk_boundary():
    pages = _pages(
        "Intro text.\n\n| Item | Qty |\n|---|---|\n| Pen | 2 |",
        "| Pencil | 5 |\n| Eraser | 1 |",
        "Unrelated prose on a third page with nothing table-like.",
    )
    seams = ss.detect_seams(pages)
    assert len(seams) == 1
    assert seams[0]["from_page"] == 1 and seams[0]["to_page"] == 2


def test_no_seam_for_unrelated_pages():
    pages = _pages("Just prose here.", "More unrelated prose there.")
    assert ss.detect_seams(pages) == []


def test_no_false_positive_merge_for_unrelated_tables_same_column_count():
    pages = _pages(
        "| Item | Qty | Price | Total |\n|---|---|---|---|\n| 12 | Pen | 2 | 10 |",
        "| 2020 | 100 | 50 | 5000 |",  # different table entirely — years, not item numbers
    )
    assert ss.detect_seams(pages) == []


def test_default_export_preserves_every_page_heading():
    pages = _pages(
        "Intro.\n\n| Item | Qty |\n|---|---|\n| Pen | 2 |",
        "| Pencil | 5 |",
    )
    out = ss.render_export(pages, clean_tables=False)
    assert "## Page 1" in out
    assert "## Page 2" in out


def test_default_export_reemits_missing_header_row_at_seam():
    pages = _pages(
        "Intro.\n\n| Item | Qty |\n|---|---|\n| Pen | 2 |",
        "| Pencil | 5 |",  # continuation, no header repeated
    )
    out = ss.render_export(pages, clean_tables=False)
    page2_block = out.split("## Page 2", 1)[1]
    assert "| Item | Qty |" in page2_block
    assert "seam: pages 1→2" in page2_block


def test_default_export_does_not_duplicate_header_if_already_present():
    pages = _pages(
        "Intro.\n\n| Item | Qty |\n|---|---|\n| Pen | 2 |",
        "| Item | Qty |\n|---|---|\n| Pencil | 5 |",  # model already repeated the header
    )
    out = ss.render_export(pages, clean_tables=False)
    page2_block = out.split("## Page 2", 1)[1]
    assert page2_block.count("| Item | Qty |") == 1


def test_clean_tables_merges_continuation_and_drops_duplicate_header():
    pages = _pages(
        "Intro.\n\n| Item | Qty |\n|---|---|\n| Pen | 2 |",
        "| Item | Qty |\n|---|---|\n| Pencil | 5 |",
    )
    out = ss.render_export(pages, clean_tables=True)
    assert out.count("## Page") == 1, "clean_tables merges the continuation under the first page's heading only"
    assert out.count("| Item | Qty |") == 1
    assert "| Pen | 2 |" in out and "| Pencil | 5 |" in out


def test_clean_tables_does_not_merge_unrelated_tables():
    pages = _pages(
        "| Item | Qty | Price | Total |\n|---|---|---|---|\n| 12 | Pen | 2 | 10 |",
        "| 2020 | 100 | 50 | 5000 |",
    )
    out = ss.render_export(pages, clean_tables=True)
    assert out.count("## Page") == 2, "unrelated tables must stay as two separate page blocks"


def test_seam_merges_correctly_past_a_section_divider_row():
    pages = _pages(
        "| Item | Qty |\n|---|---|\n| 14 | Pen |",
        "| SUB-TOTAL CARRIED FORWARD | |\n| 15 | Bolt |",
    )
    seams = ss.detect_seams(pages)
    assert len(seams) == 1


def test_render_export_is_order_independent_of_input_beyond_page_order():
    """Output assembly is always by page_num; two runs with the same pages produce
    identical output (no dependency on completion order / concurrency)."""
    pages = _pages("Page one text.", "Page two text.", "Page three text.")
    out1 = ss.render_export(pages, clean_tables=False)
    out2 = ss.render_export(list(pages), clean_tables=False)
    assert out1 == out2


# ─── stitch=False (single-page mode always; batch mode with overlap disabled) ─────

def test_stitch_false_renders_plain_pages_no_seam_marker_or_reemitted_header():
    pages = _pages(
        "Intro.\n\n| Item | Qty |\n|---|---|\n| Pen | 2 |",
        "| Pencil | 5 |",  # continuation, no header repeated — would normally trigger a seam
    )
    out = ss.render_export(pages, stitch=False)
    assert "## Page 1" in out and "## Page 2" in out
    assert "seam:" not in out
    page2_block = out.split("## Page 2", 1)[1]
    assert "| Item | Qty |" not in page2_block, "header must not be re-emitted when stitching is off"


def test_stitch_false_never_calls_detect_seams(monkeypatch):
    def _boom(pages):
        raise AssertionError("detect_seams must not be called when stitch=False")

    monkeypatch.setattr(ss, "detect_seams", _boom)
    pages = _pages(
        "Intro.\n\n| Item | Qty |\n|---|---|\n| Pen | 2 |",
        "| Pencil | 5 |",
    )
    ss.render_export(pages, stitch=False)  # must not raise


def test_stitch_false_with_clean_tables_true_is_a_documented_no_op():
    pages = _pages(
        "Intro.\n\n| Item | Qty |\n|---|---|\n| Pen | 2 |",
        "| Item | Qty |\n|---|---|\n| Pencil | 5 |",
    )
    out = ss.render_export(pages, clean_tables=True, stitch=False)
    assert out.count("## Page") == 2, "clean_tables must not merge anything when stitch=False"
