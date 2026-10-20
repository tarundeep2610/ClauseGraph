"""Tests for the HTML-<table>-blindness fix: _parse_html_table_blocks /
_HtmlTableCellParser (reference_index.py), wired into _flatten_blocks so
parse_page_blocks()/build_targets_for_page() can see inside a literal
HTML <table> the same way they already see inside a markdown pipe table.
Confirmed real bug, two structurally different fixtures — see
AI_INDEXING_ACCURACY_INVESTIGATION.md §15.3 finding #1 and §18.4."""

import reference_index as ri
from ai_reference_index import locate_heading_block


def _by_key(page_text, seed_scope=None, raw_text=None):
    candidates, scope = ri.build_targets_for_page(
        page_text, seed_scope=seed_scope, has_next_page=False, raw_text=raw_text
    )
    return {c["key"]: c for c in candidates}, scope


HTML_TABLE = (
    "<table><tr><td>A</td><td>B</td></tr><tr><td>C</td><td>D</td></tr></table>"
)
PIPE_TABLE = "| A | B |\n| --- | --- |\n| C | D |\n"


def test_html_table_cells_become_separate_blocks_in_order():
    assert ri.parse_page_blocks(HTML_TABLE) == ["A", "B", "C", "D"]
    # Companion: the equivalent pipe table produces the identical flattened
    # shape -- proves this fix reproduces the already-working precedent
    # exactly, not an approximation of it.
    assert ri.parse_page_blocks(PIPE_TABLE) == ["A", "B", "C", "D"]


def test_html_table_heading_cell_is_locatable():
    page_text = (
        "<table><tr><td>Specification No.: REC-064</td>"
        "<td>3.3 TIME SCHEDULE</td></tr></table>"
    )
    blocks = ri.parse_page_blocks(page_text)
    result = locate_heading_block(blocks, "3.3 TIME SCHEDULE", set())
    assert result is not None
    block_idx, match_kind, ratio = result
    assert blocks[block_idx] == "3.3 TIME SCHEDULE"
    assert match_kind == "exact"


def test_html_table_false_positive_guard_suppresses_generic_cell_shape():
    page_text = "<table><tr><td>DOC TITLE</td><td>SUMMARY PRICE PROPOSAL</td></tr></table>"
    by_key, _ = _by_key(page_text)
    assert not any(c["own_label"] == "SUMMARY PRICE PROPOSAL" for c in by_key.values())
    # Control: the identical text NOT inside a table DOES mint via residual
    # fallback #2 -- proves the guard, not a non-match, suppressed it.
    control_by_key, _ = _by_key("SUMMARY PRICE PROPOSAL\n")
    assert any(c["own_label"] == "SUMMARY PRICE PROPOSAL" for c in control_by_key.values())


def test_pipe_table_false_positive_guard_also_applies():
    # Same guard, via a native markdown pipe table -- confirms the unified
    # `block.get("type") != "table_cell"` check (not a separate HTML-only
    # marker) covers this already-working path too, per the explicit
    # decision to extend the guard to both table kinds in this round.
    page_text = "| DOC TITLE | SUMMARY PRICE PROPOSAL |\n| --- | --- |\n"
    by_key, _ = _by_key(page_text)
    assert not any(c["own_label"] == "SUMMARY PRICE PROPOSAL" for c in by_key.values())


def test_html_table_strong_signal_cells_still_mint():
    # A genuine keyword-matched heading inside a table cell must still
    # mint normally -- the guard is scoped to the weak shape-only
    # heuristic, never to a real CORE_RE/fuzzy match.
    page_text = "<table><tr><td>Annexure A-1</td><td>some description</td></tr></table>"
    by_key, _ = _by_key(page_text)
    assert "annexure:a-1" in by_key


def test_non_table_block_html_remains_invisible():
    assert ri.parse_page_blocks("<div>some wrapped content</div>") == []
    assert ri.parse_page_blocks("<center>CONTRACT AGREEMENT</center>") == []


def test_html_table_malformed_missing_end_tags_recovered_gracefully():
    # No </td> closes at all -- a new <td>/<tr> start must implicitly
    # close whatever's open, same as a lenient browser table parser.
    page_text = "<table><tr><td>A<td>B</tr><tr><td>C<td>D</table>"
    assert ri.parse_page_blocks(page_text) == ["A", "B", "C", "D"]


