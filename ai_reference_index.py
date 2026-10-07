"""AI-based document-structure indexing, stitched into the existing
deterministic reference_index.py pipeline. Owns everything that reaches
into reference_index.py/database.py for the AI path: heading->block
grounding, parent_ref resolution, and the chunked dispatch loop with
per-chunk fallback to the rule-based detector.

Core design decisions (see the approved plan for the full reasoning):
- The AI replaces detection only, never the scope-stack machine — both
  paths share reference_index.apply_heading_events_to_scope /
  build_targets_for_page's identical (candidates, new_scope) contract, so
  compute_scope_ranges/scopeRanges.js/StructureTree.jsx need zero changes.
- Cross-chunk carry-forward is one variable (`seed_scope`), reassigned at
  the end of every PAGE (not chunk) iteration, regardless of which
  mechanism produced it — this is the entire cross-chunk-boundary
  correctness story; see reference_index.py's own module docstring context.
- Fallback is per-chunk, persistence is per-page: one failed chunk only
  affects that chunk's pages.
- A single retried page re-runs from that page forward with an
  early-stop-on-reconvergence check, never an unconditional walk to the end
  of the document.
"""

import asyncio
import difflib
import json
import re
from typing import Any, Dict, List, Optional, Set, Tuple

import ai_indexing
import config
import database
import reference_index
import task_queue

_FUZZY_PARENT_CUTOFF = 0.85

# A decimal-numbered heading's own leading number ("3.3" from "3.3 TIME
# SCHEDULE") — used only to REJECT a same-shaped-but-differently-numbered
# candidate block during windowed grounding (see _locate_heading_in_chunk);
# never to decide a heading's real family/key (mint_key_for_heading owns
# that). Deliberately scoped to decimal-numbered headings only — the
# confirmed false-positive ("3.3 TIME SCHEDULE"/"3.4 QUALITY PLANS" landing
# on unrelated pages) is exactly this shape; a keyword+number heading like
# "Annexure A-1" is instead covered by alt_text (family_hint/number_hint).
_LEADING_DECIMAL_RE = re.compile(r"^\s*(\d+(?:\.\d+)*)\b")


def _leading_reference_number(text: str) -> Optional[str]:
    m = _LEADING_DECIMAL_RE.match((text or "").strip())
    if not m:
        return None
    segs = m.group(1).split(".")
    while len(segs) > 1 and segs[-1] == "0":
        segs.pop()
    return ".".join(segs)


def _norm_for_match(text: str) -> str:
    return re.sub(r"[\s\W]+", " ", (text or "").casefold()).strip()


# Below this length, a prefix match is too likely to be a coincidence
# between two genuinely different short headings ("(I)" vs "(II)...") to
# safely treat as a duplicate — see _dedupe_same_page_headings.
_DUPLICATE_HEADING_MIN_PREFIX_LEN = 15


def _dedupe_same_page_headings(headings: List[Any]) -> List[Any]:
    """Drops a heading whose (start_page, normalized heading_text) is an
    exact repeat of — or a truncated echo of — an earlier heading already
    kept for that same page. Confirmed real case: the model reported the
    same real heading twice in one response, the two entries differing only
    in trailing truncation ("(XIII) That the Ld. Arbitral Tribunal has
    erroneously held that the appellant was responsible for a delay of 68
    days on account of approval of Equipment drawings. The computation..."
    vs the same text cut a little shorter) — without this, both survive as
    two separate "occurrences" of the same heading on one page. Keeps the
    FIRST occurrence; a length floor avoids two genuinely different short
    headings (e.g. two different roman numerals) coincidentally prefix-
    matching."""
    seen_by_page: Dict[int, List[str]] = {}
    result = []
    for h in headings:
        norm = _norm_for_match(h.heading_text)
        page_seen = seen_by_page.setdefault(h.start_page, [])
        is_duplicate = any(
            norm == s or (
                len(min(norm, s, key=len)) >= _DUPLICATE_HEADING_MIN_PREFIX_LEN
                and (norm.startswith(s) or s.startswith(norm))
            )
            for s in page_seen
        )
        if is_duplicate:
            continue
        page_seen.append(norm)
        result.append(h)
    return result


_PAGE_NUMBER_ORNAMENT_RE = re.compile(
    r"^\s*[-–—:]+\s*\d{1,4}\s*[-–—:]*\s*$|^\s*[-–—:]*\s*\d{1,4}\s*[-–—:]+\s*$"
)


def locate_heading_block(
    blocks: List[str], heading_text: str, used_block_idxs: set,
) -> Optional[Tuple[int, str, float]]:
    """Locates an AI-reported (already-sanitized) heading against ONE
    page's real parsed blocks — exact -> prefix -> difflib ratio >= cutoff
    — scanning forward from the last consumed block so document order is
    preserved and one block is never claimed twice. Returns
    (block_idx, match_kind, ratio), match_kind one of "exact"/"prefix"/
    "fuzzy" — or None if nothing clears the fuzzy cutoff. Never mutates
    `used_block_idxs` itself — the caller commits a winning match."""
    target = _norm_for_match(heading_text)
    if not target:
        return None
    start = (max(used_block_idxs) + 1) if used_block_idxs else 0
    best_idx, best_ratio = None, 0.0
    best_kind = None
    indices = [i for i in range(start, len(blocks)) if i not in used_block_idxs] + [
        i for i in range(0, start) if i not in used_block_idxs
    ]
    for idx in indices:
        raw_blk = (blocks[idx] or "").strip()
        # Page header/footer page numbers (e.g. "-:12:-", "-9:-", "-- 12 --")
        # are printer/court marginalia, never document headings.
        if _PAGE_NUMBER_ORNAMENT_RE.match(raw_blk):
            continue
        # Bare 1-4 digit stamped numbers at the very top or bottom of a page
        if (
            re.fullmatch(r"\d{1,4}", raw_blk)
            and idx in (0, 1, len(blocks) - 1, len(blocks) - 2)
            and not re.fullmatch(r"\d{1,4}", heading_text.strip())
        ):
            continue

        candidate = _norm_for_match(blocks[idx])
        if not candidate:
            continue
        if candidate == target:
            return idx, "exact", 1.0
        if candidate.startswith(target) or target.startswith(candidate):
            if best_idx is None or best_kind != "exact":
                best_idx, best_ratio, best_kind = idx, 1.0, "prefix"
                if idx >= start:
                    return idx, "prefix", 1.0
        ratio = difflib.SequenceMatcher(None, candidate, target).ratio()
        if ratio > best_ratio:
            best_idx, best_ratio, best_kind = idx, ratio, "fuzzy"
    if best_idx is not None:
        if best_kind in ("exact", "prefix"):
            return best_idx, best_kind, 1.0
        if best_ratio >= _FUZZY_PARENT_CUTOFF:
            return best_idx, "fuzzy", best_ratio
    return None


_MATCH_KIND_RANK = {"exact": 3, "prefix": 2, "fuzzy": 1}



