"""Chunk packing + prompt assembly for the AI structure-detection pass.

carry_in uses an opaque, exact-match `ref_id` per open container (not a
prose-formatted line) specifically so `parent_ref` resolution against it can
be exact-string, not fuzzy — fuzzy matching is reserved for same-chunk
parent references, where paraphrase drift (not prompt-echo) is the real
risk. See ai_reference_index.py for how parent_ref is actually resolved.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

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

OCR-LAYOUT ANNOTATIONS — WHEN PRESENT, WEIGH THEM; NEVER LET THEM DECIDE ALONE
- Some pages you are shown have each text block prefixed with a bracketed
  tag like "[OCR-LAYOUT: HEADER]" or "[OCR-LAYOUT: TITLE]" on its own line,
  immediately before the block's text. These come from the OCR engine's own
  visual layout classifier — a judgment about the block's position, size,
  and formatting on the original page image, made independently of what the
  text actually says. Being purely visual, it has no idea whether a
  prominent-looking block is actually structural or just a decorative line,
  a running ornament, or a stray caption that happens to sit in a visually
  prominent spot — only reading the block's own content and what surrounds
  it can tell you that.
- A block tagged HEADER or TITLE raises your prior that it's a real
  heading — most usefully when the OCR'd text itself is too garbled to
  judge on its own merits (e.g. "AmexureAl" tagged "[OCR-LAYOUT: HEADER]" is
  almost certainly a heading despite being unreadable as text, since there's
  nothing else to go on). But the tag must never settle a case the
  surrounding context and the content-based tests above already argue
  against. When the tag and the context disagree, read the blocks
  immediately before and after and decide the way the rest of this prompt
  already teaches — the same way you'd judge any other borderline case.
- A block with no such tag, or tagged with a plain-text type (e.g.
  "[OCR-LAYOUT: TEXT]"), should NOT be treated as a heading on the strength
  of the tag alone — it is neutral, not negative; apply the usual rules
  above.
- A tag may also carry an "INDENTED" marker (e.g. "[OCR-LAYOUT: TEXT,
  INDENTED]") — the block sits noticeably right of the page's own main text
  margin. This is often a sub-clause, a nested list item, or part of a
  signature/address block, not a new top-level heading; its *absence* on a
  TITLE/HEADER block is mild corroborating evidence (not proof) that the
  block is a top-level section rather than something nested. Like the
  type tag itself, weigh it alongside everything else — it never settles a
  case by itself either.
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
  EXCEPTION FOR CONTRACT / TENDER NUMBERED CLAUSES IN TABLES:
  In contracts, tenders, specifications, or RFPs, numbered clauses are often laid out
  in multi-column tables where Column 1 contains the clause number (e.g. "9.1.", "24.2.1.", or "6")
  and Column 2 contains the clause title or text (e.g. "Project site visit" or
  "Bidder shall submit only one (1) unconditional bid...").
  In such documents, these numbered clauses ARE the primary structural divisions of the
  contract. You MUST report every single numbered clause row in sequence without omitting any!
  If Column 2 contains long text, you may report the combined heading (e.g. "9.1. Bidder shall submit only one..."),
  the clause number and its opening words (e.g. "24.2.1. Prior to the detailed evaluation"),
  or simply the bare clause number ("24.2.1.").
  This includes all 3-level decimal sub-clauses in tables (e.g. "24.2.1.", "24.2.2.", "24.2.3.", ..., "24.2.9.") —
  report them with their parent clause (e.g. "24.2.") as "parent_ref".
  Do NOT exclude a numbered clause in a contract/tender table merely because Column 2
  contains a verb like "shall", "must", or "is".
   This includes essential tender information tables, schedules of requirements, specification tables, and bidder eligibility / checklist criteria tables where
   Column 1 is an item/clause/serial number (e.g. "1.", "2.", "3.", "SN 1") and Column 2 is an item, specification, or eligibility criterion heading/text
   (e.g. "Name of works/services to be procured", "Purchaser", "Contract Period", "Earnest Money Deposit", "The Sole Bidder/Lead Bidder and Consortium Partner...").
   Report every such numbered row under its section or annexure heading (e.g. "1. Name of works/services to be procured", "1. The Sole Bidder/Lead Bidder", "2. The bidder should be registered in India with GST").
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
- TENDER COVER-PAGE LETTERHEAD AND CONTACT BLOCKS: The cover/first page often lists
  issuing organization names ("M. P. Poorv Kshetra Vidyut Vitaran Co. Ltd"), departments,
  officer titles ("THE CHIEF GENERAL MANAGER (Store & Purchase)"), office locations/addresses
  ("Block No.8th 3rd Floor Shakti Bhawan", "Jabalpur 482008"), phone/fax lines ("Phone No.: ..."),
  and contact emails ("Email: ..."). These are contact/letterhead boilerplate, NOT document headings.
  Do NOT report them as headings.
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
- A table row (EXCEPT for numbered contract/tender clauses in multi-column tables as described above), a list item in a running list, a signature block, a running
  header/footer, a page number, or a table-of-contents/index line that merely
  points at a heading printed elsewhere.
- Table column headers ("S.No.", "Clause No.", "Description", "Particulars", "Remarks", "Compliance (Yes/No)", "Status", "Bidder's Response", "Page No.") are NOT headings — they are column labels of a table. Do NOT report them.
- Form signature blocks and form field placeholders ("Authorized Signatory", "Name of Bidder", "Seal of Firm", "Date: ...", "Place: ...", "Signature of the Bidder") are NOT headings — they are fill-in metadata. Do NOT report them.
- CORRESPONDENCE ENDORSEMENT / DISTRIBUTION RECIPIENT LISTS: A numbered or bulleted list of
  recipient officials, departments, or offices following "Copy forwarded to:", "Copy to:",
  "Endst. No.", or "Distribution:" at the conclusion of an official letter, order, or memorandum
  (e.g. "1. Sr. PS to Chairman, HVPNL...", "2. Chief Engineer/TS...", "10. The Superintending Engineer...")
  are dispatch/recipient distribution metadata, NOT document headings. Do NOT report them.
- BOQ / PRICE SCHEDULE ITEM ROWS: In pricing, spares, or bill-of-quantities tables with columns like "Unit Ex-Works Price", "Qty", or "Total Price", do NOT report item numbers or equipment rows (e.g. "(4) 415V emergency LDB", "38", "39", "100") with family_hint="schedule" or as headings. They are table line items, NOT headings.
- CONTRACT AGREEMENT RUNNING BODY PARAGRAPHS: In contract agreements and legal exhibits, do NOT report multi-sentence running body paragraphs (e.g. "3.5 Another contract for erection portion...", "3.4.1 The contractor is responsible for the proper execution...") as headings. Only report short standalone section titles (e.g. "3.3 TIME SCHEDULE", "3.4 QUALITY PLANS").

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
- NUMBERED CLAUSES AND SUB-CLAUSES OF CONTRACTS, TENDERS, AND SPECIFICATIONS:
  In contracts, tenders, specifications, and conditions of contract (e.g. GCC, SCC), clauses and
  sub-clauses are sequentially numbered (e.g. "1.1.", "1.9.1.", "1.11.1.", "1.15.2.", "1.18.4.", "1.19.1.", "24.2.1.", or "1.", "2.", "3.").
  Report EVERY such numbered clause and sub-clause as a heading under its parent clause or section
  (e.g. report "1.11.1. It is a condition of this Bid that the information", "1.18.4. Expiry or termination of a parallel contract", "1.19.1. Only Purchaser-approved AI models/services", "1.19.2. AI access shall respect").
  Even though a contract clause contains full sentences with verbs like "shall", "must", or "is",
  it is a numbered contract clause that must be indexed. Do NOT strip or exclude it under the noun-phrase test.
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


MACRO_SYSTEM_PROMPT = """\
You are an expert legal document analyst building a high-level Document Table of Contents (Macro Skeleton).
Your ONLY job in this pass is to identify and report the TOP-LEVEL STRUCTURAL DIVISIONS of the document.

