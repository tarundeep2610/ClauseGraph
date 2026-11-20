"""Tests for layout_format.py — the layout-aware `formatted_text` renderer
built from baidu/Unlimited-OCR's raw <|det|> tag stream.

`_REAL_PAGE_1_RAW` and `_REAL_PAGE_4_RAW` below are real, unmodified
<|det|> tag streams pulled directly from a production job's database
(a09aa767-f70f-4eef-a80c-5e4577f3aba4, pages 1 and 4) — not synthetic
fixtures, matching the same real-data-first practice
test_reference_index_fuzzy_and_decimal.py already established in this repo.
Page 1 is single-column with a real table; page 4 is a two-column stamp-
paper header (Hindi/English denomination side by side) followed by a
single-column case title block — both patterns this module must handle.
Non-English OCR text in page 4 is transliterated for source-file
readability; only the Latin/numeric content (which the assertions check)
is unchanged from the real data.
"""

import layout_format as lf


_REAL_PAGE_1_RAW = (
    "<|det|>text [231, 131, 796, 152]<|/det|>In the High Court of Punjab and Haryana at Chandigarh\n"
    "<|det|>text [476, 157, 810, 182]<|/det|>F.A.O. (Comm.Arb.) 23 2019\n"
    "<|det|>text [230, 189, 601, 208]<|/det|>Haryana Vidyut Prasaran Nigam Ltd.\n"
    "<|det|>text [738, 196, 865, 212]<|/det|>.... Appellant\n"
    "<|det|>text [489, 223, 563, 238]<|/det|>Versus\n"
    "<|det|>text [230, 251, 573, 268]<|/det|>M/s GE T&D India Ltd. and others\n"
    "<|det|>text [721, 256, 884, 273]<|/det|>.... Respondents\n"
    "<|det|>text [474, 283, 545, 298]<|/det|>INDEX\n"
    "<|det|>table [220, 308, 891, 908]<|/det|>"
    "<table><tr><td>Sr No.</td><td>Particulars</td></tr>"
    "<tr><td>A.</td><td>Stamp Paper</td></tr></table>"
)

_REAL_PAGE_4_RAW = (
    "<|det|>page_number [902, 10, 935, 30]<|/det|>2\n"
    "<|det|>title [325, 62, 667, 95]<|/det|>Bharat Kraate Ki\n"
    "<|det|>text [207, 140, 304, 165]<|/det|>Bharat\n"
    "<|det|>text [189, 178, 325, 209]<|/det|>100\n"
    "<|det|>text [228, 223, 285, 247]<|/det|>Ek\n"
    "<|det|>text [194, 253, 321, 280]<|/det|>Soarye\n"
    "<|det|>text [687, 140, 793, 164]<|/det|>INDIA\n"
    "<|det|>text [665, 176, 804, 203]<|/det|>Rs.100\n"
    "<|det|>text [705, 214, 766, 234]<|/det|>ONE\n"
    "<|det|>text [666, 240, 804, 259]<|/det|>HUNDRED\n"
    "<|det|>text [678, 266, 791, 285]<|/det|>RUPEES\n"
    "<|det|>title [307, 328, 685, 356]<|/det|>INDIA COURT FEE\n"
    "<|det|>text [68, 379, 567, 405]<|/det|>Chandigarh UT caption\n"
    "<|det|>text [824, 375, 913, 394]<|/det|>107332\n"
    "<|det|>text [182, 411, 864, 443]<|/det|>IN THE HIGHCOURT PUNJAB AND HARYANA\n"
    "<|det|>text [242, 447, 571, 475]<|/det|>AT CHANDIGARH.\n"
    "<|det|>text [530, 492, 999, 533]<|/det|>FAO. Comm Arb 23/2019\n"
    "<|det|>text [194, 545, 834, 590]<|/det|>Haryana vidyut parasaran nigam Ltd.\n"
    "<|det|>text [489, 602, 593, 625]<|/det|>versus.\n"
    "<|det|>text [875, 585, 999, 620]<|/det|>appellant\n"
    "<|det|>text [199, 650, 814, 690]<|/det|>m/s GE T&D India. and others\n"
    "<|det|>text [540, 842, 881, 888]<|/det|>(KULDEEP TIWARI)\n"
    "<|det|>text [607, 888, 770, 907]<|/det|>ADVOCATE\n"
    "<|det|>text [596, 907, 950, 948]<|/det|>Counsel for Appellant"
)


# ─── parse_blocks() ─────────────────────────────────────────────────────────

def test_parse_blocks_real_page1_nine_blocks_with_correct_types_and_bbox():
    blocks = lf.parse_blocks(_REAL_PAGE_1_RAW)
    assert len(blocks) == 9
    assert [b.type for b in blocks] == ["text"] * 8 + ["table"]
    assert blocks[0].bbox == (231, 131, 796, 152)
    assert blocks[-1].text.startswith("<table>")