def _locate_heading_in_chunk(
    page_blocks: Dict[int, List[str]], claimed_page: int, heading_text: str,
    used_block_idxs: Dict[int, set], alt_text: Optional[str] = None,
) -> Optional[Tuple[int, int, str, float]]:
    """Locates an AI-reported heading across a small window of pages
    centered on the page the AI claimed it's on, not just that one page —
    the model can and does misreport which page a heading is on (confirmed:
    "3.3 TIME SCHEDULE"/"3.4 QUALITY PLANS", really on page 65, got anchored
    to pages 5/9/69 because the original single-page search had nothing to
    cross-check the claimed page against). Returns
    (found_page, block_idx, match_kind, ratio), or None if nothing in the
    window clears the cutoff.

    `alt_text`, when given (a canonical form built from the AI's own
    family_hint/number_hint, e.g. "Annexure A-1" for a garbled
    heading_text="AmexureAl"), is tried against every candidate block
    alongside `heading_text` itself, keeping whichever match is better —
    covers the model "correcting" garbled OCR text instead of copying it
    verbatim.

    Ranked by match kind first (exact > prefix > fuzzy), then ratio, then
    proximity to the claimed page as a final tiebreak — an exact/prefix
    match on a neighboring page is stronger evidence than a merely-fuzzy
    match on the claimed page, which is exactly the coincidental-match
    failure mode being fixed."""
    own_number = _leading_reference_number(heading_text)
    candidate_pages = [claimed_page]
    for delta in range(1, config.AI_INDEX_GROUND_PAGE_WINDOW + 1):
        candidate_pages.append(claimed_page - delta)
        candidate_pages.append(claimed_page + delta)

    best: Optional[Tuple[int, int, str, float]] = None
    best_score: Optional[Tuple[int, float, int]] = None
    for page in candidate_pages:
        blocks = page_blocks.get(page)
        if blocks is None:
            continue
        used = used_block_idxs.setdefault(page, set())
        for text_variant in (heading_text, alt_text):
            if not text_variant:
                continue
            result = locate_heading_block(blocks, text_variant, used)
            if result is None:
                continue
            idx, kind, ratio = result
            if own_number is not None:
                candidate_number = _leading_reference_number(blocks[idx])
                if candidate_number is not None and candidate_number != own_number:
                    continue
            is_claimed = 1 if page == claimed_page else 0
            has_direct_claimed_match = 1 if (is_claimed and kind in ("exact", "prefix")) else 0
            score = (has_direct_claimed_match, _MATCH_KIND_RANK[kind], ratio, is_claimed)
            if best_score is None or score > best_score:
                best, best_score = (page, idx, kind, ratio), score

    return best


def _carry_in_summary(seed_scope: Optional[Dict[str, Any]], last_page_text: str = "") -> "ai_indexing.CarryContext":
    stack = (seed_scope or {}).get("stack") or []
    containers = [
        {"ref_id": f"c{depth + 1}", "level": depth + 1, "label": entry.get("label", ""),
         "source": entry.get("source", "rule")}
        for depth, entry in enumerate(stack)
    ]
    return ai_indexing.CarryContext(open_containers=containers, last_page_tail=last_page_text)


def _resolve_depth_for_heading(
    parent_ref: Optional[str], level: int,
    carry_ref_map: Dict[str, int],           # ref_id -> depth of that container
    chunk_heading_depths: Dict[str, int],    # normalized heading_text (earlier in THIS chunk) -> its own depth
    current_stack_len: int,
    carry_label_map: Optional[Dict[str, int]] = None,  # normalized carried-in label -> depth
    is_subdoc_root: bool = False,
) -> Tuple[int, Optional[str]]:
    """Returns (depth_to_push_at, warning_reason_or_None). `depth` is this
    heading's own resulting depth once pushed (== number of ancestors kept
    on the stack before it). Resolution never looks at family/rank — see
    reference_index.mint_key_for_heading's docstring for why that matters.

    `carry_label_map` (normalized label -> depth, built from the SAME
    `carry_context.open_containers` `carry_ref_map` comes from) is the
    cross-chunk analogue of `chunk_heading_depths`'s same-chunk fuzzy
    fallback — without it, a `parent_ref` naming a container opened in an
    EARLIER chunk (not an exact ref_id match, and not in this chunk's own
    `chunk_heading_depths`) fell straight to `ai_unresolved_parent_ref`
    with no fuzzy attempt at all. Confirmed real bug: a chunk where the AI
    correctly flagged 12 roman-numeral grounds as headings, all with
    `parent_ref` naming "Grounds of Appeal" (opened in an earlier chunk) —
    every single one hit this exact gap and fell back to an unparented
    top-level section, even though the AI's own judgment was right."""
    if parent_ref:
        if parent_ref in carry_ref_map:
            return carry_ref_map[parent_ref] + 1, None

        norm_ref = _norm_for_match(parent_ref)
        best_key, best_ratio = None, 0.0
        for heading_key, depth in chunk_heading_depths.items():
            if heading_key == norm_ref:
                return depth + 1, None
            ratio = difflib.SequenceMatcher(None, heading_key, norm_ref).ratio()
            if ratio > best_ratio:
                best_key, best_ratio = heading_key, ratio
        if best_key is not None and best_ratio >= _FUZZY_PARENT_CUTOFF:
            return chunk_heading_depths[best_key] + 1, "ai_fuzzy_parent_ref"

        best_carry_key, best_carry_ratio = None, 0.0
        for label_key, depth in (carry_label_map or {}).items():
            if label_key == norm_ref:
                return depth + 1, None
            ratio = difflib.SequenceMatcher(None, label_key, norm_ref).ratio()
            if ratio > best_carry_ratio:
                best_carry_key, best_carry_ratio = label_key, ratio
        if best_carry_key is not None and best_carry_ratio >= _FUZZY_PARENT_CUTOFF:
            return carry_label_map[best_carry_key] + 1, "ai_fuzzy_carry_parent_ref"

        depth = max(0, min(level - 1, current_stack_len))
        return depth, "ai_unresolved_parent_ref"

    depth = max(0, min(level - 1, current_stack_len))
    return depth, None


_SENTENCE_END_CHARS = reference_index._SENTENCE_END_CHARS

# Temporary diagnostic only — env-gated, never on by default, to be removed
# once the raw-parent_ref/level question this session is chasing is answered.
import os as _os
_TEMP_DEBUG_RAW_HEADINGS = bool(_os.environ.get("AI_INDEX_DEBUG_RAW_HEADINGS"))

_PARENT_CHOICE_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_PARENT_CHOICE_MAX_CANDIDATES = len(_PARENT_CHOICE_LETTERS) - 1


def _build_parent_choice_prompt(
    heading_text: str, candidate_labels: List[str], parent_ref_hint: Optional[str] = None,
) -> Tuple[str, str]:
    """Builds a small, bounded multiple-choice prompt asking a model to
    place ONE heading under ONE of a handful of currently-open ancestors —
    never the whole document's structure. This is deliberately the opposite
    shape from a single big consolidation pass over the whole outline: the
    real deployment target (a small local model) is far more reliable at a
    bounded multiple-choice answer than at holding or re-emitting a long
    structure. See the approved plan for the full reasoning.

    Options are listed INNERMOST first, "top-level" LAST — confirmed real
    bug on live verification: with "top-level" listed first (letter A), the
    model answered the identical shape of decision inconsistently across
    calls, landing on "top-level" (the single worst, hardest-to-undo
    choice — it evicts everything currently open) noticeably more often
    than the correct nested answer, consistent with the well-documented
    first-option position bias in LLM multiple-choice answers. Listing the
    safe, usually-correct default first and the riskiest option last means
    that same bias now favors the safe answer instead of the destructive
    one."""
    system_prompt = (
        "You are placing ONE heading into an existing document outline. "
        "You are told the chain of sections currently open, from outermost to innermost.\n"
        "CRITICAL RULES:\n"
        "- Opposing party submissions (e.g. 'Respondent\\'s case', 'Submissions of petitioner', 'Submissions of respondent') "
        "are top-level sibling divisions of the lawsuit/judgment. They belong directly inside the outer document ('JUDGMENT'), "
        "NEVER inside the other party's section (like 'Challenge to the Arbitral Award').\n"
        "- If an 'Extracted parent reference' is provided, that indicates the parent extracted from the full text. "
        "Choose that parent's option.\n"
        "- If the heading is a sub-clause, detail, or part of the currently open inner section, choose that inner section.\n"
        "Reply with ONLY the single letter of your answer, nothing else — no punctuation, no explanation."
    )
    lines = [f"Heading to place: {heading_text!r}"]
    if parent_ref_hint:
        lines.append(f"Extracted parent reference: {parent_ref_hint!r}")
    lines.extend(["", "Currently open (outermost first):"])
    for i, label in enumerate(candidate_labels):
        lines.append(f"  {i + 1}. {label!r}")
    lines.append("")
    lines.append("Where does the heading above belong? Options, innermost first:")
    n = len(candidate_labels)
    for i in range(n):
        item_num = n - i  # innermost (highest item number) first
        lines.append(f"{_PARENT_CHOICE_LETTERS[i]}) Directly inside {candidate_labels[item_num - 1]!r} (item {item_num} above)")
    lines.append(f"{_PARENT_CHOICE_LETTERS[n]}) Top-level — not inside any of the sections above")
    return system_prompt, "\n".join(lines)


