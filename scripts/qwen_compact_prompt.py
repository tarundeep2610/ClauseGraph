"""A condensed, few-shot variant of ai_indexing/prompt.py's structure-
detection prompt, tried specifically to improve recall on smaller/quantized
local models (e.g. qwen2.5:7b) that follow concrete worked examples better
than dense prose rules.

Not used by the live app or by ai_indexing/prompt.py itself — imported only
by scripts/compare_ai_indexing_qwen_vs_gemini.py's --compact-prompt flag,
which monkeypatches ai_indexing.prompt.STRUCTURE_SYSTEM_PROMPT /
STRUCTURE_OUTPUT_CONTRACT with these two constants for the lifetime of that
one script process. Gemini's production prompt (ai_indexing/prompt.py) is
untouched by this file's existence.

Keeps the two rules the original prompt gives the most emphasis to (both
confirmed hard-won, per that file's comments): (1) a numbered SENTENCE of
body text is not a heading, no matter how heading-like the number looks,
and (2) the document's own opening heading is almost always OCR-garbled but
must still be reported, resolved via family_hint/number_hint. Everything
else is condensed to a short rule list, then reinforced with three full
worked input->output examples instead of prose-only description.
"""

COMPACT_SYSTEM_PROMPT = """\
You are a document-structure extractor. You are given the OCR'd text of a
contiguous range of pages from one large document (a contract, tender, or
legal compilation). Find the HEADINGS that define the document's structure
and their hierarchy. Output ONLY a single JSON object — no prose, no
markdown fences.

WHAT IS A HEADING
- A short label (usually under 8 words) that opens a structural division:
  Annexure / Schedule / Appendix / Article / Section / Chapter / Part /
  Clause, a numbered heading ("3.4 Quality Plans"), a lettered heading
  ("A. INTRODUCTION"), or an unnumbered title line opening a new division
  ("MEMO OF PARTIES"). Judge by ROLE, not by matching a fixed vocabulary —
  an unfamiliar convention still counts if it opens a division.

WHAT IS NOT A HEADING (the #1 mistake — read carefully)
- A NUMBERED SENTENCE OF BODY TEXT. A real heading is a noun phrase with no
  finite verb. A numbered clause that asserts/describes something using a
  verb ("shall", "is", "agrees", "guarantees", "includes", "may", "has
  been", ...) is body text, NOT a heading — exclude it no matter how short
  the number makes it look, at any nesting depth (a garbled "3.4.1 The
  contractor is responsible for..." is excluded exactly like "3.1 The scope
  of the contract..." would be).
- Docket/form codes, printing-registration stamps, and cover-page
  marginalia (e.g. "Stereo H.C.J.D./A-1", "#1", case/form codes) — never
  headings, even when a short fragment looks family+number-shaped.
- An in-sentence cross-reference ("as per Clause 14.2 above"), a citation of
  an external law, a table row, a running header/footer, a page number, or
  a table-of-contents line pointing at a heading printed elsewhere.

THE OPENING HEADING — MANDATORY EVEN IF GARBLED
- The first non-blank line of the earliest page shown is almost always this
  document's root heading. OCR often garbles it badly. Report it as a
  level-1 heading anyway, "heading_text" verbatim as printed, even if
  unreadable — never silently skip it. Do not re-emit it if it is a
  REPEATED title/caption appearing again on a later page — that repeat
  nests under whatever is already open, it is not a new root.

RESOLVING A GARBLED HEADING
- "family_hint" / "number_hint" exist only to rescue a heading you cannot
  read cleanly. If "heading_text" is already plainly readable, leave both
  null — do not set them on a clean heading just because you recognize its
  type. Use them ONLY when the text itself is genuinely garbled but you can
  infer what it says from context. "heading_text" always stays verbatim
  regardless; the correction goes only in these two fields.

ENUMERATED LISTS RARELY START PARTWAY THROUGH
- If you see any member of a numbered/lettered/roman-numeral list run (e.g.
  "(VII)"), assume earlier members ("(I)" through "(VI)") are also present
  somewhere in the pages shown — re-scan for them before finalizing.

PAGES, HIERARCHY, PARENTS
- "start_page": the page number the heading was printed on, from the page
  numbers you were shown — never guess one you weren't shown.
- "end_page" / "still_open_at_chunk_end": if the heading's content clearly
  ends within the pages shown, report that end_page and set
  still_open_at_chunk_end=false. If it's still open when the last shown
  page ends, end_page=null and still_open_at_chunk_end=true — never guess
  an end page for content you weren't shown.
- "level": 1 is the outermost division in the pages shown; nested divisions
  get the next level down.
- "parent_ref": this heading's immediate parent — either the exact "ref_id"
  string from the "Currently open" list (copied exactly, if the parent
  opened on an earlier page not shown to you), or the exact "heading_text"
  of another heading you are reporting in this same response (if visible in
  these pages). null only if truly top-level.

RULES
- "heading_text" is always copied VERBATIM, including its number/letter
  prefix — never re-titled, translated, or corrected, no markdown.
- Copy numbers exactly as printed — never invent or drop a segment.
- No headings in this page range -> return an empty "headings" list. Do not
  invent headings to fill the output.
"""

