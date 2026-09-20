# OCR Indexing & Performance — Decision Log

**Purpose of this document:** a full record of one extended working session —
what was reported, what was investigated, what was tried and rejected, what
was finally decided, and how each decision was verified. Written so the
reasoning behind the current code survives past the commit messages and
diffs. Organized chronologically; each section names the files touched.

---

## 1. Docker build fix

**Report:** `docker build` failed on `npm ci` with `MODULE_NOT_FOUND` for
`copy-pdfjs-assets.mjs`, plus a Node version `EBADENGINE` warning.

**Cause:** the frontend-build Dockerfile stage ran `npm ci` (which executes
`postinstall`/prepare scripts, including the pdf.js asset-copy script)
*before* `frontend/scripts/` had been copied into the image, so the script
the `postinstall` hook needed didn't exist yet at that point in the build.
Separately, the base image (`node:20-slim`) was older than the frontend's
own declared engine requirement.

**Fix** (`Dockerfile`):
- Added `COPY frontend/scripts/ ./scripts/` *before* `RUN npm ci`.
- Bumped `FROM node:20-slim` → `FROM node:22-slim`.

**Verified:** direct `docker build` of the frontend-build stage, then the
full multi-stage build, both green.

---

## 2. GPU image audit (uncommitted-changes check)

**Question asked:** did any of the session's uncommitted changes touch the
GPU OCR service's own Docker image/build, or only the main service?

**Answer:** at that point in the session, no — the GPU service's Dockerfile
and image build were untouched; only the main service's `Dockerfile` and
frontend had been modified. (The GPU service's own `unlimited_ocr_engine.py`
and `main.py` *were* touched later, in §3 below, once the grounding/crop_mode
work began.)

---

## 3. pdf.js viewer runtime errors

**Report 1:** opening a PDF threw `Failed to render page 1: Setting up fake
worker failed: ...` — a pdf.worker.mjs fetch failure.

**Report 2 (after a first fix attempt):** a second, different error surfaced
on the same action: `Unexpected server response (0) while retrieving PDF ...`
against a `blob:` URL.

**Investigation:** pdf.js's worker thread fetches its `url:`/`data:` source
from *inside the worker*, not the main thread. A `blob:` URL created on the
main thread (from an authenticated fetch of the PDF, used to avoid a 401/CSP
framing issue seen earlier in the project) isn't reliably fetchable from a
worker context — it surfaces as this opaque "Unexpected server response (0)"
failure rather than a real decode/network error.

**Fix** (`frontend/src/components/PdfCanvasViewer.jsx`): when `pdfSrc` starts
with `blob:`, read it into an `ArrayBuffer` on the main thread first (where
`blob:` URLs always resolve) and hand pdf.js the raw bytes directly, instead
of a URL for its worker to re-fetch. A plain `http(s)` URL is unaffected —
it still goes through pdf.js's own worker-side fetch as before.

**Follow-up — CSP:** the blob-read fetch itself was then blocked by the
Content-Security-Policy's `connect-src 'self'` directive (a `blob:` fetch
target isn't `'self'`).

**Fix** (`main.py`): added `blob:` to the CSP's `connect-src` directive.

**Verified:** manual test — opening a remote-loaded PDF (which produces a
`blob:` `pdfSrc`) rendered without either error.

---

## 4. Raw OCR text / grounding investigation

**Report:** partway through a new job's OCR run, asked whether the
*uncleaned* (`raw_ocr_text`) OCR output was actually being stored per the
original indexing plan.

**Finding:** confirmed the storage path worked, but a further check (0 of 4
completed pages had `<|det|>` tags in their `raw_ocr_text`) surfaced a
deeper, unplanned question: why were the tags never appearing at all?

### 4.1 Wrong hypothesis, corrected via testing

