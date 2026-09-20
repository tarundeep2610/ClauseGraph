import { describe, it, expect } from 'vitest';
import { createReferenceStore, mergeDelta, getPageScopeKey } from './referenceStore';

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

  it('still prefers a page\'s own candidate scope_key over the ranges fallback', () => {
    const store = createReferenceStore();
    mergeDelta(store, {
      reference_index_version: 1,
      changed_pages: { 3: { 'clause:1': [{ anchor_id: 'a1', collapsed_key: 'clause:1', scope_key: 'article:9' }] } },
    });
    const ranges = [{ key: 'annexure:a-2', scope_key: 'wrong', depth: 0, start_page: 1, end_page: 10 }];
    expect(getPageScopeKey(store, 3, ranges)).toBe('article:9');
  });

  it('returns null when no range covers the page either', () => {
    const store = createReferenceStore();
    const ranges = [{ key: 'annexure:a-2', scope_key: '', depth: 0, start_page: 420, end_page: 440 }];
    expect(getPageScopeKey(store, 5, ranges)).toBeNull();
  });
});
