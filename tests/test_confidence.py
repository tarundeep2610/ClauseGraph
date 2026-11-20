from PIL import Image, ImageDraw, ImageFilter

import confidence as conf


def _make_sharp_high_contrast_image():
    img = Image.new("L", (200, 200), color=255)
    draw = ImageDraw.Draw(img)
    for x in range(0, 200, 10):
        draw.line([(x, 0), (x, 200)], fill=0, width=2)
    return img.convert("RGB")


def test_compute_image_quality_distinguishes_sharp_from_blurred():
    sharp = _make_sharp_high_contrast_image()
    blurred = sharp.filter(ImageFilter.GaussianBlur(radius=8))
    sharp_q = conf.compute_image_quality(sharp)
    blurred_q = conf.compute_image_quality(blurred)
    assert blurred_q["blur_variance"] < sharp_q["blur_variance"]


def test_clean_text_scores_perfect_and_unflagged():
    text = "This is a perfectly clean, well-formed page of transcribed text.\n" * 3
    score = conf.score_page(text, ink_ratio=0.3)
    assert score.confidence == 1.0
    assert score.needs_review is False
    assert score.tier == "highly_confident"


def test_hard_failure_flag_scores_zero():
    score = conf.score_page("some text that would otherwise look fine", hard_failure=True)
    assert score.confidence == 0.0
    assert score.needs_review is True
    assert score.tier == "failed"


def test_error_sentinel_string_scores_zero():
    text = "### ❌ Unlimited-OCR inference failed\n```\nTraceback (most recent call last):\n  File x\n```"
    score = conf.score_page(text)
    assert score.confidence == 0.0
    assert score.needs_review is True
    assert score.tier == "failed"
    assert "engine_error_in_output" in score.reasons


def test_blank_page_guard_scores_perfect_and_unflagged():
    score = conf.score_page("", ink_ratio=0.001)
    assert score.confidence == 1.0
    assert score.needs_review is False
    assert score.tier == "blank_page"


def test_empty_output_on_inked_page_flags():
    score = conf.score_page("", ink_ratio=0.5)
    assert score.confidence <= 0.40
    assert score.needs_review is True
    assert "empty_output_on_inked_page" in score.reasons


def test_repetition_loop_flags():
    text = "normal line one\nnormal line two\n" + "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\n" * 2
    score = conf.score_page(text)
    assert "repetition_loop" in score.reasons
    assert score.needs_review is True


def test_dashes_and_table_separators_do_not_trip_repetition_detector():
    text = "Header\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n-----\n\nMore normal content here that is fine."
    score = conf.score_page(text)
    assert "repetition_loop" not in score.reasons


def test_mojibake_flags_unexpected_script():
    text = "Some text with replacement chars ����� sprinkled throughout the passage."
    score = conf.score_page(text)
    assert "unexpected_script" in score.reasons


def test_foreign_script_ratio_flags():
    # Bengali characters mixed into otherwise Latin text, well above the 2% threshold.
    text = "Normal English text here. " + "ঝ" * 30
    score = conf.score_page(text)
    assert "unexpected_script" in score.reasons


def test_devanagari_is_not_flagged_as_foreign():
    text = "यह हिंदी में लिखा गया एक सामान्य पाठ है और यह पूरी तरह से सही है।"
    score = conf.score_page(text)
    assert "unexpected_script" not in score.reasons


def test_malformed_table_flags():
    rows = ["| a | b | c | d |"] * 6 + ["| a | b |"] * 3
    text = "\n".join(rows)
    score = conf.score_page(text)
    assert "malformed_table" in score.reasons


def test_index_row_as_values_flags():
    text = "| Item | Qty | Price |\n|---|---|---|\n| 1 | 2 | 3 |\n\nSome more descriptive text follows here to pad length."
    score = conf.score_page(text)
    assert "index_row_as_values" in score.reasons


def test_format_violation_flags_raw_json():
    text = '{\n  "key": "value"\n}\nSome regular text after the JSON block for good measure here.'
    score = conf.score_page(text)
    assert "format_violation" in score.reasons


def test_near_timeout_flags():
    score = conf.score_page("Clean text of reasonable length for this test case here.", elapsed_s=95, timeout_s=100)
    assert "near_timeout" in score.reasons


def test_attempts_three_scores_exactly_point_eight_escalates_not_flagged():
    text = "Clean text with no structural issues at all, just retried a couple times."
    score = conf.score_page(text, attempts=3)
    assert round(score.confidence, 2) == 0.80
    assert score.escalate is True
    assert score.needs_review is False


def test_confidence_exactly_at_threshold_is_not_flagged():
    # attempts=4 -> penalty 0.30 -> confidence exactly 0.70
    text = "Clean text with no other structural issues present in this sample page."
    score = conf.score_page(text, attempts=4)
    assert round(score.confidence, 2) == 0.70
    assert score.needs_review is False, "0.70 must not be flagged — the threshold is strict <"


