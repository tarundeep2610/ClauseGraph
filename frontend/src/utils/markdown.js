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
// `[\s-]*` (zero or more spaces/hyphens), not `\s+` (one or more) — OCR
// commonly drops the space entirely ("ANNEXURE-B1", "Annexure-1"). Safe: the
// `\b(KEYWORD)\b` boundary is unaffected either way, so this only changes
// what's tolerated *between* the already-whole-word keyword and the number.
const MENTION_RE = new RegExp(
  `^\\b(${KEYWORD_ALTS})\\b\\.?[\\s-]*(?:no\\.?\\s*)?([A-Za-z]-?)?([ivxlcdmIVXLCDM]+|\\d+(?:\\.\\d+)*)\\b`,
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

// ─── Docref & Defined-Term vocabulary (mirrors reference_index.py) ────────────

const DOCREF_LABEL_ALTS =
  'technical specification|specification|tender enquiry|enquiry|tender|' +
  'bidding document|bid(?:ding)?|proposal|offer|quotation|purchase order|' +
  'work order|letter of award|letter|memo(?:randum)?|file|invoice|agreement|' +
  'contract|claim|arbitration case|case|award|order|misc(?:ellaneous)?';

const DOCREF_BARE_NUMERIC_LABELS = new Set(['claim', 'case', 'arbitration case']);

const DOCREF_START_RE = new RegExp(`\\b(?:${DOCREF_LABEL_ALTS})\\b`, 'i');

const DOCREF_MENTION_RE = new RegExp(
  `^\\b(${DOCREF_LABEL_ALTS})\\b\\.?[\\s-]*Nos?\\.?[\\s-]*:?\\s*([A-Za-z0-9](?:[A-Za-z0-9()\\-]|/(?:\\s*)?){0,49}(?:\\s+\\([A-Za-z0-9/()\\-]+\\))?)`,
  'i'
);

export function normalizeDocrefCode(code) {
  const clean = code.trim().replace(/[.,;:\-/ ]+$/, '');
  return clean.replace(/[^A-Za-z0-9]/g, '').toUpperCase();
}

export function docrefKey(code) {
  return `docref:${normalizeDocrefCode(code).toLowerCase()}`;
}

export function isPlausibleDocrefCode(code, label) {
  const cleanCode = code.trim().replace(/[.,;:\-/ ]+$/, '');
  if (!cleanCode) return false;
  if (cleanCode.includes('<|') || cleanCode.includes('|>') || cleanCode.toLowerCase().includes('det')) return false;
  if (!/\d/.test(cleanCode)) return false;
  const norm = normalizeDocrefCode(cleanCode);
  if (!norm) return false;
  const normLabel = label.toLowerCase().trim();
  if (['page', 'clause', 'section', 'article'].includes(normLabel)) return false;
  if (/^\d+$/.test(norm)) {
    if (DOCREF_BARE_NUMERIC_LABELS.has(normLabel)) {
      return norm.length >= 1 && norm.length <= 4;
    }
    return false;
  }
  return true;
}

export function normalizeTermKey(term) {
  const clean = term.trim().replace(/^the\s+/i, '').replace(/\s+/g, ' ').trim().toLowerCase();
  return clean;
}

export function termKey(term) {
  return `term:${normalizeTermKey(term)}`;
}

function escapeRegex(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
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
  if (token.type === 'xref' || token.type === 'docxref' || token.type === 'termxref') return token.raw || '';
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

// ─── Shared match-and-verdict logic ───────────────────────────────────────────
// Used by the marked tokenizer below (anchored at position 0 of whatever `src`
// marked hands it — marked's own `start()` already found the right offset).
// Consults the server's persisted verdict for pageNum: if suppressed, returns null
// so marked leaves the text un-linkified.
function tryMatchXref(src, pageNum, store) {
  const m = MENTION_RE.exec(src);
  if (!m) return null;
  const [raw, keyword, prefixRaw, numberRaw] = m;
  const key = normalizeKey(keyword, prefixRaw || null, numberRaw);
  const [prefix, number] = reconcilePrefixAndNumber(keyword, prefixRaw || null, numberRaw);

  const pageMentions = store?.mentionsByPage?.get(pageNum);
  if (pageMentions) {
    const verdicts = pageMentions.filter((item) => item.key === key);
    if (verdicts.length > 0 && verdicts.every((item) => item.resolution === 'suppressed')) {
      return null;
    }
  }

  return { type: 'xref', raw, key, keyword, prefix, number };
}

// ─── Mention-linkifying inline extension ───────────────────────────────────────
// Registered fresh per render (a new Marked instance per call — marked.use()
// mutates shared state, so a stale referenceStore closed over by an earlier
// render must never leak into a later one).

function createXrefExtension(pageNum, store) {
  return {
    name: 'xref',
    level: 'inline',
    start(src) {
      const m = MENTION_START_RE.exec(src);
      return m ? m.index : undefined;
    },
    tokenizer(src) {
      return tryMatchXref(src, pageNum, store) || undefined;
    },
    renderer(token) {
      const { exact, approximate } = resolveMention(store, token.key);
      if (exact.length + approximate.length === 0) {
        return escapeHtml(token.raw);
      }
      // The server's own verdict is authoritative when it has one for this
      // key/page (same source getPageMentions reads for the badge list) — a
      // client-only resolveMention computation here would be a second,
      // independently-arrived-at judgment that can disagree with the badge's,
      // rendering the inline link and its own page badge inconsistently for
      // the same mention. Fall back to resolveMention only when the server
      // has no mention row at all for this key on this page.
      const pageMentions = store?.mentionsByPage?.get(pageNum);
      const serverVerdict = pageMentions?.find(
        (item) => item.key === token.key && item.resolution !== 'suppressed',
      );
      const isApproximate = serverVerdict
        ? serverVerdict.resolution === 'approximate'
        : exact.length === 0;
      const cls = isApproximate ? 'xref-link xref-link--approximate' : 'xref-link';
      return `<a href="#" class="${cls}" data-xref-key="${escapeAttr(token.key)}">${escapeHtml(token.raw)}</a>`;
    },
  };
}

function tryMatchDocXref(src, pageNum, store) {
  const m = DOCREF_MENTION_RE.exec(src);
  if (!m) return null;
  const [raw, label, codeRaw] = m;
  const code = codeRaw.trim().replace(/[.,;:\-/ ]+$/, '');
  if (!isPlausibleDocrefCode(code, label)) return null;

  let key = docrefKey(code);
  const pageMentions = store?.mentionsByPage?.get(pageNum);
  if (pageMentions) {
    const matchingMention = pageMentions.find(
      (item) => item.key.startsWith('docref:') && (item.raw_text === raw || item.raw === raw || item.key === key)
    );
    if (matchingMention) {
      if (matchingMention.resolution === 'suppressed') return null;
      key = matchingMention.key;
    } else {
      const verdicts = pageMentions.filter((item) => item.key === key);
      if (verdicts.length > 0 && verdicts.every((item) => item.resolution === 'suppressed')) {
        return null;
      }
    }
  }

  return { type: 'docxref', raw, key, label, code };
}

export function createDocXrefExtension(pageNum, store) {
  return {
    name: 'docxref',
    level: 'inline',
    start(src) {
      const m = DOCREF_START_RE.exec(src);
      return m ? m.index : undefined;
    },
    tokenizer(src) {
      return tryMatchDocXref(src, pageNum, store) || undefined;
    },
    renderer(token) {
      const { exact, approximate } = resolveMention(store, token.key);
      if (exact.length + approximate.length === 0) {
        return escapeHtml(token.raw);
      }
      const pageMentions = store?.mentionsByPage?.get(pageNum);
      const serverVerdict = pageMentions?.find(
        (item) => item.key === token.key && item.resolution !== 'suppressed',
      );
      const isApproximate = serverVerdict
        ? serverVerdict.resolution === 'approximate'
        : exact.length === 0;
      const cls = isApproximate ? 'xref-link xref-link--approximate' : 'xref-link';
      return `<a href="#" class="${cls}" data-xref-key="${escapeAttr(token.key)}">${escapeHtml(token.raw)}</a>`;
    },
  };
}

function getActiveTermDefinitions(store, pageNum) {
  const termsMap = new Map();
  if (store?.byExactKey) {
    for (const k of store.byExactKey.keys()) {
      if (k.startsWith('term:')) {
        const name = k.slice(5).trim();
        if (name) termsMap.set(name, k);
      }
    }
  }
  const pageMentions = store?.mentionsByPage?.get(pageNum);
  if (pageMentions) {
    for (const m of pageMentions) {
      if (m.key?.startsWith('term:')) {
        const name = m.key.slice(5).trim();
        if (name && !termsMap.has(name)) termsMap.set(name, m.key);
      }
    }
  }
  return termsMap;
}

export function createTermXrefExtension(pageNum, store) {
  const termsMap = getActiveTermDefinitions(store, pageNum);
  if (!termsMap.size) {
    return {
      name: 'termxref',
      level: 'inline',
      start() { return undefined; },
      tokenizer() { return undefined; },
      renderer(token) { return escapeHtml(token.raw); },
    };
  }

  const shortTerms = [];
  const longTerms = [];
  for (const name of termsMap.keys()) {
    if (name.length <= 4) {
      shortTerms.push(name.toUpperCase());
    } else {
      longTerms.push(name);
    }
  }
  shortTerms.sort((a, b) => b.length - a.length);
  longTerms.sort((a, b) => b.length - a.length);

  const shortPat = shortTerms.length ? shortTerms.map(escapeRegex).join('|') : null;
  const longPat = longTerms.length ? longTerms.map(escapeRegex).join('|') : null;

  const shortGlobalRe = shortPat ? new RegExp(`\\b(?:${shortPat})\\b`, 'g') : null;
  const longGlobalRe = longPat ? new RegExp(`\\b(?:${longPat})\\b`, 'gi') : null;

  const shortAnchorRe = shortPat ? new RegExp(`^\\b(?:${shortPat})\\b`) : null;
  const longAnchorRe = longPat ? new RegExp(`^\\b(?:${longPat})\\b`, 'i') : null;

  return {
    name: 'termxref',
    level: 'inline',
    start(src) {
      let minIndex = undefined;
      if (shortGlobalRe) {
        shortGlobalRe.lastIndex = 0;
        const m = shortGlobalRe.exec(src);
        if (m) minIndex = m.index;
      }
      if (longGlobalRe) {
        longGlobalRe.lastIndex = 0;
        const m = longGlobalRe.exec(src);
        if (m && (minIndex === undefined || m.index < minIndex)) {
          minIndex = m.index;
        }
      }
      return minIndex;
    },
    tokenizer(src) {
      let raw = null;
      if (shortAnchorRe) {
        const m = shortAnchorRe.exec(src);
        if (m) raw = m[0];
      }
      if (!raw && longAnchorRe) {
        const m = longAnchorRe.exec(src);
        if (m) raw = m[0];
      }
      if (!raw) return undefined;

      const normKey = termKey(raw);
      const key = termsMap.get(normalizeTermKey(raw)) || normKey;

      const pageMentions = store?.mentionsByPage?.get(pageNum);
      if (pageMentions) {
        const verdicts = pageMentions.filter((item) => item.key === key);
        if (verdicts.length > 0 && verdicts.every((item) => item.resolution === 'suppressed')) {
          return undefined;
        }
      }

      return { type: 'termxref', raw, key };
    },
    renderer(token) {
      const { exact, approximate } = resolveMention(store, token.key);
      if (exact.length + approximate.length === 0) {
        return escapeHtml(token.raw);
      }
      const pageMentions = store?.mentionsByPage?.get(pageNum);
      const serverVerdict = pageMentions?.find(
        (item) => item.key === token.key && item.resolution !== 'suppressed',
      );
      const isApproximate = serverVerdict
        ? serverVerdict.resolution === 'approximate'
        : exact.length === 0;
      const cls = isApproximate ? 'xref-link xref-link--approximate' : 'xref-link';
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

function lexPageWithXrefs(pageText, pageNum, store) {
  const instance = new Marked();
  instance.use({
    extensions: [
      createXrefExtension(pageNum, store),
      createDocXrefExtension(pageNum, store),
      createTermXrefExtension(pageNum, store),
    ],
  });
  const tokens = instance.lexer(pageText || '');
  return { instance, tokens };
}

// Every mention on a page that actually resolves to something — used to
// populate the PDF pane's per-page reference badge/list (Result pane no
// longer being the click surface).
// Frontend getPageMentions is a pure store read against store.mentionsByPage.
export function getPageMentions(pageText, pageNum, store) {
  const pageMentions = store?.mentionsByPage?.get(pageNum);
  if (!pageMentions) return [];
  const seen = new Set();
  const mentions = [];
  for (const m of pageMentions) {
    if (m.resolution === 'suppressed') continue;
    if (seen.has(m.key)) continue;
    seen.add(m.key);
    const item = {
      key: m.key,
      raw: m.raw_text || m.raw,
      approximate: m.resolution === 'approximate',
    };
    if (m.resolution === 'undefined') {
      item.undefined = true;
    }
    mentions.push(item);
  }
  return mentions;
}

export function renderPageMarkdown(pageText, pageNum, store) {
  const text = pageText || '';
  const cacheKey = `${pageNum}::${store.version}::${text.length}::${text.slice(0, 80)}`;
  const cached = _renderCache.get(cacheKey);
  if (cached !== undefined) return cached;

  const { instance, tokens } = lexPageWithXrefs(text, pageNum, store);
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