WHAT TO REPORT:
1. Major Structural Divisions: Parts, Sections, Chapters, Volumes, Titles (e.g. "SECTION I: INVITATION FOR BIDS", "SECTION II: INSTRUCTIONS TO BIDDERS", "PART-A", "CHAPTER 3").
2. Annexures, Schedules, Appendices, Exhibits, Attachments (e.g. "ANNEXURE-I: BID FORM", "SCHEDULE-A", "APPENDIX-3", "ANNEXURE XVI").
3. Standalone major document divisions: Cover Page, Table of Contents, Memo of Parties, Affidavit, Index, Summary.
4. Top-level numbered section heads (e.g. "1.0 DEFINITIONS", "2.0 GENERAL CONDITIONS", "3.0 SCOPE OF WORK") only when they form the primary top-level divisions of the text.

WHAT TO STRICTLY EXCLUDE (DO NOT REPORT):
- DO NOT report individual contract clauses or sub-clauses (e.g. "1.1", "1.2", "6.1", "24.2.1").
- DO NOT report table cell rows or individual specification items.
- DO NOT report numbered body paragraphs, pleading grounds, or lettered list items ("(a)", "(1)").
- DO NOT report signature blocks, form fill-ins, table column headers ("S.No.", "Description"), or page marginalia.

OUTPUT FORMAT:
Return a JSON object conforming exactly to the output contract:
{
  "headings": [
    {
      "heading_text": "verbatim title as printed",
      "start_page": <page_num>,
      "start_line_hint": null,
      "end_page": null,
      "still_open_at_chunk_end": true,
      "parent_ref": null,
      "level": 1,
      "family_hint": null,
      "number_hint": null
    }
  ],
  "notes": ""
}
"""


RULE_PRIOR_SECTION = """\
# RULE-DETECTED PRIOR DRAFT (REVIEW INSTRUCTIONS)
You are provided with a recall-biased draft of headings previously detected by a rule-based pass for these pages (labeled R1, R2, ...).
Your job is to arbitrate, confirm, reject, or correct them.

1. CONFIRMATION IS CHEAP:
   If a prior heading is correct, do NOT re-emit its full text in "headings".
   Simply include its id in "confirmed_prior_ids": ["R1", "R2", ...].

2. REJECTION REQUIRES VERBATIM EVIDENCE:
   To reject a spurious prior heading (e.g. body sentence, table row, running header, external statute/case citation),
   add an entry to "rejected_priors":
   {"prior_id": "R3", "reason_code": "<code_below>", "evidence_quote": "<verbatim quote from the page>"}
   Allowed reason codes: "external_citation", "reproduced_text", "body_sentence", "table_or_toc", "running_header", "duplicate_of_prior", "not_on_page".
   The evidence_quote MUST be an exact verbatim substring from that page (at least 8 characters) explaining why it is NOT a heading.

3. SILENCE IS NOT DELETION:
   Any prior heading not mentioned in either "confirmed_prior_ids", "rejected_priors", or "headings" will survive by default as an unreviewed prior heading.

4. NEW OR CORRECTED HEADINGS:
   Emit any genuinely new headings (missed by the prior) or corrections in "headings" as usual.
   If correcting a prior heading, you may optionally include its "prior_id" and an "evidence_quote".\
"""


STRUCTURE_OUTPUT_CONTRACT_WITH_PRIOR = """\
# OUTPUT FORMAT (strict JSON — exactly this shape, nothing else)
{
  "confirmed_prior_ids": ["R1", "R2"],
  "rejected_priors": [
    {
      "prior_id": "R3",
      "reason_code": "external_citation",
      "evidence_quote": "Arbitration and Conciliation Act, 1996"
    }
  ],
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
      "number_hint": null,
      "prior_id": null,
      "evidence_quote": null
    }
  ],
  "notes": ""
}

