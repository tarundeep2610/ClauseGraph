import { marked, Marked } from 'marked';
import DOMPurify from 'dompurify';
import { getPageCandidates, resolveMention } from './referenceStore';

export function renderMarkdown(md) {
  return DOMPurify.sanitize(marked.parse(md));
}

// ─── Mention vocabulary (mirrors reference_index.py's CORE_RE / normalize_key —
// keep both sides in sync; see that module's docstring for the roman-numeral
// prefix-collision fix this also carries) ─────────────────────────────────────

const KEYWORD_ALTS = 'clause|section|sec\\.?|article|art\\.?|para(?:graph)?|annexure|schedule|appendix|page|ground';
const MENTION_START_RE = new RegExp(`\\b(?:${KEYWORD_ALTS})\\b`, 'i');
const MENTION_RE = new RegExp(
  `^\\b(${KEYWORD_ALTS})\\b\\.?\\s+(?:no\\.?\\s*)?([A-Za-z]-?)?([ivxlcdmIVXLCDM]+|\\d+(?:\\.\\d+)*)\\b`,
  'i'
);

const FAMILY_ALIASES = {
  sec: 'section', section: 'section',
  art: 'article', article: 'article',
  para: 'para', paragraph: 'para',
  clause: 'clause', annexure: 'annexure', schedule: 'schedule',
  appendix: 'appendix', page: 'page', ground: 'ground',
};
const PREFIX_ELIGIBLE_FAMILIES = new Set(['annexure', 'schedule', 'appendix']);
const ROMAN_VALUES = { i: 1, v: 5, x: 10, l: 50, c: 100, d: 500, m: 1000 };

function isRoman(s) {
  return /^[ivxlcdm]+$/i.test(s);
}

function romanToArabic(raw) {
  const s = raw.toLowerCase();
  let total = 0;
  let prev = 0;
  for (let i = s.length - 1; i >= 0; i--) {
    const val = ROMAN_VALUES[s[i]] || 0;
    if (val < prev) total -= val;
    else {
      total += val;
      prev = val;
    }
  }
  return total;
}

function familyOf(keyword) {
  return FAMILY_ALIASES[keyword.toLowerCase().replace(/\.$/, '')] || keyword.toLowerCase();
}

// A single optional letter before the number is only a real exhibit prefix for
// annexure/schedule/appendix ("Annexure A-5"). Elsewhere it's CORE_RE/MENTION_RE
// ambiguously splitting a multi-letter roman numeral's leading character (e.g.
// "Article II" -> prefix "I" + number "I") — reconciled here exactly like the
// backend's reconcile_prefix_and_number.
function reconcilePrefixAndNumber(keyword, prefix, numberRaw) {
  const family = familyOf(keyword);
  if (prefix && !PREFIX_ELIGIBLE_FAMILIES.has(family)) {
    return [null, `${prefix.replace(/-$/, '')}${numberRaw}`];
  }
  return [prefix, numberRaw];
}

function normalizeKey(keyword, prefixRaw, numberRaw) {
  const family = familyOf(keyword);
  const [prefix, number0] = reconcilePrefixAndNumber(keyword, prefixRaw, numberRaw);
  const number = isRoman(number0) ? String(romanToArabic(number0)) : number0;
  const prefixPart = prefix ? `${prefix.replace(/-$/, '').toLowerCase()}-` : '';
  return `${family}:${prefixPart}${number}`;
}