def _parse_parent_choice_answer(text: str, num_candidates: int) -> Optional[int]:
    """Parses a single-letter answer back to a depth. Options are innermost
    first: 'A' -> depth num_candidates (directly inside the innermost open
    ancestor), ..., the LAST letter -> depth 0 (top-level). Returns None on
    anything that doesn't cleanly resolve to one of the offered options —
    callers must treat that as "fall back," never as an error."""
    raw = (text or "").strip()
    if not raw:
        return None
    valid_letters = _PARENT_CHOICE_LETTERS[: num_candidates + 1]

    # 1. Try structured JSON: {"answer": "B"}
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            val = data.get("answer") or data.get("choice") or data.get("option")
            if isinstance(val, str) and len(val.strip()) == 1 and val.strip().isalpha():
                idx = _PARENT_CHOICE_LETTERS.find(val.strip().upper())
                if 0 <= idx <= num_candidates:
                    return 0 if idx == num_candidates else (num_candidates - idx)
    except Exception:
        pass

    # 2. Try explicit patterns like '"answer": "B"' or 'Answer: B' or 'Option B'
    m = re.search(r'(?:answer|choice|option)\s*(?:is|\:|=)?\s*[\'"\(\[\{]*([A-Za-z])[\'"\)\]\}]*', raw, re.IGNORECASE)
    if m:
        letter = m.group(1).upper()
        if letter in valid_letters:
            idx = _PARENT_CHOICE_LETTERS.find(letter)
            return 0 if idx == num_candidates else (num_candidates - idx)

    # 3. Look for a standalone letter (e.g. "B", "(B)", "**B**", "B.")
    m = re.match(r'^[\s\*\#\(\[\"\']*([A-Za-z])[\s\*\#\)\]\"\'.]*$', raw)
    if m:
        letter = m.group(1).upper()
        if letter in valid_letters:
            idx = _PARENT_CHOICE_LETTERS.find(letter)
            return 0 if idx == num_candidates else (num_candidates - idx)

    # 4. Fallback: search for any standalone valid letter token
    tokens = re.findall(r'\b([A-Za-z])\b', raw)
    for t in tokens:
        u = t.upper()
        if u in valid_letters:
            idx = _PARENT_CHOICE_LETTERS.find(u)
            return 0 if idx == num_candidates else (num_candidates - idx)

    return None


async def _resolve_parent_via_ai(
    heading_text: str, candidate_labels: List[str], provider_config: "ai_indexing.ProviderConfig",
    parent_ref_hint: Optional[str] = None,
) -> Optional[int]:
    """Asks the model a small, bounded multiple-choice question in place of
    _resolve_depth_for_heading's fuzzy-string/positional fallback. Returns
    the resolved depth, or None on any failure/unparseable answer — this is
    a pure safety-netted addition: a None result must always fall back to
    the existing mechanical resolution, never surface as an error."""
    if not candidate_labels or len(candidate_labels) > _PARENT_CHOICE_MAX_CANDIDATES:
        return None
    system_prompt, user_prompt = _build_parent_choice_prompt(
        heading_text, candidate_labels, parent_ref_hint=parent_ref_hint,
    )
    try:
        provider = await ai_indexing.get_provider(provider_config)
        completion = await provider.complete(system_prompt=system_prompt, user_prompt=user_prompt)
    except ai_indexing.AIIndexError:
        return None
    return _parse_parent_choice_answer(completion.text, len(candidate_labels))


