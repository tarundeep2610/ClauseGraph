# OCR Services — Project Description

*(Reference material for resume generation. Written as a factual technical brief, not as resume copy.)*

---

## 1. One-paragraph summary

A production document-intelligence platform that turns scanned, non-searchable PDFs — primarily long-form Indian legal filings and commercial contracts — into structured, navigable, machine-readable Markdown. It combines a GPU-backed vision-language OCR pipeline with a hybrid rule-based + LLM document-structure indexer, so that a 900-page court paper book becomes not just searchable text, but a navigable outline with working internal cross-references ("see clause 2", "Annexure A-1") that a reader can click to jump to the cited passage. Built as two cooperating FastAPI microservices (a CPU orchestration/API tier and a GPU inference tier) with a React single-page viewer front end, SSO authentication, per-page confidence scoring, and an automated quality-escalation and human-review workflow.

---

## 2. Problem being solved

Scanned legal and contractual documents in this domain are:

- **Not searchable** — they are page images, so neither Ctrl-F nor any downstream RAG/LLM pipeline can consume them.
- **Very long** — validated against real 25-page, 100-page, and 900+ page samples of an Indian High Court paper book.
- **Compound documents** — a single PDF bundles a petition, a lower-court judgment, grounds of appeal, affidavits, and several annexed contracts, each with its own independent numbering scheme (clause 1, section 1, paragraph 1 can all exist multiple times in the same file and mean different things).
- **Densely self-referential** — the text constantly says "see clause 14.3," "Annexure A-1 to A-5," "para 2, line 10." Without tooling, a reader must manually hunt through a scanned image to resolve each one, then find their way back.

Plain OCR solves only the first of these. This project targets all four: accurate transcription, *plus* recovery of the document's logical structure, *plus* resolution of its internal reference graph.

---

## 3. System architecture

### 3.1 Two-service split

**CPU orchestration service** (`main.py`, ~83k LOC file; FastAPI + Uvicorn, Python 3.11)
Owns everything except model inference: HTTP API, authentication, job queue, PDF rasterization (`pypdfium2`), persistence, confidence scoring, cross-reference indexing, export, and serving the built React front end.

**GPU inference service** (`gpu-ocr-service/`, separate FastAPI app + Docker image)
Owns only model loading and inference for the `baidu/Unlimited-OCR` vision-language model (PyTorch/Transformers). Exposes a small async job API (`POST /api/v1/jobs` → 202, `GET /api/v1/jobs/{id}` poll, `DELETE`, `cancel-all`), guarded by a shared-secret header and an inference semaphore so GPU memory is never oversubscribed.

The two tiers talk over a **Tailscale private mesh network** rather than the public internet. The split was a deliberate design decision: it keeps the heavy `torch`/`transformers`/CUDA dependency tree, and the multi-gigabyte image it implies, entirely out of the API tier, whose image stays small and CPU-only. A documented `OCR_LOCAL_ENGINE=true` fallback runs the engine in-process for laptop development with no GPU box available.

### 3.2 Front end

React 18 + Vite SPA, built in a multi-stage Dockerfile and served as static assets by the same FastAPI process. Key choice: the PDF is rendered with **pdf.js on a `<canvas>`** rather than a browser-native `<iframe>` viewer, because the app needs programmatic page navigation, anchor-accurate scroll targets, and synchronized dual-pane (PDF ↔ extracted text) page tracking that a native viewer does not expose.

Notable front-end surface area: a split-pane viewer with page-synced navigation, an editable/drag-and-drop structure tree (`@dnd-kit`), a cross-reference popover with stratified candidate ranking, pinned-reference reading panes, per-page reference badges, a retry/review queue UI, an advanced engine-settings panel, and light/dark theming.

### 3.3 Persistence

SQLite in WAL mode, accessed through a single `database.py` data-access layer (~63k LOC file) with periodic checkpointing, a busy-timeout policy, and forward-only schema migrations tracked in a `schema_meta` table. Thirteen-plus tables, including `ocr_jobs`, `ocr_pages`, `ocr_page_attempts` (full per-attempt audit trail), `ocr_reference_targets`, `ocr_reference_mentions`, `api_keys`, and four separate structure-override tables.

---

## 4. The OCR pipeline

Submitted jobs run as asyncio background tasks through `task_queue.py`. Per page:

1. **Render** — `pypdfium2` rasterizes at a configurable scale, serialized behind a render lock (pypdfium2 is not safe for concurrent access to one document handle).
2. **Dispatch** — the page image is submitted to the GPU service. Throughput is governed by a *module-level* semaphore shared across every job the process handles, so the GPU concurrency budget is genuinely global rather than per-job — with a second, nested semaphore reserving a bounded share for escalation work, so retries can never starve first-pass throughput.
3. **Score** — `confidence.py` computes a confidence value from structural signals on the output. Because neither OCR engine returns real token probabilities, confidence is synthesized from a dozen independent signals: blank-page ink ratio, degenerate character runs, duplicated-line ratio, foreign-script ratio, minimum text length, source-image blur and contrast (computed from the rendered image), cross-engine agreement via `difflib`, and a table-continuity check at page boundaries. Every raw signal value is persisted so thresholds can be re-tuned from production data later rather than re-guessed.
4. **Escalate** — pages below the escalation threshold are automatically re-run at a higher render scale before being surfaced to a human, so operator review time is spent only on pages a second machine pass genuinely could not fix.
5. **Flag / audit** — pages still below the confidence threshold enter a review queue. Independently, a configurable random sample of *high-confidence* pages is also audited (optionally cross-engine), so silent systematic failures are caught rather than assumed away.
6. **Index** — the page's cross-reference index is recomputed immediately, inside a failure boundary that guarantees a detection bug degrades to "this page's links are stale," never to a failed OCR job.

