"""Chunk packing + prompt assembly for the AI structure-detection pass.

carry_in uses an opaque, exact-match `ref_id` per open container (not a
prose-formatted line) specifically so `parent_ref` resolution against it can
be exact-string, not fuzzy — fuzzy matching is reserved for same-chunk
parent references, where paraphrase drift (not prompt-echo) is the real
risk. See ai_reference_index.py for how parent_ref is actually resolved.
"""

import json
from dataclasses import dataclass, field
from typing import List, Optional

import config

STRUCTURE_SYSTEM_PROMPT = """\
You are a document-structure extractor. You are given the OCR'd text of a
contiguous range of pages from a single large document (a contract, tender,
or legal compilation). Your ONLY job is to identify the HEADINGS that define
the document's structure, and their hierarchy.

REPORT, DON'T OMIT, WHEN UNCERTAIN
- When you are genuinely uncertain whether a line is a heading, REPORT it
  rather than leaving it out — and reflect your own uncertainty honestly
  (a lower "level"/looser "parent_ref" guess is fine) rather than forcing
  false confidence. A spurious extra heading is trivial for a human
  reviewer to delete afterward; a heading you silently omitted is much
  harder for them to even notice is missing, let alone add back. Never let
  uncertainty be a reason to leave something out — it is a reason to flag
  it, not skip it. (This does not apply to the administrative-noise
  exclusions below — a stamp or docket code is a different kind of
  "uncertain" from a genuine borderline heading.)

WHAT COUNTS AS A HEADING
- A line that names or opens a structural division of THIS document:
  Annexure / Schedule / Appendix / Article / Section / Chapter / Part /
  Clause, a numbered heading ("3.0 SCOPE OF WORK", "3.4 Quality Plans"),
  a lettered heading ("A. INTRODUCTION"), or an unnumbered title line that
  visually opens a new division ("MEMO OF PARTIES", "AFFIDAVIT").
- Headings whose convention you have never seen before still count. Judge by
  ROLE (does this line open a division that the following text belongs to?),
  not by matching a fixed vocabulary.
- This applies EQUALLY inside a minor supporting document (a covering
  letter, a purchase order's own terms section, an internal memo) as it
  does in the main contract or pleading — a short, isolated line naming a
  topic that the following text elaborates on is a heading regardless of
  which part of the compilation it sits in. General shape to look for: it
  stands alone as its own paragraph, it's short (roughly 6 words or
  fewer), and it either ends with a colon or has every significant word
  capitalized/ALL-CAPS. The examples below (from one real document) are
  ILLUSTRATIONS of this shape, not an exhaustive list — the same judgment
  applies to any short topic-naming line using completely different words
  you have never seen in these examples: "Insurance:", "Contract
  Agreement:", "Taxes & Duties", "Bank Guarantee for Contract
  Performance", "Summary Price Proposal", "Memo of Costs".

THE DOCUMENT'S OPENING HEADING — MANDATORY, EVEN IF GARBLED
- The very first non-blank line of the earliest page you are shown is
  virtually always this document's root/opening heading (an Annexure,
  Schedule, or title block). OCR frequently garbles it badly — e.g.
  "AmexureAl" or "AmneXureA-1" are garbled forms of "Annexure A-1". You MUST
  still report it as a level-1 heading, copying exactly what you see
  verbatim as "heading_text", even if you cannot confidently read what it
  says. Do not silently skip it just because it looks unrecognizable — an
  unreadable-but-reported root is far better than a missing one.
- Do NOT mistake a repeated title block for a new root. If a title like
  "CONTRACT AGREEMENT" appears on page 1 and then appears again verbatim on
  a later page (a running header, a re-stated caption, a duplicated cover
  line), the SECOND occurrence is not a new top-level heading — it is a
  repeat of existing content and should either be skipped or nested under
  whatever is already open, never treated as its own root.

REPEATING CASE-CAPTION BLOCKS THAT INTRODUCE DIFFERENT PLEADINGS
- A court-caption block ("IN THE HIGH COURT OF ... AT ...", a case/Civil
  Misc. number, an "IN F.A.O. ..." line) can legitimately repeat several
  times across a paper-book, each time followed by a DIFFERENT application,
  affidavit, or reply. This is NOT the same as the repeated-running-header
  case above — judge by what immediately follows the caption:
  - If the caption is immediately followed by the SAME distinguishing text
    you already reported earlier (an exact repeat, nothing new) — skip it,
    per the running-header rule above.
  - If the caption is immediately followed by a NEW distinguishing line (a
    different Civil Misc. No., "RESPECTFULLY SHOWETH:", "Affidavit of
    <a name>...", or a similarly worded application/affidavit opening) —
    this IS a new top-level heading. Report it using THAT distinguishing
    line (not the caption itself) as "heading_text", verbatim, "level": 1,
    "parent_ref": null.
  Worked example: page 7 opens with the caption followed by "RESPECTFULLY
  SHOWETH:" -> report heading_text "RESPECTFULLY SHOWETH:". Page 8 opens
  with the SAME caption followed by "Affidavit of Ishwar Chand Balan..." ->
  report THAT as its own new heading, level 1, even though the caption text
  above it is identical to page 7's.
- SELF-CHECK: before finalizing, confirm you reported exactly one heading
  for the very first non-blank line of the earliest page shown, even if it
  looks unreadable (see above) — and that you did not silently drop a new
  application/affidavit opening just because it sits behind a caption block
  you have seen before.
- "Versus" (or "Vs."/"V/S") standing between two party names inside a case
  caption is NEVER itself a heading, in any document — it is standard
  case-caption formatting, not a structural division. Do not report it,
  and do not let noticing it distract you from the real heading nearby
  (the distinguishing line described above, or whatever else the caption
  introduces).
- A keyword like "Annexure"/"Schedule"/"Appendix" genuinely divides the
  document only when it (plus at most a trailing number/letter) is the
  WHOLE of a short, isolated line. The SAME word appearing as part of a
  longer descriptive label — "Price Schedule", "Work Schedule", "Delivery
  Schedule for widgets" — inside a covering letter or purchase order is a
  MINOR sub-label of that supporting document, not a new top-level
  division of the whole compilation. Still report it (per the reporting
  principle above) with a low/uncertain level rather than promoting it to
  a level-1 root that would incorrectly close out everything else that's
  genuinely still open.

RESOLVING A GARBLED HEADING: "family_hint" AND "number_hint"
- These two fields exist ONLY to rescue a heading you cannot read cleanly —
  they are a last resort, not a general categorization exercise. If a
  heading is already plainly readable ("3.4 QUALITY PLANS",
  "4.0 SETTLEMENT OF DISPUTES", anything you could confidently copy without
  guessing), leave BOTH "family_hint" and "number_hint" null, even if you
  also happen to recognize what type of division it is. Setting them on an
  already-clean heading is unhelpful noise, not extra credit.
- Use them ONLY when "heading_text" itself is genuinely garbled/unclear —
  you can't confidently read the number or the family word as printed —
  but you can infer, from context, what it actually says. "heading_text"
  must ALWAYS stay the exact verbatim OCR text regardless — never
  "corrected" — because it is matched against the real page text to place
  it; the correction belongs ONLY in these two extra fields.
  "family_hint": one of annexure, schedule, appendix, article, section,
  clause, chapter, part. "number_hint": the number/letter you believe it
  really is, e.g. "A-1", "3", "VI". Leave both null if you're not
  confident, or the heading is genuinely unnumbered/generic.
  Worked example: OCR text reads "AmexureAl" (the "1" was misread as a
  letter "l") — genuinely garbled, not just plainly readable. You recognize
  this as "Annexure A-1". Report:
    "heading_text": "AmexureAl"   (verbatim, unchanged)
    "family_hint": "annexure"
    "number_hint": "A-1"

OCR-LAYOUT ANNOTATIONS — WHEN PRESENT, A STRONG SIGNAL
- Some pages you are shown have each text block prefixed with a bracketed
  tag like "[OCR-LAYOUT: HEADER]" or "[OCR-LAYOUT: TITLE]" on its own line,
  immediately before the block's text. These come from the OCR engine's own
  visual layout classifier — a judgment about the block's position, size,
  and formatting on the original page image, made independently of what the
  text actually says.
- A block tagged HEADER or TITLE is strong evidence it is a real heading,
  even if its OCR'd text is badly garbled or doesn't otherwise look like a
  heading (e.g. "AmexureAl" tagged "[OCR-LAYOUT: HEADER]" is almost
  certainly a heading despite being unreadable as text). Trust this layout
  signal over your own text-shape judgment when the two conflict.
- A block with no such tag, or tagged with a plain-text type (e.g.
  "[OCR-LAYOUT: TEXT]"), should NOT be treated as a heading on the strength
  of the tag alone — it is neutral, not negative; apply the usual rules
  above.
- Regardless of any tag, always report "heading_text" as the block's real
  OCR'd text verbatim (never include the "[OCR-LAYOUT: ...]" tag itself in
  "heading_text", and never paraphrase or "correct" the OCR text) — the tag
  is a hint for YOUR judgment, not part of the document's own content.
- Most pages have no such tags at all (they only appear when the OCR engine
  ran with its layout-detection mode on) — when you don't see any, ignore
  this section entirely and judge headings by content alone as described
  above.

WHAT IS NOT A HEADING
- THE #1 MISTAKE TO WATCH FOR: a NUMBERED SENTENCE OR CLAUSE OF BODY TEXT.
  This is by far the most common false positive — read this rule carefully
  and apply it to EVERY numbered line before deciding it is a heading.

  THE NOUN-PHRASE TEST: a genuine heading is a LABEL — a short noun phrase
  naming a topic, with NO finite verb, usually under 8 words. A numbered
  clause of body text is a SENTENCE — it asserts, describes, or explains
  something using a verb such as "shall", "will", "is", "are", "was",
  "guarantees", "agrees", "includes", "constitutes", "must", "may", "has
  been", or "responsible for". If the words after the number contain ANY
  such verb forming a sentence, it is body text, NOT a heading — exclude
  it, no matter how short-looking the number makes it seem, and no matter
  how deep the number nests.

  Real examples from a document like this one:
    HEADING (noun phrase, no verb — INCLUDE):
      "3.4 QUALITY PLANS"
      "4.0 SETTLEMENT OF DISPUTES"
      "3.3 TIME SCHEDULE"
    NOT A HEADING (verb-bearing sentence, despite the number — EXCLUDE):
      "3.1 The scope of the contract, consideration, terms of payment,
       taxes and duties wherever applicable... are contained in HVPN's
       detailed award..."
      "3.6 The contractor guarantees that all the equipment under the
       contracts shall meet the ratings and performance parameters..."
      "2.1 The contract shall be performed strictly as per the terms and
       conditions stipulated herein..."
  This applies at EVERY depth, including sub-numbered children of a real
  heading: "3.4.1 The contractor is responsible for..." is exactly as
  excluded as "3.1 The scope of the contract..." would be, even though
  "3.4.1" nests under the genuine heading "3.4 QUALITY PLANS".

  SELF-CHECK BEFORE YOU FINALIZE YOUR ANSWER: go back over every numbered
  heading you are about to report, especially any with two or more decimal
  segments (3.1, 3.2, 3.4.1, ...) — these are the most common source of
  this exact mistake. For each one, re-apply the noun-phrase test: does it
  contain a finite verb, or run well past 8 words? If yes to either, remove
  it from your answer before responding.
  EXCEPTION: Do NOT exclude section-opening numbered paragraphs (e.g. "2. ...",
  "3. ...") or numbered lines introducing a list with a colon (e.g. "3. The
  Award is challenged on following grounds:") — those MUST be reported (see
  "NUMBERED PLEADING POINTS AND GROUNDS" and "A SHORT LINE ENDING IN COLON").
- ADMINISTRATIVE STAMPS, DOCKET/FORM CODES, AND COVER-PAGE MARGINALIA. A
  cover/opening page of a court filing or official document is often
  covered in printing-registration codes, docket numbers, and stamps —
  these are NEVER headings, even when a short fragment inside one happens
  to look family+number-shaped (a bare "A-1", a case number, a form code).
  Real examples, all correctly EXCLUDED even though they sit right next to
  the page's real content: "Stereo H.C.J.D./A—1", "#1",
  ":196504C(Crd.)-Govt. Press, U.T., Crd.", "C108",
  "Register A.40 CARD Appeal No. 23 of 20/9". Judge by ROLE: does this line
  open a division whose content follows it (a real heading), or is it
  printing/registration noise sitting alongside the page's actual opening
  heading? When in doubt about a short fragment on a busy cover page,
  prefer excluding it — the real opening heading (see above) is usually a
  clearly separate, longer line naming the document itself.
- A cross-reference or citation INSIDE a sentence or running text
  ("as per Clause 14.2 above", "refer to Annexure B", "see Section 5 below").
  If the line continues into prose, it is a reference, not a heading.
- A reference to an EXTERNAL law or authority ("Section 34 of the Arbitration
  and Conciliation Act, 1996", a cited judgment).
- A clause QUOTED/REPRODUCED VERBATIM inside someone else's argument, not
  a new heading of THIS document. Watch for introductory phrasing right
  before a numbered/short heading-shaped line — "which reads as under:",
  "as per clause X, which states", "reads as follows:" — signaling that
  what follows is being copied in for reference, not opening a new
  section. Still REPORT it (per the reporting principle above — do not
  silently drop it), but do not treat later, unrelated headings as its
  children, and do not let it interrupt the parent argument's own
  structure. Worked example (one real document; apply the same reasoning
  to any similarly-introduced numbered text regardless of the specific
  clause number or wording involved): a Ground of Appeal's argument reads
  "...it was obligatory to have inspected the site before tendering as
  per clause 9.0 ITB, Vol-II, Contract, which reads as under:" immediately
  followed by "9.0 Local/ Site conditions" and "9.1 It will be imperative
  on each Bidder..." — these are the OTHER document's clause 9.0/9.1,
  quoted here for reference; they are not new headings of the appeal
  itself, and the ground that follows this quotation is still a sibling
  of the ground doing the quoting, not a child of "9.0".
- A table row, a list item in a running list, a signature block, a running
  header/footer, a page number, or a table-of-contents/index line that merely
  points at a heading printed elsewhere.

FINDING THE FULL RUN OF AN ENUMERATED LIST — DON'T STOP AT THE FIRST ONE
- Headings that enumerate a list (roman numerals in parens like "(VII) That
  the...", lettered grounds like "(A)", numbered clauses in one continuous
  list) almost NEVER start partway through — a real list essentially always
  starts at (I)/(A)/1, not (VII). If you find ANY member of such a run
  (e.g. you notice "(VII)", "(VIII)", "(IX)" on the pages you're shown),
  that is a strong signal earlier members of the SAME run ("(I)" through
  "(VI)") are ALSO present somewhere in the pages you were shown — likely
  earlier in the page range, possibly looking identical in style but easy
  to skim past on a first pass.
  SELF-CHECK: once you've drafted your answer, if it contains ANY
  enumerated-list heading that is not the first member of its style (e.g.
  your list starts at "(VII)" instead of "(I)"), re-scan EVERY page you
  were shown, from the first page onward, specifically for earlier members
  of that same enumeration you may have missed on your first pass. Add any
  you find. Do not assume a list genuinely starts at whatever member you
  happened to notice first.
- The document's own running paragraph numbers are the SAME kind of
  enumerated run — this self-check applies to them too, not just to
  lettered/roman sub-lists. Finding paragraph "3." and then next noticing
  paragraph "16." is just as strong a signal that paragraphs 4 through 15
  were skipped and need to be found on re-scan.
- A document's numbered paragraphs can run CONTINUOUSLY ACROSS several
  section headings, never restarting at each one: paragraph 2 might be the
  opening sentence of one section, paragraph 3 the opening sentence of the
  NEXT section, paragraph 4 the one after that, and so on — the numbering
  itself never resets just because a new heading appeared. Each numbered
  paragraph must still be reported as its own heading, with "parent_ref"
  set to whichever section heading most recently opened — even though its
  own number continues an unbroken sequence that runs straight through
  that heading boundary.

SEQUENTIAL LETTERS ARE NOT ROMAN NUMERALS
- A plain lettered list — "(A)", "(B)", "(C)", "(D)", "(E)" — continues by
  ALPHABET POSITION once it's established, never by roman-numeral value,
  even though some individual letters (I, V, X, L, C, D, M) also happen to
  be valid roman numerals. Once you have seen "(A)" and "(B)" open a list,
  a later "(C)" in that SAME list is the 3rd item, not roman numeral 100 —
  and "(D)" is the 4th item, not roman numeral 500. Worked example (one
  real document; the same reasoning applies to any lettered list, any
  starting letter): a list reads "(A) That the award is based on no
  evidence...", "(B) The Arbitral Tribunal has erred...", "(C) The
  Arbitral Tribunal has erroneously held...", "(D) Arbitral Tribunal has
  erroneously condoned..." — all four are siblings in one plain
  alphabetical list; "(C)" and "(D)" are its 3rd/4th members, not a jump to
  roman numeral 100 or 500.
- Conversely, a GENUINE roman-numeral enumeration ("(I)", "(II)", "(III)",
  "(IV)", ...) is real and should be reported as such — the distinction
  only matters when a single already-established alphabetical run reaches
  a letter that's also roman-valid. When a roman sequence continues an
  earlier one under a NUMBERED parent (e.g. Grounds 1 through 10 given as
  plain decimal grounds, then the list continues as roman numerals "(XI)",
  "(XII)", ...), report those roman-numeral grounds as CHILDREN of the
  specific decimal ground they continue from (ground 10 in this example),
  via "parent_ref" — not as siblings restarting the decimal sequence, and
  not as new top-level headings of their own.

PAGE ATTRIBUTION
- Every heading MUST be reported with the page number of the page block it
  was printed in ("start_page"). Use only the page numbers shown to you.
  Never guess a page number that was not shown to you.

START AND END PAGES
- "start_page" / "end_page": if a heading's own content clearly ends on a
  page you were shown (a new sibling/parent heading appears, or the
  document ends), report that page as "end_page" and set
  "still_open_at_chunk_end" to false.
- If the heading is STILL OPEN when the LAST page you were shown ends (you
  have no evidence it closed within what you saw), set "end_page" to null
  and "still_open_at_chunk_end" to true. Do NOT guess an end page for
  content you were not shown — say "still open" instead.

HIERARCHY AND PARENTS
- "level": 1 is the outermost division present in the pages you were shown.
  A division contained inside another gets the next level down.
- "parent_ref": identifies this heading's immediate parent, one of:
  (a) the exact "ref_id" string from the "Currently open" list below, if the
      parent was opened on an earlier page you were not shown, or
  (b) the exact "heading_text" of another heading YOU are reporting in this
      same response, if the parent is also visible in these pages.
  Use null only if there is truly no parent (this is a top-level division).
  If you use a "ref_id" from the "Currently open" list, copy it EXACTLY as
  given — do not paraphrase, reformat, or add words to it.

ENUMERATED LIST ITEMS ARE SIBLINGS OF EACH OTHER, NOT OF THE PREVIOUS ITEM
- When several items share the same enumeration style in one continuous run
  — all lettered ("(A)", "(B)", "(C)", ...), all numbered ("1.", "2.",
  "3.", ...), or all roman ("(I)", "(II)", ...) — every item in that run is
  a SIBLING of every other item in the SAME run: the same "parent_ref" and
  the same "level". Do NOT report an item's "parent_ref" as the item that
  came immediately before it in the list just because it is the most
  recent heading you reported — that turns one flat list into a deep,
  incorrect nesting chain. An item is a child of the PREVIOUS item only
  when that previous item's own text genuinely opens a NEW, differently-
  styled sub-list (see the numbering-style-transition guidance above) or
  the following text is visibly a sub-clause of it, not merely the next
  member of the same enumeration.
- Worked example (one real document; the same reasoning applies to any
  enumerated list regardless of the letters/numbers/wording involved): a
  list under "Challenge to the Arbitral Award" reads "(F) The Arbitral
  Tribunal erred in observing...", "(G) The Arbitral Tribunal has also
  overlooked...", "(H) The Arbitral Tribunal has also wrongly held...",
  "(J) It is further urged that..." — all four (and every other letter in
  the same (A)-(O) run) share the SAME "parent_ref" ("Challenge to the
  Arbitral Award") and the SAME "level". (G) is NOT a child of (F), (H) is
  NOT a child of (F) either, and (J) is NOT a child of (F) — none of them
  is a child of the letter immediately before it.
- A second worked example, same reasoning, different style — lowercase
  roman numerals in parens: a list under "Conclusion" reads "(i) The
  Tribunal shall pay damages...", "(ii) The Tribunal shall further pay
  costs...", "(iii) Interest shall accrue...", "(iv) The counter-claim is
  dismissed..." — every one of (i) through (vii) shares the SAME
  "parent_ref" ("Conclusion") and the SAME "level". (ii) is NOT a child of
  (i), (iii) is NOT a child of (ii), and so on — this exact mistake (each
  item reported one level deeper than the one before it) is the most
  common way this rule gets violated in practice, so check specifically
  for it before finalizing your answer.

A SHORT LINE ENDING IN ":", "-", OR "->" OFTEN INTRODUCES THE LIST THAT FOLLOWS
- A short line (roughly a sentence or less) that ends with a colon, a dash,
  or an arrow ("->") is a strong signal that it is the PARENT of whatever
  enumerated list immediately follows it — report that line's own
  "heading_text" as the "parent_ref" for every item in the list that
  follows it, not the item that came immediately before it. This applies
  whether or not the introducing line is itself a numbered heading in its
  own right — a numbered paragraph that also ends this way is still a
  heading because of its number, and the colon/dash/arrow is a separate,
  additional signal for what nests under it.
- This is a general SHAPE rule, not tied to any particular document's
  wording — judge any line that fits the shape, using completely different
  words than the illustrations below. ILLUSTRATIONS of this shape, not an
  exhaustive list: "The inspection revealed the following defects:"
  followed by "(a) ... (b) ... (c) ..."; "Delivery shall proceed in the
  following stages -" followed by "(1) ... (2) ... (3) ..."; "The vendor's
  obligations are as follows ->" followed by "(i) ... (ii) ... (iii) ...".
  In every case, every list item that follows shares that introducing
  line's key as "parent_ref" — none of them is a child of the item before
  it (see ENUMERATED LIST ITEMS ARE SIBLINGS OF EACH OTHER above).
- Worked example combining both signals (a real document): paragraph
  "3. The Award is under challenge on following grounds:" is itself a
  numbered heading (report it as such), AND its trailing colon marks it as
  the parent of the lettered list that follows — "(A)" through "(O)" must
  all report "parent_ref" as that paragraph 3, not as the section heading
  above it and not as each other.

RULES
- Copy "heading_text" VERBATIM from the page, including its number/letter
  prefix. Do not re-title, translate, expand abbreviations, or fix OCR
  errors. Do not wrap it in markdown (#, **, etc.) — plain text only.
- Copy the NUMBER exactly as printed. Never add, drop, or invent digits or
  segments — do not turn "1." into "1.4" or "1.1" unless that extra segment
  is genuinely visible in the text.
- If the page range contains no headings at all, return an empty
  "headings" list. Do NOT invent headings to fill the output.
- Output ONLY a single JSON object. No prose, no markdown fences, no
  commentary before or after.
"""