async def _ground_and_resolve_chunk(
    headings: List[Any],  # List[ai_indexing.AIHeading], already normalized + sorted by start_page
    page_blocks: Dict[int, List[str]],
    carry_context: "ai_indexing.CarryContext",
    detection_model: str,
    has_next_page_fn,
    resolve_parent_via_ai: bool = False,
    provider_config: Optional["ai_indexing.ProviderConfig"] = None,
) -> "Tuple[Dict[int, List[Dict[str, Any]]], Dict[str, int]]":
    """Resolves parent_ref -> depth and grounds each heading against its
    page's real blocks, walking headings in (page, order) sequence while
    tracking a simulated stack depth so within-chunk parent references
    resolve against the same state apply_heading_events_to_scope will later
    reconstruct for real. Returns ({page_num: [event, ...]}, warning_counts)."""
    headings = _dedupe_same_page_headings(headings)

    # Parent container end_page extension: a container cannot close before its children.
    heading_lookup = {_norm_for_match(h.heading_text): h for h in headings}
    last_page_in_chunk = max(page_blocks.keys()) if page_blocks else 0
    for h in headings:
        if h.parent_ref:
            parent_norm = _norm_for_match(h.parent_ref)
            parent_h = heading_lookup.get(parent_norm)
            if parent_h is not None and parent_h.end_page is not None:
                child_max = max(h.start_page, h.end_page or h.start_page)
                if child_max > parent_h.end_page:
                    parent_h.end_page = child_max
                    if child_max >= last_page_in_chunk:
                        parent_h.still_open_at_chunk_end = True
                        parent_h.end_page = None

    carry_ref_map = {c["ref_id"]: c["level"] - 1 for c in carry_context.open_containers}
    # Cross-chunk fuzzy fallback for _resolve_depth_for_heading — a
    # parent_ref naming a carried-in container by its LABEL (paraphrased,
    # not the exact opaque ref_id) instead of failing straight to
    # ai_unresolved_parent_ref.
    carry_label_map = {
        _norm_for_match(c["label"]): c["level"] - 1 for c in carry_context.open_containers if c.get("label")
    }
    sim_stack_len = len(carry_context.open_containers)
    # Parallel to sim_stack_len — the actual ANCESTOR LABELS currently open,
    # kept in sync the same way (truncate to this heading's own resolved
    # depth, then append its own text) — feeds _resolve_parent_via_ai's
    # bounded candidate list. Only ever read when resolve_parent_via_ai is
    # on; otherwise it's dead weight with no effect on resolution.
    sim_stack_labels: List[str] = [c.get("label") or c.get("ref_id", "") for c in carry_context.open_containers]
    chunk_heading_depths: Dict[str, int] = {}
    used_block_idxs: Dict[int, set] = {}
    # First pass: resolve depth + locate a block index per heading.
    located: List[Dict[str, Any]] = []
    warning_counts: Dict[str, int] = {}

    for h in headings:
        is_subdoc_root = reference_index.is_subdoc_root_candidate(h.heading_text, h.family_hint)
        depth, warn = _resolve_depth_for_heading(
            h.parent_ref, h.level, carry_ref_map, chunk_heading_depths, sim_stack_len,
            carry_label_map=carry_label_map,
            is_subdoc_root=is_subdoc_root,
        )
        if _TEMP_DEBUG_RAW_HEADINGS:
            print(f"[RAW HEADING] text={h.heading_text[:50]!r} parent_ref={h.parent_ref!r} "
                  f"level={h.level} sim_stack_len_before={sim_stack_len} "
                  f"sim_stack_labels_before={sim_stack_labels[-3:]!r} -> mechanical_depth={depth} warn={warn}")
        # Widened from "only when the mechanical resolver itself flagged
        # uncertainty" after live verification: the confirmed real bug (a
        # heading closing out a real ancestor it shouldn't have) happened
        # via a bare level-only guess with NO parent_ref at all — a path
        # that always returns warn=None, so the original narrower condition
        # never caught it. The actually risky operation is EVICTING a
        # currently-open ancestor (depth < sim_stack_len, before this
        # heading) — that's irreversible for everything nested under what
        # gets closed, whereas nesting deeper or staying level is always
        # safe. The one exception: an exact match against carry_ref_map is
        # the model copying back an opaque id the resolver itself minted —
        # the most reliable signal available, never worth second-guessing.
        # Likewise, an explicit parent_ref that matched cleanly without
        # warning is trusted directly and never overridden.
        is_trusted_ref_id_match = bool(h.parent_ref) and h.parent_ref in carry_ref_map
        # If the heading had an explicit parent_ref that matched cleanly without warning:
        # Closing a single immediate sibling (sim_stack_len - depth <= 1) is standard
        # sibling progression (e.g. Section 2 following Section 1 under the same parent).
        # Only closing MULTIPLE ancestors (sim_stack_len - depth > 1) is a deep eviction
        # that warrants double-checking.
        # But if there was NO parent_ref or resolution was uncertain (warn is not None),
        # ANY eviction (depth < sim_stack_len) warrants AI resolution.
        would_evict = depth < sim_stack_len

        if (
            resolve_parent_via_ai
            and would_evict
            and not is_trusted_ref_id_match
            and provider_config is not None
        ):
            ai_depth = await _resolve_parent_via_ai(
                h.heading_text, list(sim_stack_labels), provider_config, parent_ref_hint=h.parent_ref,
            )
            if _TEMP_DEBUG_RAW_HEADINGS:
                print(f"  [AI MICRO-DECISION] candidates={sim_stack_labels!r} -> ai_depth={ai_depth}")
            if ai_depth is not None:
                if sim_stack_len >= 1 and not is_subdoc_root and ai_depth == 0:
                    ai_depth = 1
                depth, warn = ai_depth, "ai_micro_resolved_parent_ref"

        if _TEMP_DEBUG_RAW_HEADINGS:
            print(f"  [FINAL] text={h.heading_text[:50]!r} -> final_depth={depth} final_warn={warn}")

        # Root protection: never evict an active root container unless incoming is a genuine subdoc root
        if sim_stack_len >= 1 and not is_subdoc_root and depth == 0:
            depth = 1

        # Stack simulation parity: only push to sim_stack_labels if this heading
        # is a genuine container (not a leaf like point, ground, para, clause).
        # Leaf headings do not push onto nested_stack in apply_heading_events_to_scope,
        # so skipping them here keeps sim_stack_labels in exact lockstep with reality.
        # Long multi-word lines (>9 words) starting with numbers are body sentences/points,
        # not outline section containers (matching reference_index's word cap).
        words = h.heading_text.strip().split()
        is_point_leaf = bool(
            len(words) > 9 and _leading_reference_number(h.heading_text) is not None
        )
        minted_family, _, _, _, _ = reference_index.mint_key_for_heading(
            h.heading_text, boundary_ordinal=0, label_occurrence_counts={},
            family_hint=h.family_hint, number_hint=h.number_hint,
        )
        family_def = reference_index.FAMILIES.get(minted_family)
        is_leaf = (family_def is not None and family_def.get("rank") is None) or is_point_leaf
        if not is_leaf:
            sim_stack_len = depth + 1
            sim_stack_labels = sim_stack_labels[:depth] + [h.heading_text]
        chunk_heading_depths[_norm_for_match(h.heading_text)] = depth
        if warn:
            warning_counts[warn] = warning_counts.get(warn, 0) + 1

        # alt_text: the AI's own best-guess resolution of a garbled heading
        # (e.g. family_hint="annexure", number_hint="A-1" for
        # heading_text="AmexureAl") — tried alongside the verbatim text so
        # a model that "corrects" garbled OCR instead of copying it
        # verbatim can still be grounded.
        alt_text = None
        hinted = reference_index._resolve_ai_hint(h.family_hint, h.number_hint)
        if hinted:
            alt_family, alt_prefix, alt_number_raw = hinted
            alt_key = reference_index.normalize_key(alt_family, alt_prefix, alt_number_raw)
            alt_text = reference_index._format_label_from_key(alt_key)

        result = _locate_heading_in_chunk(
            page_blocks, h.start_page, h.heading_text, used_block_idxs, alt_text=alt_text,
        )
        if result is not None:
            found_page, block_idx, match_kind, _ratio = result
            used_block_idxs.setdefault(found_page, set()).add(block_idx)
            if match_kind == "fuzzy":
                ground_reason = "ai_fuzzy_grounded"
            elif found_page != h.start_page:
                ground_reason = "ai_page_corrected"
            else:
                ground_reason = None
            if ground_reason:
                warning_counts[ground_reason] = warning_counts.get(ground_reason, 0) + 1
        else:
            found_page, block_idx = h.start_page, None
            ground_reason = "ai_unlocated"
            warning_counts["ai_unlocated"] = warning_counts.get("ai_unlocated", 0) + 1

        located.append({
            "heading": h, "depth": depth, "warn": warn,
            "found_page": found_page, "block_idx": block_idx, "ground_reason": ground_reason,
        })

    # Second pass, per FOUND page (may differ from the AI's claimed
    # start_page — that's the actual mis-anchoring correction): sort by
    # block_idx (unlocated last) so snippet boundaries are computed against
    # the next LOCATED heading on that same page.
    events_by_page: Dict[int, List[Dict[str, Any]]] = {}
    by_page: Dict[int, List[Dict[str, Any]]] = {}
    for item in located:
        by_page.setdefault(item["found_page"], []).append(item)

    for page_num, items in by_page.items():
        blocks = page_blocks.get(page_num, [])
        items_sorted = sorted(
            items,
            key=lambda i: (i["block_idx"] is None, i["block_idx"] if i["block_idx"] is not None else 0),
        )
        located_items = [i for i in items_sorted if i["block_idx"] is not None]
        for pos, item in enumerate(located_items):
            idx = item["block_idx"]
            next_idx = located_items[pos + 1]["block_idx"] if pos + 1 < len(located_items) else len(blocks)
            item["snippet_blocks"] = blocks[idx:next_idx]

        page_events = []
        # A quoted clause reproduced verbatim mid-argument often spans more
        # than one numbered line ("9.0 Local/Site conditions" immediately
        # followed by its own "9.1 ..." sub-clause) — only the first line is
        # ever immediately preceded by the "which reads as under:" intro
        # itself. Tracks the leading top-level segment of the most recent
        # possible_reproduced_text match (page-local only) so the very next
        # heading sharing that same segment inherits the flag too, instead
        # of being treated as genuine and pushed as a real ancestor.
        last_reproduced_number = None
        for item in items_sorted:  # preserve physical block order on page
            h = item["heading"]
            reasons = [item["warn"]] if item["warn"] else []
            if item["ground_reason"]:
                reasons.append(item["ground_reason"])
            if getattr(h, "heading_confidence_reasons", None):
                for r in h.heading_confidence_reasons:
                    if r not in reasons:
                        reasons.append(r)
            is_reproduced = bool(
                item["block_idx"] and reference_index._preceded_by_reproduced_text_intro(blocks[item["block_idx"] - 1])
            )
            if not is_reproduced and last_reproduced_number is not None:
                this_number = _leading_reference_number(h.heading_text)
                is_reproduced = bool(this_number) and this_number.split(".")[0] == last_reproduced_number.split(".")[0]
            if is_reproduced:
                reasons.append("possible_reproduced_text")
                last_reproduced_number = _leading_reference_number(h.heading_text) or last_reproduced_number
            else:
                last_reproduced_number = None
            if item["block_idx"] is not None:
                snippet_blocks = item.get("snippet_blocks") or [blocks[item["block_idx"]]]
                snippet = "\n\n".join(snippet_blocks)
                fingerprint = blocks[item["block_idx"]][:30]
                confidence = "high" if not reasons else "low"
                if getattr(h, "heading_confidence", None) == "low":
                    confidence = "low"
                trimmed = snippet.rstrip()
                truncated = bool(trimmed) and trimmed[-1] not in _SENTENCE_END_CHARS
            else:
                snippet = h.heading_text
                fingerprint = ""
                confidence = "low"
                truncated = False

            page_events.append({
                "heading_text": h.heading_text,
                "depth": item["depth"],
                "end_page": h.end_page if not h.still_open_at_chunk_end else None,
                "fingerprint": fingerprint,
                "snippet": snippet,
                "truncated": truncated and has_next_page_fn(page_num),
                "heading_confidence": confidence,
                "heading_confidence_reasons": reasons,
                "detection_model": detection_model,
                "start_line_hint": h.start_line_hint,
                "family_hint": h.family_hint,
                "number_hint": h.number_hint,
                "prior_id": getattr(h, "prior_id", None),
            })
        events_by_page[page_num] = page_events

    return events_by_page, warning_counts


