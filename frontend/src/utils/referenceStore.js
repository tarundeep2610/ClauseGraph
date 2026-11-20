// Client-side accumulator for the backend's page-delta cross-reference index
// (GET /api/jobs/{id}/references?since_version=V). Each delta whole-page-replaces
// that page's contribution; everything else already known stays untouched.
// collapsed_index/page_scope_index are deliberately not part of the wire format —
// they're derived here, from data the store already has, incrementally as
// deltas merge in (not recomputed by a full scan on every lookup).

import { findRangeForPage } from './scopeRanges';

export function createReferenceStore() {
  return {
    version: 0,
    pageIndex: new Map(),      // page -> { key -> [rawCandidate, ...] } (as received from the server)
    byExactKey: new Map(),     // key -> [{...candidate, page, key}, ...]
    byCollapsedKey: new Map(), // collapsed_key -> Set<key>
    mentionsByPage: new Map(), // page -> [{key, raw_text, resolution, ...}, ...]
  };
}

function removePageContribution(store, page) {
  const prev = store.pageIndex.get(page);
  if (!prev) return;
  for (const key of Object.keys(prev)) {
    const list = store.byExactKey.get(key);
    if (!list) continue;
    const filtered = list.filter((c) => c.page !== page);
    if (filtered.length) {
      store.byExactKey.set(key, filtered);
    } else {
      store.byExactKey.delete(key);
      for (const set of store.byCollapsedKey.values()) {
        set.delete(key);
      }
    }
  }
  for (const [ck, set] of store.byCollapsedKey.entries()) {
    if (!set.size) store.byCollapsedKey.delete(ck);
  }
}

function addPageContribution(store, page, byKey) {
  for (const [key, candidates] of Object.entries(byKey || {})) {
    const withPage = candidates.map((c) => ({ ...c, page, key }));
    const existing = store.byExactKey.get(key) || [];
    store.byExactKey.set(key, existing.concat(withPage));
    for (const c of withPage) {
      const ck = c.collapsed_key || key;
      let set = store.byCollapsedKey.get(ck);
      if (!set) {
        set = new Set();
        store.byCollapsedKey.set(ck, set);
      }
      set.add(key);
    }
  }
}

// Merges a /references response (or an equivalent {reference_index_version,
// changed_pages, changed_mentions} object) into the store in place. Mutates and returns `store`.
export function mergeDelta(store, delta) {
  if (!delta) return store;
  const changed = delta.changed_pages || {};
  for (const [pageStr, byKey] of Object.entries(changed)) {
    const page = Number(pageStr);
    removePageContribution(store, page);
    store.pageIndex.set(page, byKey);
    addPageContribution(store, page, byKey);
  }
  const changedMentions = delta.changed_mentions || {};
  for (const [pageStr, mentions] of Object.entries(changedMentions)) {
    const page = Number(pageStr);
    store.mentionsByPage.set(page, mentions || []);
  }
  if (typeof delta.reference_index_version === 'number') {
    store.version = delta.reference_index_version;
  }
  return store;
}

// This page's own candidates (for target-anchor tagging) — { key: [candidate, ...] }
export function getPageCandidates(store, page) {
  return store.pageIndex.get(page) || {};
}

// A representative scope_key for a page (used only to sort a mention's candidates
// by proximity to the reader's current position).
//
// Inverted order: `ranges` (Part B's GET /scope-ranges response) derives from the
// persisted end_of_page_scope stack and is authoritative across page spans; consult
// it first. Fall back to the first scope_key found among this page's own candidates
// only when ranges are missing or don't cover the page.
export function getPageScopeKey(store, page, ranges) {
  if (ranges) {
    const range = findRangeForPage(ranges, page);
    if (range) return range.scope_key;
  }
  const byKey = store.pageIndex.get(page);
  if (byKey) {
    for (const arr of Object.values(byKey)) {
      for (const c of arr) {
        // A genuine root-level scope_key is "" (no ancestors at all) — a
        // truthy check would treat that the same as absent and skip it,
        // losing the "same root level" proximity signal. null/undefined
        // (e.g. the synthesized page:N row's scope_key) is what's actually
        // absent, and stays correctly skipped.
        if (c.scope_key !== undefined && c.scope_key !== null) return c.scope_key;
      }
    }
  }
  return null;
}

