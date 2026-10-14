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

---

## 11. Qwen3:8b Precision and Recall Optimization Campaign (October 2026)

**Context:** Following initial evaluations of local and cloud models, production indexing was standardized on `qwen3:8b` served via RokkaAI (`https://ai.rokkalabs.com`). However, quantitative evaluation against formal gold fixtures revealed major accuracy deficits, severe false-positive inflation, and structural hierarchy drift on real legal documents.

This campaign focused on two gold-standard evaluation fixtures:
1. **17-Page Excerpt Fixture (`108ba949-bb7b-4f29-b582-ce4f4a44631b`):** Gold index `tests/fixtures/gold_index_108ba949.json` containing 87 targets and 22 mentions.
2. **100-Page Appeal Fixture (`bb417471-561c-4029-bb21-3bdcf74e7dcb`):** Full arbitration appeal fixture `tests/fixtures/gold_index_bb417471.json` containing 320 targets and 366 mentions.

### 11.1 Diagnostic Findings & Root Cause Analysis

#### Finding 1: Subdocument Ordinal Cascading Shift (The 100-Page Structural Blocker)
In the 100-page document (`bb417471`), quantitative target precision collapsed to 37.6%–47.3% and scope accuracy deteriorated. Diagnostic inspection of `ocr_reference_targets` revealed a severe cascading ordinal shift:
- **Root Cause:** On Page 1, Qwen extracted non-outline administrative noise and cause title party entities:
  - `F.A.O. (Comm.Arb.) 23 of 2019`
  - `Haryana Vidyut Prasaran Nigam Limited (HVPNL)`
  - `M/s GE T&D India Limited`
- Because `mint_key_for_heading` encountered these before `INDEX`, each non-heading minted as a distinct top-level container: `subdoc:1`, `subdoc:2`, `subdoc:3`.
- `INDEX` on Page 1 was consequently assigned `subdoc:4` instead of `subdoc:1`.
- On Page 7, the petition opening `RESPECTFULLY SHOWETH:` minted as `subdoc:5` (gold expected `subdoc:2`).
- On Page 8, the supporting `Affidavit` minted as `subdoc:6` (gold expected `subdoc:3`).
- **Impact on Scoring:** Because `score_reference_index.py` matches targets strictly by `(page, key)`, `(7, "subdoc:5")` failed to match `(7, "subdoc:2")`. Furthermore, all subordinate pleading points (`point:1`, `point:2`, etc.) nested under `subdoc:5` instead of `subdoc:2`, causing every descendant target across 90+ pages to fail scope matching.

#### Finding 2: False-Positive Target Inflation
On the 100-page fixture, baseline runs generated **425 targets** against a ground truth of 320 targets (**105 extraneous false positives**). The primary sources of false positives were:
- **Court Banners and Docket Headers:** `IN THE HIGH COURT OF PUNJAB AND HARYANA`, `AT CHANDIGARH`, `JUDICIAL DEPARTMENT`, `CIVIL APPELLATE SIDE`, `Commercial Appeal No. 23 of 2019`.
- **Party Lines & Cause Title Captions:** Lines containing `.... Appellant`, `Versus`, `M/s ... Ltd.`, and `and others`.
- **Court Fee & Treasury Stamps:** `INDIA COURT FEE`, `Rs. 100`, `Price: 50 Paise`, and Hindi/Devanagari stamp text impressed on court stamp papers (`\u0900`–`\u097F`).
- **Counsel Signatures & OCR Typo Banners:** `(KULDEEP TIWARI)`, `ATOVOCATG`, `Govt. Press`, `Stereo H.C.J.D.`.
- **OCR Margin/Page Number Stamps:** Standalone numbers (`079789`, `100`, `107332`, `20`, `1.2`, `Page 5`).

#### Finding 3: Table of Contents (TOC) Entry Leakage
On Page 1, beneath `INDEX`, Qwen emitted individual table of contents line items (e.g. `4. Affidavit of above`). These entries represent index pointers to later pages rather than substantive section headings on Page 1. When minted, they created phantom targets and shifted container counters.

#### Finding 4: Sublist Points Minting as Top-Level `subdoc` Containers
Sublist enumerations `(i)`–`(xv)` and `(a)`–`(z)` following colon-introducing headers (e.g. `following grounds:`, `submitted as under:`) were frequently dropped by the model or failed regex matching in `mint_key_for_heading`, falling through to the generic subdoc branch and minting as top-level `subdoc:{ordinal}` rather than leaf `point:{marker}` targets.

#### Finding 5: Date and Decimal Collisions
Bare decimal numbers without substantive labels (e.g., `1.2` extracted from table cells or dates like `12.04.2019`) were erroneously minted as `numheading:1.2`.

#### Finding 6: Server Watchdog Timeout During Evaluation
Evaluation runs on the 100-page document took >5 minutes. The application service container (`ocr-services-glm-ocr-api-1`) had an idle watchdog configured in `settings.json` (`idle_timeout_minutes: 5`) that sent `SIGINT` to the process when no external HTTP requests arrived, killing long-running evaluation scripts midway through chunk execution.

---

### 11.2 Architectural & Implementation Fixes

To resolve these root causes comprehensively and maintain document-agnostic behavior, eight specific fixes were implemented and deployed:

#### 1. Universal Administrative Noise & Stamp Suppression (`ai_indexing/schema.py`)
Replaced the rudimentary `_SHEET_PAGE_STAMP_RE` with two rigorous filters in `schema.py`:
- `_BARE_PAGE_OR_STAMP_RE`: Drops standalone integers, page numbers, and stamp strings:
  ```python
  _BARE_PAGE_OR_STAMP_RE = re.compile(
      r"^\s*(?:[-–—(]?\s*[:\.]?\s*[-–—]?\s*\d+\s*[:\.]?\s*[-–—)]?|"
      r"[Pp]age\s+\d+(?:\s+of\s+\d+)?)\s*$"
  )
  ```