def _rule_prior_for_chunk(
    chunk: ai_indexing.ChunkPlan,
    seed_scope: Optional[Dict[str, Any]],
    total_pages: int,
    rejected_so_far: Set[Tuple[int, str]],
    raw_text_by_page: Dict[int, str],
) -> List[Dict[str, Any]]:
    """Walks chunk.pages calling build_targets_for_page, chaining local_seed.
    Omits any heading previously rejected in rejected_so_far (page, _norm_for_match(text)).
    Assigns chunk-local prior_id R1, R2, ...
    """
    local_seed = seed_scope
    raw_cands: List[Tuple[int, Dict[str, Any]]] = []
    ending_stack = []

    for p in chunk.pages:
        cands, new_scope = reference_index.build_targets_for_page(
            p.text, local_seed, has_next_page=p.page_num < total_pages,
            raw_text=raw_text_by_page.get(p.page_num),
        )
        local_seed = new_scope
        ending_stack = new_scope.get("stack") or []
        for c in cands:
            label = c.get("own_label") or c.get("snippet", "")
            norm_txt = _norm_for_match(label)
            if (p.page_num, norm_txt) in rejected_so_far:
                continue
            raw_cands.append((p.page_num, c))

    container_keys = {entry.get("key") for entry in ending_stack if entry.get("key")}
    for _, c in raw_cands:
        for s in c.get("scope_chain") or []:
            if s.get("key"):
                container_keys.add(s["key"])

    priors: List[Dict[str, Any]] = []
    for idx, (p_num, c) in enumerate(raw_cands):
        key = c.get("key", "")
        family = key.split(":")[0] if ":" in key else key
        family_def = reference_index.FAMILIES.get(family, {})
        rank = family_def.get("rank")
        scope_chain = c.get("scope_chain") or []
        parent_label = scope_chain[-1]["label"] if scope_chain else None
        depth = len(scope_chain) + 1
        heading_text = c.get("own_label") or c.get("snippet", "")
        is_container = key in container_keys

        priors.append({
            "prior_id": f"R{idx + 1}",
            "page_num": p_num,
            "depth": depth,
            "key": key,
            "parent_label": parent_label,
            "heading_text": heading_text,
            "is_container": is_container,
            "rank": rank,
            "raw_candidate": c,
        })

    return priors


def _merge_prior_into_headings(
    result: ai_indexing.ChunkStructureResult,
    priors: List[Dict[str, Any]],
    chunk_page_texts: Dict[int, str],
    page_blocks: Dict[int, List[str]],
    rejected_so_far: Set[Tuple[int, str]],
    warning_counts: Dict[str, int],
) -> Tuple[List[ai_indexing.schema.AIHeading], Set[str]]:
    """Merges rule priors and AI-reviewed output before grounding.
    Returns (merged_headings, verified_rejection_ids).
    """
    priors_by_id = {r["prior_id"]: r for r in priors}
    priors_by_norm = {(r["page_num"], _norm_for_match(r["heading_text"])): r for r in priors}

    # 1. Match AI headings to prior rows
    matched_prior_ids = set()
    for h in result.headings:
        if h.prior_id and h.prior_id in priors_by_id:
            matched_prior_ids.add(h.prior_id)
        norm = _norm_for_match(h.heading_text)
        if (h.start_page, norm) in priors_by_norm:
            matched_prior_ids.add(priors_by_norm[(h.start_page, norm)]["prior_id"])

    # 2. Check rejections
    verified_rejection_ids: Set[str] = set()
    unverified_rejection_ids: Set[str] = set()

    for rej in result.rejected_priors:
        if rej.prior_id not in priors_by_id:
            warning_counts["rule_prior_unknown_id"] = warning_counts.get("rule_prior_unknown_id", 0) + 1
            continue
        if rej.prior_id in matched_prior_ids:
            # Contradiction precedence: both correction and rejection -> correction
            continue

        r = priors_by_id[rej.prior_id]
        p_text = chunk_page_texts.get(r["page_num"], "")
        norm_quote = _norm_for_match(rej.evidence_quote or "")
        norm_page = _norm_for_match(p_text)
        is_valid_reason = rej.reason_code in ai_indexing.VALID_REJECTION_REASONS and rej.reason_code != "unspecified"
        quote_len_ok = len(norm_quote) >= 8
        quote_in_page = norm_quote in norm_page

        if is_valid_reason and quote_len_ok and quote_in_page:
            verified_rejection_ids.add(rej.prior_id)
            rejected_so_far.add((r["page_num"], _norm_for_match(r["heading_text"])))
        else:
            unverified_rejection_ids.add(rej.prior_id)
            warning_counts["rule_prior_rejection_unverified"] = warning_counts.get("rule_prior_rejection_unverified", 0) + 1

    # 3. Check confirmations
    confirmed_ids = set(result.confirmed_prior_ids)
    for cid in confirmed_ids:
        if cid not in priors_by_id:
            warning_counts["rule_prior_unknown_id"] = warning_counts.get("rule_prior_unknown_id", 0) + 1
        # Contradiction precedence: both confirmed and rejected -> rejection iff verified else confirmed
        if cid in verified_rejection_ids:
            pass
        elif cid in unverified_rejection_ids:
            unverified_rejection_ids.discard(cid)

    # 4. Construct surviving prior headings
    surviving_prior_headings: List[ai_indexing.schema.AIHeading] = []
    for r in priors:
        pid = r["prior_id"]
        if pid in matched_prior_ids or pid in verified_rejection_ids:
            continue

        if pid in confirmed_ids:
            h = ai_indexing.schema.AIHeading(
                heading_text=r["heading_text"],
                start_page=r["page_num"],
                end_page=r["page_num"],
                level=r["depth"],
                parent_ref=r["parent_label"],
                prior_id=pid,
                heading_confidence="high",
                heading_confidence_reasons=[],
            )
            surviving_prior_headings.append(h)
        elif pid in unverified_rejection_ids:
            h = ai_indexing.schema.AIHeading(
                heading_text=r["heading_text"],
                start_page=r["page_num"],
                end_page=r["page_num"],
                level=r["depth"],
                parent_ref=r["parent_label"],
                prior_id=pid,
                heading_confidence="low",
                heading_confidence_reasons=["rule_prior_rejection_unverified"],
            )
            surviving_prior_headings.append(h)
        else:
            warning_counts["rule_prior_unreviewed"] = warning_counts.get("rule_prior_unreviewed", 0) + 1
            h = ai_indexing.schema.AIHeading(
                heading_text=r["heading_text"],
                start_page=r["page_num"],
                end_page=r["page_num"],
                level=r["depth"],
                parent_ref=r["parent_label"],
                prior_id=pid,
                heading_confidence="low",
                heading_confidence_reasons=["rule_prior_unreviewed"],
            )
            surviving_prior_headings.append(h)

    # 5. Merge AI headings and surviving prior headings in physical block order per page
    all_by_page: Dict[int, List[ai_indexing.schema.AIHeading]] = {}
    for h in result.headings:
        all_by_page.setdefault(h.start_page, []).append(h)
    for h in surviving_prior_headings:
        all_by_page.setdefault(h.start_page, []).append(h)

    merged_headings: List[ai_indexing.schema.AIHeading] = []
    for pn in sorted(all_by_page.keys()):
        page_heads = all_by_page[pn]
        blocks = page_blocks.get(pn, [])

        def _rank_heading(h):
            res = locate_heading_block(blocks, h.heading_text, used_block_idxs=set())
            idx = res[0] if res is not None else None
            return (idx is None, idx if idx is not None else 0)

        page_heads.sort(key=_rank_heading)
        merged_headings.extend(page_heads)

    return merged_headings, verified_rejection_ids


