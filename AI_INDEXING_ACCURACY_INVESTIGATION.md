# AI-Assisted Document-Structure Indexing — Accuracy Investigation Log

**Purpose of this document:** a full record of the multi-session investigation into
the AI-assisted cross-reference indexing feature's accuracy — what was found, what
was fixed, what was tried and didn't work, and what's still open. Complements
`CROSS_REFERENCE_DESIGN_V2.md` (the system's architecture/data model) — this
document is about *how accurate the AI path actually is on real documents* and the
reasoning behind every fix and experiment along the way. Organized chronologically.

**Primary files involved:**
- `reference_index.py` — the shared stack machine (`apply_heading_events_to_scope`
  for the AI path, `build_targets_for_page` for the rule-based path), `FAMILIES`
  rank table, ground/point resolution helpers.
- `ai_reference_index.py` — the AI-path stitching layer: chunk dispatch, parent_ref
  resolution, heading→text grounding.
- `ai_indexing/` — provider abstraction (`providers.py`), prompt assembly
  (`prompt.py`), chunk/response schema (`schema.py`), the `StructureIndexer` façade
  (`client.py`).

**Test document used throughout:** `9c3dc6e6-9e74-4088-b7f0-638022e4ab84`, a 17-page
excerpt of an HVPNL arbitration judgment (F.A.O. (Comm.Arb.) 23 of 2019), and
`bb417471-561c-4029-bb21-3bdcf74e7dcb`, a 100-page HVPNL arbitration appeal, for
full-document audits.

---

## 1. Baseline: the Round-14 full-document audit

A full manual audit of job `bb417471` (100 pages) against the AI-produced index
scored **~78/100**. All of the user's original ~14 reported complaints were either
resolved by that point or reframed as not-real-headings. Remaining gaps at that
point: recall misses (two affidavits' continuation points, several contract
sub-clauses, a letter sub-document, SCHEDULE-3A) and 2 false positives (page-stamp
numbers misread as real paragraph numbers).

**Standing design decision, confirmed early and never revisited:** the AI path
(`ai_index_pleading_points_via_ai=True` in `ai_reference_index.py`) is the default —
the AI itself detects and nests numbered pleading points/grounds, rather than the
deterministic `detect_pleading_points_for_page` regex augmentation. This trades
determinism for flexibility: a 4-round regex-narrowing effort never fully solved
mixed arabic/roman grounds lists the way one AI pass did, at the cost of recall
varying run-to-run (confirmed directly: the same document had a heading present in
one run and missing in the next, no code change in between). This was an explicit,
informed choice, not an oversight.

---

## 2. Six targeted fixes (mid-investigation round)

A fresh audit of job `9c3dc6e6` surfaced six distinct, root-caused bugs. All six were
implemented, unit-tested, and verified against a live re-run before being considered
closed.

### 2.1 Roman/letter ambiguity in `apply_heading_events_to_scope`

**Report:** a sequential-letter list like `(A)`–`(O)` had letters that are also valid
roman numerals (C, D, I, L, M) misclassified as roman and forced into the
`ground`/`point` leaf family, while non-roman-valid letters (A, B, E, F, G, H, J, K,
N, O) fell through to the generic "subdoc" container bucket and got pushed as real
ancestors. Result: `(C)`/`(D)` nested under `(B)`, `(I)` nested under `(H)`, `(L)`/`(M)`
nested under `(K)` — every time, deterministically reproducible across 3 separate
runs on 2 documents.

**Fix:** swapped in `_resolve_ground_or_point_match` (the resolver
`build_targets_for_page` already used) in place of raw `GROUND_DIGIT_RE`/
`GROUND_ROMAN_RE` matching, and threaded a new `last_ground_letter_ordinal` through
the scope dict, mirroring how `last_decimal_ground` already worked.

**Verified:** fresh duplicate-job re-run of `9c3dc6e6` — `(A)`–`(O)` all resolved as
flat, distinct siblings.

### 2.2 Paren+period regex gap

**Report:** `"(N)."` (both a closing paren *and* a trailing period) wasn't matched by
`GROUND_DIGIT_RE`/`GROUND_ROMAN_RE`/`_GROUND_SINGLE_LETTER_RE`, so that item fell
through to the generic container bucket and the next letter nested under it instead
of staying flat. Confirmed on two different documents (`"(XIV)."` in `bb417471`,
`"(N)."` in `9c3dc6e6`).

**Fix:** widened each regex's trailing-punctuation group from `(?:\)|\.)` to
`(?:\)\.?|\.)` in `reference_index.py`.

**Verified:** unit tests plus a live re-run — `"(N)."` now resolves as its own leaf,
flat sibling of `"(O)"`.

### 2.3 Sequential-letter continuations of a decimal ground/point

**Report:** the mechanism that nests a roman-numeral continuation under the last
decimal ground/point (`last_decimal_ground`/`effective_stack`) explicitly excluded
sequential-letter continuations (`not is_seq_letter` in the trigger condition, in
both `build_targets_for_page` and `apply_heading_events_to_scope`). User-clarified
correct hierarchy: paragraph "3. ...on following grounds:" should be the parent of
`(A)`–`(O)`, not "Challenge to the Arbitral Award" directly.

**Fix:** widened both conditions to `(is_seq_letter or _is_roman(number_raw))`.

**Verified:** unit-tested and confirmed correct in isolation. **Not fully provable
live** at the time — see §3, paragraph 2/3 recall gap.

### 2.4 Sibling-chaining safety net

**Report:** the AI inconsistently reports `parent_ref` for flat enumerated-list
items — confirmed **probabilistic, not a code bug**: the identical lettered/roman
list shape was correctly flat in some runs and incorrectly chained (each item
nesting under the previous) in others, within the same document and even the same
run.

**Fix (two parts):**
- Prompt: added `ENUMERATED LIST ITEMS ARE SIBLINGS OF EACH OTHER, NOT OF THE
  PREVIOUS ITEM` section to `STRUCTURE_SYSTEM_PROMPT`, plus a second worked example
  using lowercase-roman shape (the Conclusion's `(i)`–`(vii)` award list, the most
  persistent real offender).
- Code: new `_enumeration_marker_style(text)` helper in `reference_index.py`,
  classifying a heading's leading marker into a coarse style bucket
  (`"decimal"`, `"lower_roman"`, `"upper_letter_or_roman"`). In
  `apply_heading_events_to_scope`, generic-path events (not `forced_family`) whose
  marker style matches the immediately-preceding same-style item, AND whose
  resolved depth is exactly one level deeper, get flattened back to that previous
  item's depth.

**Verified:** live re-run — the Conclusion's `(i)`–`(vii)` list, which had chained on
every prior check, now correctly resolved as flat siblings.

### 2.5 Recall gap on continuous, cross-heading paragraph numbering

**Report:** the Judgment's numbered paragraphs run continuously across multiple
section headings (paragraph 2 belongs to FACTS, paragraph 3 opens "Challenge to the
Arbitral Award", paragraph 4 opens "Respondent's case", etc.) rather than each
heading restarting its own count. Paragraphs 4 through 15 were entirely absent from
one run's index — verified present in the raw OCR text — while paragraph 16 onward
(once a "next heading, then its numbered paragraph" pattern stabilized) were
correctly captured.

**Fix:** extended the existing "FINDING THE FULL RUN OF AN ENUMERATED LIST" prompt
section: the same gap self-check now applies to the document's own running
paragraph numbers, and the prompt explicitly describes the continuous
cross-heading numbering pattern.

**Verified:** live re-run recovered paragraphs 11 through 19 (previously entirely
missing). **Not fully closed** — see §3.

### 2.6 New heuristic: colon/dash/arrow-ending lines introduce lists

**User's request, verbatim:** *"if something ends with : or - or -> ,etc it can be
considered a heading if it is not too long... with some examples so that we don't
risk it getting hardcoded for just this pdf."*

**Fix:** new prompt section, `A SHORT LINE ENDING IN ":", "-", OR "->" OFTEN
INTRODUCES THE LIST THAT FOLLOWS` — a general shape rule (not tied to this
document's wording), with worked examples deliberately drawn from unrelated
domains ("The inspection revealed the following defects:", "Delivery shall proceed
in the following stages -", "The vendor's obligations are as follows ->").

**Verified:** live re-run — the Conclusion's `(i)`–`(vii)` award list correctly
nested under `"5. Award of amounts... detailed as under:-"` (the exact shape this
heuristic targets).

**All six fixes:** tested (550/550 full suite passing), deployed, verified against a
fresh live re-run of `9c3dc6e6`.

---

## 3. What the six fixes did and didn't close

The same live re-run that confirmed §2's fixes also surfaced two narrower, related
gaps:

1. **Paragraphs 2 and 3 specifically — each section's own opening numbered
   sentence, immediately following that section's heading — were still dropped**,
   even though paragraphs 4–19 were now recovered. Because paragraph 3 never gets
   emitted, `point:3` never exists, so §2.3's nesting fix never gets a chance to
   fire: `(A)`–`(O)` land flat under "Challenge to the Arbitral Award" directly,
   not because the fix is wrong, but because there's no `point:3` to nest under.
2. **Paragraphs 20–22 still nested one level under `"5. Award of amounts..."`**
   instead of directly under "Conclusion" as siblings of paragraphs 18/19.

---

## 4. User-reported recurring issues → the event-ordering deep dive

The user reported (independently, via manual inspection of a fresh run) two
recurring structural problems: FACTS not containing its own point, and the
`(A)`–`(O)` list being captured "in order but not in the correct place" — landing
under the wrong container part-way through, coinciding with "Respondent's case"
appearing at an unexpected point in the structure.

### 4.1 What was found

A direct DB read of the stored index (not just spot-checked `scope_key` values)
confirmed:

- `point:2` and `point:3` (FACTS's and Challenge's own opening sentences) simply
  don't exist in the index at all — a pure recall miss, consistent with §3 item 1.
- **"Challenge to the Arbitral Award" resolved with `scope_key=""` — a fresh
  top-level root, evicting JUDGMENT entirely** — instead of `scope_key="subdoc:2"`
  (FACTS's sibling, both children of JUDGMENT). "Respondent's case" then inherited
  this mistake, resolving as a child of "Challenge to the Arbitral Award" instead
  of also being JUDGMENT's child.

### 4.2 Root-cause investigation of the resolution mechanism

Four questions were answered by reading `ai_reference_index.py`/`ai_indexing/`
directly (not guessed):

1. **Is there a sort by true document position before events are built?** Yes, but
   only by `start_page` (`ai_indexing/schema.py`'s `normalize_chunk`,
   ~line 178-182) — a *stable* sort, so two headings reported on the *same page*
   keep whatever order the model's own JSON array happened to list them in, even
   though every heading already carries a `start_line_hint`.
2. **How does `parent_ref` resolve to depth?** `_resolve_depth_for_heading`
   (`ai_reference_index.py`, ~line 200-252) is positionally/sequentially
   dependent — it walks the array in order, building a simulated stack depth
   incrementally, with no self-correction if an earlier heading in that same array
   was mis-resolved.
3. **What carries forward across chunks?** Only the currently-open ancestor stack
   (labels/ref_ids, deliberately small — `_carry_in_summary`,
   `ai_reference_index.py`, ~line 190-197) — never the full previously-resolved
   index. Processing is strictly forward, chunk-by-chunk; a later chunk's output
   never leaks into an earlier chunk's resolution.
4. **Shape-based vs. semantic prompt instructions?** Both present — explicit
   "judge by ROLE... not a fixed vocabulary" language exists, but the actual depth
   *resolution* mechanism is 100% positional/mechanical, never semantic. Semantic
   judgment only decides *whether something is a heading*, never *where it slots
   into the hierarchy*.

### 4.3 The user's proposed architecture — evaluated, not adopted as-is

The user proposed: only ever depend on prior chunks (never later ones), feed the
model the *full* index built so far (not just currently-open ancestors), and lean
more on the model's own semantic understanding of hierarchy.

- **"Only prior chunks, never later"** — already true; no change needed.
- **"Feed the full built index, not just open ancestors"** — evaluated and NOT
  recommended: the bug found in §4.1 is a same-page/same-chunk ordering problem;
  more cross-chunk context doesn't help the model order things correctly *within*
  the page it's currently reading, and it adds real token cost against an
  already rate-limit-constrained model tier.
- **"Lean more on semantic reasoning for hierarchy"** — already partially present;
  pushing *more* of the hierarchy decision onto free-form model judgment risks
  amplifying the already-documented probabilistic parent_ref drift (§2.4), not
  reducing it, unless paired with a deterministic backstop.

---

## 5. The "own read vs. prompt-simulated read" comparison exercise

As a separate, standalone exercise (not stored anywhere — a one-off comparison, not
a pipeline change), the raw OCR text of the 17-page test document was read directly
and two independent outlines were hand-built: one from a plain reading with no
prompt constraints, one applying `STRUCTURE_SYSTEM_PROMPT`'s actual rules by hand.

**Result: the two converged on an identical, correct structure.** Point 3's
trailing colon correctly parents `(A)`–`(O)`; point 19's trailing period-plus-colon
parents its own 1–5 list, and item "5." (itself ending `"...detailed as under:-"`)
parents a second, deeper `(i)`–`(vii)` list; paragraphs 20–31 correctly resume as
Conclusion's siblings rather than getting absorbed into either list.

**Conclusion drawn:** the prompt's *rules* are sound — a careful reasoner applying
them by hand gets the same answer as a careful reasoner using no rules at all. The
gap observed in real runs (§3, §4) is a matter of a smaller/faster model's
*execution reliability*, not a flaw in what the rules ask for.

*(This exercise was published as a side-by-side HTML comparison artifact for
review; it was not persisted to any database and is not part of the shipped
pipeline.)*

---

## 6. The qwen3:8b revelation

**Turning point in the investigation:** the user clarified that Gemini
(`gemini-flash-lite-latest`, configured in `.env.local-test`) is only being used as
a *stand-in* for testing — the real deployment target is **qwen3:8b**, a small,
locally-hosted model, presumably chosen for cost/privacy/self-hosting reasons. This
ruled out "use a bigger/smarter model" as a fix, and reframed every subsequent
question around: *what works for a genuinely small model, not just a cheap cloud
one?*

Separately confirmed: `.env.local-test`'s `AI_INDEX_MODEL=gemini-flash-lite-latest`
is **not even this pipeline's own production default** — `.env`/`docker-compose.yml`
default to `gemini-2.5-flash`, a real step up. Much of this investigation's testing
happened against the *weakest* tier Google offers, one step above the eventual
qwen3:8b target, not the pipeline's actual intended baseline.

### 6.1 Three consolidation approaches considered

Given the model-consolidation problem (how to reconcile cross-chunk/cross-page
hierarchy without a bigger model), three architectural approaches were compared:

1. **Boundary-only reconciliation** — a small, separate AI call at each chunk
   seam only, deciding which open ancestor a chunk's first few orphaned headings
   belong under. Cheap, narrow, but doesn't touch within-chunk ordering bugs.
2. **One consolidation pass over the whole condensed outline** — keep per-chunk
   extraction as-is, then run one more call at the end reconsidering the *entire*
   outline (headings only, not raw text) holistically. Mirrors what worked in §5's
   manual exercise. Risk: even a condensed outline can grow long for a big
   document, and the model must *re-emit* the whole corrected structure — exactly
   the kind of long, error-prone output a small model struggles with.
3. **Running outline, rewritten every chunk** — carry the full outline-so-far
   forward and have the model re-emit it, corrected, at every single chunk.
   Catches problems earliest, but the output (and cost) grows every chunk, and
   every call risks the model garbling something it was only meant to copy
   unchanged.

**Chosen direction (Approach 1, generalized):** replace the *mechanical* resolver
with narrow, per-heading AI micro-decisions — many small, bounded, low-stakes
questions instead of one large, high-stakes one. The reasoning: a genuinely small
model's core weakness is holding/re-emitting long structure, not answering a single
bounded multiple-choice question — so keep every individual ask as small and
low-risk as possible, and never ask it to hold the whole document (or even the
whole outline) in one completion.

---

## 7. Per-heading AI parent resolution — implementation and verification

### 7.1 Design

New code in `ai_reference_index.py`:
- `_build_parent_choice_prompt(heading_text, candidate_labels)` — builds a small,
  bounded multiple-choice prompt: "given the currently-open ancestor chain, which
  one does this heading belong under, or is it top-level?"
- `_parse_parent_choice_answer(text, num_candidates)` — parses a single-letter
  answer back to a depth; returns `None` on anything unparseable.
- `_resolve_parent_via_ai(heading_text, candidate_labels, provider_config)` — calls
  the model via the *same* provider abstraction the main extraction call already
  uses (`ai_indexing.providers.get_provider`), so it works with whatever model is
  configured (Gemini today, qwen3:8b once wired up) with zero pipeline-specific
  code.
- Wired into `_ground_and_resolve_chunk`'s per-heading loop, alongside a new
  `sim_stack_labels: List[str]` tracked in parallel to the existing
  `sim_stack_len`/`chunk_heading_depths` simulation.
- New engine_params flag, `ai_index_resolve_parent_via_ai` (default `False`),
  mirroring the precedent set by `ai_index_pleading_points_via_ai`.

### 7.2 Round 1 — narrow trigger: no effect

**Design:** only call the AI micro-decision when `_resolve_depth_for_heading`
itself flags uncertainty (`warn` set — a fuzzy match or an unresolved
`parent_ref`).

**Live result:** zero effect. Every heading in the verification run resolved with
`warn=None`. The confirmed real bug (§4.1) happens via a *bare `level`-only guess
with no `parent_ref` at all* — a path that **always** returns `warn=None`, since
"no parent_ref given" was never treated as a flagged/uncertain case. The apparent
"fix" in one earlier test run was pure model-output luck (the model happened to
supply a resolvable `parent_ref` that call), not the fix doing anything.

### 7.3 Round 2 — widened trigger: real coverage, but reveals position bias

**Design change:** trigger on *any* resolution that would **evict a currently-open
ancestor** (`depth < currently-open count`), regardless of whether `parent_ref` was
given — this is the actually risky operation (irreversible for everything nested
under what gets closed), not "did the resolver already know it was guessing." One
exemption kept: an exact match against an opaque, resolver-minted `carry_ref_map`
id is never second-guessed (the single most reliable signal available).

**Live result:** the widened trigger now correctly fires on every case that
matters. But the results got *worse* on this run, and the raw per-heading log
revealed why: the model answered the **identical shape of decision inconsistently
across calls** — e.g. one heading correctly resolved as "child of Challenge to the
Arbitral Award," and the very next heading, given the *same two candidates*,
answered "top-level" instead (evicting everything). This is consistent with the
well-documented first-option position bias in LLM multiple-choice answers — and
the original prompt listed "Top-level" as option A, meaning any such bias pushed
the model toward the single worst, hardest-to-undo answer whenever it was unsure.

### 7.4 Round 3 — position-bias fix

**Fix:** reordered `_build_parent_choice_prompt`'s options to list the innermost
(most likely correct, safest-to-default-to) candidate **first**, with "top-level"
(the riskiest, least-reversible choice) listed **last**.

**Live result:** real, measurable improvement — of the four headline entities
(JUDGMENT, FACTS, "Challenge to the Arbitral Award", "Respondent's case"), **3 of 4
now resolved correctly**, versus 0–1 in every prior run. One residual bug
discovered in the same run: "Challenge to the Arbitral Award" still landed one
level too deep (child of FACTS instead of FACTS's sibling) — even though the
model's own *relative* judgment ("nest this under JUDGMENT") was correct. Traced to
a **newly discovered structural bug**: `_ground_and_resolve_chunk`'s own simulated
stack (`chunk_heading_depths`/`sim_stack_labels` — used both to resolve same-chunk
`parent_ref`s and to build the AI micro-decision's candidate list) can drift from
the *real* `nested_stack` that `apply_heading_events_to_scope` maintains when
actually persisting events page-by-page. The AI's candidate list was built from a
simulation that no longer matched reality, so even a semantically correct answer
produced an absolutely wrong depth. Not yet fixed — flagged as the next concrete
lead if this line of work resumes.

### 7.5 Consistency check across repeated trials

Given the position-bias fix's apparent success on one run, the user explicitly
asked for repeated trials before drawing conclusions. Four completed real trials
(each a fresh duplicate of `9c3dc6e6`, deleted after checking) scored, out of the
same 4 headline entities:

| Trial | Score | What was wrong |
|---|---|---|
| 1 | 3/4 | "Challenge to the Arbitral Award" nested under FACTS |
| 2 | 3/4 | "Respondent's case" nested under Challenge |
| 3 | 2/4 | Challenge under FACTS again; Respondent's case missing entirely (a recall drop, unrelated to this fix) |
| 4 | 1/4 | Only JUDGMENT correct — FACTS drifted under an unrelated element, Challenge reverted to root, Respondent's case nested wrong |

A 5th attempt was lost to an unrelated host-level OOM kill (Docker Desktop juggling
several concurrent local projects at once — the container's `unless-stopped`
restart policy brought it back cleanly; this was infrastructure noise, not a bug in
the fix or the test harness).

**Honest conclusion:** real, measurable improvement over the pre-fix baseline
(which was consistently 0–1/4, never better) — but nowhere near reliable. No trial
came back clean, the spread runs 3/4 down to 1/4 with no obvious pattern to which
entity fails each time, and the underlying model does not answer the same narrow
question consistently across calls. Since qwen3:8b is expected to be no more
capable than `gemini-flash-lite-latest`, this is treated as real evidence against
the core premise the per-heading approach was built on ("narrow, bounded questions
are the failure mode small models handle reliably"), not just an unlucky sample.

---

## 8. Current status

**Shipped and deployed, default ON (part of the normal AI-indexing path):**
- Roman/letter ambiguity fix (§2.1)
- Paren+period regex widening (§2.2)
- Sequential-letter/decimal-ground nesting fix (§2.3)
- Sibling-chaining safety net, code + prompt (§2.4)
- Cross-heading paragraph-numbering prompt guidance (§2.5)
- Colon/dash/arrow list-introducing-line prompt heuristic (§2.6)

**Shipped, but OFF by default (experimental, gated behind `engine_params`):**
- `ai_index_resolve_parent_via_ai` — the per-heading AI micro-decision resolver
  (§7). Confirmed to help on average but not reliable enough to default on yet.

**Known open gaps, not yet fixed:**
- Paragraphs 2/3 (each section's own opening numbered sentence) still dropped by
  the extraction step entirely — a recall problem, not a hierarchy problem (§3).
- The simulated-stack-vs-real-stack divergence found in §7.4.
- The Round-14 full-document audit of `bb417471` (78/100) still has unscoped
  recall gaps and 2 false positives, not re-audited against any of the fixes
  above.
- Not yet tested against qwen3:8b directly — only against Gemini flash-lite as a
  stand-in.

**Temporary scaffolding still in the codebase, not yet cleaned up:**
- `_TEMP_DEBUG_RAW_HEADINGS` / `AI_INDEX_DEBUG_RAW_HEADINGS` env var
  (`ai_reference_index.py`) — added mid-investigation to trace raw
  `parent_ref`/`level` values and AI micro-decision inputs/outputs. Env-gated,
  off by default, harmless to leave, but should be removed once this line of work
  concludes.
- `scripts/test_ai_parent_resolution.py` — the repeated-trial verification harness
  used in §7.5 (duplicates a job, enables the flag, reindexes, scores the 4
  headline entities, cleans up after itself).

---

## 9. Infrastructure notes learned along the way

- The container's uvicorn process runs without `--reload` — `docker cp`-ing
  updated `.py` files into a *running* container has zero effect until the
  process restarts (`docker restart <container>`, or a full
  `docker compose up -d --build` to pick up a port/config change too).
- Git Bash (MSYS) mangles bare absolute-Unix-path arguments passed directly to
  `docker exec`/`docker cp` (e.g. `/app/scripts` silently becomes
  `C:/Program Files/Git/app/scripts`) — always wrap the remote command in
  `sh -c "..."`.
- Gemini's free tier has been tightened significantly through 2026 (quota cuts
  since Dec 2025, Pro-tier models removed from the free tier entirely as of
  April 2026) — 503 "high demand" and 429 "quota exceeded" errors on
  `gemini-flash-latest`/`gemini-flash-lite-latest` are a real, widely-reported,
  currently-active issue, not specific to this account or project.
- `docker-compose.local-test.yml` maps the CPU app container to host port
  **12016**, not 12006 — 12006 was reassigned after it was found occupied by an
  unrelated project's own test container (`tender_ai_backend_test`) running
  concurrently on the same machine.
- Running multiple full-document reindex trials back-to-back in one long-lived
  Python process appeared to correlate with container kills (exit 137); this
  turned out to be general host-level memory pressure from several concurrent
  Docker projects (this project, `tender_ai`, `gpu-ocr-service`), not a leak
  specific to the test harness — running one trial per fresh process avoided it.

---

## 10. Final Resolution: Deterministic & Model-Agnostic Stabilization

Following the findings in §7 and §8, relying on repeated LLM micro-decisions for
hierarchy resolution was replaced with robust, document-agnostic architectural and
stack-machine rules. This eliminated prompt variance, position bias, and LLM rate-limit
pressure while ensuring compatibility with both small local models (`qwen3:8b`) and
cloud endpoints.

### 10.1 Root Container Protection (`reference_index.py` & `ai_reference_index.py`)
- **Problem:** Sub-headings like "Challenge to the Arbitral Award.", "FACTS", and
  "Respondent's case" frequently arrived with `depth=0` or no `parent_ref`, causing
  them to evict the root document container (`JUDGMENT`) and splinter the document tree.
- **Fix:** Added `is_subdoc_root_candidate(text, family)` recognizing genuine document
  boundaries (`SUBDOC_TRIGGER_RE`, `EXHIBIT`, `ATTACHMENT`, `ADDENDUM`, `AGREEMENT`,
  `TENDER`, `SPECIFICATION`, `BOQ`, `MEMO OF PARTIES`, etc.). In both simulated stack
  resolution and `apply_heading_events_to_scope`, whenever a root container (depth 0)
  is open, non-root subheadings are clamped to `depth=1` instead of evicting the root.

### 10.2 Section-Opening Numbered Paragraph Recovery (`ai_indexing/prompt.py`)
- **Problem:** Opening sentences like "2. Petitioner invited..." (under FACTS) and
  "3. The Arbitral Award has been challenged..." (under Challenge) were dropped due
  to prompt exclusions against sentences with finite verbs. Consequently, `point:3`
  was never minted, leaving `(A)`–`(O)` orphaned.
- **Fix:** Added explicit guidance to `PLEADING_POINTS_SECTION` instructing the model
  to retain section-opening numbered paragraphs as structural anchors, exempting them
  from the noun-phrase/finite-verb filter.

### 10.3 Sequential Letter Nesting via `last_decimal_ground` (`reference_index.py`)
- **Problem:** Sequential letters `(A)`–`(O)` need to nest under their introducing
  numbered paragraph (`point:3`) rather than sitting flat under the section container.
- **Fix:** Widened `last_decimal_ground` effective-stack inheritance to include
  sequential letters (`is_seq_letter or _is_roman(number_raw)`). With `point:3` now
  minted, all 15 letters `(A)`–`(O)` automatically nest directly under `point:3`.

### 10.4 Integer Sequence Resumption (`reference_index.py`)
- **Problem:** Paragraphs 20–22 on Page 13 nested inside sub-clause "5. Award of amounts..."
  instead of resuming as siblings of paragraphs 18 and 19 under "Conclusion".
- **Fix:** Added integer sequence continuity tracking in `apply_heading_events_to_scope`:
  when an incoming heading is single integer $N$ and an ancestor was integer $P$
  ($P < N \le P + 3$, e.g., $P=19$, $N=20$), the inner sub-structure is closed, and
  the node resumes at $P$'s depth as a sibling.

### 10.5 Page Margin Stamp Suppression (`ai_indexing/schema.py`)
- **Problem:** OCR artifacts at page boundaries (e.g. `"-:12:-"`, `"-:7:-"`, `"-:-10:-"`)
  were mis-minted as numbered headings.
- **Fix:** Added `_SHEET_PAGE_STAMP_RE` in `normalize_chunk` to detect and filter out
  margin stamp numbers across all document processing.

### 10.6 Verification Results
- **Unit Test Suite:** All 278 test cases passing cleanly (`278 passed in 26.08s`).
- **Live Re-run (Job `9c3dc6e6` duplicate, 17 pages):**
  - `point:2` on Page 3 and `point:3` on Page 4 successfully captured.
  - All 15 lettered grounds `(A)` through `(O)` nested directly under `point:3` (`scope='.../point:3'`).
  - Paragraphs 20–22 on Page 13 correctly scoped as siblings under `Conclusion`.
  - Zero page stamps mis-minted.
  - Document-agnostic rules confirmed functional without document-specific hardcoding.