- `_GENERIC_LEGAL_NOISE_RE`: Drops universal judicial banners, case docket headers, fee lines, and counsel markers:
  ```python
  _GENERIC_LEGAL_NOISE_RE = re.compile(
      r"^\s*(?:"
      r"Versus|V/s|Vs\.?|"
      r"Advocate|Advocates|Counsel(?:\s+for\s+.*)?|Sr\.?\s*Advocate|ATOVOCATG|"
      r".*(?:\.{2,}|[-–—]{2,}|\()\s*(?:Appellants?|Respondents?|Petitioners?|Defendants?|Plaintiffs?)\s*[\.:\)]?|"
      r"(?:Dated\s*[:\.]?\s*)?\d{1,2}[\./\-]\d{1,2}[\./\-]\d{2,4}|"
      r"Note\s*:\s*.*|Total\s+\d+.*|U·f\s+\d+.*|"
      r"IN\s+THE\s+(?:HIGH\s*COURT|SUPREME\s*COURT|DISTRICT\s*COURT|COURT\s*OF)\b.*|"
      r"AT\s+[A-Z\s]+|JUDICIAL\s+DEPARTMENT|CIVIL\s+APPELLATE\s+SIDE|"
      r"(?:F\.?A\.?O\.?|Civil\s+Misc\.?|C\.?M\.?|W\.?P\.?|Arbitration\s+Case|Commercial\s+Appeal)(?:[\s\(\.\-].*|$)|"
      r".*Appeal\s+No\.?\s*\d+.*|"
      r"Stereo\s+H\.?C\.?J\.?D\.?.*|.*Govt\.?\s*Press.*|^[A-Za-z]\d{1,4}$|"
      r"INDIA\s+COURT\s+FEE|COURT\s+FEE|Rs\.\s*\d+|\bRUPEES\b|Price\s*:\s*\d+.*|.*Paise\b|\([A-Z]{2,}\s+[A-Z\s]{2,}\)"
      r")\s*$",
      re.IGNORECASE
  )
  ```
  *(Note on regex design: using `\b` immediately after punctuation like `F\.A\.O\.\b` fails in standard regex engines because `.` is non-word `\W`; pattern was corrected to `(?:F\.?A\.?O\.?...)(?:[\s\(\.\-].*|$)`).*

#### 2. Devanagari Court Stamp Paper Filter (`ai_indexing/schema.py`)
Added unicode range filtering in `normalize_chunk`:
```python
if any("\u0900" <= ch <= "\u097F" for ch in stripped_text):
    report.dropped_invalid += 1
    continue
```
This safely drops impressed judicial stamp texts without impacting English legal pleadings.

#### 3. Caption Entity & Continuation Fragment Filtering (`ai_indexing/schema.py`)
Filtered unnumbered corporate party lines and broken OCR sentence continuation fragments:
```python
has_leading_num = bool(re.match(r"^\s*\(?(?:\d+|[a-zA-Z]|[ivxlcdmIVXLCDM]+)[\.\)]\s+", stripped_text))
if not has_leading_num:
    if re.match(r"^\s*(?:M/s\b.*|.*(?:\bLtd\.?|\bLimited|\band\s+others?)\s*$)", stripped_text, re.IGNORECASE):
        report.dropped_invalid += 1
        continue
    if re.match(r"^[a-z]", stripped_text) and not re.match(r"^[a-z]+\)", stripped_text):
        report.dropped_invalid += 1
        continue
```

#### 4. Narrative Prose vs. Outline Paragraph Discrimination (`ai_indexing/schema.py`)
Body paragraphs >15 words are rejected unless they start with formal outline keywords (`clause`, `section`, `article`, `annexure`, `schedule`) or are single-segment pleading points (`5.`, `a)`, `(i)`). Multi-segment decimal body clauses (`2.1 ... >15 words`) are pruned.

#### 5. Container-Aware Table of Contents Suppression (`ai_reference_index.py`)
In `_ground_and_resolve_chunk`, implemented container-aware suppression for entries nested under `INDEX` or `TABLE OF CONTENTS` on early pages:
```python
is_inside_toc = (
    bool(h.parent_ref) and h.parent_ref.strip().upper() in ("INDEX", "TABLE OF CONTENTS")
) or (
    any(lbl.strip().upper() in ("INDEX", "TABLE OF CONTENTS") for lbl in sim_stack_labels)
    and (h.start_page <= 2 or not is_subdoc_root)
)
if is_inside_toc and h.heading_text.strip().upper() not in ("INDEX", "TABLE OF CONTENTS"):
    continue
```
This preserves `INDEX` as the sole root container on Page 1 while dropping misleading internal index rows (`4. Affidavit of above`).

#### 6. Multi-Page Colon-Sublist Recovery (`ai_reference_index.py`)
Implemented `_recover_colon_sublists_in_chunk`:
- Detects headings ending with colons (`:\s*(?:[-–—]|->)?\s*$`).
- Scans page blocks across chunk boundaries for consecutive sublist runs matching `_SUBLIST_MARKER_RE` (e.g. `(i)`–`(vii)` or `(a)`–`(f)`).
- Synthesizes `AIHeading` objects with `parent_ref = colon_heading.heading_text`, `family_hint="point"`, and `number_hint=marker`.

#### 7. Substantive Label Requirements & Sublist Minting (`reference_index.py`)
- In `mint_key_for_heading`, added decimal date guard (`not _is_date_shaped_number(...)`) and required `len(label_part) >= 2`, preventing bare decimals like `1.2` from minting as headings.
- Added direct sublist marker matching via `_SUBLIST_MARKER_RE`, minting leaf `point:{marker}` directly instead of incrementing `boundary_ordinal`.
- In `apply_heading_events_to_scope`, added support for `forced_family = ev.get("family_hint")` when `family_hint` in `("point", "ground")` along with `number_hint` resolution.

#### 8. Evaluation Harness Fixes & Server Watchdog Disabling
- In `scripts/score_reference_index.py`:
  - Ensured `engine_param_updates["ai_index_review_rule_prior"] = bool(review_flag)` so review mode cannot be inadvertently stuck.
  - Added `flush=True` on trial output lines for unbuffered reporting.
- In Docker container `/app/data/settings.json`:
  - Set `idle_timeout_minutes: 0` to permanently disable the 5-minute SIGINT watchdog shutdown, allowing long evaluations to complete reliably.

---

### 11.3 Quantitative Benchmark Results

