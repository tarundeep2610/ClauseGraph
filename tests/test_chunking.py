import pytest

import chunking


def _spans(chunks):
    return [(c["start_page"], c["end_page"]) for c in chunks]


def _owned(chunks):
    return [(c["owned_start"], c["owned_end"]) for c in chunks]


def test_n5_t13_matches_the_documented_example():
    chunks = chunking.compute_chunks(13, 5)
    assert _spans(chunks) == [(1, 5), (5, 9), (9, 13)]
    assert _owned(chunks) == [(1, 5), (6, 9), (10, 13)]


def test_owned_ranges_partition_all_pages_with_no_gaps_or_dupes():
    for n in range(2, 21):
        for t in range(0, 201):
            chunks = chunking.compute_chunks(t, n)
            covered = []
            for c in chunks:
                covered.extend(range(c["owned_start"], c["owned_end"] + 1))
            assert covered == list(range(1, t + 1)), f"n={n} t={t}"
            if len(chunks) > 1:
                for i in range(1, len(chunks)):
                    assert chunks[i]["start_page"] == chunks[i - 1]["end_page"]


def test_n5_t12_last_chunk_clamped():
    chunks = chunking.compute_chunks(12, 5)
    assert _spans(chunks)[-1] == (9, 12)


def test_n5_t5_single_chunk():
    chunks = chunking.compute_chunks(5, 5)
    assert _spans(chunks) == [(1, 5)]


def test_n5_t6_two_chunks():
    chunks = chunking.compute_chunks(6, 5)
    assert _spans(chunks) == [(1, 5), (5, 6)]


@pytest.mark.parametrize("t", [1, 2, 4])
def test_total_pages_less_than_chunk_size_collapses_to_one_chunk(t):
    chunks = chunking.compute_chunks(t, 5)
    assert _spans(chunks) == [(1, t)]


def test_zero_total_pages_returns_empty():
    assert chunking.compute_chunks(0, 5) == []


def test_chunk_size_of_one_raises():
    with pytest.raises(ValueError):
        chunking.compute_chunks(10, 1)


def test_n2_t10_nine_chunks_one_page_owned_after_first():
    chunks = chunking.compute_chunks(10, 2)
    assert len(chunks) == 9
    assert chunks[0]["owned_start"] == 1 and chunks[0]["owned_end"] == 2
    for c in chunks[1:]:
        assert c["owned_end"] - c["owned_start"] == 0


def test_n5_t1000_zero_cost_multiplier():
    chunks = chunking.compute_chunks(1000, 5)
    assert len(chunks) == 250
    total_owned = sum(c["owned_end"] - c["owned_start"] + 1 for c in chunks)
    assert total_owned == 1000


def test_chunk_for_page_returns_owning_chunk_not_boundary_sharer():
    chunks = chunking.compute_chunks(13, 5)
    boundary_chunk = chunking.chunk_for_page(chunks, 5)
    assert boundary_chunk["chunk_index"] == 1, "boundary page 5 is owned by chunk 1, not chunk 2"


def test_is_boundary_page():
    chunks = chunking.compute_chunks(13, 5)
    assert chunking.is_boundary_page(chunks, 5) is True
    assert chunking.is_boundary_page(chunks, 9) is True
    assert chunking.is_boundary_page(chunks, 3) is False
    assert chunking.is_boundary_page(chunks, 13) is False


def test_build_chunk_lookup():
    lookup = chunking.build_chunk_lookup(13, 5)
    chunk_id, boundary = lookup(5)
    assert chunk_id == 1
    assert boundary is True
    chunk_id, boundary = lookup(7)
    assert chunk_id == 2
    assert boundary is False


# ─── overlap=False (gapless, non-overlapping partition — batch mode opt-out) ──────

def test_overlap_false_gapless_partition_property():
    for n in range(1, 21):
        for t in range(0, 201):
            chunks = chunking.compute_chunks(t, n, overlap=False)
            covered = []
            for c in chunks:
                covered.extend(range(c["owned_start"], c["owned_end"] + 1))
            assert covered == list(range(1, t + 1)), f"n={n} t={t}"
            for c in chunks:
                assert c["owned_start"] == c["start_page"]
                assert c["owned_end"] == c["end_page"]
            if len(chunks) > 1:
                for i in range(1, len(chunks)):
                    assert chunks[i]["start_page"] == chunks[i - 1]["end_page"] + 1, "chunks must abut, never share a page"


def test_overlap_false_n5_t13():
    chunks = chunking.compute_chunks(13, 5, overlap=False)
    assert _spans(chunks) == [(1, 5), (6, 10), (11, 13)]
    assert _owned(chunks) == [(1, 5), (6, 10), (11, 13)]


def test_overlap_false_is_boundary_page_always_false():
    chunks = chunking.compute_chunks(13, 5, overlap=False)
    for page in range(1, 14):
        assert chunking.is_boundary_page(chunks, page) is False


def test_overlap_false_allows_chunk_size_of_one():
    chunks = chunking.compute_chunks(10, 1, overlap=False)
    assert len(chunks) == 10
    assert _spans(chunks) == [(i, i) for i in range(1, 11)]


def test_overlap_false_zero_total_pages_returns_empty():
    assert chunking.compute_chunks(0, 5, overlap=False) == []


def test_overlap_false_chunk_size_zero_raises():
    with pytest.raises(ValueError):
        chunking.compute_chunks(10, 0, overlap=False)


def test_overlap_true_is_still_the_default():
    # overlap defaults to True — existing call sites that don't pass it keep today's behavior.
    assert chunking.compute_chunks(13, 5) == chunking.compute_chunks(13, 5, overlap=True)


def test_build_chunk_lookup_overlap_false():
    lookup = chunking.build_chunk_lookup(13, 5, overlap=False)
    chunk_id, boundary = lookup(5)
    assert chunk_id == 1
    assert boundary is False
    chunk_id, boundary = lookup(6)
    assert chunk_id == 2
    assert boundary is False
