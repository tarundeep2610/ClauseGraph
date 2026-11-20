"""Unit tests for plan_chunks overlap support and boundary handling."""

from ai_indexing.prompt import PageInput, plan_chunks


def test_overlap_zero_preserves_strict_disjoint_chunks():
    pages = [PageInput(page_num=i, text=f"text for page {i}") for i in range(1, 26)]
    chunks = plan_chunks(pages, max_pages=10, overlap_pages=0)
    assert len(chunks) == 3
    assert chunks[0].page_nums == list(range(1, 11))
    assert chunks[1].page_nums == list(range(11, 21))
    assert chunks[2].page_nums == list(range(21, 26))


def test_overlap_two_advances_by_eight_new_pages_per_chunk():
    pages = [PageInput(page_num=i, text=f"text for page {i}") for i in range(1, 26)]
    chunks = plan_chunks(pages, max_pages=10, overlap_pages=2)
    assert len(chunks) == 3
    # Chunk 0: pages 1..10 (10 new)
    assert chunks[0].page_nums == list(range(1, 11))
    # Chunk 1: pages 9..18 (starts with overlap 9, 10 + 8 new: 11..18)
    assert chunks[1].page_nums == list(range(9, 19))
    # Chunk 2: pages 17..25 (starts with overlap 17, 18 + 7 new: 19..25)
    assert chunks[2].page_nums == list(range(17, 26))
    assert chunks[1].pages[:2] == chunks[0].pages[-2:]
    assert chunks[2].pages[:2] == chunks[1].pages[-2:]


def test_even_division_produces_no_trailing_zero_new_page_chunk():
    # 18 pages: chunk 0 is 1..10 (10 new), chunk 1 is 9..18 (8 new, 18 total).
    # Since all 18 pages are consumed, no chunk 2 with just [17, 18] should be emitted.
    pages = [PageInput(page_num=i, text=f"text for page {i}") for i in range(1, 19)]
    chunks = plan_chunks(pages, max_pages=10, overlap_pages=2)
    assert len(chunks) == 2
    assert chunks[0].page_nums == list(range(1, 11))
    assert chunks[1].page_nums == list(range(9, 19))


def test_overlap_pages_clamped_when_ge_max_pages():
    pages = [PageInput(page_num=i, text=f"text for page {i}") for i in range(1, 11)]
    # max_pages=5, overlap_pages=10 -> clamped to 4 (max_pages - 1)
    chunks = plan_chunks(pages, max_pages=5, overlap_pages=10)
    assert len(chunks) > 1
    for c in chunks:
        assert len(c.pages) <= 5
    # Chunk 0: 1..5. Chunk 1: 2..6 (starts with 2,3,4,5 + 1 new: 6), etc.
    assert chunks[0].page_nums == [1, 2, 3, 4, 5]
    assert chunks[1].page_nums == [2, 3, 4, 5, 6]


def test_oversized_carried_pages_make_forward_progress_and_reseed_chars_accurately():
    # Carried pages alone exceed max_chars budget
    p1 = PageInput(page_num=1, text="A" * 60)
    p2 = PageInput(page_num=2, text="B" * 60)
    p3 = PageInput(page_num=3, text="C" * 60)
    pages = [p1, p2, p3]

    # max_pages=5, max_chars=100, overlap_pages=2
    # Chunk 0: [p1] (60 chars)
    # Chunk 1: [p1, p2] (120 chars) -> re-seeds with [p1, p2] (120 chars > 100).
    # When considering p3, size check fires (120 + 60 > 100), but new_count=0, so flush() no-ops.
    # p3 is appended unconditionally, creating Chunk 2 with [p1, p2, p3] (180 chars).
    chunks = plan_chunks(pages, max_pages=5, max_chars=100, overlap_pages=2)
    assert len(chunks) == 3
    assert chunks[0].page_nums == [1]
    assert chunks[0].total_chars == 60
    assert chunks[1].page_nums == [1, 2]
    assert chunks[1].total_chars == 120
    assert chunks[2].page_nums == [1, 2, 3]
    assert chunks[2].total_chars == 180


def test_sparse_pages_pack_into_a_single_chunk():
    pages = [PageInput(page_num=i, text="x" * 500) for i in range(1, 5)]
    chunks = plan_chunks(pages, max_pages=4, max_chars=7000)
    assert len(chunks) == 1
    assert chunks[0].page_nums == [1, 2, 3, 4]


def test_dense_pages_split_on_char_budget_before_page_cap():
    # Two 3000-char pages fit the 7000 budget (6000), a third would not
    # (9000) -> splits at 2 pages, well short of the 4-page cap.
    pages = [PageInput(page_num=i, text="x" * 3000) for i in range(1, 4)]
    chunks = plan_chunks(pages, max_pages=4, max_chars=7000)
    assert len(chunks) == 2
    assert chunks[0].page_nums == [1, 2]
    assert chunks[1].page_nums == [3]


def test_oversized_page_isolates_itself_whole_and_untruncated():
    huge_text = "x" * 20000
    pages = [
        PageInput(page_num=1, text="small page one"),
        PageInput(page_num=2, text=huge_text),
        PageInput(page_num=3, text="small page three"),
    ]
    chunks = plan_chunks(pages, max_pages=4, max_chars=7000)
    assert len(chunks) == 3
    assert chunks[1].page_nums == [2]
    assert chunks[1].pages[0].text == huge_text
    assert len(chunks[1].pages[0].text) == 20000
    assert "truncated" not in chunks[1].pages[0].text


def test_pages_never_split_across_chunk_boundaries():
    pages = [PageInput(page_num=i, text="x" * 1000) for i in range(1, 11)]
    chunks = plan_chunks(pages, max_pages=4, max_chars=2500)
    assert len(chunks) > 1
    seen = []
    for c in chunks:
        seen.extend(c.page_nums)
    assert seen == list(range(1, 11))
    assert len(seen) == len(set(seen))
