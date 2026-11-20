"""Unit tests for hybrid pleading points supplementation and listing container gate."""

import pytest
import reference_index


def test_detect_pleading_points_suppressed_in_listing_container():
    """Hazard 1 verification: table rows inside Price Schedule, Index, or Spares
    must NOT be minted as point targets by detect_pleading_points_for_page."""
    page_text = (
        "11. Relays, power contactors and auxiliary switches\n\n"
        "12. Current Transformers 400kV\n\n"
        "A. 2000A Isolators\n"
    )
    # Stack where active immediate parent is a listing container
    scope_listing = {
        "stack": [
            {"family": "subdoc", "key": "subdoc:judgment", "label": "JUDGMENT"},
            {"family": "subdoc", "key": "subdoc:price-schedule", "label": "PRICE SCHEDULE"},
        ]
    }
    cands, last_dec = reference_index.detect_pleading_points_for_page(page_text, scope_listing, [])
    assert cands == []
    assert last_dec is None


def test_detect_pleading_points_active_in_pleading_container():
    """Legitimate pleading points inside an application/averment container ARE detected."""
    page_text = (
        "1. That the appellant is filing the accompanying appeal\n\n"
        "2. That for proper and just decision of the appeal\n"
    )
    scope_pleading = {
        "stack": [
            {"family": "subdoc", "key": "subdoc:respectfully-showeth", "label": "RESPECTFULLY SHOWETH:"},
        ]
    }
    cands, last_dec = reference_index.detect_pleading_points_for_page(page_text, scope_pleading, [])
    assert len(cands) == 2
    keys = [c["key"] for c in cands]
    assert "point:1" in keys
    assert "point:2" in keys


def test_detect_pleading_points_dedup_against_existing_candidates():
    """Points already reported by AI are not duplicated."""
    page_text = (
        "1. That the appellant is filing the accompanying appeal\n\n"
        "2. That for proper and just decision of the appeal\n"
    )
    scope_pleading = {
        "stack": [
            {"family": "subdoc", "key": "subdoc:respectfully-showeth", "label": "RESPECTFULLY SHOWETH:"},
        ]
    }
    # Pretend AI already reported point:1
    existing_cands = [{
        "key": "point:1",
        "fingerprint": "1. That the appellant is filin",
        "scope_key": "subdoc:respectfully-showeth",
    }]
    cands, _ = reference_index.detect_pleading_points_for_page(page_text, scope_pleading, existing_cands)
    # detect_pleading_points_for_page uses already_claimed_fingerprints to skip block 1
    assert len(cands) == 1
    assert cands[0]["key"] == "point:2"
