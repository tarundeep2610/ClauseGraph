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
// changed_pages} object) into the store in place. Mutates and returns `store`.
export function mergeDelta(store, delta) {
  if (!delta) return store;
  const changed = delta.changed_pages || {};
  for (const [pageStr, byKey] of Object.entries(changed)) {
    const page = Number(pageStr);
    removePageContribution(store, page);
    store.pageIndex.set(page, byKey);
    addPageContribution(store, page, byKey);
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
// by proximity to the reader's current position) — the first one found among that
// page's own candidates. Pages very rarely straddle more than one scope, and this
// is a sort hint, not a correctness-bearing value, so "first found" is an accepted
// approximation rather than something needing a full scope-boundary derivation.
//
// `ranges` (optional, Part B's GET /scope-ranges response) is the fallback for a
// page with no candidates of its own at all — previously such a page lost all
// proximity-ranking context entirely; findRangeForPage recovers the deepest
// container actually covering it, derived from the persisted per-page stack.
export function getPageScopeKey(store, page, ranges) {
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
  if (ranges) {
    const range = findRangeForPage(ranges, page);
    if (range) return range.scope_key;
  }
  return null;
}

// Resolves a normalized mention key against the accumulated index: `exact` is
// every candidate stored under that exact key; `approximate` is every candidate
// reached only via a shared collapsed_key under a DIFFERENT real key (an
// OCR-tolerant fallback — e.g. a mention of "Clause 11" also surfacing a real,
// distinct "Clause 1.1" if the document has one). Always both computed, never
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
  return { exact, approximate };
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