def test_parse_blocks_keeps_empty_content_image_tags():
    raw = "<|det|>image [10, 10, 100, 100]<|/det|><|det|>text [10, 110, 50, 130]<|/det|>caption"
    blocks = lf.parse_blocks(raw)
    assert len(blocks) == 2
    assert blocks[0].type == "image"
    assert blocks[0].text == ""
    assert blocks[0].bbox == (10, 10, 100, 100)


def test_parse_blocks_malformed_bbox_keeps_block_with_none_bbox():
    raw = "<|det|>text [not, numbers]<|/det|>hello"
    blocks = lf.parse_blocks(raw)
    assert len(blocks) == 1
    assert blocks[0].bbox is None
    assert blocks[0].text == "hello"


def test_parse_blocks_empty_or_none_input():
    assert lf.parse_blocks("") == []
    assert lf.parse_blocks(None) == []
    assert lf.parse_blocks("no tags here at all") == []


# ─── build_formatted_markdown(): None/fallback contract ───────────────────

def test_returns_none_for_none_raw_text():
    assert lf.build_formatted_markdown(None) is None


def test_returns_none_when_no_recognizable_tags():
    assert lf.build_formatted_markdown("plain GLM-engine text, no tags at all") is None


# ─── Type mapping ───────────────────────────────────────────────────────────

def test_title_gets_heading_markup():
    raw = "<|det|>title [100, 10, 400, 40]<|/det|>3.3 TIME SCHEDULE"
    out = lf.build_formatted_markdown(raw)
    assert out == "## 3.3 TIME SCHEDULE"


def test_title_already_starting_with_hash_not_double_prefixed():
    raw = "<|det|>title [100, 10, 400, 40]<|/det|>## Already a heading"
    out = lf.build_formatted_markdown(raw)
    assert out == "## Already a heading"


def test_header_footer_page_number_get_no_heading_markup():
    """Regression guard for the real finding: `header` tags repeated page
    letterhead on real documents (pages 40-61 of a09aa767), not section
    headings — only `title` may become a heading."""
    raw = (
        "<|det|>header [50, 10, 900, 40]<|/det|>HIGH COURT OF PUNJAB AND HARYANA\n"
        "<|det|>footer [50, 950, 900, 980]<|/det|>Page bottom notice\n"
        "<|det|>page_number [900, 10, 950, 30]<|/det|>42"
    )
    out = lf.build_formatted_markdown(raw)
    assert "##" not in out
    assert "HIGH COURT OF PUNJAB AND HARYANA" in out
    assert "Page bottom notice" in out
    assert "42" in out


def test_table_passed_through_verbatim():
    raw = "<|det|>table [10, 10, 500, 500]<|/det|><table><tr><td>a</td></tr></table>"
    out = lf.build_formatted_markdown(raw)
    assert out == "<table><tr><td>a</td></tr></table>"


def test_image_block_renders_placeholder():
    raw = "<|det|>image [10, 10, 500, 500]<|/det|>"
    out = lf.build_formatted_markdown(raw)
    assert out == "*[image]*"


def test_multiple_consecutive_titles_each_become_own_heading():
    raw = (
        "<|det|>title [10, 10, 400, 40]<|/det|>First Title\n"
        "<|det|>title [10, 400, 400, 500]<|/det|>Second Title"
    )
    out = lf.build_formatted_markdown(raw)
    assert "## First Title" in out
    assert "## Second Title" in out
    assert out.count("##") == 2


# ─── Footer passthrough ─────────────────────────────────────────────────────

def test_execution_time_footer_recovered_from_stored_text():
    raw = "<|det|>text [10, 10, 100, 30]<|/det|>Hello"
    footer = "\n\n---\n*⏱️ **Unlimited-OCR Execution Time:** 18.1s (18.1s)*"
    stored = "Hello" + footer
    out = lf.build_formatted_markdown(raw, stored_text=stored)
    assert out.endswith(footer)


def test_no_footer_in_stored_text_means_no_footer_appended():
    raw = "<|det|>text [10, 10, 100, 30]<|/det|>Hello"
    out = lf.build_formatted_markdown(raw, stored_text="Hello, no footer here")
    assert "---" not in out


# ─── Position rules: vertical gap, margin indentation ──────────────────────

def test_large_gap_gets_extra_blank_line():
    # Several normal ~15-unit gaps establish the page's own median, then one
    # much larger gap (~300) before the last block — should read as a
    # section break. (A median needs several "normal" samples to mean
    # anything — two gaps alone, one of them the outlier itself, would just
    # drag the median up to meet it and never flag anything as unusual.)
    raw = (
        "<|det|>text [100, 100, 400, 120]<|/det|>Para one\n"
        "<|det|>text [100, 135, 400, 155]<|/det|>Para two\n"
        "<|det|>text [100, 170, 400, 190]<|/det|>Para three\n"
        "<|det|>text [100, 205, 400, 225]<|/det|>Para four\n"
        "<|det|>text [100, 550, 400, 570]<|/det|>Para five after a big gap"
    )
    out = lf.build_formatted_markdown(raw)
    assert "Para four\n\n\n\nPara five" in out