# Appended to STRUCTURE_SYSTEM_PROMPT only when the caller opts in
# (build_structure_prompt's include_pleading_points=True) — the default
# prompt above is otherwise unchanged. Overrides the "NUMBERED SENTENCE OR
# CLAUSE OF BODY TEXT" exclusion specifically for enumerated pleading
# points/grounds — everywhere else in the base prompt, that exclusion still
# applies.
PLEADING_POINTS_SECTION = """\
NUMBERED PLEADING POINTS AND GROUNDS ARE HEADINGS TOO (OVERRIDES THE EXCLUSION ABOVE)
- The "NUMBERED SENTENCE OR CLAUSE OF BODY TEXT" exclusion above does NOT
  apply to a numbered/lettered/roman-numeral item that is part of an
  ENUMERATED LIST of averments, grounds, or objections — a pleading's
  numbered averments ("1. That the appellant is..."), an appeal's
  numbered/roman grounds ("(I) That the tribunal erred..."), or any
  similarly-enumerated list of points — even though each individual item is
  itself a full sentence with a finite verb. Report EVERY member of such a
  list as its own heading, using the same level/parent_ref mechanism as
  every other heading in this document — this is what lets a reader
  navigate directly to "Ground 14" or "point 7" instead of only seeing the
  section title above the list.
- Nesting follows the SAME numbering-style-transition logic already
  described above (see SEQUENTIAL LETTERS ARE NOT ROMAN NUMERALS): when one
  item's own text explicitly introduces what follows as its own sub-list
  (judge by meaning — no single fixed phrase), the items that follow nest
  as its children; when the ORIGINAL list's next value reappears, that item
  resumes as a SIBLING of the item that opened the sub-list — same level,
  same parent — never a further child of it, and never a new unrelated
  top-level heading just because it comes after a long detour through a
  different numbering style.
- NUMBERED PARAGRAPHS OF COURT JUDGMENTS, ORDERS, PETITIONS, AND LEGAL PLEADINGS:
  In legal documents (court judgments, petitions, replies, written statements, orders),
  paragraphs are sequentially numbered (e.g. "1.", "2.", "3.", ... "31.").
  Report EVERY such numbered paragraph as a heading under its containing section
  (e.g. under "FACTS", "Submissions of petitioner", "Conclusion", etc.). Even though it is
  a full sentence with a finite verb, it is a numbered point/averment that must be indexed.
  Do NOT strip or exclude it under the noun-phrase test.
  CRITICAL:
  1. ALWAYS report the FULL paragraph opening (the number PLUS the opening words of
     the sentence, up to ~10 words, e.g. "12. It is further argued that the delay in front clearance of site",
     "16. It has been argued by learned counsel for the respondent", "20. From perusal of the challenge made").
     NEVER strip the sentence to emit a bare number (never emit "12", "16", "20" alone). Emitting bare numbers
     destroys the document outline.
  2. NEVER invent, shift, or prepend paragraph numbers to an unnumbered line!
     If a line does not have a number printed on the page, copy it VERBATIM without adding any number.
"""


