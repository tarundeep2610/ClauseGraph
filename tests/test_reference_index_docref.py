import pytest
import uuid
import database
import reference_index
from reference_index import (
    normalize_docref_code,
    docref_key,
    _is_plausible_docref_code,
    reconcile_docref_variants,
    detect_mentions_for_page,
    build_targets_for_page,
)


def test_normalization_of_hyphen_and_space_variants():
    # REC-064, REC064, REC 064 -> REC064 -> docref:rec064
    assert normalize_docref_code("REC-064") == "REC064"
    assert normalize_docref_code("REC064") == "REC064"
    assert normalize_docref_code("REC 064") == "REC064"

    assert docref_key("REC-064") == "docref:rec064"
    assert docref_key("REC064") == "docref:rec064"
    assert docref_key("REC 064") == "docref:rec064"


def test_compound_slash_paren_codes_with_and_without_internal_space():
    # Compound slash/paren codes with and without the internal space produce one key
    code1 = "Ch-35/REC-064/Xen(Projects)"
    code2 = "Ch-35/REC-064/XEN (Projects)"
    assert normalize_docref_code(code1) == "CH35REC064XENPROJECTS"
    assert normalize_docref_code(code2) == "CH35REC064XENPROJECTS"
    assert docref_key(code1) == "docref:ch35rec064xenprojects"
    assert docref_key(code2) == "docref:ch35rec064xenprojects"


def test_bare_numeral_rejection_and_claim_acceptance():
    # File No. 5 is rejected (bare numeral under non-permitted label)
    assert _is_plausible_docref_code("5", "File") is False
    assert _is_plausible_docref_code("5", "file") is False
    assert _is_plausible_docref_code("12", "Contract") is False

    # Claim No. 1, Case No. 2, Arbitration Case No. 3 are accepted
    assert _is_plausible_docref_code("1", "Claim") is True
    assert _is_plausible_docref_code("1", "claim") is True
    assert _is_plausible_docref_code("24", "case") is True
    assert _is_plausible_docref_code("105", "arbitration case") is True

    # Real alphanumeric codes with digits accepted under any label
    assert _is_plausible_docref_code("REC-064", "specification") is True
    assert _is_plausible_docref_code("EI070151", "proposal") is True

    # Reject codes with no digits at all
    assert _is_plausible_docref_code("ABC", "specification") is False


def test_no_docref_emitted_for_page_or_clause():
    # Page No. 4 and Clause No. 5 must never emit docref:* mentions
    text = "Please see Page No. 4 and Clause No. 5 for further details."
    mentions = detect_mentions_for_page(text, 1, target_index=[])
    docref_keys = [m["key"] for m in mentions if m["key"].startswith("docref:")]
    assert docref_keys == []


def test_fuzzy_folding_rec054_into_rec064_under_frequency_asymmetry():
    # REC064 appears 103 times, REC054 appears 3 times (103 >= 5 * 3 = 15; ratio >= 0.80)
    raw_codes = ["REC064"] * 103 + ["REC054"] * 3
    dominant_map, resolution_map = reconcile_docref_variants(raw_codes)
    assert dominant_map["REC054"] == "REC064"
    assert resolution_map["REC054"] == "approximate"
    assert dominant_map["REC064"] == "REC064"
    assert resolution_map["REC064"] == "exact"


def test_negative_case_two_comparably_frequent_similar_codes_stay_separate():
    # Two similar codes with comparable frequency (neither >= 5x of the other)
    raw_codes = ["REC064"] * 10 + ["REC065"] * 8
    dominant_map, resolution_map = reconcile_docref_variants(raw_codes)
    assert dominant_map["REC064"] == "REC064"
    assert resolution_map["REC064"] == "exact"
    assert dominant_map["REC065"] == "REC065"
    assert resolution_map["REC065"] == "exact"


def test_confusable_canonical_folds_e1_ei_at_any_frequency():
    # E1070151 (23x) and EI070151 (14x) are a SYSTEMATIC OCR confusion (1<->I),
    # not a rare typo -- neither count is 5x (or even 3x) of the other, so the
    # old frequency-asymmetry rule could never fold them. The confusable map
    # must fold them regardless of the frequency ratio.
    raw_codes = ["E1070151"] * 23 + ["EI070151"] * 14 + ["El070151"] * 2
    dominant_map, resolution_map = reconcile_docref_variants(raw_codes)
    assert dominant_map["E1070151"] == "E1070151"
    assert resolution_map["E1070151"] == "exact"
    assert dominant_map["EI070151"] == "E1070151"
    assert resolution_map["EI070151"] == "approximate"
    assert dominant_map["EL070151"] == "E1070151"
    assert resolution_map["EL070151"] == "approximate"