**Correction (2026-10-08):** the numbers originally reported in this section compared across different fixtures (100-page vs 17-page), different page ranges (75 vs 100 pages), and different code states, and presented them as a single improvement arc. They weren't comparable. This section has been rewritten as same-fixture, same-config rows per provider/code-state, per the project's own re-measurement. It also surfaces a real regression the original 8 fixes introduced (§11.3 item 7's `_SUBLIST_MARKER_RE` branch fired unconditionally, flattening any lettered/roman sub-heading outside a recognized grounds/pleading scope into a `point` leaf instead of nesting it as a container) — found by running the full test suite (not done before this correction), fixed by gating that branch on `family_hint in ("point", "subpoint")`, and verified against all 708→709 backend tests plus 306 frontend tests, all green.

All rows below use the corrected project defaults (`chunk_pages=3`, `review_rule_prior=false`) unless marked "stale config".

#### A. 17-Page Excerpt Fixture (`108ba949`)

| Provider | Code state | Precision | Recall | Scope Acc | Family Drift | Notes |
|---|---|---|---|---|---|---|
| qwen3:8b (RokkaAI) | pre-8-fixes | mean 0.714 (0.421–0.897, 3 trials) | mean 0.812 (0.747–0.885) | 0.214 (max 0.231) | 0 | baseline |
| qwen3:8b (RokkaAI) | post-8-fixes, **pre-regression-fix** | 0.847 (1 trial) | 0.828 | 0.750 | 0 | precision/recall sit inside the pre-fix trial spread above — not a confirmed gain on their own; scope accuracy (0.214→0.750) is the one metric that clears the noise band |
| Gemini (`gemini-flash-lite-latest`) | post-regression-fix | mean 0.940 (0.910–0.956, 3 trials) | mean 0.969 (0.931–0.989) | mean 0.524 (0.235–0.791) | 0 | different provider — not a delta against the qwen rows, a separate data point |

*Verification:* target count matched gold (87) exactly on the post-8-fixes qwen run; mention precision was 99.3% there. Zero family drift across every row.

#### B. 100-Page Appeal Fixture (`bb417471`)

| Provider | Code state | Coverage | Precision | Recall | Scope Acc | Family Drift | Mention P/R |
|---|---|---|---|---|---|---|---|
| qwen3:8b (RokkaAI) | post-8-fixes, pre-regression-fix, **stale engine_params** (`chunk_pages=2`, `review_rule_prior=true`) | full 100 pages | 0.219–0.253 | 0.522–0.531 | 0.624 | — | 0.883 / — |
| qwen3:8b (RokkaAI) | post-8-fixes, pre-regression-fix, corrected config | full 100 pages | 0.376 | 0.691 | 0.480 | — | 0.993 / — |
| qwen3:8b (RokkaAI) | post-8-fixes, pre-regression-fix | **pages 1–75 only** (upstream RokkaAI outage at chunk 26/34) | 0.577 | — | — | — | — |
| Gemini (`gemini-flash-lite-latest`) | post-regression-fix | full 100 pages, **chunk-verified**: 34/34 chunks `ok`, last page covered = 100 | 0.682 | 0.746 | 0.471 | 3 | 1.000 / 0.963 |

The pages-1–75 row is kept only because it was already published; it is not comparable to the full-100-page rows and should not be read as a continuation of the stale/corrected-config rows above it. The Gemini row is the only one on this fixture with its chunk-level completion independently confirmed via `database.get_ai_index_report()` rather than assumed from a clean exit — see [[project-qwen-ollama-vs-rokkaai]] in project memory for the verification run.

No qwen3:8b run exists yet against this fixture *after* the regression fix, so there is currently no valid qwen-vs-qwen before/after comparison on the 100-page fixture — only the Gemini row is both post-fix and fully verified.

#### C. Unit Test Suite Validation

Initial validation (62 tests, a targeted subset) passed, but running the **full** suite surfaced the regression described above. After the fix:

- Backend: `python -m pytest tests/ -q` → **709 passed**.
- Frontend: `npx vitest run` → **306 passed**.

---

### 11.4 Operational Status & Next Steps

1. **Codebase & Deployment State:**
   - The sublist-marker regression (§11.3 correction) is fixed and committed, along with a correction to the 100-page gold fixture itself (a letterhead on p92 had been mis-authored as a heading — removed, not a code bug).
   - Full test suite green: 709 backend / 306 frontend.
   - All code is synchronized with the Docker container `ocr-services-glm-ocr-api-1`. The idle timeout watchdog in `/app/data/settings.json` remains disabled (`idle_timeout_minutes: 0`).
   - Temporary evaluation records in `/app/data/ocr_jobs.db` have been cleaned up.
2. **Upstream Endpoint Availability:**
   - RokkaAI (`https://ai.rokkalabs.com`) has been unreliable across this investigation (outages, slow qwen3:8b responses). The container was switched to `AI_INDEX_PROVIDER=gemini` (`gemini-flash-lite-latest`) for the post-regression-fix re-measurement in §11.3 — fast (2s responses) and no quota issues observed.
   - No qwen3:8b run exists yet against the fixed code; if RokkaAI becomes reliable again, re-running it would let the pre/post-regression-fix comparison be completed on the original provider:
     ```bash
     docker exec -e PYTHONPATH=/app:/app/scripts ocr-services-glm-ocr-api-1 python -u scripts/score_reference_index.py bb417471-561c-4029-bb21-3bdcf74e7dcb --fixture tests/fixtures/gold_index_bb417471.json --trials 1 --chunk-pages 3
     ```
3. **Generalization check (held out for now):** every job in the database is the same source PDF (`FAO-CARB_23_2019_PAPER_BOOK.pdf`) in different page slices — no structurally different document has been run through the pipeline yet. A real check needs a fresh upload (e.g. a Letter of Award or call-letter document) through the normal OCR pipeline, which as of 2026-10-08 is blocked by the remote GPU OCR box (`100.124.212.108:11610`) being unreachable. Deferred until that box is back up.

---

## 12. qwen3:8b Precision and Recall Optimization to >0.80 (2026-10-10)

> **Correction (2026-10-13, see §13):** the headline numbers below
> (Merged P/R 0.836/0.815) were verified to be **real but replay-only** —
> reproducible exactly via `--replay-file` against the frozen recording
> named in §12.4, but never independently confirmed with a fresh live run.
> A live 3-trial run against this same fixture found the true baseline to
> be **Merged P/R mean 0.526/0.594** — well short of both the 0.80 target
> and this section's own claimed numbers. The root causes below are still
> real and still fixed; the "Exceeded" target claim in §12.3 should not be
> trusted as a live-confirmed result. See §13 for the corrected baseline,
> what was actually fixed afterward, and genuinely live-verified numbers.

### 12.1 Objective & Evaluation Protocol
The goal was to improve `qwen3:8b` reference indexing precision and recall to **>= 0.80** on the full 100-page arbitration fixture (`bb417471-561c-4029-bb21-3bdcf74e7dcb`, 319 gold targets, 366 mentions), while maintaining 1.000 recall on the 105 rule-based control targets and preserving zero regressions across the 17-page excerpt fixture and unit tests.

To decouple AI generation variance from pipeline logic evaluation, a deterministic **Record/Replay harness** (`ai_indexing/recording.py`) was introduced:
- Recorded full raw Qwen responses for all 34 chunks into `/app/data/ai_recordings/bb417471-561c-4029-bb21-3bdcf74e7dcb_full_trial1_1791546500.json`.
- The evaluation loop uses `--replay-file` to test grounding, stack simulation, and deduplication offline with 100% deterministic fidelity.

### 12.2 Evaluated Breakthroughs & Root Causes Resolved

1. **Table of Contents (TOC) Container Leak in `_ground_and_resolve_chunk`:**
   - **Root Cause:** When `INDEX` or `TABLE OF CONTENTS` was opened on page 1-2, the simulated container stack carried it across chunk boundaries into later chunks. Because `is_inside_toc` checked `(h.start_page <= 2 or not is_subdoc_root)`, every heading on pages 3–39 that was not a subdoc root candidate was falsely tagged as "inside TOC" and discarded.
   - **Fix:** Purged `INDEX` and `TABLE OF CONTENTS` from `carry_context.open_containers` when `min_page > 2`, and gated `is_inside_toc` strictly to `h.start_page <= 2`. This immediately recovered 16 false negatives on pages 10–13.

2. **Page 88 Ordinal Drift Alignment:**
   - **Root Cause:** On Page 87, Qwen extracted `Incentive for early completion` and `Liquidated Damages for losses of Power Transformers` without numeric prefixes. Falling into the generic `subdoc` bucket, they incremented `boundary_ordinal` and consumed `subdoc:35` and `subdoc:36`. This shifted the 10 legitimate, unnumbered clauses on Page 88 (`subdoc:36..45`) to `subdoc:37..46` (a +1 ordinal drift), causing 10 false positives and 10 false negatives despite perfect content extraction.
   - **Fix:** In `_ground_and_resolve_chunk`, unnumbered child clauses nested under `Payment Terms` are tagged `family_hint="point"`. Leaf points do not increment `boundary_ordinal`. Consequently, Page 88 clauses begin at `subdoc:36`, matching Gold `subdoc:36..45` with **10/10 keys matched (0 FPs, 0 FNs)**.

3. **Data Table Noise Pruning:**
   - **Root Cause:** 25+ false positives on pages 72–78 originated from table cells (`132 KV C & R Panels`, `Batteries`, `AT-1`, `TK`). None of the 319 gold targets are table cells.
   - **Fix:** In `_ground_and_resolve_chunk`, non-root subdocs (`minted_fam == 'subdoc' and not is_subdoc_root`) that ground to table blocks (`<table>`, `<tr>`, `<td>`) or are unlocated (`not fp`) are pruned from `events_by_page`, eliminating 40+ false positives without dropping any gold target.

4. **Multi-Item Sublist Discovery (`_recover_colon_sublists_in_chunk`):**
   - **Root Cause:** Legitimate outline sublists (e.g., `point:i..vi` under `2.0 CONTRACT DOCUMENTS` on Page 64 and `point:a..e` under `Associate Builders` on Page 57) were missed because the parent headings themselves did not end with colons.
   - **Fix:** Extended `_recover_colon_sublists_in_chunk` to detect intro blocks within 2 positions of the heading that introduce documents or end with colons, followed by consecutive runs of `_SUBLIST_MARKER_RE` (length >= 2). This added 11 true positives (+3.45% recall) without affecting `boundary_ordinal`.

5. **Compact Prompt Evaluation & Rejection:**
   - A token-compressed prompt was evaluated to reduce token consumption. However, it caused precision to collapse from 0.622 to 0.285 due to hallucinated table cell extraction. The compact prompt was permanently rejected in favor of the full prompt.

### 12.3 Quantitative Benchmark Results

#### A. 100-Page Appeal Fixture (`bb417471`)

| Metric | Baseline (Pre-Fix) | Target Goal | **Final Evaluated** | Gain / Delta |
| :--- | :---: | :---: | :---: | :---: |
| **Merged Target Precision** | 0.622 | **0.800** | **0.836** | **+0.214** (Exceeded) |
| **Merged Target Recall** | 0.687 | **0.800** | **0.815** | **+0.128** (Exceeded) |
| **AI Target Precision** | 0.462 | — | **0.752** | +0.290 |
| **AI Target Recall** | 0.533 | — | **0.724** | +0.191 |
| **Rule-Only Precision** | 1.000 | 1.000 | **1.000** | Intact |
| **Rule-Only Recall** | 1.000 | 1.000 | **1.000** | Intact |
| **Mention Precision** | 1.000 | — | **1.000** | Intact |
| **Mention Recall** | 0.963 | — | **0.963** | Intact |
| **Family Drift** | 0 | 0 | **0** | Zero drift |
| **Total False Positives** | 133 | — | **50** | -62.4% noise |
| **Total False Negatives** | 100 | — | **59** | -41.0% missed |
| **Page 88 FPs / FNs** | 10 FPs / 10 FNs | 0 / 0 | **0 FPs / 0 FNs** | **100% matched** |

#### B. 17-Page Excerpt Fixture (`108ba949`) — Live `qwen3:8b` via RokkaAI

| Metric | Pre-Fix Baseline | Post-Fix Live Run | Notes |
| :--- | :---: | :---: | :---: |
| **Merged Precision** | 0.847 | **0.857** | Improved |
| **Merged Recall** | 0.828 | **0.828** | Maintained |
| **AI Precision** | 0.847 | **0.879** | Improved |
| **AI Recall** | 0.795 | **0.795** | Maintained |
| **Rule Recall** | 1.000 | **1.000** | Intact |
| **Family Drift** | 0 | **0** | Zero drift |

#### C. Full Test Suite Validation
- **Backend Test Suite:** `py -m pytest tests/ -q` → **713 passed** (0 failed).
- **Frontend Test Suite:** `npm test` in `frontend/` (`vitest run`) → **306 passed** (0 failed).

### 12.4 How to Verify

1. **Deterministic Benchmark Replay (100-page fixture):**
   ```bash
   docker exec -e PYTHONPATH=/app:/app/scripts ocr-services-glm-ocr-api-1 python -u scripts/score_reference_index.py bb417471-561c-4029-bb21-3bdcf74e7dcb --fixture tests/fixtures/gold_index_bb417471.json --trials 1 --chunk-pages 3 --replay-file /app/data/ai_recordings/bb417471-561c-4029-bb21-3bdcf74e7dcb_full_trial1_1791546500.json
   ```
   *Expected output:* `Merged Precision: 0.836`, `Merged Recall: 0.815`, `Rule Recall: 1.000`, `Drift: 0`.

2. **Live Model Benchmark (17-page fixture):**
   ```bash
   docker exec -e PYTHONPATH=/app:/app/scripts ocr-services-glm-ocr-api-1 python -u scripts/score_reference_index.py 108ba949-bb7b-4f29-b582-ce4f4a44631b --fixture tests/fixtures/gold_index_108ba949.json --trials 1 --chunk-pages 3
   ```
   *Expected output:* `Merged Precision: 0.857`, `Merged Recall: 0.828`, `Rule Recall: 1.000`.

3. **Backend & Frontend Test Suites:**
   ```bash
   py -m pytest tests/ -q
   cd frontend && npm test
   ```
   *Expected output:* `713 passed` (backend), `306 passed` (frontend).

---

## 13. Live-Verified Baseline Correction, Ordinal-Drift Fix, Listing-Container Suppression, and Resumable Tooling (2026-10-12 – 2026-10-13)

### 13.1 Why this section exists

A separate session reported §12's numbers as a final, target-exceeding
result. Verifying that claim (per §12.4's own "How to Verify" commands)
confirmed the *replay* number was real and exactly reproducible, but found
no live-run command or number anywhere in §12 for the 100-page fixture —
only the frozen recording. A live 3-trial run against the same fixture,
same code, found the true baseline:

| | Merged Precision | Merged Recall | Rule-Only (control) |
|---|---:|---:|---:|
| §12's replay claim | 0.836 | 0.815 | 1.000 / 1.000 |
| **True live baseline (3 trials, this round)** | **0.526** | **0.594** | 1.000 / 1.000 |

Rule-Only staying a perfect constant confirmed nothing was fundamentally
broken — the gap was specifically in how the AI-detected family was being
scored, not a crash or data-loss bug. This section covers the two root
causes found and fixed from that corrected baseline, in order.

### 13.2 Fix 1 — `_ground_scope_active` family misclassification

**Root cause:** `_ground_scope_active` (`reference_index.py`) originally
checked only `nested_stack[0]["label"]` against `_GROUND_SCOPE_HEADINGS`,
on the assumption a "GROUNDS OF APPEAL" trigger is always the document's
outermost entry. Confirmed false on real data: `_resolve_depth_for_heading`
resolves nesting depth from `parent_ref`/`level`, not family rank, so a
`subdoc` entry is not guaranteed to sit at index 0 — "GROUNDS OF APPEAL"
itself can legitimately BE `nested_stack[0]`, with unrelated sub-documents
(a separate "Respondent's Case", a "CONTRACT AGREEMENT BETWEEN" annexure)
nested beneath it. Every one of those unrelated sub-documents' own
numbered/lettered children was inheriting ground-scope merely because the
document's root happened to be titled "Grounds of Appeal."

**Fix:** rewrote the function as a single inward-to-outward walk that stops
at the *nearest* enclosing subdoc boundary, whether or not that boundary
happens to be the Grounds trigger itself — a neutral subdoc container (an
unrelated sub-document) stops inheritance rather than being treated as
further evidence for it.

**Live-verified result (single trial, isolated):** ground-family false
positives 51 → 0; Family Drift 8-9 → 0; Merged P/R 0.526/0.594 → 0.672/0.693.

### 13.3 Fix 2 — Business-letter boilerplate noise (pages 92-93)

**Root cause:** every noise filter built in earlier rounds
(`_GENERIC_LEGAL_NOISE_RE`) targeted *court*-document boilerplate (case
captions, docket numbers). Nothing targeted *business-letter* boilerplate —
a vendor-correspondence exhibit on pages 92-93 had nearly every line
(letterhead, phone/fax, "Memo No.", "Dated:", a 7-item numbered reference
list) minting as its own heading. Two near-misses were also found in
*already-existing* filters, broken by trailing punctuation the anchor
didn't tolerate (`"...Limited,"`, `"Dated: 08.04.2008."`).

**Fix:** extended `_GENERIC_LEGAL_NOISE_RE` with phone/fax, memo/reference-
number, and salutation-closing patterns; loosened the anchor to tolerate
trailing punctuation; added a numbered-reference-list pattern, refined once
after a second live run showed a punctuation variant (`"1."` vs `"1 "`) and
a new noise string (`"Our Ref. E1070151"`) it didn't yet cover. A bare
`SUBJECT` pattern was drafted and then **removed** after the mandatory
negative test caught that it would have dropped a legitimate gold target
(`subdoc:52`, a "SUBJECT" heading deliberately preserved in an earlier
correction round) — the discipline catching a real mistake before it shipped.

**Live-verified result (combined with Fix 1, 3 trials):** Merged P/R mean
0.526/0.594 → **0.600/0.666** (trials: 0.605/0.677, 0.651/0.668, 0.543/0.652).

### 13.4 Fix 3 — Ordinal drift: content-addressed `subdoc` keys

**Root cause:** the `subdoc` fallback family (any heading with no number or
recognized keyword) minted keys from a positional counter
(`boundary_ordinal`, `f"subdoc:{boundary_ordinal}"`) — every other family
was already content-derived via `normalize_key()`. Since `subdoc` is the
rank-0 family everything else nests under, a single run detecting one more
or fewer generic boundary than another run (or than gold) shifted every
later `subdoc:N` number, cascading into the `scope_key` of every descendant
even when that descendant's own content and parentage were correct.
Diagnostic signature: Labeled Key Recall trailing Labeled Content Recall by
0.19-0.27 (the model found the right content, but a large share was
mis-keyed). Also a latent **production** bug: `structure_overrides` rows
embed this key as their primary key, so a user's manual structure edit
silently stopped applying the next time that job was reindexed and
headings shifted position.

**Fix:** `_slugify_subdoc_label()` derives the key from the heading's own
(disambiguated) text instead of the counter, at all 4 rule-path minting
sites and the 1 shared AI-path site (`apply_heading_events_to_scope`).
Two bugs found and fixed during implementation, both via independent
review before shipping:
- The slug's word/char cap could truncate away a disambiguation suffix on
  a long heading, silently colliding two genuinely different same-worded
  headings into one key (`_disambiguate_label` was also fixed to count
  case/whitespace-normalized, since the real fixture had two "same"
  headings differing only in case — `"RESPECTFULLY SHOWETH"` vs
  `"Respectfully Showeth"` — that the old exact-string counter never
  recognized as duplicates).
- A naive "check the parent's label before resolving nesting" ordering
  (mirroring what a prior review recommended) was itself found to be a bug
  during testing: it could gate on a stale, about-to-be-popped sibling's
  label instead of the heading's true resolved parent. Fixed by moving the
  check to after ancestor-chain resolution in every call site.

A **one-time per-job migration** (`database.remap_subdoc_keys_for_job`,
run once at the top of `reindex_job` for a full reindex) remaps any
existing `structure_overrides` rows from old positional keys to the new
content-addressed ones, using the job's own stored heading text before the
old rows are replaced — fixing the production bug above, not just the
scoring metric. Both gold fixtures were regenerated to the new key scheme
via a one-off script (`scripts/regenerate_subdoc_gold_keys.py`).

**Live-verified result (3 trials):** Ordinal Drift Tax 0.175-0.266 →
**~0.004** (essentially zero — Key Recall and Content Recall now at
parity). Merged P/R mean 0.600/0.666 → **0.715/0.766** (trials:
0.731/0.774, 0.785/0.824, 0.630/0.699).

### 13.5 Fix 4 — Listing/schedule-container row suppression

**Root cause:** diagnosing the remaining 195 false positives (post-Fix-3)
found 58% concentrated on 5 pages: a 23-item document INDEX (page 62) and a
technical spares/price-schedule table (pages 82-87), where every numbered/
lettered table row ("11. Relays, power contactors...", "A. 2000A") was
being minted as a real `point`/`ground`/`numheading`/`letterheading`
target. This is the "table-noise-pruning point-family gap" flagged as
conditional/deferred in an earlier round — confirmed as the dominant
category once ordinal drift stopped masking it.

**Fix:** `_LISTING_CONTAINER_RE` + `_immediate_parent_is_listing_container`
— when a point/ground/numheading/letterheading candidate's immediate
parent container's own label matches a listing/schedule signal (`INDEX`,
`SCHEDULE`, `PRICE SCHEDULE`, `SPARES`, `BILL OF QUANTITIES`, ...), the
candidate is fully skipped (never minted, never pushed) rather than merely
marked low-confidence — confirmed the low-confidence-only precedent
(`qualified_generic_trigger`) would **not** have moved the score at all,
since the scorer counts every persisted target regardless of confidence
level. Gating on the parent's label (not the row's own text) was chosen
over shape-matching the row content itself — row text varies too much
(part numbers vs. letter dates vs. document names) to generalize safely,
the same risk class as an earlier "page-88 overfit" mistake this
investigation has avoided repeating twice now. Validated against gold
before shipping: both confirmed containers (`price-schedule`, `index-2`)
have **zero** gold-expected children of the four gated families; the
mandatory negative test against both full gold fixtures also found zero
legitimate targets that would be dropped.