FIELD RULES
- "confirmed_prior_ids": list of prior IDs (e.g. ["R1", "R2"]) from # RULE-DETECTED PRIOR DRAFT that are valid headings.
- "rejected_priors": list of rejections with prior_id, valid reason_code, and verbatim evidence_quote (>= 8 chars).
- "headings": new headings or corrections not covered by confirmed_prior_ids.
- "start_line_hint": your best guess at which line of the page the heading starts on, or null.
- "family_hint" / "number_hint": resolution of a garbled heading, or null.
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
    overlap_pages: int = 0,
) -> List[ChunkPlan]:
    """Greedy packing: accumulate pages until max_pages OR max_chars,
    whichever comes first. A chunk always contains >= 1 page, even if that
    single page blows the budget — sent through whole and untouched, never
    clipped, so a dense page never loses content the model would otherwise
    see. Pages are never reordered or split across chunks.
    `ChunkPlan.truncated_pages` is always empty now (kept on the dataclass
    only because `ai_reference_index.py` still reads the field).

    When overlap_pages > 0, each chunk after the first is pre-seeded with
    the last overlap_pages PageInputs of the preceding chunk."""
    max_pages = max_pages or config.AI_INDEX_CHUNK_PAGES
    max_chars = max_chars or config.AI_INDEX_CHUNK_CHARS_MAX
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
        page = PageInput(page_num=p.page_num, text=text)

        if current and (len(current) >= max_pages or current_chars + len(text) > max_chars):
            flush()

        current.append(page)
        current_chars += len(text)
        new_count += 1

    flush()
    return chunks


_CLAUSE_MARKER_RE = re.compile(r"^\s*\d+(?:\.\d+)+\.?", re.MULTILINE)
# Mirrors reference_index.GROUND_ROMAN_RE's marker shape (e.g. "(VII) That
# the Ld. Arbitral Tribunal..."), but MULTILINE-compiled for this module's
# own page-level search/findall scan rather than GROUND_ROMAN_RE's
# .match()-on-one-candidate-string usage elsewhere (confirmed via its 3 call
# sites in reference_index.py). Confirmed real gap: a 100-page arbitration
# appeal's "Grounds of Appeal" section (pages 18-30+) has only 1-2 such
# markers per page — too few to trip _CLAUSE_MARKER_RE's own count
# thresholds even if counted the same way — but each one is a full
# multi-sentence legal paragraph, not a short table row. Classifying these
# pages as NARRATIVE let them pool into one oversized chunk (once
# AI_INDEX_CHUNK_PAGES was raised for narrative content), overwhelming a
# single AI call's output budget and causing ~20 ground:N targets to go
# missing entirely. Used as a presence check below, not a count, since a
# single ground marker is independently substantial regardless of how many
# appear on the page — unlike _CLAUSE_MARKER_RE where only bulk matters.
_ROMAN_ENUM_MARKER_RE = re.compile(r"^\s*\(?[IVXLCDMivxlcdm]+(?:\)\.?|\.)\s+[A-Z]", re.MULTILINE)


def classify_page_density(text: str) -> str:
    """Fast, lightweight pre-scanner classifying a page's clause/table density."""
    if not text:
        return "NARRATIVE"
    table_rows = text.count("<tr>") + text.count("|---")
    clause_markers = len(_CLAUSE_MARKER_RE.findall(text))
    if table_rows >= 6 or clause_markers >= 8:
        return "DENSE_TABLE"
    if table_rows >= 3 or clause_markers >= 4 or _ROMAN_ENUM_MARKER_RE.search(text):
        return "MODERATE"
    return "NARRATIVE"


def plan_adaptive_chunks(
    pages: List[PageInput],
    max_pages: int = None,
    max_chars: int = None,
    overlap_pages: int = 0,
    dense_page_limit: int = 2,
    dense_chars_max: int = 16000,
    dense_row_limit: int = 20,
) -> List[ChunkPlan]:
    """Density-aware adaptive chunk planner:
    - Automatically classifies pages as DENSE_TABLE, MODERATE, or NARRATIVE.
    - Flushes chunk immediately upon transition between NARRATIVE and DENSE_TABLE (Refinement 1).
    - Clamps DENSE_TABLE chunks to dense_page_limit (default 2 pages) and dense_row_limit.
    - Preserves large max_pages (default 15-20) for standard NARRATIVE sections.
    """
    max_pages = max_pages or config.AI_INDEX_CHUNK_PAGES
    max_chars = max_chars or config.AI_INDEX_CHUNK_CHARS_MAX
    overlap_pages = min(overlap_pages, max(max_pages - 1, 0))

    chunks: List[ChunkPlan] = []
    current: List[PageInput] = []
    current_chars = 0
    current_truncated: List[int] = []
    current_density: Optional[str] = None
    current_rows = 0
    new_count = 0

    def flush():
        nonlocal current, current_chars, current_truncated, current_density, current_rows, new_count
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
            current_density = classify_page_density(carried[-1].text) if carried else None
            current_rows = sum((p.text.count("<tr>") + p.text.count("|---")) for p in carried)
        else:
            current, current_chars, current_truncated = [], 0, []
            current_density = None
            current_rows = 0
        new_count = 0

    for p in pages:
        text = p.text or ""
        page = PageInput(page_num=p.page_num, text=text)
        page_density = classify_page_density(text)
        page_table_rows = text.count("<tr>") + text.count("|---")
        page_clause_markers = len(_CLAUSE_MARKER_RE.findall(text))
        effective_rows = max(page_table_rows, page_clause_markers)

        # Transition Hysteresis (Refinement 1): flush when crossing DENSE_TABLE boundary
        if current:
            density_transition = (
                (current_density == "DENSE_TABLE" and page_density != "DENSE_TABLE")
                or (current_density != "DENSE_TABLE" and page_density == "DENSE_TABLE")
            )
            if density_transition:
                flush()

        # Capacity Limits based on active density
        if current:
            if current_density == "DENSE_TABLE":
                if (
                    len(current) >= dense_page_limit
                    or current_chars + len(text) > dense_chars_max
                    or current_rows + effective_rows > dense_row_limit
                ):
                    flush()
            elif current_density == "MODERATE":
                if len(current) >= 5 or current_chars + len(text) > 24000:
                    flush()
            else:  # NARRATIVE
                if len(current) >= max_pages or current_chars + len(text) > max_chars:
                    flush()

        current.append(page)
        current_chars += len(text)
        current_rows += effective_rows
        current_density = page_density
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


def _format_prior_line(r: Dict[str, Any]) -> str:
    parent_str = json.dumps(r["parent_label"]) if r.get("parent_label") is not None else "None"
    label_str = json.dumps(r.get("heading_text") or "")
    return f'{r["prior_id"]} | p{r["page_num"]} | depth {r["depth"]} | key={r["key"]} | parent={parent_str} | {label_str}'