Supporting modules: `chunking.py` (overlapping vs. gapless page partitioning for parallel and batch inference), `context_primer.py` (best-effort rolling context so sequential pages don't lose a mid-table or mid-sentence thread), and `seam_stitching.py` (a global post-hoc pass over *every* consecutive page pair, which is the actual continuity guarantee, with two export modes — one preserving every `## Page N` heading for citation and RAG chunking, one merging split tables into valid continuous tables for pandas/SQL consumers).

Operational controls: pause-at-next-page-boundary, resume, per-page retry, bulk page retry, job-level stop-all, graceful shutdown with idle detection, and a complete per-attempt history.

---

## 5. The distinguishing feature: cross-reference and structure indexing

This is the largest and most technically substantial part of the project (`reference_index.py` alone is ~167k LOC worth of file; `ai_reference_index.py` another ~67k).

### 5.1 The algorithm

The indexer detects **targets** (the definition sites: a heading that *is* clause 14.3) and **mentions** (the citation sites: body text that *refers to* clause 14.3), then resolves mentions to targets. Hard parts it handles explicitly:

- **Cross-page scope tracking.** A scope-stack state machine tracks Article → Section → Clause nesting across the whole document, so "clause 2" inside Article VII resolves differently from "clause 2" inside Article IX.
- **Bound-in sub-document boundaries.** Detects where one document ends and an annexed one begins (`ANNEXURE`, `IN THE COURT OF`, `VERSUS`, `GROUNDS OF APPEAL`, `IMPUGNED ORDER`, …) and resets numbering scope accordingly — without this, every repeated "clause 1" in a 900-page bundle collides.
- **External-statute exclusion.** "Section 34 of the Arbitration Act 1996" is a reference to external law, not an in-document target, and must not be linked. Handled by dedicated signal patterns (`Act|Code|Rules|Regulations` + year, `CPC|IPC|CrPC`, etc.).
- **OCR-tolerant matching.** Every key also gets a "collapsed" form so that an OCR'd `l` vs `1` or `O` vs `0` still resolves. Genuine collisions are surfaced to the reader as a visible "Possible OCR Alternatives" group rather than silently guessed at — a design decision reached only after an earlier version was found to trade link noise for *silent misdirection*, which is strictly worse.
- **Out-of-order and retry safety.** Pages complete out of order under concurrency, so index updates use a concurrency-controlled "frontier + bounded cascade" mechanism: a page whose predecessors haven't landed yet is marked provisional and re-resolved when the frontier advances, with the cascade bounded so a retried page never triggers an unconditional walk to the end of the document.
- **Fingerprint-based anchoring.** Rendered anchor placement matches on a content fingerprint rather than a positional index, because the back end's `mistune` parser and the front end's `marked` parser do not produce byte-identical block boundaries.

### 5.2 The hybrid AI layer

On top of the deterministic detector sits an optional LLM-based structure indexer, behind a provider abstraction supporting **Google Gemini**, **OpenAI-compatible endpoints**, and **local Ollama models** (`qwen3:8b` is the real deployment target; Gemini Flash is a stand-in that is deliberately *more* capable than the production model, so results are a known upper bound).

Design decisions that made this work in practice:

- **The AI replaces detection only, never the scope-stack machine.** Both paths return the identical `(candidates, new_scope)` contract, so every downstream consumer — scope-range computation, the structure tree, the viewer — required zero changes to support the AI path.
- **Chunked dispatch with per-chunk fallback and per-page persistence.** One failed or rate-limited chunk degrades only that chunk's pages to the rule-based detector; it never fails the job.
- **Heading grounding.** The LLM returns headings as text; a windowed fuzzy-matching layer grounds each one back to a specific block on a specific page, with a deliberate rejection rule for same-shaped-but-differently-numbered candidates (a confirmed real false-positive class: "3.3 TIME SCHEDULE" landing on an unrelated page).
- **A deterministic safety net under a probabilistic model.** When the LLM reports sibling list items at inconsistent depths, a code-level marker-style check (`_enumeration_marker_style`) flattens them back. The project's working principle here is that LLM output is a *prior*, not an authority — rule-based machinery sits both above and below it.
- **Position-bias mitigation.** When the model is asked bounded multiple-choice questions ("which open ancestor does this heading belong under?"), the candidate ordering was deliberately inverted to list the safest/innermost option first and "top-level" (the worst, hardest-to-undo answer) last — a direct response to measured, reproducible first-option bias where the model answered two *identical* decisions differently between calls.

### 5.3 Measurement discipline

A custom offline evaluation harness (`scripts/score_reference_index.py`) scores the indexer against hand-built gold fixtures over N repeated trials, reporting **target precision, target recall, scope-path exact-match accuracy, family-drift count** (a custom metric for the specific damaging error class where content is captured but misclassified into the wrong structural family — invisible to precision/recall alone), and **mention precision/recall**. Supporting scripts run controlled A/B arms across chunk size, chunk overlap, prompt variants, and provider (Gemini vs. Qwen).

This harness is what turned "the AI indexing feels inconsistent" into measurable statements — e.g. establishing that target precision held at 1.000 while recall varied 0.77–0.99 across identical runs, and that *run-to-run variance*, not mean accuracy, was the real production blocker at the target model tier. Multiple hypotheses were tested and rejected against real production data rather than synthetic examples, including one case where a widely-assumed fix (a `<|grounding|>` prompt token) was disproved by direct testing and the real mechanism (`crop_mode=True`, switching the model's image-tiling configuration) identified instead — independently corroborated afterwards against an upstream project issue.

### 5.4 User override layer

Auto-detection is never the last word. A separate persisted override layer (`structure_overrides.py`) records manual adds, edits, deletes, and drag-and-drop reparent/reorder operations in **tables the reindex path never reads or writes**. The result is a structural guarantee rather than a special case: a "Rebuild Cross-References" click can wipe and fully regenerate the entire detected index without destroying a single user correction. Overrides are applied as a pure read-time merge; nothing is ever written back into detected rows.

---

## 6. Security and operations

- **SSO via AuthNexus/Zitadel** — RS256 JWT validation against a cached JWKS endpoint (1-hour TTL, lock-guarded refresh), with project-scoped role extraction. A token carrying roles for a *different* project is explicitly treated as unauthorized rather than silently falling back to another project's roles.
- **Dual authentication** — interactive OIDC for humans, hashed API keys for machine integrations, unified behind a single `Principal` abstraction so every route's authorization logic is identical regardless of caller type.
- **RBAC** on admin-only routes (shutdown, stop-all, settings), with a dedicated route-level test suite.
- **Defensive HTTP layer** — strict Content-Security-Policy, `X-Frame-Options: DENY`, CORS policy, per-principal sliding-window rate limiting with `Retry-After`, and streaming upload size enforcement that rejects oversized files *without* buffering them fully into memory.
- **Secret hygiene** — AI provider credentials are read live from the environment and never persisted to the database; a dedicated redaction module keeps them out of logs and error payloads.
- **Containerized deployment** — multi-stage Docker build (Node frontend build stage → slim Python runtime), Docker Compose with named-volume persistence and `restart: unless-stopped`, Caddy in front of the GPU service, and a 12k-word `DEPLOYMENT.md` runbook.

---

## 7. Engineering practice

- **~940 automated tests** — 663 Python (pytest, including async pipeline tests) and 284 JavaScript (Vitest + React Testing Library) — across 53 Python and 20 front-end test files. Coverage is deliberately weighted toward the hard parts: 46 tests on the task-queue pipeline alone, 36 on the AI scope-stack machine, and roughly 180 across the cross-reference detection, scope-range, and mention-resolution modules.
- **Test-first on algorithmic work.** The core detector was deliberately split into a pure, synchronous, DB-free function (`build_targets_for_page`) and a thin async orchestration wrapper — specifically so the algorithm is fully unit-testable in isolation from persistence and concurrency.
- **Real-data-first calibration.** The layout-formatting module's rules were derived and verified against 3,513 real bounding boxes pulled from a production job, not synthetic fixtures — a discipline adopted after an earlier draft of that same module was found to contain five real bugs that synthetic tests had not caught.
- **Written decision records.** Three substantial design documents are maintained alongside the code: a 34k-word technical design for the cross-reference system (carrying its own revision history across four adversarial review rounds, each of which found that the *previous* round's fixes had introduced new problems), a 29k-word decision log recording what was tried, rejected, and why, and a 30k-word accuracy investigation. These exist so the reasoning survives past commit messages and diffs.
- **Trunk-based feature branches** — four sequential feature branches (`auth-and-gpu-ocr-service`, `chunked-ocr-confidence-and-review`, `failsafe-persistence-and-history`, `structured-cross-reference-indexing`) over 44 commits.

---

## 8. Scale and technology summary

| | |
|---|---|
| **Codebase** | ~30,000 lines Python, ~9,900 lines JavaScript/JSX (excluding tests' share where separable) |
| **Tests** | ~940 automated (663 Python / 284 JS) |
| **Timeline** | August 2026 – ongoing (~2 months of active development at time of writing) |
| **Backend** | Python 3.11, FastAPI, Uvicorn, asyncio, SQLite (WAL), pypdfium2, Pillow, mistune, httpx, python-jose |
| **ML / inference** | PyTorch, Transformers, `baidu/Unlimited-OCR` vision-language model, GLM, Ollama, Google Gemini API, OpenAI-compatible APIs |
| **Frontend** | React 18, Vite, pdf.js, marked, DOMPurify, @dnd-kit, oidc-client-ts, React Router |
| **Testing** | pytest, pytest-asyncio, Vitest, React Testing Library, jsdom |
| **Infrastructure** | Docker, multi-stage builds, Docker Compose, Caddy, Tailscale mesh networking, NVIDIA GPU runtime |
| **Auth** | OIDC / Zitadel (AuthNexus), RS256 JWT + JWKS, hashed API keys, RBAC |

---

## 9. Points worth emphasizing for a resume

Ranked by how distinctive they are:

1. **Designed and built a hybrid rule-based + LLM document-structure indexer** that resolves in-document cross-references across 900-page compound legal documents, including cross-page scope tracking, sub-document boundary detection, and external-statute exclusion.
2. **Built a custom offline evaluation harness** with precision/recall/scope-accuracy and a bespoke "family drift" metric, and used it to run controlled multi-arm A/B experiments across models, chunk sizes, and prompt variants — converting subjective quality complaints into measured, reproducible findings.
3. **Diagnosed and mitigated LLM position bias** in a production system through controlled repeated trials, then designed deterministic safety nets so probabilistic model output could not corrupt downstream structural state.
4. **Architected a two-tier CPU/GPU microservice split** over a private mesh network, keeping a multi-gigabyte ML dependency tree out of the API tier while preserving a single-process local-development path.
5. **Engineered a confidence-scoring and automated-escalation system** that synthesizes a dozen structural and image-quality signals where no model-native confidence exists, cutting human review to only the pages a second machine pass could not fix.
6. **Solved concurrency correctness** for an incrementally-built index over out-of-order, retryable page completions (frontier + bounded cascade, provisional-state resolution).
7. **Designed a user-override layer whose durability is structural, not conditional** — full index regeneration cannot destroy manual corrections, by construction.
8. **Maintained ~940 tests** and formal written design/decision records across a 30k-line production codebase.

---

## 10. Honest caveats

Included so the resume does not overstate anything:

- The AI indexing layer is **functional but variance-limited** at the small-model tier it targets. Target precision is excellent (1.000 across measured runs); recall varies run-to-run (0.77–0.99 measured). This is a known, documented, consciously-accepted trade-off, with the deterministic rule-based detector always available as a per-chunk fallback. It should be described as "built and measured," not "solved."
- Two specific recall gaps and one parent-resolution drift are documented as outstanding.
- The project is **actively under development on a feature branch**, not frozen and shipped.
- Per-heading AI parent resolution remains behind a default-off feature flag pending further accuracy work.