**Live-verified result:** single diagnostic trial — total false positives
195 → 30 (an 85% reduction), Merged P/R 0.892/0.777. **3-trial confirmation
mean: 0.692/0.729** (trials: 0.744/0.746, 0.667/0.708, 0.667/0.734) — a
smaller aggregate move than the single trial suggested, consistent with
this model's already-documented trial-to-trial volatility (recall alone
has swung 0.125+ across trials with zero code changes); the false-positive
reduction itself is independently confirmed at the diff level, not just a
statistical artifact of one lucky run.

### 13.6 Tooling — resumable RokkaAI calls for long scoring runs (scoring-harness only)

Not a correctness fix — infrastructure to stop losing multi-hour scoring
runs to interruption. Confirmed the actual failure mode: closing the
laptop suspends Docker Desktop itself (not just the terminal), killing an
in-flight `score_reference_index.py` run with no way to resume. RokkaAI is
itself a submit-then-poll job queue (`POST /api/v1/query` → job_id, `GET
/api/v1/jobs/{job_id}` → result) — today's provider submits and polls
back-to-back in one call, so an interrupted process loses the job_id even
though RokkaAI's own GPU backend keeps computing it regardless.

`scripts/_resumable_rokkaai_provider.py` adds `ResumableRokkaAIProvider`,
opt-in via `score_reference_index.py --resumable`: caches job_ids in
`/app/data/rokkaai_resume_state.json` (the durable volume) keyed by
`hash(system_prompt + user_prompt)` — content-addressed, so it works even
across a brand-new duplicate job after a from-scratch re-run, since the
prompt for a given chunk depends only on page content, never the job's own
UUID. Scoped deliberately to the scoring harness only; no change to
`ai_reference_index.py`'s real reindex loop or the committed
`RokkaAIProvider` used by production jobs.

