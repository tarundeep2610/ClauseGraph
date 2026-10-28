# Actionable-Items Extraction — Accuracy Investigation Log

**Purpose of this document:** a full record of the investigation into the
AI-assisted actionable-items extraction feature's accuracy (deadlines,
obligations, conditions, and litigation-shaped "other" items pulled from
contract/tender text) — what was found, what was fixed, what was tried and
didn't work, and what's still open. Sibling to
`AI_INDEXING_ACCURACY_INVESTIGATION.md` (the same kind of log for the
document-structure/cross-reference indexing pipeline) and
`EVALS_FRAMEWORK_DESIGN.md` (the shared evals architecture both pipelines
use). Organized chronologically. Target: precision ≥ 0.85, recall ≥ 0.95,
matching the structure pipeline's own existing bar
(`evals/settings.py::INDEX_GATES`).

**Primary files involved:**
- `ai_action_items.py` — the orchestration layer: chunk dispatch, grounding
  (`_ground_action_item`, `locate_action_item_block`), truncation-split
  retry loop.
- `ai_indexing/prompt.py` — `ACTION_ITEM_SYSTEM_PROMPT`,
  `ACTION_ITEM_OUTPUT_CONTRACT`, `build_action_item_prompt`.
- `ai_indexing/client.py` — `ActionItemIndexer`, `ChunkActionItemResult`.
- `ai_indexing/schema.py` — `AIActionItem`, `AIActionItemChunk`.
- `evals/metrics/grounding.py` — `score_action_items`, the bipartite
  fuzzy-quote matcher, the per-type breakdown.
- `evals/runners/action_items_runner.py`, `scripts/score_action_items.py` —
  the eval harness/CLI.
- `tests/fixtures/gold_action_items_*.json` — the four gold fixtures
  (`89cfa529` tender-135p, `30d148dd` dfccil, `179ab46e` midhani, `bb417471`
  appeal-100p).
- `tests/fixtures/ANNOTATION_NOTES.md` — the fixture annotation standard
  this investigation produced.

**Test documents used throughout:**
- `89cfa529-2390-464c-9c3e-2446ef68c588` ("tender-135p") — a 135-page
  Madhya Pradesh government ERP tender (MPPKVVCL, Oracle EBS
  implementation/O&M).
- `30d148dd-c1e4-4e38-877b-608b5584ff4e` ("dfccil") — a 10-page GeM bid
  cover sheet (Dedicated Freight Corridor Corporation).
- `179ab46e-b105-4107-ad38-7a0160724948` ("midhani") — an 8-page GeM bid
  cover sheet.
- `bb417471-561c-4029-bb21-3bdcf74e7dcb` ("appeal-100p") — a 100-page
  arbitration appeal judgment (used elsewhere in the structure-pipeline
  investigation too; action items are sparse here, mostly litigation-shaped
  "other" items).

---

## 1. Starting state: the pipeline had never actually been benchmarked

Before this investigation, no `eval_action_items_*` report existed anywhere
— not on the host, not in the committed history.
`AI_INDEXING_ACCURACY_INVESTIGATION.md` (2667 lines, covering the sibling
structure pipeline in depth) never once mentions action items. Every
existing action-items test (`tests/test_eval_runners.py::test_action_items_runner_self_score`,
`tests/test_eval_smoke.py::test_action_items_smoke_offline`) was a synthetic
self-match (gold scored against itself), trivially returning 1.0. So "action
items precision/recall is pretty worse" was never a recorded number —
establishing a real baseline was step zero.

A `docker exec` into the running container (`ocr-services-glm-ocr-api-1`)
turned up 4 real `eval_action-items_*.json` reports dated 2026-10-23,
produced by someone running the eval harness directly against the
container — invisible from the host because the container had been silently
recreated at some point (`RestartCount: 0`, fresh `Created` timestamp),
wiping everything that only ever existed via manual `docker cp` (the entire
`evals/`, `scripts/`, `tests/fixtures/` content needed to be re-copied in
more than once this investigation after unexplained container recreations —
a real, recurring operational hazard worth remembering).

**The real, pre-fix baseline (qwen3:8b via RokkaAI/Ollama, 1 trial each):**