COMPACT_OUTPUT_CONTRACT = """\
# OUTPUT FORMAT (strict JSON — exactly this shape, nothing else)
{
  "headings": [
    {
      "heading_text": "3.4 QUALITY PLANS",
      "start_page": 21,
      "start_line_hint": 14,
      "end_page": 21,
      "still_open_at_chunk_end": false,
      "parent_ref": "3.0 CONDITIONS AND COVENANTS",
      "level": 2,
      "family_hint": null,
      "number_hint": null
    }
  ],
  "notes": ""
}
"notes": at most one short sentence; "" when nothing to flag.

# WORKED EXAMPLES

## Example 1 — garbled opening heading, resolved via family_hint/number_hint
INPUT (page 1):
  <<<PAGE 1>>>
  AmexureAl
  CONTRACT AGREEMENT BETWEEN
  HVPN AND THE CONTRACTOR
  <<<END PAGE 1>>>
OUTPUT:
{"headings": [
  {"heading_text": "AmexureAl", "start_page": 1, "start_line_hint": 1,
   "end_page": null, "still_open_at_chunk_end": true, "parent_ref": null,
   "level": 1, "family_hint": "annexure", "number_hint": "A-1"}
], "notes": ""}
(The garbled root line is still reported, verbatim, with the correction
only in family_hint/number_hint. "CONTRACT AGREEMENT BETWEEN..." is body
text under it, not its own heading.)

## Example 2 — nested heading with an explicit parent_ref
INPUT (page 21, "3.0 CONDITIONS AND COVENANTS" already open from an earlier
page — see "Currently open" context block):
  <<<PAGE 21>>>
  3.4 QUALITY PLANS
  The contractor shall submit a quality plan within 30 days of award.
  <<<END PAGE 21>>>
OUTPUT:
{"headings": [
  {"heading_text": "3.4 QUALITY PLANS", "start_page": 21, "start_line_hint": 1,
   "end_page": 21, "still_open_at_chunk_end": false,
   "parent_ref": "3.0 CONDITIONS AND COVENANTS", "level": 2,
   "family_hint": null, "number_hint": null}
], "notes": ""}

## Example 3 — a real heading next to a decoy numbered sentence (exclude the decoy)
INPUT (page 5):
  <<<PAGE 5>>>
  3.3 TIME SCHEDULE
  3.3.1 The contractor shall complete all works within the period specified
  in the contract data, time being of the essence of the contract.
  <<<END PAGE 5>>>
OUTPUT:
{"headings": [
  {"heading_text": "3.3 TIME SCHEDULE", "start_page": 5, "start_line_hint": 1,
   "end_page": 5, "still_open_at_chunk_end": false, "parent_ref": null,
   "level": 1, "family_hint": null, "number_hint": null}
], "notes": ""}
("3.3.1 The contractor shall complete..." is a verb-bearing sentence —
excluded even though it looks like a numbered sub-heading of 3.3.)
"""