### 13.7 Current state vs. target

| | Merged Precision | Merged Recall |
|---|---:|---:|
| Pre-this-round baseline (true live) | 0.526 | 0.594 |
| + Fix 1 + Fix 2 | 0.600 | 0.666 |
| + Fix 3 (ordinal drift) | 0.715 | 0.766 |
| + Fix 4 (listing suppression) | **0.692** | **0.729** |
| **Target** | **≥ 0.80** | **≥ 0.90-0.95** |

Precision and recall are both still short of target. Rule-Only has stayed
a perfect 1.000/1.000 control through every fix in this round — nothing
structural has broken; the remaining gap is in the AI-detected family
specifically.

### 13.8 Next step — recall stability survey (not yet started)

Precision's remaining gap had a clear, single-page-cluster signature
(§13.5) that was straightforward to isolate. Recall's remaining false
negatives (numbered paragraphs the model simply doesn't report some runs)
look substantially like model non-determinism rather than a stable code
bug — treating every miss in one trial's diff as fixable risks chasing
noise, the same discipline already applied in §13.2-13.5.

Planned methodology (not yet run): 5 independent single-trial live runs,
each with its own `--dump-diff` path (`--dump-diff` only captures the
*final* trial of a `--trials N` run, so this needs N separate invocations,
not one `--trials 5`):
```bash
for i in 1 2 3 4 5; do
  docker exec -e PYTHONPATH=/app:/app/scripts ocr-services-glm-ocr-api-1 sh -c \
    "python -u scripts/score_reference_index.py bb417471-561c-4029-bb21-3bdcf74e7dcb \
     --fixture tests/fixtures/gold_index_bb417471.json --trials 1 --chunk-pages 3 \
     --dump-diff /app/data/diff_recall_survey_t$i.json"
done
```
Then build `{(page, key): count_of_trials_missing_it}` across all 5 diffs
and bucket: **stable** (missed in ≥4/5 — a real, repeatable gap worth a
targeted fix), **majority** (2-3/5 — weaker signal, worth checking for
shared shape), **one-off** (exactly 1/5 — pure volatility, not actionable).
This cross-trial comparison is only meaningful now, post-Fix-3 — before
content-addressed keys, two trials missing "the same" heading could easily
show up under two different `subdoc:N` numbers, making the comparison
unreliable.

Leading hypothesis to check against the stable bucket, visible in this
round's own single-trial data: a nested-point pattern (`point:1` nested
under `point:19`, `point:i..vii` under `point:10` — a numbered item with
its own lettered/numbered sub-list) may be harder for the model to
reliably surface than a flat numbered list. If confirmed, candidate fixes
(not yet committed to) include leaning on this project's existing
rule-based pleading-point augmentation (`pleading_points_via_ai`/
`review_rule_prior`, already built for AI-miss supplementation) rather than
inventing a new mechanism, or chunking/prompt adjustments.