def test_html_table_unclosed_table_flushes_last_row_on_close():
    # Confirmed live during review: stdlib HTMLParser.close() does NOT
    # synthesize end-tag events for anything still open -- without the
    # _HtmlTableCellParser.close() override, this would silently lose
    # both cells of the only row.
    page_text = "<table><tr><td>Cell A</td><td>Cell B</td>"
    assert ri.parse_page_blocks(page_text) == ["Cell A", "Cell B"]


def test_html_table_entities_and_inline_formatting_in_cells():
    # Confirmed live: HTMLParser(convert_charrefs=True) decodes &nbsp; to
    # U+00A0, and \s in the cell-text cleanup regex matches U+00A0 too, so
    # both collapse to a plain space as expected. Also tests <p>/<div>/<br> spacing.
    page_text = "<table><tr><td>A&nbsp;&amp;&nbsp;B</td><td>bold<br>text</td><td><p>para 1</p><p>para 2</p></td></tr></table>"
    assert ri.parse_page_blocks(page_text) == ["A & B", "bold text", "para 1 para 2"]


def test_trailing_text_glued_to_same_html_blob_stays_invisible():
    # Known, accepted limitation: mistune glues trailing non-tag text onto
    # the SAME block_html token when it immediately follows </table> with
    # no blank line -- that trailing text stays invisible exactly as it
    # was pre-fix, not a new regression. Locked in explicitly rather than
    # left as an undiscovered edge case.
    page_text = "<table><tr><td>A</td></tr></table>\nglued trailing text, no blank line before it"
    assert ri.parse_page_blocks(page_text) == ["A"]


def test_html_table_plural_family_keyword_headers_do_not_crash_or_mint():
    # Confirmed live, pre-existing crash (reproducible today even with
    # plain text, no table involved) that this fix makes far more likely
    # to hit in practice, since exactly these words are common table
    # column headers: AttributeError: 'NoneType' object has no attribute
    # 'end' in build_targets_for_page's _fuzzy_family_match call site.
    page_text = "<table><tr><th>Pages</th><th>Articles</th></tr></table>"
    by_key, _ = _by_key(page_text)  # must not raise
    assert "page:5" not in by_key
    assert "article:5" not in by_key


# ─── parse_page_blocks_with_row_ids: opt-in row grouping for the AI-path's
# row-merge grounding fallback (ai_reference_index.py). Never touches
# parse_page_blocks/parse_page_blocks_with_types's own output -- see the
# dedicated regression guard below. ──────────────────────────────────────


def test_parse_page_blocks_with_row_ids_groups_cells_by_row():
    texts, types, row_ids = ri.parse_page_blocks_with_row_ids(HTML_TABLE)
    assert texts == ["A", "B", "C", "D"]
    assert types == ["table_cell", "table_cell", "table_cell", "table_cell"]
    assert row_ids == [0, 0, 1, 1]


def test_parse_page_blocks_with_row_ids_does_not_change_the_shared_parser_contract():
    # Regression guard: this whole feature must be a no-op for the
    # rule-path's existing, already-tested contract.
    assert ri.parse_page_blocks(HTML_TABLE) == ["A", "B", "C", "D"]
    texts, types = ri.parse_page_blocks_with_types(HTML_TABLE)
    assert texts == ["A", "B", "C", "D"]
    assert types == ["table_cell", "table_cell", "table_cell", "table_cell"]


def test_parse_page_blocks_with_row_ids_is_globally_unique_across_multiple_tables():
    page_text = HTML_TABLE + "\n\n" + "<table><tr><td>E</td><td>F</td></tr></table>"
    texts, _, row_ids = ri.parse_page_blocks_with_row_ids(page_text)
    assert texts == ["A", "B", "C", "D", "E", "F"]
    assert row_ids == [0, 0, 1, 1, 2, 2]


def test_layout_aligned_degrades_gracefully_on_table_page():
    # A table-bearing page's det-tag count (one region) no longer lines up
    # with the post-fix per-cell block count (e.g. 1 det-tag vs 2 cell blocks)
    # -- confirmed this mismatch already existed pre-fix too (0 blocks vs.
    # 1 det-tag region), so this is accepted, pre-existing degradation, not
    # a new regression. The candidates must still be produced correctly even
    # when layout alignment degrades to False.
    page_text = "<table><tr><td>Annexure A-1</td><td>Scope of Supply</td></tr></table>"
    raw_ocr_text = "<|det|>table [100, 100, 800, 800]<|/det|><table><tr><td>Annexure A-1</td><td>Scope of Supply</td></tr></table>"
    by_key, _ = _by_key(page_text, raw_text=raw_ocr_text)
    assert "annexure:a-1" in by_key
