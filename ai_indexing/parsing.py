"""Output parsing pipeline: strip fences/chatter -> extract the JSON object
-> string-aware bracket repair for truncation -> json.loads -> json_repair
fallback -> Pydantic-validate.

Adapted from tender_ai's llm_client.py (_repair_json/_parse_json_response),
with one real bug fixed: that version's brace-matching scans every character
unconditionally, so a `{`/`[` INSIDE A STRING VALUE pushes a phantom stack
frame and the repair appends bogus closers. Our payload is verbatim document
headings ("3.4(a) {as amended}", "[Annexure B]" are entirely plausible
heading text) — a real failure mode here, not a theoretical one. This
version tracks string/escape state and ignores brackets inside strings.
"""

import json
import re
from typing import Any, Dict, List, Tuple

from ai_indexing.errors import AIInvalidOutputError
from ai_indexing.schema import AIHeading, AIMentionReview, AIPriorRejection, AIStructureChunk, NormalizationReport, normalize_chunk

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
_LEADING_CHATTER_RE = re.compile(r"^[^{\[]*(?=[{\[])", re.DOTALL)

_ENVELOPE_KEYS = ("headings", "items", "result", "data")


def strip_code_fences(text: str) -> str:
    return _FENCE_RE.sub("", text or "").strip()


def extract_json_object(text: str) -> str:
    """Finds the outermost {...} or [...] — whichever appears first — from
    the start of the (already fence-stripped) text. Raises
    AIInvalidOutputError if neither bracket exists at all."""
    text = text or ""
    if "{" not in text and "[" not in text:
        raise AIInvalidOutputError("No JSON object/array found in the response.", raw_excerpt=text)
    stripped = _LEADING_CHATTER_RE.sub("", text)
    open_char = stripped[0]
    close_char = "}" if open_char == "{" else "]"
    idx = stripped.rfind(close_char)
    if idx == -1:
        # No closing bracket at all — hand the whole thing to the repairer,
        # which will attempt to synthesize one.
        return stripped
    return stripped[: idx + 1]


def repair_truncated_json(s: str) -> str:
    """String-aware bracket-stack auto-close. Ignores brackets that occur
    inside a JSON string value (tracking escape state), closes a dangling
    open string, strips a trailing comma or partial trailing token, then
    appends the missing closers in reverse stack order."""
    stack: List[str] = []
    in_string = False
    escape = False
    for ch in s:
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if stack and stack[-1] == ch:
                stack.pop()

    result = s
    if in_string:
        result += '"'

    # Strip a dangling trailing comma (with optional whitespace) before closing.
    result = re.sub(r",\s*$", "", result)

    result += "".join(reversed(stack))
    return result


def _envelope_to_list(data: Any) -> List[Dict[str, Any]]:
    """Handles both a bare-array response and an object-wrapped one
    ({"headings": [...]}, or a provider that insists on "items"/"result"/
    "data" instead) — models frequently wrap a requested array in an
    envelope object regardless of instructions."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in _ENVELOPE_KEYS:
            val = data.get(key)
            if isinstance(val, list):
                return val
        # A single heading object with no wrapper at all.
        if "heading_text" in data or "start_page" in data:
            return [data]
    raise AIInvalidOutputError(
        f"Parsed JSON has no recognizable headings array (root type: {type(data).__name__}).",
    )


def parse_json_object(s: str) -> Dict[str, Any]:
    try:
        data = json.loads(s)
    except json.JSONDecodeError:
        try:
            import json_repair
            data = json_repair.loads(s)
        except Exception as e:
            raise AIInvalidOutputError(
                "Response is not valid JSON, even after repair.", raw_excerpt=s,
                validation_error=str(e),
            ) from e
    if not isinstance(data, (dict, list)):
        raise AIInvalidOutputError(
            f"Parsed JSON root is a {type(data).__name__}, not an object or array.", raw_excerpt=s,
        )
    return data if isinstance(data, dict) else {"headings": data}


def _validate_headings_individually(headings_list: List[Any]) -> "Tuple[List[AIHeading], int]":
    """Validates each heading dict ONE AT A TIME rather than constructing
    AIStructureChunk(headings=headings_list) in one shot — Pydantic fails
    an entire list field atomically on the first bad item, which would
    otherwise discard every other, genuinely good heading in the same
    chunk over one item's problem (confirmed via a real run). Every field
    on AIHeading is designed to coerce/truncate rather than raise (see its
    validators), so an item only lands here on a genuinely unexpected
    shape (e.g. not a dict at all) — dropped and counted, never crashing
    the batch."""
    valid: List[AIHeading] = []
    dropped = 0
    for item in headings_list:
        try:
            valid.append(AIHeading(**item) if isinstance(item, dict) else AIHeading())
        except Exception:
            dropped += 1
    return valid, dropped


def _validate_rejections_individually(rejections_list: List[Any]) -> "Tuple[List[AIPriorRejection], int]":
    valid: List[AIPriorRejection] = []
    dropped = 0
    for item in rejections_list:
        try:
            if isinstance(item, dict):
                valid.append(AIPriorRejection(**item))
            elif isinstance(item, str):
                valid.append(AIPriorRejection(prior_id=item))
            else:
                dropped += 1
        except Exception:
            dropped += 1
    return valid, dropped


def parse_structure_response(
    raw: str, expected_pages: List[int],
) -> Tuple[AIStructureChunk, NormalizationReport]:
    fenced = strip_code_fences(raw)
    extracted = extract_json_object(fenced)
    repaired = repair_truncated_json(extracted)
    envelope = parse_json_object(repaired)
    headings_list = _envelope_to_list(envelope)
    if not isinstance(headings_list, list):
        raise AIInvalidOutputError(
            f"'headings' is a {type(headings_list).__name__}, not a list.", raw_excerpt=raw,
        )

    valid_headings, dropped_invalid = _validate_headings_individually(headings_list)

    rejections_raw = envelope.get("rejected_priors") or []
    if not isinstance(rejections_raw, list):
        rejections_raw = []
    valid_rejections, dropped_rejections = _validate_rejections_individually(rejections_raw)

    confirmed_raw = envelope.get("confirmed_prior_ids") or []
    if not isinstance(confirmed_raw, list):
        confirmed_raw = []

    chunk = AIStructureChunk(
        headings=valid_headings,
        notes=str(envelope.get("notes", ""))[:500],
        confirmed_prior_ids=confirmed_raw,
        rejected_priors=valid_rejections,
    )
    cleaned, report = normalize_chunk(chunk, expected_pages)
    report.dropped_invalid = dropped_invalid + dropped_rejections
    return cleaned, report


def parse_mention_response(raw: str) -> List[AIMentionReview]:
    """Parses model response for mention review into a list of AIMentionReview objects."""
    fenced = strip_code_fences(raw)
    extracted = extract_json_object(fenced)
    repaired = repair_truncated_json(extracted)
    try:
        envelope = parse_json_object(repaired)
    except Exception:
        return []

    reviews_raw = envelope.get("reviews") if isinstance(envelope, dict) else envelope
    if not isinstance(reviews_raw, list):
        return []

    valid: List[AIMentionReview] = []
    for item in reviews_raw:
        if isinstance(item, dict):
            try:
                valid.append(AIMentionReview(**item))
            except Exception:
                pass
    return valid