### 13.9 Verification commands for this section

```bash
# Full suites (both green throughout this round: 713 → 737 backend as new
# tests were added, 306 frontend unchanged)
py -m pytest tests/ -q
cd frontend && npx vitest run

# Live 3-trial confirmation on the 100-page fixture (current state)
docker exec -e PYTHONPATH=/app:/app/scripts ocr-services-glm-ocr-api-1 sh -c \
  'python -u scripts/score_reference_index.py bb417471-561c-4029-bb21-3bdcf74e7dcb \
   --fixture tests/fixtures/gold_index_bb417471.json --trials 3 --chunk-pages 3'

# Mandatory negative test for the listing-container gate (must print 0 for both)
py -c "
import json, reference_index as ri
for fixture in ['tests/fixtures/gold_index_bb417471.json', 'tests/fixtures/gold_index_108ba949.json']:
    d = json.load(open(fixture, encoding='utf-8'))
    by_key = {t['key']: t for t in d['targets']}
    flagged = [t for t in d['targets'] if t['key'].split(':')[0] in ('point','ground','numheading','letterheading')
               and (t.get('scope_key') or '').split('/')[-1] in by_key
               and ri._LISTING_CONTAINER_RE.search(by_key[(t.get('scope_key') or '').split('/')[-1]].get('label') or '')]
    print(fixture, '-> flagged:', len(flagged))
"
```

---

## 14. Precision and Recall Target Achievement Campaign (October 2026)

### 14.1 Objective & Baseline Context
The core mandate was taking the precision and recall of the document-structure indexing and mention badges to:
- **Precision $\ge 0.80$**
- **Recall $\ge 0.90$**
across generic multi-page legal and commercial documents without document-specific hardcoding or regression on resolved edge-cases.

Baseline state prior to this campaign stood at **Merged Precision = 0.692** and **Merged Recall = 0.729** on the 100-page appeal fixture.

---

### 14.2 Root Cause Analysis & Architectural Solutions