| Fixture | Precision | Recall | Gold / Actual / Matched |
|---|---|---|---|
| gem-bid-dfccil-10p | 0.237 | 0.333 | 27 / 38 / 9 |
| gem-bid-midhani-8p | 0.081 | 0.176 | 17 / 37 / 3 |
| appeal-100p | 0.065 | 0.250 | 12 / 46 / 3 |
| tender-135p | **0.024** | 0.123 | 73 / 370 / 9 |

Catastrophic, especially `tender-135p` (370 extracted items against 73 gold
— a ~5x flood). "Obligation" was the single worst offender there: 13 gold
vs **262 actual**.

---

## 2. Four confirmed infrastructure gaps (fixed before any prompt work)

### 2.1 Eval scoring was stricter than the live pipeline's own grounding tolerance

Identity for a match was `(page, item_type, normalize_quote(evidence_quote))`
with `normalize_quote` doing only whitespace/curly-quote/casefold
normalization — far stricter than the live grounding path
(`_norm_for_action_item_match` + `locate_action_item_block`), which
`html.unescape()`s first and allows substring/fuzzy (difflib ≥0.85) matches.
A genuinely correct extraction with a slightly different quote span or an
undecoded HTML entity counted as **both** a false positive and a false
negative for the same real hit.

**Fix:** rewrote `evals/metrics/grounding.py::score_action_items` around a
two-phase (exact, then score-ranked fuzzy/substring) greedy bipartite
matcher, mirroring the structure pipeline's own `score_content_dual_match`
shape. Validated **retroactively**, with zero new LLM calls, by re-running
the new matcher over the already-captured false-positive/false-negative
lists from the Oct-23 baseline reports (`scratch/analyze_gap1_impact.py`):
recall roughly doubled-to-tripled across all 4 fixtures from this fix alone.

### 2.2 No truncation detection/retry for action-items chunks

The structure pass (`ai_reference_index.py`) had just shipped exactly this
fix (`ChunkStructureResult.truncated`, a `collections.deque`-based
split-and-requeue loop) — the action-items pass had no equivalent.
`ChunkActionItemResult` had no `truncated` field, and
`ActionItemIndexer.extract_action_items_for_chunk` never read
`completion.finish_reason`. A chunk that hit the token cap either failed
outright (contributing zero items for its pages — no rule-based fallback
exists for this pass) or silently dropped trailing items from a
truncated-but-still-parseable response. Dense, table-heavy pages (price
schedules, BOQ, eligibility tables) are exactly where action items
concentrate and exactly where truncation is most likely.

**Fix:** ported the structure pass's pattern exactly — `truncated: bool`
field, `finish_reason` check short-circuiting the correction-retry, and a
`collections.deque`-based requeue loop in `ai_action_items.py` that splits
a truncated chunk in half and retries at finer granularity (floor: single
page, accepted as-is). Confirmed firing for real in production multiple
times during later testing (not just a theoretical fix) — e.g. a chunk that
hit a 14,000-token cap at 36,204 characters of output was split and
recovered cleanly on dfccil.

### 2.3 The two-pass macro-context mechanism defaulted off and wasn't exercised

A macro-context pass (reusing the structure pipeline's own
`_extract_macro_skeleton`/`_get_active_macro_context`), purpose-built to
stop false-positive extraction from "AS-IS/already-implemented" catalogue
sections, existed but was gated behind `config.AI_INDEX_TWO_PASS`
(default `false`, shared with the structure pipeline), and the eval runner
called `reindex_action_items_for_job` with zero override kwargs even though
the function already accepted `two_pass_override`.

**Fix:** wired `page_from`/`page_to` slicing (see 2.4) and
`two_pass`/`chunk_pages` overrides through the eval runner and CLI, then
A/B tested directly: on `tender-135p` pages 84-126 (the documented
AS-IS-catalogue zone — a 40+-page "Annexure-XIII – ERP Modules implemented"
section), two-pass OFF produced **69** false positives; two-pass ON cut
that to **26** (-62%), with zero recall cost (gold has 0 items in that
zone either way). Per the user's decision, `config.AI_INDEX_TWO_PASS`'s
default was flipped to `true` for **both** pipelines (not a narrower
action-items-only knob), since the structure pipeline's own macro-context
mechanism is the same feature.

### 2.4 Page-range "slice mode" (enabling infrastructure, not a fix per se)

