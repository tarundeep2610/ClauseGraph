"""Pure confidence scoring — no I/O. Neither OCR engine returns real probabilities, so
confidence is built from structural signals on the output text, plus a few signals
that look at the source image or compare independent attempts at the same page.

Every signal's raw value is persisted (see `PageScore.signals`) so thresholds can be
retuned from real production data later, rather than re-guessed.
"""
import difflib
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from context_primer import continues_table

DEFAULT_CFG = {
    "confidence_threshold": 0.70,       # below this -> needs_review
    "escalation_threshold": 0.85,       # below this -> escalate
    "very_confident_threshold": 0.85,
    "highly_confident_threshold": 0.90,  # also the audit-sampling high band floor
    "min_text_chars": 40,
    "blank_ink_ratio": 0.005,
    "max_char_run": 25,
    "max_line_dup_ratio": 0.50,
    "max_foreign_script_ratio": 0.02,
    "engine_agreement_min": 0.60,
    "image_blur_threshold": 50.0,
    "image_contrast_threshold": 15.0,
    "token_confidence_enabled": False,  # gated on S-6 — GLM only, unverified for vision calls
    "token_min_top1_prob": 0.3,
}

_LATIN_MIN, _LATIN_MAX = 0x0041, 0x024F
_DEVANAGARI_MIN, _DEVANAGARI_MAX = 0x0900, 0x097F


@dataclass
class PageScore:
    confidence: float
    needs_review: bool
    escalate: bool
    tier: str
    reasons: List[str] = field(default_factory=list)
    signals: Dict = field(default_factory=dict)


def text_similarity(a: Optional[str], b: Optional[str]) -> float:
    """Normalized-text similarity ratio in [0, 1], used for engine/overlap/escalation
    agreement checks."""
    a_norm = re.sub(r"\s+", " ", (a or "").strip().lower())
    b_norm = re.sub(r"\s+", " ", (b or "").strip().lower())
    if not a_norm and not b_norm:
        return 1.0
    if not a_norm or not b_norm:
        return 0.0
    return difflib.SequenceMatcher(None, a_norm, b_norm).ratio()


def _has_error_sentinel(text: str) -> bool:
    if not text:
        return False
    if text.lstrip().startswith("### ❌"):
        return True
    if "Unlimited-OCR inference failed" in text[:300]:
        return True
    if "Traceback (most recent call last)" in text[:500]:
        return True
    return False


_REPEAT_RUN_RE = None  # built lazily per max_char_run since it's parameterized


def _repetition_signal(text: str, max_char_run: int, max_line_dup_ratio: float):
    pattern = re.compile(r"([^\-=|\s])\1{" + str(max_char_run) + r",}")
    if pattern.search(text):
        return 0.35, "repetition_loop"

    lines = [l for l in text.splitlines() if l.strip()]
    if len(lines) >= 6:
        dup_ratio = 1 - len(set(lines)) / len(lines)
        if dup_ratio > max_line_dup_ratio:
            return 0.35, "repetition_loop"

    if len(text) >= 60:
        shingles = [text[i:i + 20] for i in range(0, len(text) - 20, 20)]
        if shingles:
            most_common_count = Counter(shingles).most_common(1)[0][1]
            if most_common_count >= 3 and most_common_count / len(shingles) > 0.3:
                return 0.35, "repetition_loop"

    return 0.0, None


def _in_expected_script(ch: str) -> bool:
    cp = ord(ch)
    return (_LATIN_MIN <= cp <= _LATIN_MAX) or (_DEVANAGARI_MIN <= cp <= _DEVANAGARI_MAX)


def _script_signal(text: str, max_foreign_ratio: float):
    if not text:
        return 0.0, None
    mojibake = text.count("�") + sum(1 for c in text if ord(c) < 32 and c not in "\n\t\r")
    if mojibake > 0:
        return 0.35, "unexpected_script"
    alphabetic = [c for c in text if c.isalpha()]
    if not alphabetic:
        return 0.0, None
    foreign = [c for c in alphabetic if not _in_expected_script(c)]
    if len(foreign) / len(alphabetic) > max_foreign_ratio:
        return 0.35, "unexpected_script"
    return 0.0, None


_TABLE_SEP_ROW_RE = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?$")