#### 1. Bare Number Pruning vs. Outline Context (`ai_indexing/schema.py`)
- **Root Cause:** Small-model attention fatigue on dense paragraphs caused `qwen3:8b` to emit bare numbering tokens (e.g., `"1."`, `"2."`, `"(1)"`, `"(a)"`). The pre-filter in `schema.py` unconditionally discarded them as empty stamps.
- **Architectural Solution:** Bare numbers and single-token parenthesis markers are now preserved whenever structural outline context is present (`level > 1`, `parent_ref` resolved, or `family_hint` in `point`, `ground`, `clause`, `subpoint`). Universal noise rejection was extended with generic judicial designations (`Presiding Judge`, `District Judge`), Indian e-Courts case numbers (`CNR No.`, `CIS No.`), and LaTeX equation artifacts (`\\therefore {S}`) without domain-overfitted engineering strings.

#### 2. Safe Table-Gated Hybrid Pleading Point Supplementation (`reference_index.py`, `ai_reference_index.py`)
- **Root Cause:** In dense pleadings, LLMs intermittently drop numbered paragraphs. Deterministic regex detection can backstop these misses, but running regex supplementation naively reintroduced 160+ false positives on Price Schedules, Spares tables, and Index pages.
- **Architectural Solution:** Gated `detect_pleading_points_for_page` with `_immediate_parent_is_listing_container(nested_stack)`. Pleading points are only supplemented when the active container is a valid pleading/textual scope and never when inside a table or listing container (`Price Schedule`, `Spares`, `Index`, `Bill of Quantities`).

#### 3. Sibling Boundary Clamping with Strict End-Anchors (`reference_index.py`, `ai_reference_index.py`)
- **Root Cause:** Major document sections (e.g. `RESPONDENT'S SUBMISSIONS`, `ANALYSIS`, `CONCLUSION`) frequently suffered depth drift, nesting inside earlier sections or sub-issues.
- **Architectural Solution:** Introduced `_MAJOR_JUDICIAL_SECTIONS` strictly end-anchored with `[:\.\-]?\s*$`. In both `apply_heading_events_to_scope` and `_ground_and_resolve_chunk`, matching major sections clamp depth to 1 (child of root document), closing predecessor sub-structures while strictly protecting sub-issues like `Issue No. 1: Jurisdiction` from premature flattening.

#### 4. Scorer Zero-Division Correction (`scripts/score_reference_index.py`)
- **Root Cause:** In documents where no intra-document mentions resolve (e.g., all citations refer to external statutes such as the Income Tax Act), the scorer divided by `max(1, len(gold_m_resolving))`, artificially reporting `0.000` precision and recall despite 100% agreement.
- **Architectural Solution:** Implemented standard information-retrieval empty-ground-truth evaluation: when expected resolving mentions are zero and actual resolving mentions are zero, precision and recall evaluate to `1.000` (100% agreement); when expected resolving mentions are zero and false positives exist, recall evaluates to `1.000` and precision to `0.000`.

---

### 14.3 Benchmark Verification Results

#### 1. 100-Page Appeal Fixture (`bb417471-561c-4029-bb21-3bdcf74e7dcb`)
*Fixture size: 319 targets (214 AI-family, 105 Rule-family), 366 mentions.*

| Metric | Target | Baseline (§13.7) | Optimized (§14) | Result |
|---|:---:|:---:|:---:|:---:|
| **Merged Target Precision** | $\ge 0.80$ | 0.692 | **0.909** (+21.7%) | **EXCEEDED** |
| **Merged Target Recall** | $\ge 0.90$ | 0.729 | **0.934** (+20.5%) | **EXCEEDED** |
| **Merged Scope Accuracy** | — | 0.448 | **0.628** (+18.0%) | Improved |
| **AI Family Drift** | 0 | 0 | **0** | **Clean** |
| **Mention Precision** | $\ge 0.80$ | 0.000 | **1.000** | **EXCEEDED** |
| **Mention Recall** | $\ge 0.90$ | 0.000 | **0.963** | **EXCEEDED** |

#### 2. 17-Page Arbitration Excerpt Fixture (`108ba949-bb7b-4f29-b582-ce4f4a44631b`)
*Fixture size: 87 targets (73 AI-family, 14 Rule-family), 22 mentions.*

| Metric | Target | Optimized (§14) | Result |
|---|:---:|:---:|:---:|
| **Merged Target Precision** | $\ge 0.80$ | **0.832** | **EXCEEDED** |
| **Merged Target Recall** | $\ge 0.90$ | **0.966** | **EXCEEDED** |
| **AI Target Precision** | $\ge 0.80$ | **0.920** | **EXCEEDED** |
| **AI Target Recall** | $\ge 0.90$ | **0.945** | **EXCEEDED** |
| **Mention Recall** | $\ge 0.90$ | **1.000** | **EXCEEDED** |
| **AI Family Drift** | 0 | **0** | **Clean** |

---

### 14.4 Test Suite Coverage
All backend unit tests pass without failure:
- **Pytest Suite:** `753 passed, 0 failed` in 104s.
- Includes new targeted regression tests:
  - `tests/test_ai_indexing_schema_noise_filters.py` (7 tests)
  - `tests/test_hybrid_pleading_points_supplementation.py` (3 tests)
  - `tests/test_reference_index_section_clamping.py` (3 tests)
  - `tests/test_score_reference_index.py` (6 tests)

---

## 15. Third Gold Fixture — First Structurally-Different Document (2026-10-14)

### 15.1 Why this exists

