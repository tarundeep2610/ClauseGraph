import { describe, it, expect } from 'vitest';
import { createReferenceStore, mergeDelta, getPageScopeKey, resolveMention } from './referenceStore';

describe('getPageScopeKey — root-level scope must not be treated as absent', () => {
  it('returns "" (not null) for a page whose only candidate is root-level', () => {
    const store = createReferenceStore();
    mergeDelta(store, {
      reference_index_version: 1,
      changed_pages: {
        3: {
          'clause:1': [{
            anchor_id: 'a1', collapsed_key: 'clause:1', fingerprint: '',
            scope_label: '', scope_key: '', snippet: '', truncated: false, continues_on_page: null,
          }],
        },
      },
    });
    expect(getPageScopeKey(store, 3)).toBe('');
  });

  it('still returns null when the only candidate has a genuinely absent scope_key (e.g. a page:N row)', () => {
    const store = createReferenceStore();
    mergeDelta(store, {
      reference_index_version: 1,
      changed_pages: {
        3: {
          'page:14': [{
            anchor_id: 'page-block-3', collapsed_key: 'page:14', fingerprint: '',
            scope_label: 'This document (page 14)', scope_key: null, snippet: '', truncated: false, continues_on_page: null,
          }],
        },
      },
    });
    expect(getPageScopeKey(store, 3)).toBeNull();
  });

  it('returns null for a page with no candidates at all', () => {
    const store = createReferenceStore();
    expect(getPageScopeKey(store, 5)).toBeNull();
  });

  it('falls back to a scope range when the page has no candidates of its own', () => {
    const store = createReferenceStore();
    const ranges = [
      { key: 'annexure:a-2', scope_key: '', depth: 0, start_page: 420, end_page: 440 },
    ];
    expect(getPageScopeKey(store, 430, ranges)).toBe('');
  });

  it('prefers authoritative ranges over candidate scope_key iteration', () => {
    const store = createReferenceStore();
    mergeDelta(store, {
      reference_index_version: 1,
      changed_pages: { 3: { 'clause:1': [{ anchor_id: 'a1', collapsed_key: 'clause:1', scope_key: 'article:9' }] } },
    });
    const ranges = [{ key: 'annexure:a-2', scope_key: 'authoritative_scope', depth: 0, start_page: 1, end_page: 10 }];
    expect(getPageScopeKey(store, 3, ranges)).toBe('authoritative_scope');
  });

  it('merges changed_mentions into store.mentionsByPage', () => {
    const store = createReferenceStore();
    mergeDelta(store, {
      reference_index_version: 1,
      changed_mentions: {
        5: [{ key: 'clause:8', raw_text: 'Clause 8', resolution: 'exact' }],
      },
    });
    expect(store.mentionsByPage.get(5)).toEqual([
      { key: 'clause:8', raw_text: 'Clause 8', resolution: 'exact' },
    ]);
  });

  it('returns null when no range covers the page either', () => {
    const store = createReferenceStore();
    const ranges = [{ key: 'annexure:a-2', scope_key: '', depth: 0, start_page: 420, end_page: 440 }];
    expect(getPageScopeKey(store, 5, ranges)).toBeNull();
  });
});

describe('resolveMention — cross-family fallback', () => {
  // Modeled directly on job bb417471-561c-4029-bb21-3bdcf74e7dcb: this job
  // has zero clause:*/section:*/article:*/schedule:*/appendix:* targets at
  // all (the AI structure indexer mints generic numheading:* keys for
  // numbered headings it can't attribute to a specific vocabulary word), yet
  // has real in-text citations like "clause 9 of the ITB". `numheading:9.0`
  // (a real contract clause) and `numheading:9` (a genuine page-margin-stamp
  // misfire) coexist as distinct rows in the real data.

  it('finds a fallback-family target and folds it into approximate', () => {
    const store = createReferenceStore();
    mergeDelta(store, { reference_index_version: 1, changed_pages: { 25: { 'numheading:9.0': [{ anchor_id: 'nh9', collapsed_key: 'numheading:90' }] } } });
    const { exact, approximate } = resolveMention(store, 'clause:9');
    expect(exact).toHaveLength(0);
    expect(approximate.map((c) => c.key)).toEqual(['numheading:9.0']);
  });

  it('applies integer/.0 equivalence both directions, surfacing both the real clause and the bogus stamp', () => {
    const store = createReferenceStore();
    mergeDelta(store, {
      reference_index_version: 1,
      changed_pages: {
        25: { 'numheading:9.0': [{ anchor_id: 'nh9-real', collapsed_key: 'numheading:90' }] },
        48: { 'numheading:9': [{ anchor_id: 'nh9-stamp', collapsed_key: 'numheading:9' }] },
      },
    });
    const forward = resolveMention(store, 'clause:9');
    expect(forward.approximate.map((c) => c.page).sort()).toEqual([25, 48]);

    const reversed = resolveMention(store, 'clause:9.0');
    expect(reversed.approximate.map((c) => c.page).sort()).toEqual([25, 48]);
  });

  it('falls back from para to point', () => {
    const store = createReferenceStore();
    mergeDelta(store, { reference_index_version: 1, changed_pages: { 8: { 'point:2': [{ anchor_id: 'p2', collapsed_key: 'point:2' }] } } });
    const { approximate } = resolveMention(store, 'para:2');
    expect(approximate.map((c) => c.key)).toEqual(['point:2']);
  });

  it('does not apply a fallback to a family that already mints its own matching keys', () => {
    const store = createReferenceStore();
    mergeDelta(store, { reference_index_version: 1, changed_pages: { 5: { 'numheading:1': [{ anchor_id: 'nh1', collapsed_key: 'numheading:1' }] } } });
    const { exact, approximate } = resolveMention(store, 'annexure:a-1');
    expect(exact).toHaveLength(0);
    expect(approximate).toHaveLength(0);
  });

  it('never returns a duplicate entry in approximate (defensive — no live trigger in the current design)', () => {
    // Constructed directly against the store's internal Map, not via
    // mergeDelta: simulates two fallback lookups (numheading:9 and
    // numheading:9.0) coincidentally reaching a row carrying the identical
    // key/page/anchor_id triple — not something the real data can produce
    // today (the .0-equivalence and collapsed-key paths land on
    // structurally disjoint keys, verified above), but the dedupe step must
    // hold regardless, since the failure mode (a real single target showing
    // as a 2-row dropdown) is bad and silent.
    const store = createReferenceStore();
    const dup = { key: 'numheading:9', page: 48, anchor_id: 'dup-anchor', collapsed_key: 'numheading:9' };
    store.byExactKey.set('numheading:9', [dup]);
    store.byExactKey.set('numheading:9.0', [dup]);
    const { approximate } = resolveMention(store, 'clause:9');
    expect(approximate).toHaveLength(1);
  });
});