def _table_signals(text: str):
    results = []
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    table_rows = [l for l in lines if l.startswith("|") and l.endswith("|") and l.count("|") >= 3]
    data_rows = [r for r in table_rows if not _TABLE_SEP_ROW_RE.match(r)]

    if len(data_rows) >= 4:
        cell_counts = [r.count("|") - 1 for r in data_rows]
        modal_count, _ = Counter(cell_counts).most_common(1)[0]
        deviating = sum(1 for c in cell_counts if c != modal_count)
        if deviating / len(cell_counts) > 0.30:
            results.append((0.25, "malformed_table"))

    for row in data_rows:
        cells = [c.strip() for c in row.strip("|").split("|")]
        if len(cells) >= 3 and all(c.isdigit() for c in cells):
            nums = [int(c) for c in cells]
            if nums[0] in (0, 1) and nums == list(range(nums[0], nums[0] + len(nums))):
                results.append((0.15, "index_row_as_values"))
                break

    return results


def _format_violation_signal(text: str):
    if re.search(r"^\s*[{}]", text, re.MULTILINE):
        return 0.10, "format_violation"
    if re.search(r'\{[^{}]*"[^"]+"\s*:', text):
        return 0.10, "format_violation"
    return 0.0, None


def _image_quality_signal(image_quality: Optional[dict], blur_threshold: float, contrast_threshold: float):
    if not image_quality:
        return 0.0, []
    penalty = 0.0
    reasons = []
    blur = image_quality.get("blur_variance")
    contrast = image_quality.get("contrast")
    if blur is not None and blur < blur_threshold:
        penalty += 0.10
        reasons.append("low_image_sharpness")
    if contrast is not None and contrast < contrast_threshold:
        penalty += 0.10
        reasons.append("low_image_contrast")
    return penalty, reasons


def _token_confidence_signal(token_logprobs, min_top1_prob: float, enabled: bool):
    if not enabled or not token_logprobs:
        return 0.0, None
    probs = [math.exp(t["logprob"]) for t in token_logprobs if "logprob" in t]
    if not probs:
        return 0.0, None
    low_prob_fraction = sum(1 for p in probs if p < min_top1_prob) / len(probs)
    if low_prob_fraction > 0.15:
        return round(0.20 * min(1.0, low_prob_fraction * 2), 3), "low_token_confidence"
    return 0.0, None


def _tier_for(confidence: float, cfg: dict) -> str:
    if confidence < cfg["confidence_threshold"]:
        return "needs_review"
    if confidence < cfg["very_confident_threshold"]:
        return "confident"
    if confidence < cfg["highly_confident_threshold"]:
        return "very_confident"
    return "highly_confident"