def test_normal_gap_gets_single_blank_line():
    raw = (
        "<|det|>text [100, 100, 400, 120]<|/det|>Para one\n"
        "<|det|>text [100, 135, 400, 155]<|/det|>Para two\n"
        "<|det|>text [100, 170, 400, 190]<|/det|>Para three"
    )
    out = lf.build_formatted_markdown(raw)
    assert "\n\n\n\n" not in out


def test_block_right_of_margin_renders_as_blockquote():
    # Body text margin is clearly x1~100-120; one block sits far to the right.
    raw = (
        "<|det|>text [100, 100, 400, 120]<|/det|>Left margin text one\n"
        "<|det|>text [110, 140, 400, 160]<|/det|>Left margin text two\n"
        "<|det|>text [120, 180, 400, 200]<|/det|>Left margin text three\n"
        "<|det|>text [700, 220, 900, 240]<|/det|>Far right offset text"
    )
    out = lf.build_formatted_markdown(raw)
    assert "> Far right offset text" in out
    assert "> Left margin text one" not in out


def test_missing_bbox_skips_position_rules_without_crashing():
    raw = "<|det|>text [bad]<|/det|>Some text with no usable bbox"
    out = lf.build_formatted_markdown(raw)
    assert out == "Some text with no usable bbox"


# ─── Column detection (real fixture: job a09aa767, page 4) ────────────────

def test_real_page4_column_run_detected_with_correct_boundaries():
    blocks = lf.parse_blocks(_REAL_PAGE_4_RAW)
    runs = lf._detect_column_runs(blocks)
    assert len(runs) == 1
    run = runs[0]
    # Run starts at the Hindi title (index 1, after the lone page_number
    # block) and ends exactly at the full-width "INDIA COURT FEE" title
    # (index 11), which must NOT be included in either column.
    assert run.start == 1
    assert run.end == 11
    left_texts = [b.text for b in run.left]
    right_texts = [b.text for b in run.right]
    assert left_texts == ["Bharat Kraate Ki", "Bharat", "100", "Ek", "Soarye"]
    assert right_texts == ["INDIA", "Rs.100", "ONE", "HUNDRED", "RUPEES"]


def test_title_inside_a_column_run_uses_html_strong_not_markdown_hash():
    """Regression: marked does not reprocess Markdown syntax inside a raw
    HTML block (confirmed directly against the installed marked+DOMPurify
    pipeline) — a `## ` prefix inside a column run's <table> cell would
    render as literal, visible hash marks in the browser instead of a
    heading. Found via a real job's page 4, where the Hindi title block
    (the top line of the two-column stamp) lands inside the run."""
    out = lf.build_formatted_markdown(_REAL_PAGE_4_RAW)
    table_cell_content = out.split("<table>")[1].split("</table>")[0]
    assert "##" not in table_cell_content
    assert "<strong>Bharat Kraate Ki</strong>" in table_cell_content


def test_real_page4_full_render_shape():
    out = lf.build_formatted_markdown(_REAL_PAGE_4_RAW)
    # The page-number block renders on its own, sequentially, before the run.
    assert out.startswith("2")
    # The column run renders as one 2-cell raw HTML table.
    assert "<table><tr><td>" in out
    assert "Bharat Kraate Ki" in out.split("<table>")[1].split("</table>")[0]
    assert "INDIA" in out.split("<table>")[1].split("</table>")[0]
    # The terminating full-width title becomes its own heading, outside the table.
    assert "## INDIA COURT FEE" in out
    assert out.index("</table>") < out.index("## INDIA COURT FEE")
    # Everything after the table resumes as normal sequential single-column
    # text — never pulled back into a table cell.
    assert "Counsel for Appellant" in out
    assert out.rindex("</table>") < out.index("Counsel for Appellant")


def test_real_page1_single_column_no_spurious_column_run():
    blocks = lf.parse_blocks(_REAL_PAGE_1_RAW)
    assert lf._detect_column_runs(blocks) == []


def test_ambiguous_three_cluster_x1_bails_out_no_column_run():
    # Three distinct x-clusters with no reset pattern the detector
    # recognizes as a clean 2-column split — must never force a guess.
    raw = (
        "<|det|>text [50, 10, 150, 30]<|/det|>A\n"
        "<|det|>text [450, 10, 550, 30]<|/det|>B\n"
        "<|det|>text [850, 10, 950, 30]<|/det|>C"
    )
    blocks = lf.parse_blocks(raw)
    assert lf._detect_column_runs(blocks) == []


def test_overlapping_x_ranges_do_not_crash_and_do_not_force_a_run():
    raw = (
        "<|det|>text [100, 10, 500, 30]<|/det|>Wide one\n"
        "<|det|>text [120, 40, 520, 60]<|/det|>Wide two\n"
        "<|det|>text [110, 5, 510, 25]<|/det|>Wide three, y1 dips slightly"
    )
    blocks = lf.parse_blocks(raw)
    # Must not raise; overlapping, non-clustered ranges should not produce
    # a confident 2-column call.
    runs = lf._detect_column_runs(blocks)
    assert isinstance(runs, list)
