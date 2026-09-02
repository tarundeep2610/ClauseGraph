import context_primer as cp


# ─── strip_engine_artifacts ────────────────────────────────────────────────────

def test_strip_unlimited_timing_footer():
    text = "Some real content.\n\n---\n*⏱️ **Unlimited-OCR Execution Time:** 2m 52.3s (172.3s)*"
    assert cp.strip_engine_artifacts(text) == "Some real content."


def test_strip_glm_tiling_headers():
    text = (
        "### Document Section: Top Half (Metadata)\n\nheader text\n\n---\n\n"
        "### Document Section: Middle (Table Grid)\n\n| a | b |\n\n---\n\n"
        "### Document Section: Bottom Half (Descriptions & Verification)\n\nfooter text"
    )
    cleaned = cp.strip_engine_artifacts(text)
    assert "Document Section" not in cleaned
    assert "header text" in cleaned and "| a | b |" in cleaned and "footer text" in cleaned


def test_error_sentinel_page_yields_empty_string():
    text = "### ❌ Unlimited-OCR inference failed\n```\nTraceback (most recent call last):\n...\n```"
    assert cp.strip_engine_artifacts(text) == ""


# ─── get_tail ──────────────────────────────────────────────────────────────────

def test_get_tail_respects_max_chars_and_cuts_on_whitespace():
    text = "word " * 200  # 1000 chars
    tail = cp.get_tail(text, max_chars=50)
    assert len(tail) <= 50
    assert not tail.startswith(" ")


def test_get_tail_of_short_text_returns_everything():
    assert cp.get_tail("short text", max_chars=300) == "short text"


def test_get_tail_strips_unlimited_footer_before_tailing():
    text = "Real content at the end.\n\n---\n*⏱️ **Unlimited-OCR Execution Time:** 1.0s (1.0s)*"
    tail = cp.get_tail(text, max_chars=300)
    assert "Execution Time" not in tail
    assert "Real content at the end." in tail


def test_get_tail_of_error_page_is_empty():
    assert cp.get_tail("### ❌ Unlimited-OCR inference failed\n```\ntrace\n```") == ""


# ─── ends_mid_table / ends_mid_sentence / table_header ─────────────────────────

def test_ends_mid_table_true_for_pipe_row():
    assert cp.ends_mid_table("Some prose.\n\n| Item | Qty | Price |") is True


def test_ends_mid_table_false_for_prose():
    assert cp.ends_mid_table("This is a normal sentence.") is False


def test_ends_mid_sentence_variants():
    assert cp.ends_mid_sentence("This is complete.") is False
    assert cp.ends_mid_sentence("This is complete?") is False
    assert cp.ends_mid_sentence("This is complete!") is False
    assert cp.ends_mid_sentence("यह पूर्ण है।") is False  # Devanagari danda
    assert cp.ends_mid_sentence("This trails off and") is True


def test_table_header_finds_nearest_header_and_separator():
    text = "Some intro.\n\n| Item | Qty | Price |\n|---|---|---|\n| Pen | 2 | 10 |"
    header = cp.table_header(text)
    assert header is not None
    assert "Item" in header and "---" in header


def test_table_header_none_when_no_separator_row():
    assert cp.table_header("just some text with | a pipe | in it") is None


# ─── build_primer / render_primer ───────────────────────────────────────────────

def test_build_primer_none_for_error_sentinel():
    assert cp.build_primer("### ❌ Unlimited-OCR inference failed\n```\ntrace\n```") is None


def test_build_primer_none_for_empty_text():
    assert cp.build_primer("") is None


def test_render_primer_byte_identical_when_disabled():
    prompt = "Perform OCR on this image."
    primer = cp.build_primer("Some previous page text ending in a table.\n\n| a | b |")
    assert cp.render_primer(prompt, primer, enabled=False) == prompt


def test_render_primer_byte_identical_when_no_primer():
    prompt = "Perform OCR on this image."
    assert cp.render_primer(prompt, None, enabled=True) == prompt


def test_render_primer_appends_trailer_when_enabled_with_primer():
    prompt = "Perform OCR on this image."
    primer = cp.build_primer("Ends mid table.\n\n| Item | Qty |\n|---|---|\n| Pen | 2 |")
    rendered = cp.render_primer(prompt, primer, enabled=True)
    assert rendered.startswith(prompt)
    assert "for continuity only" in rendered
    assert "do NOT transcribe" in rendered.lower() or "Do NOT transcribe" in rendered


# ─── continues_table (shared predicate) ────────────────────────────────────────

def test_continues_table_matches_same_schema_continuation():
    prior_header = "| Item | Qty | Price |\n|---|---|---|"
    prior_last_row = "| Pen | 2 | 10 |"
    following = "| Pencil | 5 | 20 |\n| Eraser | 1 | 5 |"
    matches, reason = cp.continues_table(prior_header, prior_last_row, following)
    assert matches is True
    assert reason == "match"


def test_continues_table_rejects_unrelated_table_same_column_count():
    prior_header = "| Item | Qty | Price | Total |"
    prior_last_row = "| 12 | Pen | 2 | 10 |"  # numeric leading column (item numbers)
    following = "| 2020 | 100 | 50 | 5000 |"  # year leading column — different table (financial schedule)
    matches, reason = cp.continues_table(prior_header, prior_last_row, following)
    assert matches is False
    assert reason == "leading_column_pattern_mismatch"


def test_continues_table_skips_section_divider_row():
    prior_header = "| Item | Qty | Price |\n|---|---|---|"
    prior_last_row = "| 14 | Pen | 2 |"
    following = "| SECTION 2: MECHANICAL & ELECTRICAL WORKS |\n| 15 | Bolt | 100 |"
    matches, reason = cp.continues_table(prior_header, prior_last_row, following)
    assert matches is True, "must skip the divider row and match the real continuation"


def test_continues_table_skips_subtotal_row():
    prior_header = "| Item | Qty | Price |\n|---|---|---|"
    prior_last_row = "| 14 | Pen | 2 |"
    following = "| SUB-TOTAL CARRIED FORWARD | | 500 |\n| 15 | Bolt | 100 |"
    matches, _ = cp.continues_table(prior_header, prior_last_row, following)
    assert matches is True


def test_continues_table_window_overshoot_rejects_unrelated_later_table():
    """A wide scan window must not wrongly accept an unrelated table found further
    down the page — the first genuine table-row candidate must match, not just any
    table-like row found anywhere in the window."""
    prior_header = "| Item | Qty | Price |\n|---|---|---|"
    prior_last_row = "| 14 | Pen | 2 |"
    following = (
        "Some unrelated paragraph text discussing a new topic entirely.\n"
        "Another line of prose here for good measure.\n"
        "A NEW SECTION TITLE\n"
        "| Year | Revenue | Margin |\n"
        "| 2020 | 100 | 50 |"
    )
    matches, reason = cp.continues_table(prior_header, prior_last_row, following)
    assert matches is False
    assert reason in ("leading_column_pattern_mismatch", "column_count_mismatch")


def test_continues_table_no_table_marker_found():
    matches, reason = cp.continues_table("| a | b |\n|---|---|", "| 1 | x |", "Just plain prose, no table at all.")
    assert matches is False
    assert reason == "no_table_marker_found"