The structure pipeline already had `--page-from`/`--page-to` slicing
(`evals/cli.py`, `IndexTrialConfig`); action items had no equivalent —
`ai_action_items.reindex_action_items_for_job` had no `start_page`/
`end_page` parameters at all, forcing every iteration cycle through a full
100-135 page run. Ported the same mechanism (`database.get_pages` already
had built-in `page_from`/`page_to` SQL filtering, simplifying the port) —
this is what made the rest of this investigation's fast iteration loop
possible at all.

**Combined effect of 2.1-2.4 (qwen3:8b, 3-trial means) vs. the Oct-23 baseline:**

| Fixture | Before | After |
|---|---|---|
| gem-bid-dfccil-10p | P=0.237, R=0.333 | P=0.295, R=0.469 |
| gem-bid-midhani-8p | P=0.081, R=0.176 | P=0.222, R=0.510 |

Real gains, but far short of target — `tender-135p` and `appeal-100p`
remained much worse (precision as low as 0.02-0.10 in several slices). Root
cause: a 4th, previously-unscoped issue.

---

## 3. The 4th gap: broad, genre-dependent over-extraction

Direct inspection of false positives (not just the aggregate numbers)
revealed the dominant remaining problem wasn't the AS-IS catalogue — it was
much broader. In `tender-135p` pages 1-30 (ordinary tender content, not the
catalogue zone), "obligation" was still 1 gold vs **70 actual**. In
`appeal-100p` pages 16-36 (a zone with **zero** gold items), the model still
produced 28 items. The false positives were ordinary procedural/boilerplate
language — "bidder must sign all pages," conditional damages clauses in a
judgment — being extracted despite the prompt already having guardrails
against exactly this shape of content.

**Decision:** switch from live-testing against qwen3:8b (slow — minutes per
trial, noisy at 1-2 trials) to **Gemini** (`gemini-flash-lite-latest`, fast
— seconds per call, via a working key in `.env.local-test`, overridden
per-`docker exec` call without touching the container's persistent
`ollama_native` config). Validate the prompt logic on a strong model first,
then check how much "distills" down to qwen3:8b.

### Prompt iteration v1 → v6 (`ai_indexing/prompt.py::ACTION_ITEM_SYSTEM_PROMPT`)

- **v1** — narrow "portal mechanics" exclusion (download docs/pay fee/
  digital cert/training). **Caused a real recall regression**: dfccil
  obligation recall dropped 0.625→0.125 by over-generalizing to genuine
  eligibility items (TReDS registration, Integrity Pact upload, labour-law
  compliance) that happen to share surface language with portal mechanics.
- **v2** — broadened the rule with explicit REPORTABLE eligibility contrast
  examples. Recovered most of the regression but didn't touch the dominant
  failure mode (confirmed via a deterministic qwen3:8b slice — `tender-135p`
  pages 1-30 produced byte-identical results across 2 trials).
- **v3** — added an "eligibility/qualification criteria are not reportable"
  rule, based on `tender-135p`'s gold fixture having only 1 "obligation"
  item across 30 pages. **This premise turned out to be wrong** (see §4).
  Also attempted (twice — v1 and v3) to fix "other"-type AS-IS-catalogue
  leakage in the 84-126 zone; **both attempts made it measurably worse**
  (14→27→19 false positives, reproducible both times) — reverted to the
  original wording, confirmed best (14 FPs).
- **v4** — three more false-positive patterns found via fast Gemini
  diagnosis: generic bid-conduct procedural rules (broader than just portal
  UI), the Purchaser's-own-procedural-steps exclusion ("Purchaser shall
  open bids..." is not a bidder obligation), and bare-fact-vs-obligation (a
  standalone fee amount isn't an action). Real, confirmed improvement:
  `tender-135p` pages 1-30 obligation false positives 33→19.