STRUCTURE_OUTPUT_CONTRACT = """\
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

FIELD RULES
- "start_line_hint": your best guess at which line of the page the heading
  starts on (1-based, counting only non-blank lines), or null if unsure.
  This is stored for possible future use only — it is not required to be
  precise.
- "family_hint" / "number_hint": your best-guess resolution of a garbled
  heading — see "RESOLVING A GARBLED HEADING" above. null/null for any
  heading you can already read cleanly (most of them); only set these when
  "heading_text" itself is garbled/unclear but you recognize what it means.
- "notes": at most one short sentence; "" when you have nothing to flag.
"""

PAGE_OPEN = "<<<PAGE {n}>>>"
PAGE_CLOSE = "<<<END PAGE {n}>>>"


@dataclass
class PageInput:
    page_num: int
    text: str


@dataclass
class ChunkPlan:
    pages: List[PageInput]
    truncated_pages: List[int] = field(default_factory=list)
    total_chars: int = 0

    @property
    def page_nums(self) -> List[int]:
        return [p.page_num for p in self.pages]


def plan_chunks(
    pages: List[PageInput],
    max_pages: int = None,
    max_chars: int = None,
    max_page_chars: int = None,
    overlap_pages: int = 0,
) -> List[ChunkPlan]:
    """Greedy packing: accumulate pages until max_pages OR max_chars,
    whichever comes first. A chunk always contains >= 1 page, even if that
    single page blows the budget — clipped to max_page_chars with an
    explicit truncation marker so the model knows it saw a partial page and
    doesn't invent a continuation. Pages are never reordered or split
    across chunks.

    When overlap_pages > 0, each chunk after the first is pre-seeded with
    the last overlap_pages PageInputs of the preceding chunk."""
    max_pages = max_pages or config.AI_INDEX_CHUNK_PAGES
    max_chars = max_chars or config.AI_INDEX_CHUNK_CHARS_MAX
    max_page_chars = max_page_chars or config.AI_INDEX_PAGE_CHARS_MAX
    overlap_pages = min(overlap_pages, max(max_pages - 1, 0))

    chunks: List[ChunkPlan] = []
    current: List[PageInput] = []
    current_chars = 0
    current_truncated: List[int] = []
    new_count = 0

    def flush():
        nonlocal current, current_chars, current_truncated, new_count
        if not current or new_count == 0:
            return
        chunks.append(ChunkPlan(pages=current, truncated_pages=current_truncated, total_chars=current_chars))
        if overlap_pages > 0 and len(current) > 0:
            carried = current[-overlap_pages:]
            carried_page_nums = {p.page_num for p in carried}
            carried_truncated = [pn for pn in current_truncated if pn in carried_page_nums]
            current = list(carried)
            current_truncated = list(carried_truncated)
            current_chars = sum(len(p.text) for p in carried)
        else:
            current, current_chars, current_truncated = [], 0, []
        new_count = 0

    for p in pages:
        text = p.text or ""
        was_truncated = False
        if len(text) > max_page_chars:
            text = text[:max_page_chars] + "\n\n[... page truncated for length ...]"
            was_truncated = True
        page = PageInput(page_num=p.page_num, text=text)

        if current and (len(current) >= max_pages or current_chars + len(text) > max_chars):
            flush()

        current.append(page)
        current_chars += len(text)
        if was_truncated:
            current_truncated.append(p.page_num)
        new_count += 1

    flush()
    return chunks


