"""Tests for _slugify_subdoc_label and the occurrence_index fix at the
`subdoc` fallback family's minting sites — the fix for "ordinal drift"
(subdoc:N keys assigned from a positional counter, which shifts whenever one
run detects a different number of generic boundaries than another). See
reference_index._slugify_subdoc_label's own docstring for the full
rationale."""

from reference_index import _disambiguate_label, _slugify_subdoc_label, build_targets_for_page, mint_key_for_heading


def test_slug_is_independent_of_the_boundary_ordinal_counter():
    # The whole point of content-addressing: minting the identical heading
    # at two different counter starting points must yield the identical key.
    _, key_a, _, _, _ = mint_key_for_heading("SCOPE OF WORK", 0, {})
    _, key_b, _, _, _ = mint_key_for_heading("SCOPE OF WORK", 41, {})
    assert key_a == key_b == "subdoc:scope-of-work"


def test_different_headings_get_different_slugs():
    assert _slugify_subdoc_label("GROUNDS OF APPEAL") != _slugify_subdoc_label("SCOPE OF WORK")


def test_empty_or_punctuation_only_text_falls_back_to_a_stable_hash():
    key1 = _slugify_subdoc_label("....")
    key2 = _slugify_subdoc_label("....")
    assert key1 == key2  # deterministic, not random
    assert key1.startswith("subdoc:unlabeled-")

    assert _slugify_subdoc_label("") .startswith("subdoc:unlabeled-")
    assert _slugify_subdoc_label(None).startswith("subdoc:unlabeled-")


def test_non_latin_or_garbled_text_does_not_crash_and_is_still_deterministic():
    key1 = _slugify_subdoc_label("आदेश")  # Hindi script, no ASCII letters at all
    key2 = _slugify_subdoc_label("आदेश")
    assert key1 == key2
    assert key1.startswith("subdoc:")


def test_long_heading_text_is_capped():
    long_text = " ".join(f"word{i}" for i in range(20))
    key = _slugify_subdoc_label(long_text)
    assert len(key) <= len("subdoc:") + 40


def test_disambiguation_suffix_survives_even_on_a_heading_long_enough_to_be_truncated():
    """Regression: found while regenerating the real 100-page gold fixture's
    keys. A long heading's disambiguation suffix ("... (2)") landed past the
    6-word cap and was silently truncated away, colliding with the unsuffixed
    first occurrence's key — exactly the collision this whole change exists
    to eliminate, just reintroduced by the cap itself."""
    base = "I, the above named deponent do hereby solemnly affirm and state"
    first = _slugify_subdoc_label(base)
    second = _slugify_subdoc_label(f"{base} (2)")
    assert first != second
    assert second == f"{first}-2"


def test_disambiguate_label_recognizes_a_case_only_repeat():
    """Regression: found on the real gold fixture — "RESPECTFULLY SHOWETH"
    and "Respectfully Showeth" are the same heading recurring with only a
    case difference. Exact-string counting treated both as unrelated first
    occurrences (used to be harmless when boundary_ordinal kept their keys
    unique regardless of label); now that the key is slugged FROM the label,
    two genuinely different sub-documents would otherwise mint the identical
    key."""
    counts = {}
    first = _disambiguate_label("RESPECTFULLY SHOWETH", counts)
    second = _disambiguate_label("Respectfully Showeth", counts)
    assert first == "RESPECTFULLY SHOWETH"
    assert second == "Respectfully Showeth (2)"
    assert _slugify_subdoc_label(first) != _slugify_subdoc_label(second)


def test_two_identical_same_page_fallback_headings_disambiguate_via_occurrence_index_not_a_db_crash():
    """Negative-case regression for the review-caught bug: before the fix,
    these two residual-fallback headings (same text, same page) both
    hardcoded occurrence_index=0 — once the key stopped being globally
    unique (boundary_ordinal), that was a guaranteed sqlite3.IntegrityError
    on the real ocr_reference_targets PRIMARY KEY (job_id, key, page,
    occurrence_index). This proves the occurrence_counts-based fix is
    load-bearing, not merely defensive."""
    import database
    import uuid

    # "Insurance:" is a colon-terminated generic-shaped heading (residual
    # fallback #2, reference_index.py's _looks_like_generic_heading_shape) —
    # a pure leaf, never pushed as a stack ancestor, so a repeated exact
    # match here relies solely on occurrence_index to disambiguate, not
    # _disambiguate_label's key-level suffixing (that only applies to the
    # two sites that push onto nested_stack as a real ancestor).
    page_text = (
        "Insurance:\n\nSome unrelated filler text that is not a heading at all here today.\n\n"
        "Insurance:\n\nMore filler text that is also not a heading at all in this spot either.\n"
    )
    candidates, _ = build_targets_for_page(page_text, seed_scope=None, has_next_page=False)
    insurance_candidates = [c for c in candidates if c["key"] == "subdoc:insurance"]
    assert len(insurance_candidates) == 2
    assert {c["occurrence_index"] for c in insurance_candidates} == {0, 1}

    job_id = str(uuid.uuid4())
    database.create_job(
        job_id=job_id, filename="t.pdf", file_path="/tmp/x.pdf", file_type="pdf",
        engine="unlimited", total_pages=1,
    )
    database.upsert_page(job_id, 1, status="success", text=page_text, confidence=0.9)
    version = database.bump_reference_index_version(job_id)
    # Must not raise sqlite3.IntegrityError.
    database.upsert_reference_targets_for_page(job_id, 1, candidates, version)