def _check_rule_prior_key_drift(
    cands: List[Dict[str, Any]],
    events: List[Dict[str, Any]],
    priors: List[Dict[str, Any]],
    warning_counts: Dict[str, int],
) -> None:
    priors_by_id = {r["prior_id"]: r for r in priors}
    for idx, cand in enumerate(cands):
        ev = events[idx] if idx < len(events) else {}
        pid = ev.get("prior_id")
        prior_row = priors_by_id.get(pid) if pid else None
        if prior_row and prior_row.get("key") and cand.get("key") != prior_row["key"]:
            warning_counts["rule_prior_key_drift"] = warning_counts.get("rule_prior_key_drift", 0) + 1
            reasons = cand.setdefault("heading_confidence_reasons", [])
            if "rule_prior_key_drift" not in reasons:
                reasons.append("rule_prior_key_drift")


async def review_mentions_for_job(job_id: str, engine_params: Optional[Dict[str, Any]] = None) -> None:
    """Phase 5a: AI review of detected mentions for a job."""
    all_mentions = database.get_all_reference_mentions_for_job(job_id)
    if not all_mentions:
        return

    try:
        resolved = ai_indexing.resolve_active_config()
    except ai_indexing.AIConfigError as e:
        print(f"[MentionReview] AIConfigError: {e}")
        return

    provider = await ai_indexing.get_provider(resolved.provider_config)
    pages = database.get_pages(job_id)
    page_texts = {p["page_num"]: p.get("text") or "" for p in pages}
    mentions_by_page: Dict[int, List[Dict[str, Any]]] = {}
    for m in all_mentions:
        mentions_by_page.setdefault(m["page"], []).append(m)

    chunk_pages = (engine_params or {}).get("ai_index_chunk_pages") or config.AI_INDEX_CHUNK_PAGES
    page_nums = sorted(page_texts.keys())

    targets = database.get_reference_targets_for_job(job_id)
    target_lookup = reference_index.TargetLookupIndex(targets)

    for i in range(0, len(page_nums), chunk_pages):
        chunk_pns = page_nums[i:i + chunk_pages]
        chunk_mentions = [m for pn in chunk_pns for m in mentions_by_page.get(pn, [])]
        if not chunk_mentions:
            continue

        chunk_pages_input = [ai_indexing.PageInput(page_num=pn, text=page_texts.get(pn, "")) for pn in chunk_pns]
        sys_prompt, user_prompt = ai_indexing.build_mention_review_prompt(chunk_pages_input, chunk_mentions)

        try:
            completion = await provider.complete(system_prompt=sys_prompt, user_prompt=user_prompt)
            reviews = ai_indexing.parse_mention_response(completion.text)
        except Exception as e:
            print(f"[MentionReview] Error reviewing chunk {chunk_pns}: {e}")
            continue

        reviews_by_id = {r.mention_id: r for r in reviews}
        modified_pages = set()

        for m in chunk_mentions:
            m_id = m.get("mention_id") or f"M{m.get('occurrence_index', 0)}"
            rev = reviews_by_id.get(m_id)
            if not rev:
                continue

            p_text = page_texts.get(m["page"], "")
            norm_quote = _norm_for_match(rev.evidence_quote)
            norm_page = _norm_for_match(p_text)
            quote_ok = len(norm_quote) >= 8 and norm_quote in norm_page

            if rev.action == "reject" and quote_ok:
                m["resolution"] = "suppressed"
                m["suppressed_reason"] = "ai_rejected"
                m["detection_source"] = "ai"
                m["detection_model"] = resolved.provider_config.model
                modified_pages.add(m["page"])
            elif rev.action == "unsuppress" and quote_ok:
                m["resolution"] = "exact" if target_lookup.has_exact_key(m["key"]) else "approximate"
                m["suppressed_reason"] = None
                m["detection_source"] = "ai"
                m["detection_model"] = resolved.provider_config.model
                modified_pages.add(m["page"])
            elif rev.action == "confirm":
                m["detection_source"] = "ai"
                m["detection_model"] = resolved.provider_config.model
                modified_pages.add(m["page"])

        if modified_pages:
            version = database.bump_reference_index_version(job_id)
            for pn in modified_pages:
                page_m = mentions_by_page.get(pn, [])
                database.upsert_reference_mentions_for_page(job_id, pn, page_m, version)



