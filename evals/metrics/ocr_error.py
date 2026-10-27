"""OCR transcription accuracy and table structure metrics.

Computes:
- Character Error Rate (CER): Levenshtein(ref, hyp) / len(ref)
- Word Error Rate (WER): Levenshtein(tokenize(ref), tokenize(hyp)) / len(tokenize(ref))
- Table Structure Similarity: Row and cell exact/normalized match rate.

Uses rapidfuzz when available for maximum speed (<10ms per page),
with an optimized linear-memory DP fallback in pure Python.
"""

import re
from typing import Any, List, Optional, Sequence, Tuple

_WORD_SPLIT_RE = re.compile(r"\s+")

try:
    from rapidfuzz.distance import Levenshtein as _rf_levenshtein

    def _levenshtein_distance(seq1: Sequence[Any], seq2: Sequence[Any]) -> int:
        return _rf_levenshtein.distance(seq1, seq2)

except ImportError:

    def _levenshtein_distance(seq1: Sequence[Any], seq2: Sequence[Any]) -> int:
        """Optimized O(min(N, M)) memory Levenshtein distance in pure Python."""
        if seq1 == seq2:
            return 0
        if len(seq1) < len(seq2):
            seq1, seq2 = seq2, seq1
        if len(seq2) == 0:
            return len(seq1)

        previous_row = list(range(len(seq2) + 1))
        for i, item1 in enumerate(seq1):
            current_row = [i + 1] + [0] * len(seq2)
            for j, item2 in enumerate(seq2):
                insertions = previous_row[j + 1] + 1
                deletions = current_row[j] + 1
                substitutions = previous_row[j] + (item1 != item2)
                current_row[j + 1] = min(insertions, deletions, substitutions)
            previous_row = current_row
        return previous_row[-1]


def character_error_rate(reference: str, hypothesis: str) -> float:
    """Computes Character Error Rate (CER).

    CER = Levenshtein(reference, hypothesis) / max(1, len(reference)).
    Returns 0.0 when both are empty.
    """
    if reference == hypothesis:
        return 0.0
    ref_len = len(reference)
    if ref_len == 0:
        return 1.0 if len(hypothesis) > 0 else 0.0

    dist = _levenshtein_distance(reference, hypothesis)
    return dist / ref_len


def word_error_rate(reference: str, hypothesis: str) -> float:
    """Computes Word Error Rate (WER).

    Tokens are extracted by whitespace splitting.
    WER = Levenshtein(reference_tokens, hypothesis_tokens) / max(1, len(reference_tokens)).
    """
    ref_words = [w for w in _WORD_SPLIT_RE.split(reference.strip()) if w]
    hyp_words = [w for w in _WORD_SPLIT_RE.split(hypothesis.strip()) if w]

    if ref_words == hyp_words:
        return 0.0
    ref_len = len(ref_words)
    if ref_len == 0:
        return 1.0 if len(hyp_words) > 0 else 0.0

    dist = _levenshtein_distance(ref_words, hyp_words)
    return dist / ref_len


def table_cell_accuracy(
    reference_table: List[List[str]],
    hypothesis_table: List[List[str]],
) -> float:
    """Computes exact match rate across aligned table cells.

    Returns float in [0.0, 1.0].
    """
    if not reference_table and not hypothesis_table:
        return 1.0
    if not reference_table or not hypothesis_table:
        return 0.0

    total_cells = sum(len(row) for row in reference_table)
    if total_cells == 0:
        return 1.0

    matched_cells = 0
    for r_idx, ref_row in enumerate(reference_table):
        if r_idx >= len(hypothesis_table):
            break
        hyp_row = hypothesis_table[r_idx]
        for c_idx, ref_cell in enumerate(ref_row):
            if c_idx >= len(hyp_row):
                break
            if ref_cell.strip() == hyp_row[c_idx].strip():
                matched_cells += 1

    return matched_cells / total_cells