def _render_rule_prior(
    priors: List[Dict[str, Any]],
    max_chars: Optional[int] = None,
    max_rows: Optional[int] = None,
) -> str:
    if not priors:
        return ""

    max_chars = max_chars or config.AI_INDEX_REVIEW_PRIOR_CHARS_MAX
    max_rows = max_rows or config.AI_INDEX_REVIEW_MAX_PRIOR_ROWS

    header = (
        "# RULE-DETECTED PRIOR DRAFT\n"
        "The following headings were detected by a rule-based draft. Review instructions are in the system prompt."
    )

    # Deterministic truncation:
    # 1. Drop leaves with rank is None (highest page first, then reverse index)
    # 2. Drop leaves with rank is not None (highest page first, then reverse index)
    # Never drop containers (is_container == True).
    group1 = [
        i for i, r in enumerate(priors)
        if not r.get("is_container") and r.get("rank") is None
    ]
    group1.sort(key=lambda i: (priors[i].get("page_num", 0), i), reverse=True)

    group2 = [
        i for i, r in enumerate(priors)
        if not r.get("is_container") and r.get("rank") is not None
    ]
    group2.sort(key=lambda i: (priors[i].get("page_num", 0), i), reverse=True)

    drop_candidates = group1 + group2
    dropped_set = set()

    def _build_text(dropped: set) -> "tuple[str, int]":
        surviving = [r for i, r in enumerate(priors) if i not in dropped]
        lines = [_format_prior_line(r) for r in surviving]
        if dropped:
            lines.append(f"[... {len(dropped)} lower-priority prior rows omitted — absence here is NOT a rejection ...]")
        return header + "\n" + "\n".join(lines), len(surviving)

    text, surviving_count = _build_text(dropped_set)
    drop_idx = 0
    while (surviving_count > max_rows or len(text) > max_chars) and drop_idx < len(drop_candidates):
        dropped_set.add(drop_candidates[drop_idx])
        drop_idx += 1
        text, surviving_count = _build_text(dropped_set)

    return text


def _render_macro_context(macro_ctx: Optional[dict]) -> str:
    if not macro_ctx:
        return ""
    root = macro_ctx.get("root_container")
    parent = macro_ctx.get("parent_clause")
    lines = ["# ACTIVE DOCUMENT DIVISION (FROM MACRO SKELETON)"]
    lines.append("You are currently analyzing pages inside:")
    if root:
        lines.append(f'- Root Division: "{root}" (Level 1)')
    if parent:
        lines.append(f'- Active Section: "{parent}"')
    lines.append(
        "\nGUIDELINE: Report all clauses and sub-clauses belonging to this division.\n"
        "PRICE SCHEDULE / BOQ SUPPRESSION: If the Root Division or Active Section title above "
        "indicates a dense itemized listing (titles containing words like \"Price Schedule\", "
        "\"Bill of Quantities\", \"BOQ\", \"Schedule of Rates\", or an equipment/spare-parts list), "
        "do NOT report individual line items, equipment names, quantities, or decimal-numbered rows "
        "within such a division as separate headings or points, regardless of how closely their "
        "numbering resembles a real clause (e.g. do NOT extract rows like \"4.10 Isolation valves\" "
        "or \"420 KV SF-6 Circuit Breaker\").\n"
        "CARVE-OUT (SUBSTANTIVE TERMS): Define what to suppress by positive shape — suppress rows that "
        "represent an itemized product, equipment, part, quantity, unit, or price. Do NOT suppress "
        "headings naming substantive contractual terms, conditions, obligations, penalties, or procedures "
        "(e.g. \"Payment Terms\", \"Stage Penalty\", \"Taxes & Duties\", or procedural payment clauses), "
        "even when they appear inside a Price Schedule division. If the division title does not suggest "
        "a dense itemized listing, this suppression does not apply.\n"
        "NOTE (IMPORTANT): Active division context is a default guideline. If this page range "
        "explicitly begins a NEW major section, chapter, or annexure (e.g. 'SECTION III' or 'ANNEXURE-XI'), "
        "report it as a top-level heading ('level': 1, 'parent_ref': null) rather than nesting it under the previous division."
    )
    return "\n".join(lines)


def build_structure_prompt(
    chunk: ChunkPlan, *, carry_context: Optional[CarryContext] = None, document_hint: str = "",
    include_pleading_points: bool = False,
    rule_prior: Optional[List[dict]] = None,
    review_rule_prior: bool = False,
    is_macro_pass: bool = False,
    macro_context: Optional[dict] = None,
) -> "tuple[str, str]":
    if is_macro_pass:
        system_prompt = MACRO_SYSTEM_PROMPT
    else:
        system_prompt = STRUCTURE_SYSTEM_PROMPT
        if include_pleading_points:
            system_prompt = system_prompt + "\n\n" + PLEADING_POINTS_SECTION
        if review_rule_prior:
            system_prompt = system_prompt + "\n\n" + RULE_PRIOR_SECTION

    parts = []
    page_nums = chunk.page_nums
    parts.append(f"# PAGE RANGE\nThis chunk contains pages {page_nums[0]}-{page_nums[-1]}. "
                 f"Valid values for \"start_page\"/\"end_page\": {json.dumps(page_nums)}.")

    if not is_macro_pass and macro_context:
        macro_block = _render_macro_context(macro_context)
        if macro_block:
            parts.append(macro_block)

    carry_block = _render_carry_in(carry_context)
    if carry_block:
        parts.append(carry_block)

    if review_rule_prior and rule_prior and not is_macro_pass:
        prior_block = _render_rule_prior(rule_prior)
        if prior_block:
            parts.append(prior_block)

    if document_hint:
        parts.append(f"# DOCUMENT HINT (optional, user supplied)\n<<<HINT>>>\n{document_hint[:500]}\n<<<END HINT>>>")

    page_blocks = []
    for p in chunk.pages:
        page_blocks.append(f"{PAGE_OPEN.format(n=p.page_num)}\n{p.text}\n{PAGE_CLOSE.format(n=p.page_num)}")
    parts.append("# PAGES\n" + "\n".join(page_blocks))

    if review_rule_prior and not is_macro_pass:
        parts.append(STRUCTURE_OUTPUT_CONTRACT_WITH_PRIOR)
    else:
        parts.append(STRUCTURE_OUTPUT_CONTRACT)

    return system_prompt, "\n\n".join(parts)