function escapeHtml(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function escapeAttr(s) {
  return String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;');
}

// ─── Block flattening (JS mirror of reference_index.py's _flatten_blocks —
// only the granularity needed for fingerprint anchor matching: paragraph,
// heading, list-item body text, table cells) ───────────────────────────────────

function leafText(token) {
  if (!token) return '';
  if (token.type === 'text' || token.type === 'codespan') return token.text || '';
  if (token.type === 'br' || token.type === 'space') return ' ';
  // A block's own opening words are frequently themselves a valid mention
  // (e.g. a "Clause 4.2 ..." target's own text starts with a "Clause 4.2"
  // mention) — the xref inline extension already consumed that run into an
  // xref token during lexing, before this fingerprint pass ever runs on the
  // same shared token tree. `raw` preserves the original matched substring,
  // so plain-text reconstruction stays correct regardless of linkification.
  if (token.type === 'xref') return token.raw || '';
  if (Array.isArray(token.tokens)) return token.tokens.map(leafText).join('');
  return '';
}

function flattenListItem(item, out, ordinal) {
  const ownTokens = [];
  for (const child of item.tokens || []) {
    if (child.type === 'list') {
      flattenBlocks([child], out);
    } else if (child.type === 'text' || child.type === 'paragraph') {
      ownTokens.push(child);
    }
  }
  if (ownTokens.length) {
    out.push({ __listItemBlock: true, textTokens: ownTokens, injectTarget: ownTokens[0], ordinal });
  }
}

function flattenBlocks(tokens, out = []) {
  for (const tok of tokens || []) {
    if (tok.type === 'paragraph' || tok.type === 'heading') {
      out.push(tok);
    } else if (tok.type === 'list') {
      const ordered = tok.ordered;
      const start = ordered && typeof tok.start === 'number' ? tok.start : 1;
      (tok.items || []).forEach((item, idx) => {
        flattenListItem(item, out, ordered ? start + idx : undefined);
      });
    } else if (tok.type === 'table') {
      for (const cell of tok.header || []) out.push(cell);
      for (const row of tok.rows || []) for (const cell of row) out.push(cell);
    }
  }
  return out;
}

function blockPlainText(block) {
  if (block.__listItemBlock) {
    // marked strips an ordered list item's own "N." marker out of `.text`
    // entirely (same as mistune on the Python side) — reattached here so this
    // matches the fingerprint the backend computed from the reconstructed
    // "N. <body>" form (reference_index.py's own _ordinal handling).
    const body = block.textTokens.map(leafText).join(' ').replace(/\s+/g, ' ').trim();
    return block.ordinal !== undefined ? `${block.ordinal}. ${body}` : body;
  }
  return leafText({ tokens: block.tokens }).replace(/\s+/g, ' ').trim();
}

function injectAnchor(block, anchorId) {
  // marked's inline html renderer reads `.text`, not `.raw` (verified against
  // the installed marked 13.0.3: `Renderer.prototype.html({ text })`).
  const span = `<span id="${escapeAttr(anchorId)}" class="xref-anchor"></span>`;
  const spanToken = { type: 'html', raw: span, text: span, block: false };
  const target = block.__listItemBlock ? block.injectTarget : block;
  target.tokens = target.tokens || [];
  target.tokens.unshift(spanToken);
}

// ─── Mention-linkifying inline extension ───────────────────────────────────────
// Registered fresh per render (a new Marked instance per call — marked.use()
// mutates shared state, so a stale referenceStore closed over by an earlier
// render must never leak into a later one).

function createXrefExtension(store) {
  return {
    name: 'xref',
    level: 'inline',
    start(src) {
      const m = MENTION_START_RE.exec(src);
      return m ? m.index : undefined;
    },
    tokenizer(src) {
      const m = MENTION_RE.exec(src);
      if (!m) return undefined;
      const [raw, keyword, prefixRaw, numberRaw] = m;
      const key = normalizeKey(keyword, prefixRaw || null, numberRaw);
      return { type: 'xref', raw, key };
    },
    renderer(token) {
      const { exact, approximate } = resolveMention(store, token.key);
      if (exact.length + approximate.length === 0) {
        return escapeHtml(token.raw);
      }
      const cls = exact.length === 0 ? 'xref-link xref-link--approximate' : 'xref-link';
      return `<a href="#" class="${cls}" data-xref-key="${escapeAttr(token.key)}">${escapeHtml(token.raw)}</a>`;
    },
  };
}

// ─── Per-page render (Phase B) ─────────────────────────────────────────────────
// Single combined lex/render pass: mention-linkifying (via the xref extension
// above, applied during .lexer()) and fingerprint-based target-anchor tagging
// (a walkTokens-style mutation of the same token tree) share one marked.lexer()
// call and one marked.parser() call, not two.

const _renderCache = new Map();
const _RENDER_CACHE_LIMIT = 2000;

// Shared lex step for both renderPageMarkdown (below) and getPageMentions —
// a fresh Marked instance + the xref extension, exactly as renderPageMarkdown
// always used, factored out so the two can never drift on what counts as a
// mention (getPageMentions used to not exist; it now reuses this verbatim).
function lexPageWithXrefs(pageText, store) {
  const instance = new Marked();
  instance.use({ extensions: [createXrefExtension(store)] });
  const tokens = instance.lexer(pageText || '');
  return { instance, tokens };
}

// Every 'xref' inline token found anywhere in a page's token tree (inside
// paragraphs, headings, list items, table cells, and nested inline formatting
// like bold/italic) — walks the same shape flattenBlocks/blockPlainText read.
function collectXrefTokens(tokens, out = []) {
  for (const tok of tokens || []) {
    if (tok.type === 'xref') {
      out.push(tok);
      continue;
    }
    if (Array.isArray(tok.tokens)) collectXrefTokens(tok.tokens, out);
    // 'list' tokens nest their block children under .items (each a
    // list_item with its own .tokens), not .tokens directly.
    if (Array.isArray(tok.items)) collectXrefTokens(tok.items, out);
    // 'table' tokens nest header/body cells, each with its own .tokens.
    if (Array.isArray(tok.header)) for (const cell of tok.header) collectXrefTokens(cell.tokens, out);
    if (Array.isArray(tok.rows)) for (const row of tok.rows) for (const cell of row) collectXrefTokens(cell.tokens, out);
  }
  return out;
}

// Every mention on a page that actually resolves to something — used to
// populate the PDF pane's per-page reference badge/list (Result pane no
// longer being the click surface). Only the resolving ones are returned,
// matching exactly what renderPageMarkdown would have linkified (not greyed
// out as an unresolved plain-text mention); deduped by key since a page-level
// list should offer each distinct target once, not once per occurrence.
export function getPageMentions(pageText, pageNum, store) {
  const { tokens } = lexPageWithXrefs(pageText, store);
  const seen = new Set();
  const mentions = [];
  for (const tok of collectXrefTokens(tokens)) {
    if (seen.has(tok.key)) continue;
    const { exact, approximate } = resolveMention(store, tok.key);
    if (exact.length + approximate.length === 0) continue;
    seen.add(tok.key);
    mentions.push({ key: tok.key, raw: tok.raw });
  }
  return mentions;
}

export function renderPageMarkdown(pageText, pageNum, store) {
  const text = pageText || '';
  const cacheKey = `${pageNum}::${store.version}::${text.length}::${text.slice(0, 80)}`;
  const cached = _renderCache.get(cacheKey);
  if (cached !== undefined) return cached;

  const { instance, tokens } = lexPageWithXrefs(text, store);
  const blocks = flattenBlocks(tokens);

  const remaining = [];
  for (const [key, candidates] of Object.entries(getPageCandidates(store, pageNum))) {
    for (const c of candidates) {
      if (c.fingerprint) remaining.push({ key, ...c });
    }
  }

  for (const block of blocks) {
    if (!remaining.length) break;
    const plain = blockPlainText(block).toLowerCase();
    if (!plain) continue;
    const idx = remaining.findIndex((c) => plain.startsWith(c.fingerprint.toLowerCase().trim()));
    if (idx !== -1) {
      injectAnchor(block, remaining[idx].anchor_id);
      remaining.splice(idx, 1);
    }
  }

  const html = instance.parser(tokens);
  const clean = DOMPurify.sanitize(html, { ADD_ATTR: ['data-xref-key'] });

  if (_renderCache.size >= _RENDER_CACHE_LIMIT) {
    _renderCache.delete(_renderCache.keys().next().value);
  }
  _renderCache.set(cacheKey, clean);
  return clean;
}