def test_boundary_continuity_mismatch_fires_on_genuine_mismatch():
    primer = {"ends_mid_table": True, "table_header": "| Item | Qty | Price |\n|---|---|---|"}
    text = "Just plain prose with no table markers anywhere in this text at all."
    score = conf.score_page(text, primer=primer)
    assert "boundary_continuity_mismatch" in score.reasons


def test_boundary_continuity_mismatch_silent_on_valid_continuation():
    primer = {"ends_mid_table": True, "table_header": "| Item | Qty | Price |\n|---|---|---|"}
    text = "| Pencil | 5 | 20 |\n| Eraser | 1 | 5 |"
    score = conf.score_page(text, primer=primer)
    assert "boundary_continuity_mismatch" not in score.reasons


def test_overlap_disagreement_fires_on_divergent_transcriptions():
    text = "The quick brown fox jumps over the lazy dog in this passage."
    other = "Completely different content that shares almost nothing with the above."
    score = conf.score_page(text, overlap_alt_text=other)
    assert "overlap_disagreement" in score.reasons
    # 0.25 alone is deliberately in the "survivable noise" tier, not "trips review
    # alone" — matches the plan's weight design (any single penalty <= 0.25 shouldn't
    # cross the review threshold by itself; it needs a partner signal).
    assert round(score.confidence, 2) == 0.75
    assert score.escalate is True
    assert score.needs_review is False


def test_overlap_disagreement_silent_on_close_agreement():
    text = "The quick brown fox jumps over the lazy dog in this passage of text."
    other = "The quick brown fox jumps over the lazy dog in this passage of text!"
    score = conf.score_page(text, overlap_alt_text=other)
    assert "overlap_disagreement" not in score.reasons


def test_image_quality_signal_penalizes_blurry_low_contrast_source():
    score = conf.score_page(
        "Clean-looking text output despite a poor source image.",
        image_quality={"blur_variance": 5.0, "contrast": 3.0},
    )
    assert "low_image_sharpness" in score.reasons
    assert "low_image_contrast" in score.reasons


def test_image_quality_signal_silent_on_sharp_high_contrast_source():
    score = conf.score_page(
        "Clean text output from a nice sharp source image.",
        image_quality={"blur_variance": 500.0, "contrast": 60.0},
    )
    assert "low_image_sharpness" not in score.reasons
    assert "low_image_contrast" not in score.reasons


def test_token_confidence_disabled_by_default_is_a_pure_noop():
    logprobs = [{"logprob": -5.0}] * 20  # would be low-confidence if enabled
    score_disabled = conf.score_page("Some clean text output here for this page.", token_logprobs=logprobs)
    assert "low_token_confidence" not in score_disabled.reasons

    score_enabled = conf.score_page(
        "Some clean text output here for this page.",
        token_logprobs=logprobs,
        cfg={"token_confidence_enabled": True},
    )
    assert "low_token_confidence" in score_enabled.reasons


def test_token_confidence_skipped_when_no_logprobs_even_if_enabled():
    score = conf.score_page("Some clean text.", token_logprobs=None, cfg={"token_confidence_enabled": True})
    assert "low_token_confidence" not in score.reasons


def test_tier_boundaries_exact():
    cfg = conf.DEFAULT_CFG
    assert conf.tier_for_stored_confidence(0.699, cfg=cfg) == "needs_review"
    assert conf.tier_for_stored_confidence(0.700, cfg=cfg) == "confident"
    assert conf.tier_for_stored_confidence(0.849, cfg=cfg) == "confident"
    assert conf.tier_for_stored_confidence(0.850, cfg=cfg) == "very_confident"
    assert conf.tier_for_stored_confidence(0.899, cfg=cfg) == "very_confident"
    assert conf.tier_for_stored_confidence(0.900, cfg=cfg) == "highly_confident"


def test_tier_special_cases_override_numeric_scale():
    assert conf.tier_for_stored_confidence(1.0, signals={"blank_page": True}) == "blank_page"
    assert conf.tier_for_stored_confidence(0.0, signals={"engine_error_in_output": True}) == "failed"


def test_tier_none_for_unscored_legacy_page():
    assert conf.tier_for_stored_confidence(None) is None


def test_retuning_threshold_reclassifies_at_read_time():
    """Tier is computed from current config, not frozen at processing time."""
    tier_default = conf.tier_for_stored_confidence(0.72)
    tier_retuned = conf.tier_for_stored_confidence(0.72, cfg={"confidence_threshold": 0.75})
    assert tier_default == "confident"
    assert tier_retuned == "needs_review"


def test_text_similarity_identical_and_empty():
    assert conf.text_similarity("hello world", "hello world") == 1.0
    assert conf.text_similarity("", "") == 1.0
    assert conf.text_similarity("something", "") == 0.0