// The AI structure indexer mints generic keys for numbered headings it can't
// attribute to a specific vocabulary word (`numheading:9.0`, `point:3`), while
// mention-matching only recognizes the literal words below. A mention in one
// of these families that finds no same-family target is still very likely
// pointing at one of these generic targets under the same number — verified
// against job bb417471-561c-4029-bb21-3bdcf74e7dcb, which has zero clause:*/
// section:*/article:*/schedule:*/appendix:* targets at all, yet real in-text
// citations like "clause 9.0 ITB" and "para 1 & 2 of above affidavit" that
// clearly mean an indexed numheading:9.0 / point:1 target. `annexure`,
// `ground`, `page` are deliberately absent — those families already mint
// matching target keys directly and don't need a fallback.
const FAMILY_FALLBACKS = {
  clause: ['numheading'],
  section: ['numheading'],
  article: ['numheading'],
  schedule: ['numheading'],
  appendix: ['numheading'],
  para: ['point', 'numheading'],
};

// A fallback-family lookup for number N must also try N's integer/`.0` dual
// form — verified against real data, not theoretical: job bb417471 has
// `numheading:9.0` on page 25 (the real contract clause "9.0 Local/ Site
// conditions") and a *distinct* `numheading:9` on page 48 that is a genuine
// page-margin-stamp misfire (own_label "9", snippet "-9:-..."). A bare
// string-equality lookup for a "clause 9" mention would either miss the real
// page-25 target entirely or surface only the bogus page-48 stamp; trying
// both forms surfaces both, and proximity sorting at display time naturally
// ranks the real nearby one first.
function numberDualForms(number) {
  if (/\.0$/.test(number)) return [number, number.slice(0, -2)];
  if (!number.includes('.')) return [number, `${number}.0`];
  return [number];
}

// Resolves a normalized mention key against the accumulated index: `exact` is
// every candidate stored under that exact key; `approximate` is every candidate
// reached only via a shared collapsed_key under a DIFFERENT real key (an
// OCR-tolerant fallback — e.g. a mention of "Clause 11" also surfacing a real,
// distinct "Clause 1.1" if the document has one), or via a cross-family
// numeric fallback (see FAMILY_FALLBACKS above). Always both computed, never
// one gated on the other being empty — stratification happens at display time.
export function resolveMention(store, key) {
  const exact = store.byExactKey.get(key) || [];
  const approximate = [];
  const candidateKey = store.byExactKey.has(key) ? key : null;
  const ck = (candidateKey && store.byExactKey.get(candidateKey)?.[0]?.collapsed_key) || collapseKeyFallback(key);
  const siblingKeys = store.byCollapsedKey.get(ck);
  if (siblingKeys) {
    for (const k of siblingKeys) {
      if (k === key) continue;
      approximate.push(...(store.byExactKey.get(k) || []));
    }
  }

  const sepIdx = key.indexOf(':');
  if (sepIdx !== -1) {
    const family = key.slice(0, sepIdx);
    const number = key.slice(sepIdx + 1);
    const fallbackFamilies = FAMILY_FALLBACKS[family];
    if (fallbackFamilies) {
      for (const fam of fallbackFamilies) {
        for (const n of numberDualForms(number)) {
          approximate.push(...(store.byExactKey.get(`${fam}:${n}`) || []));
        }
      }
    }
  }

  return { exact, approximate: dedupeCandidates(approximate) };
}

// Cheap insurance, not a currently-live bug: no concrete duplicate is
// constructible against the lookup paths above (the collapsed-key and
// cross-family-fallback paths land on structurally disjoint keys), but a
// future fallback addition or an overlapping FAMILY_FALLBACKS entry could
// silently regress this, and the failure mode (a single real target showing
// up as a 2-row dropdown) is bad enough to guard for near-zero cost.
function dedupeCandidates(list) {
  const seen = new Set();
  const out = [];
  for (const c of list) {
    const id = `${c.key}::${c.page}::${c.anchor_id ?? c.occurrence_index}`;
    if (seen.has(id)) continue;
    seen.add(id);
    out.push(c);
  }
  return out;
}

// All accumulated candidates, for the Structure tab's tree builder — excludes
// the `page:*` family (both the always-synthesized self-reference and any
// stored stamped-page-number row): those are pagination bookkeeping, not
// document structure, and would otherwise swamp the outline with one entry
// per physical page. Mention resolution and the underlying store are
// untouched; this only affects what the tree view sees.
export function getStructureCandidates(store) {
  const all = [];
  for (const [key, list] of store.byExactKey.entries()) {
    if (key.startsWith('page:')) continue;
    all.push(...list);
  }
  return all;
}

// Only used to know which collapsed-key bucket a MENTION's own (already
// normalized) key falls into — real candidate rows carry their own
// server-computed collapsed_key, this just mirrors that same period-stripping
// transform for a key that has no stored row of its own to read it from.
function collapseKeyFallback(key) {
  const idx = key.indexOf(':');
  if (idx === -1) return key;
  const family = key.slice(0, idx);
  const number = key.slice(idx + 1);
  return `${family}:${number.replace(/\./g, '')}`;
}