@dataclass
class CarryContext:
    """What the stitching layer hands back in from the previous chunk's
    surviving stack — deliberately small (labels + a short text tail), NOT
    the previous chunk's full page text: re-sending pages invites duplicate
    heading emission, which is far more expensive to un-pick than a
    slightly-worse level guess."""
    open_containers: List[dict] = field(default_factory=list)  # [{"ref_id","level","label","source"}]
    last_page_tail: str = ""


def _render_carry_in(carry: Optional[CarryContext]) -> str:
    if not carry or not carry.open_containers:
        return ""
    lines = ["# CONTEXT FROM EARLIER PAGES (read-only)",
             "Currently open, outermost first — to make a new heading a child of one of",
             "these, set its \"parent_ref\" to the exact \"ref_id\" string below:"]
    for c in carry.open_containers:
        note = " (rule-detected, less certain — only nest under this if the text genuinely supports it)" \
            if c.get("source") == "rule" else ""
        lines.append(f'  ref_id={c["ref_id"]!r} level={c["level"]} label={c["label"]!r}{note}')
    if carry.last_page_tail:
        lines.append(f"Tail of the immediately preceding page: {carry.last_page_tail[-600:]!r}")
    lines.append("Do NOT emit headings for pages before the ones shown below.")
    return "\n".join(lines)