def score_page(
    text: str,
    *,
    attempts: int = 1,
    hard_failure: bool = False,
    ink_ratio: Optional[float] = None,
    elapsed_s: Optional[float] = None,
    timeout_s: Optional[float] = None,
    primer: Optional[dict] = None,
    overlap_alt_text: Optional[str] = None,
    escalation_alt_text: Optional[str] = None,
    image_quality: Optional[dict] = None,
    token_logprobs: Optional[list] = None,
    cfg: Optional[dict] = None,
) -> PageScore:
    """Scores one page's OCR output. `primer` is the *previous* page's
    context_primer.build_primer() result, used for `boundary_continuity_mismatch`.
    `overlap_alt_text` is the other batch's transcription of a shared boundary page
    (batch mode only). `escalation_alt_text` is the other of {base pass, escalation
    attempt} — whichever one this call isn't scoring — for the free self-consistency
    check."""
    c = {**DEFAULT_CFG, **(cfg or {})}
    text = text or ""
    reasons: List[str] = []
    signals: Dict = {}

    if hard_failure or _has_error_sentinel(text):
        signals["engine_error_in_output"] = True
        return PageScore(0.0, True, False, "failed", ["engine_error_in_output"], signals)

    stripped_len = len(text.strip())

    if ink_ratio is not None and ink_ratio <= c["blank_ink_ratio"] and stripped_len < c["min_text_chars"]:
        signals["blank_page"] = True
        return PageScore(1.0, False, False, "blank_page", ["blank_page"], signals)

    confidence = 1.0

    if ink_ratio is not None and ink_ratio > c["blank_ink_ratio"] and stripped_len < c["min_text_chars"]:
        confidence -= 0.60
        reasons.append("empty_output_on_inked_page")
        signals["empty_output_on_inked_page"] = True

    rep_penalty, rep_reason = _repetition_signal(text, c["max_char_run"], c["max_line_dup_ratio"])
    if rep_penalty:
        confidence -= rep_penalty
        reasons.append(rep_reason)
        signals[rep_reason] = True

    script_penalty, script_reason = _script_signal(text, c["max_foreign_script_ratio"])
    if script_penalty:
        confidence -= script_penalty
        reasons.append(script_reason)
        signals[script_reason] = True

    for penalty, reason in _table_signals(text):
        confidence -= penalty
        reasons.append(reason)
        signals[reason] = True

    fmt_penalty, fmt_reason = _format_violation_signal(text)
    if fmt_penalty:
        confidence -= fmt_penalty
        reasons.append(fmt_reason)
        signals[fmt_reason] = True

    if elapsed_s is not None and timeout_s:
        signals["elapsed_s"] = elapsed_s
        if elapsed_s > 0.9 * timeout_s:
            confidence -= 0.10
            reasons.append("near_timeout")
            signals["near_timeout"] = True

    if attempts > 1:
        confidence -= 0.10 * (attempts - 1)
        reasons.append("retry_count")
        signals["attempts"] = attempts

    if primer and primer.get("ends_mid_table"):
        matches, match_reason = continues_table(primer.get("table_header"), None, text)
        if not matches:
            confidence -= 0.35
            reasons.append("boundary_continuity_mismatch")
            signals["boundary_continuity_mismatch"] = match_reason

    if overlap_alt_text is not None:
        sim = text_similarity(text, overlap_alt_text)
        signals["overlap_similarity"] = round(sim, 3)
        if sim < c["engine_agreement_min"]:
            confidence -= 0.25
            reasons.append("overlap_disagreement")

    if escalation_alt_text is not None:
        sim = text_similarity(text, escalation_alt_text)
        signals["escalation_self_consistency"] = round(sim, 3)
        if sim < c["engine_agreement_min"]:
            reasons.append("escalation_self_consistency_disagreement")
            signals["escalation_self_consistency_disagreement"] = True

    iq_penalty, iq_reasons = _image_quality_signal(image_quality, c["image_blur_threshold"], c["image_contrast_threshold"])
    if iq_penalty:
        confidence -= iq_penalty
        for r in iq_reasons:
            reasons.append(r)
            signals[r] = True
    if image_quality:
        signals["image_quality"] = image_quality

    tok_penalty, tok_reason = _token_confidence_signal(token_logprobs, c["token_min_top1_prob"], c["token_confidence_enabled"])
    if tok_penalty:
        confidence -= tok_penalty
        reasons.append(tok_reason)
        signals[tok_reason] = True

    confidence = max(0.0, min(1.0, confidence))
    needs_review = confidence < c["confidence_threshold"]
    escalate = confidence < c["escalation_threshold"]
    tier = _tier_for(confidence, c)

    return PageScore(confidence, needs_review, escalate, tier, reasons, signals)


def compute_image_quality(pil_image) -> dict:
    """Blur (edge variance) and contrast (intensity stddev) on the *source* rendered
    page image — the only signal category that evaluates input difficulty rather
    than the model's output. Cheap: two PIL filter passes, no new dependencies."""
    from PIL import ImageFilter, ImageStat

    gray = pil_image.convert("L")
    contrast = ImageStat.Stat(gray).stddev[0]
    edges = gray.filter(ImageFilter.FIND_EDGES)
    blur_variance = ImageStat.Stat(edges).var[0]
    return {"contrast": round(contrast, 2), "blur_variance": round(blur_variance, 2)}


def tier_for_stored_confidence(confidence: Optional[float], signals: Optional[dict] = None, cfg: Optional[dict] = None) -> Optional[str]:
    """Computes the display tier at *read* time from a stored confidence value —
    deliberately not persisted as a frozen label, so retuning thresholds later
    reclassifies existing pages' displayed tier rather than leaving it stale."""
    if confidence is None:
        return None
    signals = signals or {}
    if signals.get("blank_page"):
        return "blank_page"
    if signals.get("engine_error_in_output"):
        return "failed"
    c = {**DEFAULT_CFG, **(cfg or {})}
    return _tier_for(confidence, c)