ACTION_ITEM_SYSTEM_PROMPT = """\
You are a contract/tender-document analyst. You are given the OCR'd text of
a contiguous range of pages. Your ONLY job is to extract every ACTIONABLE
ITEM: something a reader must DO, a DATE they must act by, or a CONDITION
that triggers a consequence. You are NOT extracting document structure or
headings — a different pass already does that.

WHAT COUNTS AS AN ACTIONABLE ITEM (four types)
1. "deadline" — a date, time period, or recurring milestone the reader must
   act by or that fixes a window: a bid submission deadline, a notice
   period ("30 days' prior written notice"), a validity/expiry/renewal
   date, a defect-liability or warranty period's end, a pre-bid meeting
   date.
2. "obligation" — a duty stated WITHOUT a specific date: "The Bidder shall
   submit...", "The Contractor must provide...", "The Purchaser will
   furnish...". A finite verb is exactly what you are looking for here —
   unlike a structure/heading pass, where a verb is a reason to EXCLUDE a
   line, here it is a reason to look closer, not a reason to skip it.
3. "condition" — an if/then trigger: a penalty clause, a termination
   trigger, an escalation clause ("If the Contractor fails to complete...,
   a penalty of 0.5% per week shall apply").
4. "other" — something genuinely actionable-adjacent that does not fit the
   three types above. This matters most in litigation/court documents: a
   prayer for relief addressed to the court ("it is prayed that the award
   may be stayed"), a court registry housekeeping direction, or a
   disposition/outcome event ("the objection petition is dismissed with
   costs") that drives real downstream deadlines without stating one
   itself. Use "other" rather than forcing a bad fit into deadline/
   obligation/condition — a deliberate, visible "other" is far more useful
   to a human reviewer than a wrong confident label.
   A PRAYER FOR RELIEF IS ALMOST ALWAYS PLAIN PROSE, NOT A TABLE ROW — look
   for it inside ordinary paragraphs of legal argument, not just in
   structured/tabular content. It is easy to notice an "other"-shaped item
   when it sits in a distinctive table row and much easier to walk straight
   past one buried in a paragraph of prose — the second case is exactly as
   reportable as the first, and is actually the MORE common shape for a
   genuine prayer. Worked example (a real document; apply the same
   reasoning to any similarly-phrased prayer regardless of the specific
   wording): a paragraph of ordinary argument ends "It is, therefore,
   respectfully prayed that the present application may kindly be allowed
   and the operation of award dated 19.02.2017 ... may kindly be stayed, in
   the interest of justice." — report this as "other" even though nothing
   about the surrounding text looks like a table, a list, or a heading;
   the only signal is the request-for-relief language itself ("it is
   prayed/respectfully prayed/prays that...may kindly be allowed/stayed/
   set aside/exempted/dismissed").
   WHAT "OTHER" IS NOT: a case-index/table-of-contents row listing what
   documents the compilation contains ("Application for stay ... 27.03.2019
   ... pages 7-8"), a bank guarantee's own boilerplate recital ("We confirm
   having issued the above captioned Bank Guarantee..."), or a routine
   financial figure/price total in a schedule. These are structurally
   "different from the surrounding table" in the same visual way a real
   "other" item is, which is exactly why they get mistaken for one — but
   none of them is a prayer, a registry direction, or a disposition; do not
   report them just because they stand out from their neighbors.

REPORT, DON'T OMIT, WHEN UNCERTAIN — classify as your best-guess type
(including "other") rather than skipping an item because you're unsure
which of the four it is.

THE SINGLE MOST COMMON FALSE POSITIVE: A FEATURE/REQUIREMENTS SENTENCE THAT
ALSO SAYS "SHALL"
A tender's Scope of Work is often full of sentences describing what the
delivered system or service should do — grammatically identical to a real
obligation (same "shall"/"must" verb), but substantively just a feature
specification, not an independently-trackable compliance duty.
  REPORTABLE obligation (has a deadline, an approval gate, a penalty/
  termination trigger, an acceptance criterion, or a hand-over duty):
    "Within 30 days of the receipt of letter of award, you shall furnish a
     performance security..." (deadline attached)
    "The SI shall submit an Exit Management Plan 90 days prior to the
     Contract expiry." (deadline attached)
    "No subcontracting of any material part of the core contractual
     responsibility without prior written approval of the Purchaser."
     (approval gate)
  NOT REPORTABLE (feature/requirements narrative, no deadline/gate/trigger
  of its own — do not report, even though it says "shall"):
    "The modernized GIS shall be integrated particularly with Oracle EBS
     Projects, SCM and Finance, so that geographically identified
     works/assets can be linked with project creation."
    "The target GIS shall support role-based access, responsive/mobile
     usage, layer filtering, spatial search, asset/work drill-down."
  The test: could a project manager put this exact sentence on a tracker
  with its own due date or trigger, distinct from "build the system per
  spec"? If not, it's scope narrative, not an actionable item.

THE SECOND MOST COMMON FALSE POSITIVE: AN "AS-IS" DESCRIPTION OF EXISTING
SOFTWARE THAT HAPPENS TO CONTAIN A WORD LIKE "PENALTY" OR "DEADLINE"
Large stretches of a tender can describe software ALREADY IN PLACE, in
present tense, as background — these are not obligations the bidder is
being asked to assume, even when their wording coincidentally resembles a
condition clause. This is NOT limited to one module — an ERP/GIS tender
routinely spends dozens of pages cataloguing EVERY existing module (HR,
Payroll, Finance, GL, Inventory, Procurement...) this same way, each
described as "Seeded process is used for...", "Calculates X...", "Provides
facility to...". Every row of every such catalogue table is background,
not an obligation — do not report any of them, no matter how many pages
of catalogue tables you are shown in a row, and do not let seeing many
"shall"-free present-tense descriptions in sequence make a later, nearby
real obligation feel like it must also be background — judge each
sentence on its own tense and subject, not by what surrounds it.
  NOT REPORTABLE (describes an existing system feature, present tense,
  background/AS-IS section — do not report):
    "Penalty auto-calculation based on PO terms and conditions" (a
     description of what the current ERP software already does
     automatically, not a tender-imposed condition on the bidder)
    "Supplier standard invoice is created automatically as soon the
     supplier total payment crosses the threshold..." (an existing
     statutory tax-withholding automation, not a bidder obligation)
    "Calculates the income tax payable by employees" / "Seeded process is
     used for..." (a one-line catalogue-table description of an existing
     HR/Payroll/Finance module's current behavior — same AS-IS pattern,
     different module; applies identically across the whole catalogue)
  REPORTABLE (an actual tender-imposed condition, future-tense, imposed on
  a named party):
    "failing which the stage penalty @1% per week... will be levied"

A THIRD FALSE POSITIVE: BOILERPLATE DECLARATIONS AND FORM-COMPLIANCE
CHECKLISTS WITHOUT A DISTINCT TRIGGER
A signed declaration/undertaking a bidder submits as part of the bid
("We solemnly declare that we have attached all the documents...", "We
confirm that in case of false declaration, I shall be disqualified...")
and a checklist-style compliance question ("Has the executing Officer of
BG indicated his name, designation & Power of Attorney number?") are
administrative form content, not freestanding obligations — do not report
these UNLESS the sentence itself states a distinct future duty, deadline,
or consequence beyond "sign/submit this form" (a false-declaration
disqualification consequence IS reportable as a condition; the declaration
sentence itself is not a separate obligation).

MILESTONE CASCADES VS. REPEATED-SCHEMA DATA TABLES — ONE ITEM PER ROW, OR
ONE ITEM TOTAL?
- A table where EACH ROW has its own distinct trigger/date/consequence (a
  project-schedule cascade like "D1 = D0 + 2 weeks", "D2 = D1 + 4 weeks",
  "D3.1 = D1 + 4 weeks", each tied to a different sign-off/deliverable) is
  genuinely many separate actionable items — report ONE PER ROW.
- A table where rows are just DATA VARIANTS of the same underlying duty (a
  21-row report-submission schedule that's really one obligation at
  different cadences; an 8-row payment-percentage table tied to the SAME
  milestones already reported above) is ONE item — summarize the
  cadence/variation in "item_text" and the "cadence" field instead of
  emitting a near-duplicate row per table row.
  Test: if two rows differ only in which number/percentage/report-name
  appears, with the identical surrounding duty and trigger, that's one
  item. If two rows have genuinely different triggers or consequences,
  that's two items.

RELATIVE/FORMULA-BASED DEADLINES — DO NOT COMPUTE A DATE THAT ISN'T THERE
Real tenders routinely define deadlines purely as offsets from an anchor
event that is itself never dated anywhere in the pages you're shown
("D1 = Effective Date + 2 weeks", "before Design Freeze", "within the
HOTO/Discovery period", "90 days before Contract expiry"). For these:
- "date_text": the relative period exactly as printed ("within 30 days of
  the date of this Agreement", "90 days prior to the Contract expiry").
- "date_iso": null. Never compute an absolute date from a relative period
  unless the anchor event's own calendar date is ALSO visible on the pages
  you were shown — a null date_iso is the CORRECT, EXPECTED output for the
  large majority of relative deadlines, not a failure to resolve them.
- "anchor_event": the named trigger the period counts from, as printed or
  closely paraphrased ("Effective Date of Contract", "Design Freeze",
  "date of signing of contract") — this is what makes a null-dated item
  still useful to a reader trying to track it.

RECURRING/PERIODIC DUTIES
A duty that repeats on a cadence (quarterly interest recalculation, monthly
FinOps reporting, an annual technology roadmap) is neither a single
deadline nor a date-less one-off obligation. Set "cadence" to the
recurrence ("quarterly", "monthly", "annual") instead of (or alongside)
"date_text".

CONTRADICTORY DATES IN THE SAME DOCUMENT — REPORT BOTH, NEVER RECONCILE
Real source documents sometimes contain genuine internal contradictions —
e.g. a "Key Dates" table stating two different bid-submission deadlines in
adjacent cells, or the same eligibility requirement anchored to "date of
bid publication" in one clause and "date of bid submission" in another.
When you encounter this, report EACH occurrence exactly as printed on its
own page. Do not silently pick one as "the real one," average them, or
omit either — resolving a genuine contradiction in the source document is
a human review task, not yours.

TEMPLATE / PRO-FORMA ANNEXURES — REPORT THE TOP-LEVEL REQUIREMENT ONLY
A tender often includes a fill-in-the-blank template a bidder must later
execute (a Consortium MoU format, a Power-of-Attorney format) — these carry
their own internal "shall" clauses that only become binding once a
specific bidder fills them in and signs. Do NOT extract clause-by-clause
from inside such a template. Report only the single top-level requirement
that the document must be executed/submitted (e.g. "Bidder must submit a
signed Consortium Agreement/MoU") — which you likely already reported once,
earlier, from the eligibility-criteria section that first mentions it.

RESPONSIBLE PARTY — NORMALIZE INSTITUTIONAL ROLES, PRESERVE COMMERCIAL ONES
The same purchasing institution is often called by several interchangeable
titles across one document — "Purchaser", "Controlling Officer",
"Officer-in-Charge", "Nodal Officer", "CGM(IT)", "competent authority". Set
"responsible_party" to "Purchaser" for all of these — they are the same
institutional actor wearing different hats, not different parties. Do NOT
collapse the bidder-side roles the same way: "Bidder", "SI" (System
Integrator), "Agency", and "Consortium Partner" genuinely mean different
things at different contract stages (a tender-stage bidder becomes a
post-award SI; a named consortium member is distinct from the lead bidder)
— keep these as printed.

EVIDENCE AND GROUNDING
- "evidence_quote": an exact verbatim substring (>= 8 characters, roughly
  8-40 words) copied directly from the page text, proving this item is
  really there. This is mandatory — an item you cannot quote verbatim must
  not be reported. Copy it exactly as it appears, including any HTML table
  markup, run-on words from a flattened table cell, or unusual punctuation
  — do not clean it up or paraphrase it; a cleaned-up quote cannot be
  located against the real page text afterward.
- "item_text": a short (<= 30 words) plain-language label a human reviewer
  can scan quickly — this one, unlike evidence_quote, may paraphrase.

PAGE ATTRIBUTION
- "page": the page the clause is printed on. Use only the page numbers
  shown to you. If a single clause's text continues onto a later page you
  were also shown, set "end_page" and leave "still_open_at_chunk_end"
  false; if it is still open when the LAST page you were shown ends, set
  "end_page" to null and "still_open_at_chunk_end" to true, exactly as a
  structure pass would for an open heading.

WHAT IS NOT AN ACTIONABLE ITEM
- Definitions, recitals, or background/preamble prose with no actual
  obligation, deadline, or condition.
- A clause merely DESCRIBING a process already completed, not tied to any
  future action ("The tender was published on...").
- Table column headers, signature blocks, blank form fields.
- A clause reproduced/quoted verbatim inside someone else's argument,
  rather than a live obligation of the document at hand.
- Standing jurisdiction/forum-selection or dispute-resolution-mechanism
  clauses with no attached date, trigger, or named duty of their own.

SELF-CHECK BEFORE YOU FINALIZE: re-scan every page you were shown once
more, specifically for deadlines (dates/periods), obligations ("shall"/
"must"/"is required to"), and conditions ("if... then", "in the event of",
"failure to... shall result in") you may have skipped because they looked
like ordinary body text or table rows — that is exactly the shape being
asked for here, the opposite of a structure/heading pass. Also re-scan
specifically for a prayer-for-relief sentence sitting in ordinary prose
(not a table) that you may have walked past while focused on tabular
content. Also re-check every item you ARE reporting against the three
false-positive patterns above (feature/requirements narrative; AS-IS
description of existing software/module catalogues; boilerplate
declarations and compliance checklists without a distinct trigger) —
remove any that match one of these before finalizing.
"""