def test_confusable_canonical_folds_rec_family_variants():
    # REC064 (dominant) vs REC0G4 (G<->6 confusion) and RECO64 (O<->0 confusion),
    # regardless of frequency.
    raw_codes = ["REC064"] * 50 + ["REC0G4"] * 1 + ["RECO64"] * 1
    dominant_map, resolution_map = reconcile_docref_variants(raw_codes)
    assert dominant_map["REC064"] == "REC064"
    assert dominant_map["REC0G4"] == "REC064"
    assert resolution_map["REC0G4"] == "approximate"
    assert dominant_map["RECO64"] == "REC064"
    assert resolution_map["RECO64"] == "approximate"


def test_residual_fuzzy_fold_relaxed_to_3x_for_xen_gen_projects():
    # Real case: "...XENPROJECTS" (17x) vs "...GENPROJECTS" (4x). XEN/GEN is
    # not a glyph confusion in the confusable map (X and G aren't swapped),
    # so this must be caught by the residual fuzzy stage. 17 >= 3*4=12 (passes
    # the relaxed 3x threshold) but 17 < 5*4=20 (would have failed the old 5x).
    raw_codes = ["CH35REC064XENPROJECTS"] * 17 + ["CH35REC064GENPROJECTS"] * 4
    dominant_map, resolution_map = reconcile_docref_variants(raw_codes)
    assert dominant_map["CH35REC064XENPROJECTS"] == "CH35REC064XENPROJECTS"
    assert resolution_map["CH35REC064XENPROJECTS"] == "exact"
    assert dominant_map["CH35REC064GENPROJECTS"] == "CH35REC064XENPROJECTS"
    assert resolution_map["CH35REC064GENPROJECTS"] == "approximate"


def test_residual_fuzzy_fold_still_requires_3x_not_just_similarity():
    # Two similar codes at only ~2x frequency ratio must NOT fold under the
    # relaxed 3x rule either -- relaxing the multiplier must not turn into
    # "fold on similarity alone".
    raw_codes = ["ABCPROJECTS1"] * 10 + ["ABCPROJECTS2"] * 5
    dominant_map, resolution_map = reconcile_docref_variants(raw_codes)
    assert dominant_map["ABCPROJECTS1"] == "ABCPROJECTS1"
    assert resolution_map["ABCPROJECTS1"] == "exact"
    assert dominant_map["ABCPROJECTS2"] == "ABCPROJECTS2"
    assert resolution_map["ABCPROJECTS2"] == "exact"


def test_cross_label_linking_of_rec064_under_five_label_nouns():
    # Target defined under technical specification on page 1
    target = {
        "key": "docref:rec064",
        "page": 1,
        "occurrence_index": 0,
        "raw_text": "HVPN Technical Specification No. REC-064",
    }

    labels = [
        "bidding document",
        "tender enquiry",
        "file",
        "specification",
        "technical specification",
    ]

    for label in labels:
        page_text = f"As noted in {label} No. REC-064 attached hereto."
        mentions = detect_mentions_for_page(page_text, 2, target_index=[target])
        docrefs = [m for m in mentions if m["key"] == "docref:rec064"]
        assert len(docrefs) == 1, f"Failed for label {label}"
        assert docrefs[0]["resolution"] == "exact", f"Failed resolution for label {label}"
        assert "REC-064" in docrefs[0]["raw_text"]


def test_undefined_reference_yields_undefined_resolution():
    # A docref code appearing with no target row in target_index yields resolution="undefined"
    text = "HVPN letter No. Ch-35/REC-064/Xen(Projects) dated 11.12.2007"
    mentions = detect_mentions_for_page(text, 1, target_index=[])
    docrefs = [m for m in mentions if m["key"] == "docref:ch35rec064xenprojects"]
    assert len(docrefs) == 1
    assert docrefs[0]["resolution"] == "undefined"
    assert docrefs[0]["suppressed_reason"] == "unresolved"


def test_generic_triggers_fixture_still_produces_identical_subpoint_output():
    # Fixture from tests/test_reference_index_generic_triggers.py:171-189
    page_text = (
        "2.0 CONTRACT DOCUMENTS\n\n"
        "2.1 The contract shall be performed as per the following documents.\n\n"
        "i) HVPNL bidding document No. REC-064.\n\n"
        "ii) HVPN Technical Specification No. REC-064.\n\n"
        "iii) M/s Areva proposal No. EI070151.\n"
    )
    candidates, _ = build_targets_for_page(page_text, seed_scope=None, has_next_page=False)
    by_key = {c["key"]: c for c in candidates}
    for k, label_fragment in [
        ("subpoint:1", "i) HVPNL bidding document"),
        ("subpoint:2", "ii) HVPN Technical Specification"),
        ("subpoint:3", "iii) M/s Areva proposal"),
    ]:
        assert k in by_key, f"Missing {k}"
        assert by_key[k]["scope_key"] == "numheading:2.0/numheading:2.1", k
        assert by_key[k]["own_label"].startswith(label_fragment), k