Initial hypothesis: a `<|grounding|>` prompt token needed to be prepended.
Direct testing disproved this — the token is registered in the model's own
tokenizer (id `128820`) but does nothing useful on this checkpoint, and can
actively *degrade* transcription quality (hallucinated "Ground Truth
critique" text was observed). The user independently found and shared two
corroborating external sources — a Docling project issue
([`docling-project/docling#4030`](https://github.com/docling-project/docling/issues/4030))
and a community writeup — describing the same finding for the same model
lineage, plus a real detail this project's own first direct test had missed:
the literal `<image>` prompt-prefix token, which `modeling_unlimitedocr.py`'s
`infer()` splits the prompt on to know where to insert image embeddings.
Omitting it (as an early standalone test script did) caused double-encoding
of the prompt text — a second, independent bug alongside a `crop_mode`/
`image_size` mismatch that had also crashed that same early test.

### 4.2 Real mechanism, confirmed

The actual trigger for `<|det|>type [bbox]<|/det|>content` tag output is
`crop_mode=True` — i.e. switching the model from its "base" config
(`image_size=base_size`, one global view, no per-region output) to its
official "gundam" config (`image_size=640`, per-region crops + global view).
Confirmed directly against the model on a real page after fixing both bugs
from §4.1 (14 real `<|det|>` tags observed on real jobs).

### 4.3 A second, independent bug found during this work

`unlimited_ocr_engine.run_ocr_with_raw()`'s primary source for the "raw"
text was the model's own `save_results=True` output *file* — but that file
is already tag-stripped by the vendor's own `infer()` before it's written.
The tags only ever exist in the live generation stream, captured separately
via `captured_stdout`/`stdout_capture`. The existing code only used that
capture as a last-resort fallback, never as the primary source — meaning
`raw_ocr_text` could never contain tags regardless of `crop_mode`, until
fixed to prefer the filtered captured-stdout text whenever it's non-empty.

### 4.4 Frontend toggle built

A per-job "Ask Unlimited-OCR for layout/region tags (grounding)" checkbox
was added to `AdvancedSettingsPanel.jsx`, wired through `Dashboard.jsx` →
`POST /api/settings` → `main.py`'s `_load_settings()`/`save_settings()`
(`allowed_keys`) → frozen into a job's `engine_params.grounding_enabled` at
creation time (never re-read mid-job) → `_build_runners()` →
`run_unlimited_ocr_for_image_with_raw(..., grounding_enabled=...)` →
`crop_mode=grounding_enabled` passed to both the local engine and
`gpu_ocr_client`. `config.ENABLE_UNLIMITED_GROUNDING` (default `false`) is
the fallback when no runtime setting exists yet.

**Files touched:** `main.py`, `config.py`, `unlimited_ocr_engine.py` (both
the main-repo copy and the `gpu-ocr-service/` copy, kept in lockstep),
`gpu_ocr_client.py`, `gpu-ocr-service/main.py`
(`SubmitJobPayload.crop_mode`), `frontend/src/components/
AdvancedSettingsPanel.jsx`, `frontend/src/pages/Dashboard.jsx`, plus test
fixture updates in `gpu-ocr-service/tests/conftest.py` and
`test_gpu_service_routes.py` (signature changes needed a `crop_mode=False`
default added to fakes), and `tests/test_grounding_prompt.py` (rewritten to
assert the prompt is *never* modified by the toggle — only `crop_mode` is).

**Verified:** full backend test suite green; real-job testing (crop_mode
threading, raw-text sourcing) confirmed 14 real `<|det|>` tags on real
pages.

---

## 5. PDF viewer quality (separate report, same viewer component)

**Report:** the pdf.js-rendered PDF was low quality — couldn't zoom, and
whole-page zoom distorted pixels; without zoom the content was too small to
read.

**Investigation:** read pdf.js's own documentation/examples directly.
Confirmed three independent causes:
1. No HiDPI handling — the canvas backing buffer was sized at CSS pixels
   only, never at device-pixel resolution, so every render (zoomed or not)
   was blurry on any `devicePixelRatio > 1` display.
2. No real zoom mechanism at all — only CSS `max-width`/`height: auto`,
   which just stretches an already-rasterized canvas (visibly pixelated),
   never triggers a genuine pdf.js re-render at a new scale.
3. A flexbox `overflow: auto` + `justify-content: center` combination on
   the scroll container clipped the overflowing content on one side,
   making it unreachable via scroll once zoomed.

**Fix** (`frontend/src/components/PdfCanvasViewer.jsx`,
`frontend/src/index.css`):
- Mozilla's documented HiDPI pattern: `outputScale = devicePixelRatio || 1`;
  canvas backing buffer sized at `viewport.width/height * outputScale`;
  `canvas.style.width/height` kept at the unscaled CSS size; a `transform`
  matrix passed to `page.render()`.
- A `zoomLevel` state multiplied into the render `scale`, so zooming
  triggers a real pdf.js re-rasterization each step, not a CSS stretch.
  Added `pdf-zoom-in`/`pdf-zoom-out`/`pdf-zoom-reset` buttons plus
  Ctrl+wheel support.
- Replaced the container's `justify-content: center` with `margin: 0 auto`
  on the canvas itself, so overflow scrolling reaches the full zoomed
  content on both sides.

**Verified:** 14 new tests added across two new `describe` blocks in
`PdfCanvasViewer.test.jsx` (zoom behavior; HiDPI rendering, using
`Object.defineProperty(window, 'devicePixelRatio', ...)`); full test suite
green.

---

## 6. OCR speed regression — first pass (deferred by the user)

**Report:** per-page OCR processing had gone from ~3 min/page to ~4-6
min/page since recent changes.

**Investigation:** GPU service's own logged execution times showed a clean
split — "base" config runs at ~85-105s/page, "gundam" config
(`crop_mode=True`, from §4's grounding toggle) at ~212-380s/page — matching
the complaint almost exactly. Root cause: the grounding toggle from §4 had
been left on (from testing it against real data) after that investigation
concluded.

**Proposed fix at the time:** flip the setting off, and add explicit
timing-cost language to the checkbox's description.

**User's decision:** explicitly deferred — *"keep this plan for later."*
**Not implemented at that time.**

---

## 7. Structure-tree fix — no headings after "Rebuild Cross-References"

**Report:** for a completed 6-page job (`b6a06baf`) with raw tagged OCR
output already present, clicking "Rebuild Cross-References" produced no
Structure tree at all — and it should have run automatically, without
needing the button.

**Initial diagnosis:** checked job `b6a06baf` directly against the
database. The rebuild mechanism itself worked (`reference_index_version`
bumped, confirming a real reindex ran) — it produced 5 targets, all
`page:N` self-references, correctly excluded from the Structure tree by
design. Checking all 6 pages' real OCR text against `CORE_RE` explained
why zero real headings were ever detected:
- Page 1: `"AmneXureA-1"` — human-readable as "Annexure A-1," but
  OCR-garbled (`"Amne"` not `"Anne"`) and missing the space before the
  number; `CORE_RE` requires an exact literal keyword.
- Pages 5-6: `"3.3 TIME SCHEDULE"`, `"4.0 SETTLEMENT OF DISPUTES"` — real
  headings, but bare decimal numbers with no family keyword at all;
  `CORE_RE` only ever matches `keyword + number`.

### 7.1 Round 1 — rejected: keyword-hardcoded, no fuzziness

First draft proposed a narrow regex tweak for decimal headings plus a
*silent* context-only fallback for layout-tagged headers (never producing
a visible node).

**User's rejection:** this re-introduced exactly the rigidity the project
had already rejected external ontologies (Akoma Ntoso, OWL/RDF) *for* —
`FAMILIES` was meant to be a tolerant, document-derived vocabulary, but a
vocabulary only matched by exact-literal-spelling regex isn't actually
tolerant of anything. Directive: use fuzzy matching for OCR-garbled
keywords, and let the model's own layout tag drive a *real*, correctly
nested heading when nothing textual matches at all — matching what had
already been decided once real tagged output existed (§4).

### 7.2 Round 2 — rejected: vague "current depth + 1" nesting

Second draft added fuzzy matching plus a `rank`-field-based dynamic
nesting for layout-only fallbacks, using `current_max_rank + 1`.

**User's rejection, with a worked example:** consecutive unmatched
headings with no family would each nest one level deeper than the last
(a chain), when many of them are actually siblings under the same parent —
"we need to consider each and every case," and this concern applied to
the *overall* nesting logic, not just the no-match case.

### 7.3 Round 3 — rejected: bbox-x1 "concrete" nesting still not good enough

Third draft replaced `current_max_rank + 1` with a bounding-box left-edge
(`x1`) indent/dedent stack walk — position on the page as "measured,
concrete evidence" instead of an assumption.

**User's rejection:** "the depth of each of the keyword shouldn't be
determined from which level they belong to — that is a pretty vague and
non-trustable metric. We need something concrete for determining if
something is a sibling, a child, or the start of a new parent." (This
specifically flagged that even the "concrete-sounding" bbox metric hadn't
actually been checked against real, multi-heading data yet.)

### 7.4 First real verification round — the bbox design fails on the real document

**Explicit instruction:** trace the actual algorithm against the actual
6-page PDF and its actual raw tagged output — read the PDF directly,
determine the real hierarchy, and check whether the algorithm would
reproduce it.

Read the PDF page-by-page. Real structure: `1. Article` → `1.1 Award of
contract`; `2.0 CONTRACT DOCUMENTS`; `3.0 CONDITIONS AND COVENANTS` →
`3.3 TIME SCHEDULE`, `3.4 QUALITY PLANS`; `4.0 SETTLEMENT OF DISPUTES` →
`4.1 NOTICE OF DEFAULT` — a plain decimal-outline contract, `1`/`2.0`/
`3.0`/`4.0` as top-level siblings. This directly disproved two of the
design's own load-bearing assumptions:

1. **The `"header"`-only trigger was backwards.** All 8 real headings are
   tagged `"title"` by the model; `"header"` only ever tags page 1's
   hand-written case-bundle margin annotation (`"AmneXureA-1"`) and a
   garbled stamp (`"\( \xi a \)"`) — neither is real document structure.
2. **The bbox `x1` carries no usable signal on this document.** Measured
   `x1` for every real heading: `1.`=204, `1.1`=204 (identical, yet `1.1`
   must nest *under* `1`), `2.0`=208, `3.0`=202, `3.3`=221, `3.4`=220,
   `4.0`=212, `4.1`=211 — a flush-left legal contract never visually
   indents sub-clauses. The bbox walk would have flattened the entire
   document to one rank.

**Redesign:** use the decimal number's own segments (stripping a trailing
`.0`, so `"2.0"` behaves as top-level `"2"`) as rank, checking a new
number's leading segment against what's actually still open — the
number's own author-asserted nesting claim, not an inference. Widened the
tag trigger to `"title"` (what's actually true on this job).

### 7.5 Second verification round — the redesign still has real bugs

**Same instruction, repeated on the revised design:** ran the actual repo
functions (`CORE_RE`, `parse_layout_blocks`, `_flatten_blocks`,
`_block_own_text`) against all 6 real pages, not a hand trace. Found three
more concrete problems:

1. **Fuzzy-match tail bug.** As first written, `"AmneXureA-1"` resolved to
   `annexure:-1` (the `A` prefix silently dropped) instead of
   `annexure:a-1` — because when the *trimmed* candidate (`"AmneXure"`,
   8 chars) won the fuzzy ratio race against `"annexure"`, the code still
   used the tail matched against the *untrimmed* group, discarding the
   leftover character. Fixed by reattaching the leftover and returning a
   proper 3-tuple (the original 2-tuple return didn't even match its own
   docstring).
2. **`layout_aligned` is `False` on every one of this job's 6 pages** —
   cleaned-text block counts never matched raw-tag counts, because the
   cleaned markdown includes an injected "⏱️ Execution Time" footer, image
   placeholders, and table-cell splitting the raw tag stream doesn't
   produce the same way. So the `"title"`-tag corroboration never actually
   activates on this job; documented plainly as additive-when-available,
   not load-bearing, rather than overclaiming it.
3. **Two real hijacks from the pre-existing `detect_subdoc_trigger`/
   `SUBDOC_TRIGGER_RE` mechanism** — not new code, but only actively
   destructive now that decimal detection made number-first headings
   meaningful for the first time:
   - Page 2's block is literally `"INDEX"` — this Annexure's own
     table-of-contents heading, not a new document — but the generic
     trigger fired anyway, replacing `"Annexure A-1"` as root with a
     spurious `"Index"` node.
   - Page 5's `"3.3 TIME SCHEDULE"` contains `"SCHEDULE"` within the
     trigger's 15-character noise window — same hijack, replacing
     `"3.0 CONDITIONS AND COVENANTS"` with a spurious `"Schedule"` node.
   A pre-existing test comment
   (`tests/test_reference_index_scope_ranges.py:64-66`) had already
   flagged this exact class of risk in the abstract, but it had never
   actually been hit by real data before.

**Scope decision:** the `"INDEX"` hijack is a pre-existing defect, not
caused by this session's new code — asked the user whether to fix it in
this same plan or defer it. **Decision: fix it now**, since leaving it
would still leave the feature visibly broken on this exact job.

### 7.6 Final approved design

- **Fuzzy keyword matching** (`_fuzzy_family_match`, `_FUZZY_PREFIX_RE`,
  `_FUZZY_MATCH_CUTOFF = 0.75`): tried before falling through to anything
  else; resolves to the *canonical* family spelling (never the garbled OCR
  text) for both key and label; adds `"fuzzy_keyword_match"` as a
  confidence reason, but that reason is deliberately excluded from the
  gate that blocks a container from pushing/anchoring later content —
  fuzzy spelling-uncertainty is a different kind of doubt than a
  citation-shaped false positive, and blocking the push would have
  defeated the entire point (an OCR-garbled `"AmneXureA-1"` still needs to
  anchor everything nested under it).
- **Decimal-heading detection & nesting** (`DECIMAL_HEADING_RE`): a short
  label (≤9 words) after a plain decimal number, in either case style
  (title-case or all-caps — an earlier all-caps-only draft would have
  missed `"1. Article"`/`"1.1 Award of contract"`). When layout data is
  aligned, additionally requires the model's own tag to be `"title"`/
  `"header"` — additive, not required. Nested by splitting the number on
  `.`, dropping a trailing `"0"` segment, and popping the stack only while
  the top entry is itself a numbered-heading whose segments are a proper
  prefix of the new number's — never touching a real family container
  underneath. Any genuine new container match unconditionally clears all
  numbered-heading scope first (fresh numbering under a new sub-document).
- **`detect_subdoc_trigger` fixes:** (a) never fires on a block that's
  itself decimal-heading-shaped, regardless of which trigger word
  matched; (b) `"INDEX"` demoted to a *weak* trigger, split into its own
  `_WEAK_SUBDOC_TRIGGER_RE`, gated on nothing already open at rank 0 — a
  bare `"INDEX"` heading while an Annexure/Schedule/Appendix is already
  open is that container's own internal table of contents, not a new
  document.
- **Residual layout-only fallback** (no number, no keyword at all):
  widened to `"title"`/`"header"` (not `"header"`-only), gated by the same
  alpha-content-ratio guard (`alpha_count >= 3 and alpha_ratio >= 0.5` —
  validated directly: `"AmneXureA-1"` = 0.82, passes; the garbled stamp
  `"\( \xi a \)"` = 0.27, correctly rejected), nested simply as a child of
  whatever's currently open — no geometric walk. Documented honestly as
  the one path with no real verified example on this job (the only
  candidate for it, `"AmneXureA-1"`, resolves via fuzzy matching first).

**Files touched:** `reference_index.py` (all three sections above);
`tests/test_reference_index_fuzzy_and_decimal.py` (new, 19 tests,
including a full end-to-end replay of this job's real 6 pages, embedded
verbatim from the database — not synthetic fixtures).

### 7.7 Verification

- Full backend suite: 339 passed (320 pre-existing + 19 new), no
  regressions.
- Ran the real, unmodified `build_targets_for_page` directly against all
  6 real pages in order — resulting tree matched the PDF exactly:
  `Annexure A-1` (root, surviving the `"INDEX"` page) → `1` → `1.1`;
  `2.0`/`3.0`/`4.0` as top-level siblings; `3.3`/`3.4` under `3.0
  CONDITIONS AND COVENANTS`; `4.1` under `4.0 SETTLEMENT OF DISPUTES`. No
  numbered body paragraph (`2.1`, `3.1`, `3.2`, `3.4.1`, `3.4.2`,
  `3.5`-`3.8`) was ever falsely promoted to a heading.
- Copied the fixed `reference_index.py` into the running
  `ocr-services-glm-ocr-api-1` container and ran a **fresh** Python
  process (not the live uvicorn process, which still had the old module
  loaded in memory) calling the real, production `reindex_job()` against
  the real database for job `b6a06baf`. The real `ocr_reference_targets`
  rows written matched the same 9-node tree, confirming the fix through
  the actual production code path, not just a direct function call.
- Noted to the user: the live API process still had the old code loaded
  in memory at that point and needed a restart to actually serve the fix.
  **User's decision: restart deferred — "I'll restart it myself later."**
  The fix is on disk in the container and in the repo, pending that
  restart/redeploy.

---

## 8. OCR speed regression — second pass (this session, re-investigated fresh)

**Trigger:** asked to "implement the plan we made earlier" for OCR speed
(§6) — but that plan file had since been fully overwritten by the
Structure-tree work (§7), and enough code had changed since §6 that the
old draft couldn't be trusted blindly. Re-investigated from scratch.

**Finding 1 — the code-level fix was already in place**, apparently from
the same work that built and verified the grounding toggle in §4:
- `config.py`'s `ENABLE_UNLIMITED_GROUNDING` fallback is already `False`.
- `AdvancedSettingsPanel.jsx`'s grounding checkbox is already unchecked by
  default, and its description already contains the exact timing-cost
  warning §6's deferred plan wanted added.

**Finding 2 — the actual live cause is a leftover runtime setting, not a
code default.** Read `/app/data/settings.json` directly inside the
running `ocr-services-glm-ocr-api-1` container:
```json
{ "enable_unlimited_grounding": true, ... }
```
This persisted value (saved via `POST /api/settings`, which overrides the
code default) is almost certainly left over from turning grounding on
manually to verify the `<|det|>` tag fix (§4) against a real job, and
never turned back off. Every OCR job submitted right now still runs in
slow gundam config as a direct, unconditional result — `_build_runners()`
→ `run_unlimited_ocr_for_image_with_raw()` → `crop_mode=grounding_enabled`
on every single-image Unlimited-OCR call.

### 8.1 User's concern: doesn't turning grounding off break the indexing work?

Raised directly: if grounding/tags are turned off, does the Structure-tree
fix from §7 stop working?

**Answer, and why it's not actually a conflict:** §7's fuzzy-matching and
decimal-heading-nesting logic was specifically verified (§7.7) to work
with **no** layout-tag data at all — the real job tested had
`layout_aligned=False` on every page throughout, and detection still
worked correctly from text shape alone. Turning grounding's *default* off
doesn't touch that path. What genuinely still needs grounding is the
narrower residual fallback (§7.6) — a heading with no number and no
family keyword, recognized only via the model's own tag — which stays
fully coded and available, just unable to fire for a job OCR'd with
grounding off. Also: grounding is a *per-job* setting frozen into
`engine_params` at job creation, not a permanent global switch — nothing
stops a future job from opting in via the checkbox.

### 8.2 Why gundam mode is actually slower — checked directly, not assumed

Asked directly whether the timing difference was documented anywhere or
just speculation. Investigated the actual cached model implementation
(`modeling_unlimitedocr.py`, the `trust_remote_code` file the GPU service
downloads and runs — same architecture family as the official
`deepseek-ai/DeepSeek-OCR`, confirmed by matching class names and function
signatures):

- `crop_mode=True` runs `dynamic_preprocess()`, which splits the page into
  2-32 separate 640×640 tiles based on aspect ratio.
- The model then runs a full vision-encoder pass (SAM + CLIP) over those
  tiles as one batched forward pass, **in addition to** the same
  global-view pass base mode already runs — base mode
  (`crop_mode=False`) only ever runs that one global-view pass.
- Gundam mode also feeds the LLM roughly 100 extra image tokens per tile,
  lengthening the input sequence for autoregressive decoding.

This exact design ("n×640×640 tiles + a 1024×1024 global view") and its
cost ("involves more computation than simpler modes due to the multi-scale
processing approach") is stated directly in DeepSeek-OCR's own published
documentation
([HuggingFace model card](https://huggingface.co/deepseek-ai/DeepSeek-OCR);
[arXiv:2510.18234](https://arxiv.org/html/2510.18234v1)) — confirmed
externally, not this project's own inference. (Attributing the *specific*
130-280s gap precisely to that extra pass, rather than something else, is
reasoning from the code — no per-stage profiling exists in this repo to
confirm the exact breakdown.)

### 8.3 Advantages comparison — what each config gives for indexing

**Base config (`crop_mode=False`, grounding off):**
- Clean markdown text only, no layout tags.
- Structure detection still fully works via all four text-based
  mechanisms from §7: exact keyword+number (`CORE_RE`), fuzzy keyword
  recovery, decimal-heading detection, and generic unnumbered triggers
  (`detect_subdoc_trigger`).
- Cannot catch a heading with *no* family keyword and *no* number at all
  (e.g. a bare "SCOPE OF WORK") — it silently gets absorbed as body text;
  nothing flags that it happened.
- ~85-105s/page.

**Gundam config (`crop_mode=True`, grounding on):**
- Everything base config gives, plus:
  1. The *only* path that catches the keyword-less/number-less heading
     case above (the residual layout-only fallback, §7.6).
  2. Extra corroboration on already-detected headings — `layout_title`/
     `layout_header` as a positive cross-check, `non_heading_layout_type`
     as a negative one that can catch a citation-shaped false positive.
  3. Bounding boxes — not used by the current nesting logic (rejected
     after §7.4 showed position-based nesting fails on real documents),
     but available for future use.
  4. Per DeepSeek-OCR's own documentation, gundam's multi-scale tiling is
     generally described as the model's higher-resolution mode, intended
     for documents with dense/small text. Whether that also measurably
     improves *plain transcription accuracy* on this project's documents
     (separate from the tag output) has not been A/B tested here — that's
     the model's stated general design intent, not a finding of this
     project's own testing.
- ~212-380s/page (2-4x slower), confirmed mechanism per §8.2.

### 8.4 Decision

Asked directly, given grounding is per-job (not permanent): what should
the *default* be for new jobs?

**User's decision: default off, opt-in per job.** Fast by default; a user
who suspects a specific document has the keyword-less/number-less heading
case can check the grounding box for that job and accept the timing cost.

**Action status: the runtime setting itself was intentionally NOT
flipped this session.** The user chose to write this documentation file
first (see §9) and defer actually changing `/app/data/settings.json`'s
`enable_unlimited_grounding` value back to `false` to a later step.

---

## 9. This documentation file

**Request:** document every decision, input, and output from this entire
session in one durable file, so the reasoning survives past the diffs.

**Constraint hit:** the request arrived while still in Plan Mode, which
restricts file writes to the plan file only. Asked the user how to
sequence this against the still-pending OCR-speed action item (§8.4).
**Decision:** skip acting on the OCR-speed setting for now; exit plan mode
solely to write this file. Asked for a more meaningful filename than a
generic "session log"; named
`OCR_INDEXING_AND_PERFORMANCE_DECISIONS.md`, matching the existing
`CROSS_REFERENCE_DESIGN_V2.md` naming precedent already in this repo.

---

## Outstanding, not yet acted on

- **§8.4's decided fix is not yet applied**: `/app/data/settings.json`'s
  `enable_unlimited_grounding` is still `true` in the running container.
  Flipping it to `false` via `POST /api/settings` (or unchecking the
  Advanced Settings checkbox) is the next concrete action, whenever the
  user wants it done.
- **§7.7's container restart is not yet done**: the running
  `ocr-services-glm-ocr-api-1` API process still has the pre-fix
  `reference_index.py` loaded in memory; the fixed file is on disk
  (copied in directly) and in the repo, but won't actually serve until
  the process restarts/redeploys.