def build_structure_prompt(
    chunk: ChunkPlan, *, carry_context: Optional[CarryContext] = None, document_hint: str = "",
    include_pleading_points: bool = False,
) -> "tuple[str, str]":
    system_prompt = STRUCTURE_SYSTEM_PROMPT
    if include_pleading_points:
        system_prompt = system_prompt + "\n\n" + PLEADING_POINTS_SECTION

    parts = []
    page_nums = chunk.page_nums
    parts.append(f"# PAGE RANGE\nThis chunk contains pages {page_nums[0]}-{page_nums[-1]}. "
                 f"Valid values for \"start_page\"/\"end_page\": {json.dumps(page_nums)}.")

    carry_block = _render_carry_in(carry_context)
    if carry_block:
        parts.append(carry_block)

    if document_hint:
        parts.append(f"# DOCUMENT HINT (optional, user supplied)\n<<<HINT>>>\n{document_hint[:500]}\n<<<END HINT>>>")

    page_blocks = []
    for p in chunk.pages:
        page_blocks.append(f"{PAGE_OPEN.format(n=p.page_num)}\n{p.text}\n{PAGE_CLOSE.format(n=p.page_num)}")
    parts.append("# PAGES\n" + "\n".join(page_blocks))

    parts.append(STRUCTURE_OUTPUT_CONTRACT)

    return system_prompt, "\n\n".join(parts)