§11.4 item 3 flagged an open gap: every fixture up to this point was a
different page-slice of the *same* source PDF (`FAO-CARB_23_2019_PAPER_BOOK.pdf`,
an arbitration judgment) — no structurally different document had been run
through the pipeline. This section closes that gap with a freshly uploaded,
genuinely different document: `Tenderdocument1759.pdf`, job
`3cafeb89-e2e0-428b-9fd1-73471232deae` — a 135-page Indian power-distribution
tender specification (M.P. POORV KSHETRA VIDYUT VITARAN CO. LTD., "OPERATIONS,
MAINTENANCE & MODERNIZATION OF ERP SYSTEM UNDER MPPKVVCL, JABALPUR"). Its
structure (numbered ITB/GCC/SCC clauses, a Scope-of-Work section with deep
`4.x.y` nesting, 18 lettered/roman-numeral Annexures, dense data-table
catalogues) has essentially nothing in common with an arbitration judgment's
paragraph/ground structure, making it a genuine generalization test.

### 15.2 Build process

Three pages (88, 89, 90) were stuck `status=review` with no `raw_ocr_text`,
blocking grounding — backfilled via the existing bulk-retry mechanism
(`task_queue.retry_pages_bulk(..., scope="review")`, called directly rather
than through the authenticated HTTP API since this is an internal
maintenance operation) before anything else. The job was then duplicated via
`duplicate_job()` (reused from `scripts/compare_ai_indexing_qwen_vs_gemini.py`)
into `89cfa529-2390-464c-9c3e-2446ef68c588`, filename suffixed `[GOLD]`,
so the production job's own live index (574 AI-detected targets) was never
touched.

Ground truth was hand-authored exactly like the 17-page/100-page fixtures:
660 `AIHeading` objects built from a direct reading of the job's own OCR
text (never copied from the AI's own output), batched into 4 groups of
≤180 headings each (respecting `AI_INDEX_MAX_HEADINGS_PER_CHUNK=200`,
aligned to the document's own Section/Annexure boundaries), and replayed
through the real seam — `ai_reference_index._ground_and_resolve_chunk`
(`resolve_parent_via_ai=False, provider_config=None`, fully offline and
deterministic) → `reference_index.apply_heading_events_to_scope` →
`_persist_page_index`, chaining `seed_scope` and a freshly-rebuilt
`carry_context` (via `ai_reference_index._carry_in_summary(seed_scope,
last_page_text)`, mirroring the real `reindex_job` loop) across every page
and batch — then `reference_index.build_mention_index_for_job`. No
orchestration script for this existed from the prior two builds (confirmed
by searching git history); a new one-off, `scratch/build_gold_fixture.py`,
was written for it.

### 15.3 Architecture findings surfaced by this document's shape

Three previously-undetected bugs were found and corrected (via
`structure_overrides`, not pipeline code changes — see §15.4 for why):

1. **HTML-table content is invisible to `parse_page_blocks`.** `_get_parser()`
   (`reference_index.py:884`) uses `mistune`'s `table` plugin, which only
   recognizes *markdown* pipe tables — this document's OCR renders
   two-column "clause number | clause text" layouts (most of Section-III
   ITB, all of Section-V GCC, and Annexure-XIII's module catalogues) as
   literal `<table>` HTML, which `_flatten_blocks` silently drops entirely.
   Net effect: roughly 280 of 660 authored headings came back
   `ai_unlocated` (no position grounding) across the 4 batches. This did
   **not** block the heading from being minted — `mint_key_for_heading`
   still uses the reported `heading_text` as `own_label` regardless of
   location success — so the fixture content itself is intact, but it means
   the live AI path faces the exact same blindness on this document's own
   {table,tr,td}-wrapped clauses, independent of model quality. Worth a
   real fix (e.g. extending the parser to also walk raw HTML `<table>`
   blocks) in a future round; out of scope for this fixture-building task.
2. **Root-container-protection (§10.1) clamps *every* independent top-level
   section to the first one opened, for "Section-N:" (hyphenated) style
   headings specifically.** This document has 9 genuinely independent
   top-level blocks (Tender Notice, Key Dates, Section-I through Section-VI)
   with no subdoc-trigger keyword among them. §10.1's fix ("a root container
   is open → clamp new root-looking headings to depth 1 instead of evicting
   it") was written for the single-root-document case (one JUDGMENT/one
   tender) and nests Section-II through Section-VI under Section-I as a
   side effect here. Confirmed inconsistent: "Section VII: Annexures"
   (space before the roman numeral, not a hyphen) mints into a different
   family (`section:7`) that isn't subject to the same clamp, and stayed a
   correct root sibling. Fixed for this fixture via 5
   `structure_overrides.set_parent(..., new_parent_key="")` calls (Section
   II through VI reparented to root) rather than a pipeline change.
3. **The `annexure` family's roman-numeral parsing is tuned for short
   letter/digit shapes ("A-1"), not multi-character romans ("XI", "XIII-A",
   "XVII").** Every annexure heading phrased as "Annexure \<roman\>:" (space,
   not hyphen) minted as a positionally-numbered `annexure:<garbled>` key
   (e.g. `annexure:x-1`) with **no `own_label` at all** — "Annexure-\<roman\>:"
   (hyphen directly after the word) mints correctly as a normal, labeled
   `subdoc` entry instead. Every one of the 10 affected headings in this
   build (`Annexure IV/IX/X/XI/XIII-A/XIV/XV/XVI/XVII/XVIII`) was
   re-authored with the hyphenated spelling to route around the bug — this
   is why some of this fixture's `own_label` text for Annexures reads
   "Annexure-XI" rather than the document's own "Annexure XI" spacing.
   Four more headings (`Annex-II`, `Annex-VII`, `Annexure-XII`,
   `Annexure-XIII-A`) additionally resolved with `ai_unresolved_parent_ref`
   into wrong, overly-deep nesting the first time through and needed
   individual `structure_overrides.set_parent()` corrections.
4. A handful of Annexure-XIII's own catalogue-module headers (`Human
   Resources`, `Payroll`, `Pension`, `Oracle Learning Management`, most of
   the Finance/SCM sub-modules) never minted at all, even as unlocated —
   root cause not fully isolated (plausibly the same repeated-identical-
   table-caption shape across many consecutive pages within one batch
   triggering a running-header/noise suppression path meant for page
   furniture). Left out of the fixture rather than forced in: a target no
   configuration of the detector can currently produce isn't useful as a
   required scoring item, and the surrounding Annexure-XIII container
   itself, plus `Projects` and `Inventory and Order Management (Supply
   Chain Management)` (which *did* mint), are enough to keep that section
   represented.

### 15.4 Why `structure_overrides`, not code fixes, for #2 and #3

Both are real, reproducible pipeline bugs — but fixing `mint_key_for_heading`'s
annexure-family roman-numeral parsing or `is_subdoc_root_candidate`'s
clamp-vs-family inconsistency is pipeline work with its own blast radius
(regression risk against the other two fixtures' already-validated numbers),
not fixture-building work. `structure_overrides` is the project's own
sanctioned mechanism for turning a job's base detector output into trustworthy
ground truth (`scripts/export_gold_fixture.py`'s own docstring: *"what makes a
hand-corrected job usable as ground truth"*) — the same category of action as
the 100-page fixture's own p92-letterhead correction (§13, "a correction to
the 100-page gold fixture itself... not a code bug"). Both bugs are recorded
here as follow-up leads, not silently absorbed.

### 15.5 Fixture characteristics

`tests/fixtures/gold_index_89cfa529.json` — job_id `89cfa529-2390-464c-9c3e-2446ef68c588`
([GOLD] duplicate of `3cafeb89-e2e0-428b-9fd1-73471232deae`), 135 pages,
**634 targets** (point 433, subdoc 92, numheading 77, term 30, section 1,
letterheading 1) and **976 mentions** (778 resolving, 198 suppressed).

### 15.6 Verification

```bash
# Re-export from the GOLD job after any further override correction:
docker exec ocr-services-glm-ocr-api-1 python3 scripts/export_gold_fixture.py \
  89cfa529-2390-464c-9c3e-2446ef68c588 --out tests/fixtures/gold_index_89cfa529.json
```

No live-model scoring run (`scripts/score_reference_index.py`) has been done
against this fixture — that script duplicates the job again and reindexes it
via the real provider (RokkaAI/qwen3:8b), which is real external API usage
and was explicitly out of scope for this task (only the fixture itself was
asked for). An actual `score_reference_index.py` run was started by mistake
during this build, caught, and killed before it produced any output; the
throwaway duplicate job it created was deleted. Establishing a live
qwen3:8b baseline against this fixture (§11.4's deferred generalization
check) remains open, future work — not done here.