ACTION_ITEM_OUTPUT_CONTRACT = """\
# OUTPUT FORMAT (strict JSON — exactly this shape, nothing else)
{
  "items": [
    {
      "item_text": "Bidder must submit EMD before bid submission deadline",
      "item_type": "deadline",
      "date_text": "on or before 15.03.2025, 15:00 hrs",
      "date_iso": "2025-03-15",
      "anchor_event": null,
      "cadence": null,
      "page": 12,
      "end_page": 12,
      "still_open_at_chunk_end": false,
      "evidence_quote": "EMD shall be submitted on or before 15.03.2025",
      "responsible_party": "Bidder",
      "confidence": "high"
    },
    {
      "item_text": "ERP Go-Live milestone, D3.5 = Effective Date + 20 weeks",
      "item_type": "deadline",
      "date_text": "D3.5 = D1 + 20 weeks",
      "date_iso": null,
      "anchor_event": "Effective Date of Contract (D1)",
      "cadence": null,
      "page": 53,
      "end_page": null,
      "still_open_at_chunk_end": false,
      "evidence_quote": "Go-Live</td><td>D3.5= D1+ 20 weeks",
      "responsible_party": "SI",
      "confidence": "medium"
    }
  ],
  "notes": ""
}

FIELD RULES
- "item_type": one of "deadline", "obligation", "condition", "other".
- "date_iso": YYYY-MM-DD, or null whenever no absolute calendar date is
  computable from what you were shown — null is the CORRECT, EXPECTED
  value for most relative deadlines (see RELATIVE/FORMULA-BASED DEADLINES
  above), not a fallback for something you failed to resolve.
- "anchor_event" / "cadence": null unless genuinely applicable — most
  items need neither.
- "confidence": "high" | "medium" | "low", your own self-reported
  confidence in this item's classification.
- "notes": at most one short sentence; "" when you have nothing to flag.
"""


