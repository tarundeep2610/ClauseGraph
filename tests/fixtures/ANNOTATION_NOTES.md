# Gold action-items fixture annotation standard

Two judgment calls came up repeatedly while auditing/correcting
`gold_action_items_*.json` (2026-10-28) and need a written-down rule so
future edits follow the same logic instead of drifting per-editor. Not a
new audit — this documents decisions already made.

## 1. Restatement rule: when does the same fact get counted twice?

A real-world requirement sometimes appears more than once in the same
document, in different sections. Whether each occurrence is separately
countable in gold depends on *why* it recurs:

- **Separately countable**: the restatement is a deliberate, named,
  structurally distinct section serving a different functional purpose than
  the original — not just a shorter preview of it. Precedent:
  `gold_action_items_89cfa529.json` (tender-135p) counts both
  "Section-II: Eligibility Criteria" (pages 8-9) and "Annexure XVI: Check
  List" (pages 130-131) separately, even though they list the same ~10
  criteria almost verbatim — Annexure XVI is explicitly named and
  cross-referenced elsewhere in the document ("mention page no. of the
  documents attached... as per Annexure-XVI 'Check List'") and serves a
  distinct purpose (a bidder self-verification checklist), not a mere
  summary of Section-II.
- **NOT separately countable**: the "restatement" is a condensed
  summary/preview that doesn't carry the full substantive content, or a
  sentence fragment that merely resurfaced due to grounding-window drift
  (the model misreporting which page a real, already-captured clause is
  on). Precedent 1: `gold_action_items_30d148dd.json` (dfccil) does NOT add
  a separate item for page 1's "Bid Details" cover-sheet row ("Past
  Experience of Similar Services required: Yes", a bare Yes/No flag,
  platform-generated and identical in shape across every GeM bid document
  regardless of what's procured) — the substantive version, with the actual
  three cost-threshold alternatives, is captured once at page 4. Precedent
  2: `gold_action_items_89cfa529.json` (tender-135p) does NOT add a separate
  item for page 13's Oracle Partner Network mention (section 5.3) beyond
  the one already captured at page 9 — it's the same clause, not a distinct
  section.
  Test: does the second occurrence carry the FULL substantive content on
  its own (criteria + specific document/threshold detail), in a section
  with its own distinct name/purpose? If yes, count it separately. If it's
  a shorter preview or an incidental re-mention, don't.

## 2. Granularity rule: when does a compound sentence split into multiple items?

See `ai_indexing/prompt.py`'s "PARALLEL PROHIBITED ACTIONS SHARING ONE GATE
ARE ONE ITEM" section for the live-pipeline-facing version of this rule;
gold fixtures should follow the same split:

- **One item**: parallel PROHIBITED ACTIONS sharing one trigger/consequence
  in a single sentence (e.g. "shall not assign... nor sub-contract...
  without prior written consent" — one condition item, not two).
- **Separate items, one per document**: parallel REQUIRED
  DOCUMENTS/DELIVERABLES in a single sentence, even when grammatically
  similar in shape (e.g. "must upload Balance Sheets, P&L Statements, and
  GST Returns" — each named document is independently verifiable and
  countable).
