"""Pure scheduling/throughput helper — computes how a document's pages are split into
overlapping chunks for parallel processing (and, when batch mode is active, the literal
page groups passed to a single infer_multi() call).

This module has no opinion on context/continuity — that's handled uniformly by
context_primer.py (best-effort, live) and seam_stitching.py (the actual guarantee,
post-hoc). See the plan's "Chunking model" section for why.
"""
from typing import Callable, List, Optional, TypedDict


class Chunk(TypedDict):
    chunk_index: int
    start_page: int
    end_page: int
    owned_start: int
    owned_end: int


def compute_chunks(total_pages: int, chunk_size: int, overlap: bool = True) -> List[Chunk]:
    """Chunk k (1-indexed) spans pages [1+(k-1)*(N-1), that+N-1], clamped to total_pages.
    Consecutive chunks share exactly one boundary page (chunk k's end_page == chunk
    k+1's start_page). owned_start/owned_end give each chunk's *exclusive* pages
    (the boundary page is owned only by the lower-numbered chunk).

    overlap=False produces a gapless, non-overlapping partition instead (no shared
    boundary page between any two chunks) — used by batch mode when a job opts out
    of page-sharing between infer_multi() calls. Only batch mode ever calls this;
    single-page mode has no chunk concept at all (see task_queue.py)."""
    if not overlap:
        if chunk_size < 1:
            raise ValueError(f"chunk_size must be >= 1, got {chunk_size}")
        if total_pages <= 0:
            return []
        chunks: List[Chunk] = []
        k = 1
        start = 1
        while start <= total_pages:
            end = min(start + chunk_size - 1, total_pages)
            chunks.append({
                "chunk_index": k, "start_page": start, "end_page": end,
                "owned_start": start, "owned_end": end,
            })
            start = end + 1
            k += 1
        return chunks

    if chunk_size < 2:
        raise ValueError(f"chunk_size must be >= 2 (stride would be 0 or negative), got {chunk_size}")
    if total_pages <= 0:
        return []
    if total_pages <= chunk_size:
        return [{"chunk_index": 1, "start_page": 1, "end_page": total_pages,
                  "owned_start": 1, "owned_end": total_pages}]

    stride = chunk_size - 1
    chunks: List[Chunk] = []
    k = 1
    start = 1
    while start < total_pages:
        end = min(start + chunk_size - 1, total_pages)
        owned_start = start if k == 1 else start + 1
        chunks.append({
            "chunk_index": k, "start_page": start, "end_page": end,
            "owned_start": owned_start, "owned_end": end,
        })
        if end == total_pages:
            break
        start += stride
        k += 1
    return chunks


def chunk_for_page(chunks: List[Chunk], page_num: int) -> Optional[Chunk]:
    """Returns the chunk that *owns* page_num (never the chunk it's merely a boundary
    member of, except for the chunk that actually owns it)."""
    for c in chunks:
        if c["owned_start"] <= page_num <= c["owned_end"]:
            return c
    return None


def is_boundary_page(chunks: List[Chunk], page_num: int) -> bool:
    """True if page_num is the shared boundary page between two chunks (i.e. it's
    some chunk's end_page and also the next chunk's start_page)."""
    for i, c in enumerate(chunks[:-1]):
        if c["end_page"] == page_num and chunks[i + 1]["start_page"] == page_num:
            return True
    return False


def build_chunk_lookup(total_pages: int, chunk_size: int, overlap: bool = True) -> Callable[[int], tuple]:
    """Convenience for database.init_pages_for_job's chunk_of callback:
    returns a function page_num -> (chunk_id, is_boundary)."""
    chunks = compute_chunks(total_pages, chunk_size, overlap=overlap)

    def _lookup(page_num: int):
        chunk = chunk_for_page(chunks, page_num)
        chunk_id = chunk["chunk_index"] if chunk else None
        return chunk_id, is_boundary_page(chunks, page_num)

    return _lookup