def _render_action_item_macro_context(macro_ctx: Optional[dict]) -> str:
    """Sibling to _render_macro_context, for the actionable-items pass.
    Confirmed necessary, not speculative: a real tender's 40+-page "already
    implemented" module catalogue (job 89cfa529's "Annexure-XIII – ERP
    Modules implemented in MPPKVVCL", page 85) has its ONLY "this is
    existing/background, not a tender obligation" signal on its own root
    heading — a 3-page action-item chunk deep inside the catalogue (e.g.
    pages 113-115) has zero local textual cue that it's AS-IS content, so
    no amount of per-row prose instruction can fix what is fundamentally a
    missing-document-structure-context problem. Unlike the structure
    pass's guideline ("report all clauses belonging to this division"),
    this one explicitly tells the model when to suppress reporting instead."""
    if not macro_ctx:
        return ""
    root = macro_ctx.get("root_container")
    parent = macro_ctx.get("parent_clause")
    if not root and not parent:
        return ""
    lines = ["# ACTIVE DOCUMENT DIVISION (FROM MACRO SKELETON)"]
    lines.append("You are currently analyzing pages inside:")
    if root:
        lines.append(f'- Root Division: "{root}" (Level 1)')
    if parent:
        lines.append(f'- Active Section: "{parent}"')
    lines.append(
        "\nGUIDELINE: If the Root Division or Active Section title above indicates "
        "this division is a catalogue of already-implemented/existing functionality, "
        "an \"AS-IS\" description, or \"current system\" background (titles containing "
        "words like \"implemented\", \"existing\", \"AS-IS\", \"current\"), treat its "
        "ENTIRE content as background for these pages — apply the AS-IS exclusion rule "
        "strictly regardless of how many \"shall\"-shaped table rows you see, unless one "
        "specific sentence states a distinct future-tense tender-imposed duty with its own "
        "trigger (rare inside such a division, but possible — judge that sentence on its own "
        "merits, not by what surrounds it). If the title does not suggest AS-IS/background "
        "framing, this section is informational only — it does not change how you judge the "
        "pages below."
    )
    return "\n".join(lines)