async def reindex_job_with_ai(
    job_id: str, start_page: Optional[int] = None, chunk_pages_override: Optional[int] = None,
    review_rule_prior_override: Optional[bool] = None, overlap_pages_override: Optional[int] = None,
) -> Dict[str, Any]:
    """Full-job AI reindex, chunked, with per-chunk fallback to the
    rule-based detector. `start_page`, when given, is a targeted re-run
    (triggered after a single page's manual retry) rather than a full
    rebuild — see the bounded/early-stop logic below.

    `chunk_pages_override`, `review_rule_prior_override`, and
    `overlap_pages_override`, when given, each win over both the job's own
    frozen `engine_params` and the config default — used by a manual
    "Rebuild Cross-References" call (main.py), which always wants the
    CURRENT settings, not whatever was frozen at this job's original
    creation time (the same reasoning already applied to
    `enable_ai_indexing` there). The always-rule-based auto-trigger right
    after OCR never passes any of these, so it stays pinned to frozen
    values. `review_rule_prior_override`/`overlap_pages_override` are
    resolved with explicit `is not None` checks, never `or`-chaining —
    `False` and `0` are both meaningful values an `or`-chain would silently
    discard in favor of the engine_params/config fallback.

    Every page's indexing input is picked automatically, once, by
    `_page_text`/`reference_index.build_layout_annotated_text` — there is
    no caller-facing choice between the cleaned OCR text (`text`) and the
    raw, pre-clean_detection_tags() text (`raw_ocr_text`) that still
    carries Unlimited-OCR's `<|det|>type [bbox]<|/det|>` layout tags when
    the job ran with grounding enabled: grounding/persistence always use
    the plain cleaned `text`, and a richer, self-explanatory
    `[OCR-LAYOUT: ...]`-annotated version (built straight from `raw_ocr_text`
    when present, via layout_format.parse_blocks — no alignment with `text`
    needed or attempted) is used ONLY for what gets sent to the AI model's
    prompt — never a second, separately-stored index. A page OCR'd without
    grounding has no tags in `raw_ocr_text` at all, so this is a silent
    no-op (the AI sees the same plain `text` as everything else).

    Checkpoint/resume (start_page=None only — a targeted retry always runs
    from its own explicit start_page): a prior run of THIS function that
    was paused mid-way is resumed from `ai_index_report["ai_last_persisted_
    page"]` rather than restarting at page 1. This is deliberately its own
    tracked value, NOT `database.get_reference_index_frontier()` — that
    frontier is shared with the always-rule-based LIVE per-page hook, which
    already advances it to total_pages during ordinary OCR, so reusing it
    here would make a genuinely fresh AI run think it was already done."""
    job = database.get_job(job_id, include_pages=False)
    if not job:
        return {}
    total_pages = job.get("total_pages") or 0
    engine_params = job.get("engine_params") or {}
    chunk_pages_size = chunk_pages_override or engine_params.get("ai_index_chunk_pages") or config.AI_INDEX_CHUNK_PAGES
    # Primary behavior as of Round 10's A/B test (job bb417471): the AI
    # itself detects and nests numbered pleading points/grounds, rather than
    # the deterministic detect_pleading_points_for_page augmentation —
    # confirmed to nest an entire 22-item mixed arabic/roman grounds list
    # correctly in one pass (something 4 rounds of narrowing regex fixes
    # never fully achieved), at the cost of occasionally missing a point
    # elsewhere that the regex safety net would have caught. A job's
    # engine_params can still explicitly set this to False to opt back into
    # the regex-augmented behavior instead.
    pleading_points_via_ai = bool(engine_params.get("ai_index_pleading_points_via_ai", True))
    # Off by default — a per-heading AI micro-decision (see
    # _resolve_parent_via_ai) that replaces _resolve_depth_for_heading's
    # fuzzy-string/positional fallback specifically, not its exact-match
    # path. Ships behind this flag for A/B testing before becoming the
    # default, mirroring pleading_points_via_ai's own rollout above.
    resolve_parent_via_ai = bool(engine_params.get("ai_index_resolve_parent_via_ai", False))
    review_rule_prior = (
        review_rule_prior_override if review_rule_prior_override is not None
        else bool(engine_params.get("ai_index_review_rule_prior", config.AI_INDEX_REVIEW_RULE_PRIOR))
    )
    overlap_pages = (
        overlap_pages_override if overlap_pages_override is not None
        else engine_params.get("ai_index_chunk_overlap_pages", config.AI_INDEX_CHUNK_OVERLAP_PAGES)
    )
    review_mentions = bool(engine_params.get("ai_index_review_mentions", False))

    try:
        resolved = ai_indexing.resolve_active_config()
    except ai_indexing.AIConfigError as e:
        database.update_ai_index_report(job_id, status="failed", error=str(e))
        return {"status": "failed", "error": str(e)}

    indexer = ai_indexing.StructureIndexer(resolved)

    all_pages = database.get_pages(job_id)
    resume_from = None
    if start_page is None:
        prior_report = database.get_ai_index_report(job_id)
        if prior_report.get("status") == "paused" and prior_report.get("ai_last_persisted_page"):
            resume_from = prior_report["ai_last_persisted_page"] + 1

    effective_start = start_page if start_page is not None else resume_from
    if effective_start is not None:
        pages = [p for p in all_pages if p["page_num"] >= effective_start]
        seed_scope = database.get_end_of_page_scope(job_id, effective_start - 1) if effective_start > 1 else None
    else:
        pages = all_pages
        seed_scope = None
        database.set_reference_index_frontier(job_id, 0)

    if not pages:
        return {"status": "done", "pages_done": 0}

    def _page_text(p):
        return p.get("text") or ""
    # Kept separately (never fed into PageInput/plan_chunks) so grounding
    # and persistence always see the plain clean text — only the prompt
    # sent to the AI model gets the layout-annotated version, built
    # per-chunk right before the model call (see build_layout_annotated_text).
    raw_text_by_page: Dict[int, str] = {p["page_num"]: (p.get("raw_ocr_text") or "") for p in pages}
    page_inputs = [ai_indexing.PageInput(page_num=p["page_num"], text=_page_text(p)) for p in pages]
    chunks = ai_indexing.plan_chunks(
        page_inputs,
        max_pages=chunk_pages_size,
        overlap_pages=overlap_pages,
    )
    total_chunks = len(chunks)
    bounded_stop = start_page is not None

    continuing = start_page is not None or resume_from is not None
    report = database.get_ai_index_report(job_id)
    report.update({
        "status": "running", "provider": resolved.provider_config.provider,
        "model": resolved.provider_config.model, "chunk_pages": chunk_pages_size,
        "current_chunk": 0, "total_chunks": total_chunks,
        "pages_done": report.get("pages_done", 0) if continuing else 0,
        "chunks": report.get("chunks") if continuing else [],
    })
    database.set_ai_index_report(job_id, report)

    last_page_text = ""
    if effective_start and effective_start > 1:
        prev = database.get_page(job_id, effective_start - 1)
        last_page_text = (prev or {}).get("text") or ""

    final_status = "done"
    final_error = ""
    pages_done_total = report.get("pages_done", 0)
    last_persisted_page_num: Optional[int] = None
    carried_headings: Dict[int, List[Any]] = {}
    highest_processed_this_run: int = 0
    rejected_so_far: Set[Tuple[int, str]] = set()

    def _cancel_check():
        return task_queue.is_job_paused(job_id)

    for chunk_idx, chunk in enumerate(chunks):
        if task_queue.is_job_paused(job_id):
            final_status = "paused"
            break

        if chunk_idx > 0:
            await asyncio.sleep(config.AI_INDEX_MIN_CHUNK_INTERVAL_SECONDS)

        if chunk_idx > 0:
            first_page_num = chunk.pages[0].page_num
            if first_page_num > 1:
                seed_scope = database.get_end_of_page_scope(job_id, first_page_num - 1)
                prev_page = database.get_page(job_id, first_page_num - 1)
                last_page_text = (prev_page or {}).get("text") or ""
            else:
                seed_scope, last_page_text = None, ""

        carry_context = _carry_in_summary(seed_scope, last_page_text)
        # Layout-annotated text is built fresh per chunk, for the MODEL
        # PROMPT ONLY — `chunk` itself (plain clean text) is untouched and
        # keeps being used below for grounding (page_blocks) and the
        # persisted index (chunk_page_texts), so this never produces a
        # second, differently-sourced index for the same pages.
        annotated_chunk = ai_indexing.ChunkPlan(
            pages=[
                ai_indexing.PageInput(
                    page_num=p.page_num,
                    text=reference_index.build_layout_annotated_text(p.text, raw_text_by_page.get(p.page_num)),
                )
                for p in chunk.pages
            ],
            truncated_pages=chunk.truncated_pages,
            total_chars=chunk.total_chars,
        )

        priors: List[Dict[str, Any]] = []
        if review_rule_prior:
            priors = _rule_prior_for_chunk(
                chunk, seed_scope, total_pages, rejected_so_far, raw_text_by_page,
            )

        try:
            result = await indexer.extract_structure_for_chunk(
                annotated_chunk, carry_context=carry_context, cancel_check=_cancel_check,
                include_pleading_points=pleading_points_via_ai,
                rule_prior=priors,
                review_rule_prior=review_rule_prior,
            )
        except ai_indexing.AIConfigError as e:
            final_status, final_error = "failed", str(e)
            break

        if result.outcome == ai_indexing.ChunkOutcome.CANCELLED:
            final_status = "paused"
            break

        source = "rule" if result.outcome == ai_indexing.ChunkOutcome.FAILED else "ai"
        events_by_page: Dict[int, List[Dict[str, Any]]] = {}
        warning_counts: Dict[str, int] = {}
        chunk_page_texts = {p.page_num: p.text for p in chunk.pages}

        if source == "ai":
            page_blocks = {p.page_num: reference_index.parse_page_blocks(p.text) for p in chunk.pages}

            verified_rejections: Set[str] = set()
            if review_rule_prior and priors:
                raw_headings, verified_rejections = _merge_prior_into_headings(
                    result, priors, chunk_page_texts, page_blocks, rejected_so_far, warning_counts,
                )
            else:
                raw_headings = list(result.headings)

            # Raw heading merge for overlap pages before grounding
            chunk_headings_by_page: Dict[int, List[Any]] = {}
            for h in raw_headings:
                chunk_headings_by_page.setdefault(h.start_page, []).append(h)

            final_headings: List[Any] = []
            for p in chunk.pages:
                pn = p.page_num
                cur_headings = chunk_headings_by_page.get(pn, [])
                carried_for_page = carried_headings.get(pn, [])
                if carried_for_page:
                    cur_norm_texts = {_norm_for_match(h.heading_text) for h in cur_headings}
                    cur_leads = {
                        _leading_reference_number(h.heading_text)
                        for h in cur_headings
                        if _leading_reference_number(h.heading_text)
                    }
                    # Upgrade bare numbers in cur_headings if carried heading has richer text:
                    for idx, ch in enumerate(cur_headings):
                        ch_lead = _leading_reference_number(ch.heading_text)
                        if ch_lead and len(ch.heading_text.strip()) <= 4:
                            for car in carried_for_page:
                                car_lead = _leading_reference_number(car.heading_text)
                                if car_lead == ch_lead and len(car.heading_text.strip()) > 4:
                                    cur_headings[idx] = car
                                    cur_norm_texts.add(_norm_for_match(car.heading_text))
                                    break

                    missed_headings = [
                        h for h in carried_for_page
                        if _norm_for_match(h.heading_text) not in cur_norm_texts
                        and (_leading_reference_number(h.heading_text) is None or _leading_reference_number(h.heading_text) not in cur_leads)
                    ]
                    if missed_headings:
                        blocks = page_blocks.get(pn, [])
                        # Only add a missed heading if it can actually be located on this page's blocks
                        # (do not insert unlocated phantom duplicates carried over from previous chunk)
                        valid_missed = []
                        for h in missed_headings:
                            res = locate_heading_block(blocks, h.heading_text, used_block_idxs=set())
                            if res is not None:
                                valid_missed.append(h)
                        if valid_missed:
                            merged_group = list(cur_headings) + valid_missed

                            def _rank_heading(h):
                                result = locate_heading_block(blocks, h.heading_text, used_block_idxs=set())
                                idx = result[0] if result is not None else None
                                return (idx is None, idx if idx is not None else 0)

                            merged_group.sort(key=_rank_heading)
                            final_headings.extend(merged_group)
                        else:
                            final_headings.extend(cur_headings)
                    else:
                        final_headings.extend(cur_headings)
                else:
                    final_headings.extend(cur_headings)


            if config.AI_INDEX_CHUNK_OVERLAP_PAGES > 0:
                trailing_overlap_pages = [p.page_num for p in chunk.pages[-config.AI_INDEX_CHUNK_OVERLAP_PAGES:]]
                carried_headings = {
                    pn: [h for h in final_headings if h.start_page == pn]
                    for pn in trailing_overlap_pages
                }
            else:
                carried_headings = {}

            events_by_page, grounding_warnings = await _ground_and_resolve_chunk(
                final_headings, page_blocks, carry_context, resolved.provider_config.model,
                has_next_page_fn=lambda pn: pn < total_pages,
                resolve_parent_via_ai=resolve_parent_via_ai, provider_config=resolved.provider_config,
            )
            warning_counts.update(grounding_warnings)

            # Review degradation fallback:
            # If review_rule_prior is on, len(priors) >= 3, every prior was rejected,
            # and no AI heading was grounded -> review failure, fall back to rule prior, log review_degraded.
            if (
                review_rule_prior
                and len(priors) >= 3
                and len(verified_rejections) == len(priors)
                and not any(len(events) > 0 for events in events_by_page.values())
            ):
                source = "rule"
                warning_counts["review_degraded"] = warning_counts.get("review_degraded", 0) + 1
        else:
            carried_headings = {}

        stopped_early = False
        pages_processed_this_chunk = 0

        if source == "rule":
            overlap_count = config.AI_INDEX_CHUNK_OVERLAP_PAGES if chunk_idx > 0 else 0
            if overlap_count > 0 and len(chunk.pages) > overlap_count:
                last_overlap_page_num = chunk.pages[overlap_count - 1].page_num
                seed_scope = database.get_end_of_page_scope(job_id, last_overlap_page_num)
                prev_p = database.get_page(job_id, last_overlap_page_num)
                last_page_text = (prev_p or {}).get("text") or ""
            pages_to_process = chunk.pages[overlap_count:]
        else:
            pages_to_process = chunk.pages

        for p in pages_to_process:
            page_num = p.page_num
            text = chunk_page_texts[page_num]
            is_revisit = page_num <= highest_processed_this_run
            previous_scope = (
                database.get_end_of_page_scope(job_id, page_num)
                if (bounded_stop and not is_revisit)
                else None
            )

            if source == "ai":
                events = events_by_page.get(page_num, [])
                cands, new_scope = reference_index.apply_heading_events_to_scope(
                    events, seed_scope, page_num, has_next_page=page_num < total_pages,
                )
                if review_rule_prior and priors:
                    _check_rule_prior_key_drift(cands, events, priors, warning_counts)
                # Additive, deterministic augmentation: numbered/roman
                # pleading paragraphs the AI is deliberately never asked to
                # report as headings (they're body text, not headings) but
                # the user still wants indexed as navigable points/grounds.
                # This is very often the ONLY place a decimal ground/point
                # gets detected at all, so its last_decimal_ground finding
                # must be folded back into new_scope before persistence —
                # otherwise a later page's AI-reported roman sub-grounds
                # have nothing to nest under.
                if not pleading_points_via_ai:
                    extra_cands, last_decimal_ground = reference_index.detect_pleading_points_for_page(
                        text, new_scope, cands,
                    )
                    cands = cands + extra_cands
                    new_scope["last_decimal_ground"] = last_decimal_ground
                # else: pleading_points_via_ai — the AI was asked to detect
                # and nest these itself (see build_structure_prompt's
                # include_pleading_points), so the flat regex augmentation
                # is skipped entirely for a clean A/B comparison, trusting
                # apply_heading_events_to_scope's own forced-family/
                # last_decimal_ground handling of whatever the AI reported.
            else:
                cands, new_scope = reference_index.build_targets_for_page(
                    text, seed_scope, has_next_page=page_num < total_pages,
                )

            reference_index.validate_scope_shape(new_scope)
            reference_index._persist_page_index(
                job_id, page_num, text, cands, new_scope, provisional=False,
                detection_source=source, detection_model=resolved.provider_config.model,
            )
            current_frontier = database.get_reference_index_frontier(job_id) or 0
            database.set_reference_index_frontier(job_id, max(current_frontier, page_num))
            seed_scope = new_scope
            last_page_text = text
            last_persisted_page_num = page_num
            pages_processed_this_chunk += 1
            highest_processed_this_run = max(highest_processed_this_run, page_num)

            if bounded_stop and not is_revisit and previous_scope is not None and previous_scope == new_scope:
                stopped_early = True
                break

        overlap_count_ai = config.AI_INDEX_CHUNK_OVERLAP_PAGES if (chunk_idx > 0 and source == "ai") else 0
        new_in_chunk = max(pages_processed_this_chunk - overlap_count_ai, 0)
        pages_done_total += new_in_chunk
        rep = database.get_ai_index_report(job_id)
        chunks_log = rep.get("chunks") or []
        chunks_log.append({
            "first_page": chunk.page_nums[0], "last_page": chunk.page_nums[-1],
            "status": "ok" if source == "ai" else "fallback",
            "error_kind": result.error_kind, "error": result.error_message,
            "heading_count": len(result.headings) if source == "ai" else None,
            "warnings": warning_counts,
        })
        database.update_ai_index_report(
            job_id, current_chunk=chunk_idx + 1, total_chunks=total_chunks,
            pages_done=pages_done_total, chunks=chunks_log,
            ai_last_persisted_page=last_persisted_page_num,
        )

        if stopped_early:
            break

    reference_index.build_mention_index_for_job(job_id)
    if review_mentions:
        await review_mentions_for_job(job_id, engine_params)
    database.update_ai_index_report(job_id, status=final_status, error=final_error)
    return {"status": final_status, "pages_done": pages_done_total, "total_chunks": total_chunks}