- **v5** — reverted v3's eligibility-exclusion rule (see §4), narrowed the
  bid-rejection exclusion to distinguish timing/misconduct (not reportable)
  from document-preparation-gated rejection (reportable), and extended the
  "commonality is never the test" principle from `obligation` to
  `condition` too (a national-security land-border-registration clause,
  common to nearly every GeM tender, was being treated as unreportable
  purely because it's boilerplate).
- **v6** — added a rule distinguishing parallel *prohibited actions* sharing
  one gate (merge into one item) from parallel *required documents* (stay
  separately countable per document) — see §6.

---

## 4. Critical finding: fixture under-labeling, not model over-extraction

Inspecting Gemini's v4 false **negatives** (not just false positives) on
dfccil/midhani directly contradicted v3's premise:

- dfccil's **own gold fixture** wants `"Bidder must have Past Experience of
  Similar Services meeting one of three cost-threshold alternatives"` and
  `"Turnover documentary evidence... must be uploaded"` reported as
  `obligation` — the exact shape of item v3's rule told the model to
  reject.
- `"Bid summarily rejected if Pre Contract Integrity Pact not signed and
  uploaded"` was also a real false negative — directly contradicted by the
  bid-rejection exclusion, which had been modeled on *differently-shaped*
  rejection clauses (late-bid rejection, EMD forfeiture-on-withdrawal,
  fraud) that are NOT document-preparation gated the way this one is.

**Conclusion:** `tender-135p`'s gold fixture was genuinely **incomplete**
for `obligation`/`condition` labeling, not evidence that eligibility
criteria are categorically unreportable. Tuning the prompt against an
incomplete fixture was teaching the model to suppress genuinely-correct
extractions to match a fixture gap — exactly what v3 did.

### The fixture audit

Read the real stored OCR text directly (not re-derived or paraphrased, per
`EVALS_FRAMEWORK_DESIGN.md`'s own fixture lifecycle) for `tender-135p`
pages 1-30 and 127-135. Found:

- **"Section-II: Eligibility Criteria"** (pages 8-9) — a formal 10-row
  table, each row with an explicit required-document column (RoC
  certificate, CA certificate, work-order completion certificate, CMMI/ISO
  certificates) — structurally identical to dfccil's confirmed-reportable
  eligibility content. Gold had only 1 of these 10 rows (plus 2 rows
  mistyped as `deadline`/`condition` instead of `obligation`).
- **"Annexure XVI: Check List"** (pages 130-131) — a **deliberate full
  restatement** of the same 10-row table, explicitly cross-referenced
  elsewhere in the document ("mention page no. of the documents attached...
  as per Annexure-XVI 'Check List'"). Gold already counted 3 of its rows
  separately from the Section-II occurrence, confirming the fixture's own
  established convention treats this as legitimately double-countable (a
  distinct, named, functionally-different section, not an incidental
  duplicate).
- Page 16's SSI/MSME EMD-exemption condition, and page 28's `"SI shall
  submit... before Target Architecture sign-off"` (a genuinely gated SOW
  item, matching the prompt's own REPORTABLE example precisely) were also
  missing.

**Result:** `tests/fixtures/gold_action_items_89cfa529.json` grew from 73
to 92 grounding-validated items. Direct before/after on the same slice,
same model, same prompt, zero other changes:

| | Before fixture fix | After |
|---|---|---|
| `tender-135p` pages 1-30, Gemini | P=0.155, R=0.591 | **P=0.256, R=0.645** |
| obligation (gold/actual/matched) | 1 / 52 / 0 | 10 / 53 / 9 |

A 65% relative precision gain from correcting the gold standard alone —
concrete proof that part of the "low precision" was a measurement artifact,
not a real pipeline defect.

### A parallel discovery not pursued to resolution: dfccil's page 1

dfccil's page-1 "Bid Details" table restates the same turnover/past-
experience facts in condensed form (a bare number, a Yes/No flag).
Resolved (not left open): this is a GeM-platform-generated **summary**,
identical in shape across every GeM bid regardless of what's procured, not
a parallel independently-complete restatement like Annexure XVI — the
substantive version is already captured once at page 4. No fixture edit
needed. See `tests/fixtures/ANNOTATION_NOTES.md` for the general rule this
established.

---

## 5. External review caught a real scoring bug before it shipped

A second pass (an outside review of the in-progress plan) flagged that
allowing obligation↔condition cross-type matching (proposed after finding
the same type-mismatch pattern recur across dfccil/midhani — see §6) would
corrupt the `by_type` precision/recall breakdown if implemented naively.
**Verified directly against the code, not taken on faith:**
`score_action_items`'s per-type loop computed one shared `m_t` by checking
gold-set membership, then fed it into `compute_precision_recall_f1`, whose
hardcoded edge cases (`actual_count==0 and gold_count>0 → 0.0`) silently
discard `matched_count` whenever a type bucket's own actual/gold count is
zero — safe only because same-type-only matching guarantees
`actual_count==0 implies matched_count==0`. Cross-type matching breaks that
invariant.

**Fix:** `_bipartite_match_by_quote` now returns explicit `(gold_idx,
actual_idx)` pairs, not just two sets; `by_type` computes each type's
precision from actual-side pair membership and recall from gold-side pair
membership independently, rather than reusing one count for both — a
formula verified to reduce to byte-identical results to the old approach
whenever a gold item's type and its matched actual item's type coincide
(the pre-cross-type-matching case), and to generalize correctly when they
don't. Unit-tested directly against the real confirmed cases
(`tests/test_eval_metrics.py::test_score_action_items_cross_type_obligation_condition`
and `..._does_not_apply_to_deadline`).

---

## 6. Generalizable finding: the type-mismatch issue isn't fixture-specific

Retroactive analysis (`scratch/analyze_crosstype_impact.py`, zero new live
calls — re-scoring already-captured diffs) found `"EMD exemption requires
valid supporting documents for the relevant category"` — gold-tagged
`condition`, naturally extracted as `obligation` by the model — recoverable
at score 1.00 (identical verbatim text, only the type differs) in **three
independent diffs** across dfccil and midhani. A second case — `"Seller
must not assign or sub-contract the Contract without prior written
consent"` (gold: one `condition`; model: two separate `obligation` items,
one for "assign" and one for "sub-contract") — recovered at score 1.00 in
two dfccil diffs.

**Two fixes, addressing the two distinct root causes:**
1. **Scoring-level**: `evals/metrics/grounding.py` now allows
   obligation↔condition cross-type matching (same-page, same fuzzy-quote
   test used everywhere else), scoped narrowly — `deadline` and `other`
   aren't subject to the same human/model classification ambiguity, so
   they're excluded from this cross-type pass. Zero false recoveries
   observed across every diff checked.
2. **Prompt-level** (v6): a sentence restricting two or more parallel
   actions under one shared gate ("shall not assign... nor sub-contract...
   without consent") is one item, not one per action — explicitly
   contrasted against a list of required documents in one sentence
   ("upload Balance Sheets, P&L Statements, and GST Returns"), which stays
   separately countable per document. This distinction (prohibited actions
   vs. required deliverables) was specifically added after a review
   flagged the initial wording risked over-merging document checklists.

This made the planned manual retype of dfccil's fixture entries
unnecessary — step 1 alone resolves the EMD-exemption case natively
(verified by unit test using the real verbatim quote).

---

## 7. Results after the full fix set (prompt v6 + scoring fix + fixture audit)

### On Gemini (3-trial means where noted)

| Fixture/slice | Precision | Recall |
|---|---|---|
| tender-135p pages 1-30 | 0.293 | 0.727 |
| tender-135p pages 84-126 (AS-IS zone) | **1.000** | **1.000** |
| tender-135p pages 127-135 | 0.772 | 0.548 |
| gem-bid-dfccil-10p | 0.540 | 0.691 |
| gem-bid-midhani-8p | 0.244* | 0.216* |

\* midhani's last 2 of 3 trials, and all of appeal-100p's 3 trials, hit
Gemini's free-tier daily quota (`429 ... generate_content_free_tier_requests,
limit: 500`) mid-run — `status=failed`, zero items, not a real 0.000
result. The free tier was exhausted by this investigation's own heavy
iteration. Single-trial reads from before the quota hit: midhani
P=0.417/R=0.294, appeal-100p P=0.500/R=0.667 (pages 1-15).

### On qwen3:8b (the actual production model), after resuming once Ollama was reachable again

| Fixture/slice | Before (pre-investigation) | After |
|---|---|---|
| gem-bid-dfccil-10p | P=0.295, R=0.469 | **P=0.386, R=0.593** |
| gem-bid-midhani-8p | P=0.222, R=0.510 | **P=0.256, R=0.588** |
| tender-135p 84-126 (AS-IS zone) | 27 FPs | **17 FPs** (obligation 11→5) |
| tender-135p pages 1-30 | — | P=0.111, R=0.424 (gold=33, act=126) |

Real, reproducible gains carried over from Gemini to the actual production
model on 3 of 4 tracked points — the investigation's work wasn't
Gemini-specific. "Other"-type AS-IS-catalogue leakage (10 of 17 remaining
FPs in that zone) persists on **both** models — two-pass suppresses
`obligation` noise there but not `other`, a real, unresolved gap.

**Confirmed qwen3:8b-specific capability ceiling**: on `tender-135p` pages
1-30, `obligation` is gold=13 vs actual=**83** on qwen — far worse than
Gemini's equivalent ratio (gold=13, actual=19, same prompt, same zone). This
is specifically the dense Section-II eligibility/SOW-instruction content —
an 8B model applying many negative-exclusion rules precisely at volume is
measurably harder than it is for a stronger model, even though the same
prompt delivers clear, real gains elsewhere. This matches what was
predicted going in, not a new surprise.

---

## 8. Current status and open items

**Shipped, deployed, test-covered (919 passed; the same 8 pre-existing,
unrelated structure-pipeline test failures present in commit `14b507d`
before this investigation started remain, untouched):**
- Page-range slice mode for action items.
- Bipartite fuzzy-quote scoring (`evals/metrics/grounding.py`), now with
  obligation↔condition cross-type matching and a correctly-decomposed
  per-type breakdown.
- Truncation detection/split-retry for action-items chunks.
- Two-pass macro-context, default `true` for both pipelines.
- `ACTION_ITEM_SYSTEM_PROMPT` at v6 — eligibility criteria restored as
  reportable (not categorically excluded), bid-rejection exclusion narrowed
  to timing/misconduct only, "commonality is never the test" extended to
  `condition`, parallel-prohibition-vs-document-checklist granularity rule
  added.
- `tests/fixtures/gold_action_items_89cfa529.json` corrected (73→92 items,
  grounding-validated, two type-mismatches fixed).
- `tests/fixtures/ANNOTATION_NOTES.md` — the restatement/granularity
  standard this investigation produced, for future fixture edits to follow.

**Known open gaps, not yet fixed:**
- "Other"-type AS-IS-catalogue leakage (software-feature table rows
  mislabeled "other") persists on both Gemini and qwen3:8b despite two
  prompt attempts — both attempts made it *worse*, reverted both times.
  Needs a different approach, not more wording tweaks to the same section.
- `midhani`'s `condition`-type recall gap (0/10, 0/8 matched in early
  single-trial reads) — not yet separately investigated.
- The two-pass-on-pages-1-30 hypothesis (does macro-context framing also
  suppress generic bid-conduct noise, the way it suppresses AS-IS noise?)
  — untested, needs a live trial.
- `appeal-100p`'s gold fixture has not been audited for completeness the
  way `tender-135p`'s was.
- `evals/settings.py::ACTION_ITEMS_GATES` (`min_precision: 0.800,
  min_recall: 0.750`) is still looser than the 0.85/0.95 target, and its
  `min_grounding_rate` key is still dead code — deliberately not touched
  yet, pending the numbers above converging further.
- The confirmed qwen3:8b capability ceiling on dense eligibility/SOW
  content may need a different mitigation than prompt wording (few-shot
  examples embedded in the qwen-specific prompt path, or an explicitly
  tiered/relaxed qwen-specific gate) — not yet decided or attempted.

**Infrastructure notes learned along the way:**
- The container (`ocr-services-glm-ocr-api-1`) has no source-code volume
  mount — only `/app/data` persists. It was recreated (not just restarted)
  by something outside this investigation's control at least once,
  silently wiping `evals/`, `scripts/`, and `tests/fixtures/` (anything
  that only ever existed via manual `docker cp`). Always verify these
  directories are present before trusting a live run.
- Git Bash (MSYS) mangles bare absolute-Unix-path arguments passed to
  `docker exec`/`docker cp` (e.g. `--dump-diff /app/data/...` silently
  became `C:/Program Files/Git/app/data/...`, writing the file to a bizarre
  nested path inside the container rather than failing loudly). Always
  prefix with `MSYS_NO_PATHCONV=1`.
- Gemini's free tier (`gemini-flash-lite-latest`) has a hard daily cap
  (`generate_content_free_tier_requests, limit: 500`) — heavy same-day
  iteration (many multi-trial, multi-slice sweeps) exhausts it well before
  a full investigation naturally concludes; pace accordingly.