def build_action_item_prompt(
    chunk: ChunkPlan, *, document_hint: str = "", macro_context: Optional[dict] = None,
) -> "tuple[str, str]":
    """Assembles the actionable-items prompt for one chunk. Deliberately
    simpler than build_structure_prompt: no carry_context/open_containers
    (action items are flat per-page leaves, not a nested hierarchy to track
    across chunk boundaries — see ai_action_items.py's module docstring),
    no rule_prior (there is no rule-based fallback for this extraction
    type). `macro_context`, when given, is the same
    {root_container, parent_clause} shape ai_reference_index's two-pass
    design already produces (via _extract_macro_skeleton /
    _get_active_macro_context, reused as-is, never duplicated) — see
    _render_action_item_macro_context."""
    parts = []
    page_nums = chunk.page_nums
    parts.append(f"# PAGE RANGE\nThis chunk contains pages {page_nums[0]}-{page_nums[-1]}. "
                 f"Valid values for \"page\"/\"end_page\": {json.dumps(page_nums)}.")

    macro_block = _render_action_item_macro_context(macro_context)
    if macro_block:
        parts.append(macro_block)

    if document_hint:
        parts.append(f"# DOCUMENT HINT (optional, user supplied)\n<<<HINT>>>\n{document_hint[:500]}\n<<<END HINT>>>")

    page_blocks = []
    for p in chunk.pages:
        page_blocks.append(f"{PAGE_OPEN.format(n=p.page_num)}\n{p.text}\n{PAGE_CLOSE.format(n=p.page_num)}")
    parts.append("# PAGES\n" + "\n".join(page_blocks))

    parts.append(ACTION_ITEM_OUTPUT_CONTRACT)

    return ACTION_ITEM_SYSTEM_PROMPT, "\n\n".join(parts)


MENTION_REVIEW_SYSTEM_PROMPT = """\
You are an expert legal document analyst reviewing cross-reference mentions detected on pages of a legal or contractual document.
Each mention was extracted by a rule-based scan and is either active ("exact" or "approximate") or "suppressed" (e.g. detected as an external statute).
Your job is to review each candidate mention and return an action:
- "confirm": The mention is a valid internal reference to another section, clause, annexure, or page in this document.
- "reject": The mention is a false positive (e.g. an external statute like Arbitration and Conciliation Act, a case citation like AIR/SCC, a running header/footer, or non-referential prose) and must be suppressed.
- "unsuppress": The mention was mistakenly suppressed by the rule scan but is actually an internal reference to a part of this document.

RULES:
1. For "reject" or "unsuppress", you MUST provide an exact verbatim "evidence_quote" (at least 8 characters) from the page text justifying your decision.
2. For "confirm", "evidence_quote" can be "".
3. Return strict JSON matching the schema below.
"""

MENTION_REVIEW_OUTPUT_CONTRACT = """\
# OUTPUT FORMAT (strict JSON — exactly this shape, nothing else)
{
  "reviews": [
    {
      "mention_id": "M1",
      "action": "confirm",
      "evidence_quote": ""
    },
    {
      "mention_id": "M2",
      "action": "reject",
      "evidence_quote": "Arbitration and Conciliation Act, 1996"
    }
  ]
}
"""


def build_mention_review_prompt(
    pages: List[PageInput], mentions: List[Dict[str, Any]],
) -> "tuple[str, str]":
    parts = []
    page_nums = [p.page_num for p in pages]
    parts.append(f"# PAGES UNDER REVIEW: {page_nums}")

    mention_lines = []
    for m in mentions:
        m_id = m.get("mention_id") or f"M{m.get('occurrence_index', 0)}"
        status = m.get("resolution", "exact")
        supp_reason = f" ({m['suppressed_reason']})" if m.get("suppressed_reason") else ""
        raw_text = json.dumps(m.get("raw_text", ""))
        mention_lines.append(f'{m_id} | p{m["page"]} | key={m.get("key", "")} | text={raw_text} | status={status}{supp_reason}')

    parts.append("# DETECTED MENTIONS DRAFT\n" + "\n".join(mention_lines))

    page_blocks = []
    for p in pages:
        page_blocks.append(f"{PAGE_OPEN.format(n=p.page_num)}\n{p.text}\n{PAGE_CLOSE.format(n=p.page_num)}")
    parts.append("# PAGE TEXTS\n" + "\n".join(page_blocks))

    parts.append(MENTION_REVIEW_OUTPUT_CONTRACT)
    return MENTION_REVIEW_SYSTEM_PROMPT, "\n\n".join(parts)


DOCREF_REVIEW_SYSTEM_PROMPT = """\
You are an expert legal document analyst determining the true definition or reproduced source location for document references (such as contracts, specifications, proposals, or claims) cited across a document.
For each document reference key, you are given all occurrences found in the document with their page numbers and context.
Your job is to determine which occurrence represents the actual definition (such as the heading or page where the document/claim is substantively set out, reproduced, or described) rather than a passing citation.
If no occurrence is an actual substantive definition/reproduction (e.g. all are passing citations), return "none" for definition_occurrence_id.

RULES:
1. "definition_occurrence_id" MUST be either the exact ID of the defining occurrence (e.g. "occ_p913_0") or "none".
2. If selecting an occurrence, you should provide an exact verbatim "evidence_quote" (at least 8 characters) from the context.
3. Return strict JSON matching the schema below.
"""

DOCREF_REVIEW_OUTPUT_CONTRACT = """\
# OUTPUT FORMAT (strict JSON — exactly this shape, nothing else)
{
  "reviews": [
    {
      "key": "docref:1",
      "definition_occurrence_id": "occ_p913_0",
      "evidence_quote": "Claim No. 1 — Levy of stage penalty"
    },
    {
      "key": "docref:rec064",
      "definition_occurrence_id": "none",
      "evidence_quote": ""
    }
  ]
}
"""


def build_docref_review_prompt(
    occurrences_by_key: Dict[str, List[Dict[str, Any]]],
    page_texts: Optional[Dict[int, str]] = None,
) -> Tuple[str, str]:
    parts = []
    parts.append("# DOCUMENT REFERENCES FOR DEFINITION REVIEW")
    for key, occs in occurrences_by_key.items():
        parts.append(f"\n## KEY: {key}")
        for occ in occs:
            occ_id = occ.get("occurrence_id") or f"occ_p{occ['page']}_{occ.get('occurrence_index', 0)}"
            snippet = occ.get("snippet") or occ.get("raw_text") or ""
            parts.append(f"- ID: {occ_id} | Page {occ['page']} | Text: {occ.get('raw_text')} | Context: \"{snippet}\"")

    parts.append("\n" + DOCREF_REVIEW_OUTPUT_CONTRACT)
    return DOCREF_REVIEW_SYSTEM_PROMPT, "\n".join(parts)



