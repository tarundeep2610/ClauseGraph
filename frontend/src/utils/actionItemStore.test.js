import { describe, it, expect } from 'vitest';
import {
  createActionItemStore, mergeActionItemDelta, getAllActionItems, getActionItemTypeCounts,
} from './actionItemStore';

function item(overrides = {}) {
  return {
    occurrence_index: 0, item_type: 'deadline', item_text: 'x',
    date_text: null, date_iso: null, anchor_event: null, cadence: null,
    anchor_id: 'page-block-1', evidence_quote: 'x', responsible_party: null,
    confidence: 'medium',
    ...overrides,
  };
}

describe('mergeActionItemDelta', () => {
  it('whole-page-replaces changed pages and leaves untouched pages alone', () => {
    const store = createActionItemStore();
    mergeActionItemDelta(store, {
      action_item_index_version: 1,
      changed_pages: { 1: [item({ item_text: 'first' })] },
    });
    mergeActionItemDelta(store, {
      action_item_index_version: 2,
      changed_pages: { 2: [item({ item_text: 'second' })] },
    });
    expect(store.version).toBe(2);
    expect(store.pageIndex.get(1)[0].item_text).toBe('first');
    expect(store.pageIndex.get(2)[0].item_text).toBe('second');
  });

  it('replacing a page with an empty list clears its prior contribution', () => {
    const store = createActionItemStore();
    mergeActionItemDelta(store, { action_item_index_version: 1, changed_pages: { 1: [item()] } });
    mergeActionItemDelta(store, { action_item_index_version: 2, changed_pages: { 1: [] } });
    expect(store.pageIndex.get(1)).toEqual([]);
  });

  it('ignores a null/undefined delta without throwing', () => {
    const store = createActionItemStore();
    expect(() => mergeActionItemDelta(store, null)).not.toThrow();
    expect(store.version).toBe(0);
  });
});

describe('getAllActionItems', () => {
  it('sorts by page order by default', () => {
    const store = createActionItemStore();
    mergeActionItemDelta(store, {
      action_item_index_version: 1,
      changed_pages: { 5: [item({ item_text: 'later page' })], 2: [item({ item_text: 'earlier page' })] },
    });
    const all = getAllActionItems(store, 'page');
    expect(all.map((i) => i.item_text)).toEqual(['earlier page', 'later page']);
  });

  it('sorts dated items chronologically ascending, undated items last, in "date" mode', () => {
    const store = createActionItemStore();
    mergeActionItemDelta(store, {
      action_item_index_version: 1,
      changed_pages: {
        1: [item({ item_text: 'no date', date_iso: null })],
        2: [item({ item_text: 'later date', date_iso: '2026-05-01' })],
        3: [item({ item_text: 'earlier date', date_iso: '2026-01-01' })],
      },
    });
    const all = getAllActionItems(store, 'date');
    expect(all.map((i) => i.item_text)).toEqual(['earlier date', 'later date', 'no date']);
  });

  it('attaches the page number onto each returned item', () => {
    const store = createActionItemStore();
    mergeActionItemDelta(store, { action_item_index_version: 1, changed_pages: { 7: [item()] } });
    expect(getAllActionItems(store)[0].page).toBe(7);
  });
});

describe('getActionItemTypeCounts', () => {
  it('counts items per type plus an "all" total, and buckets an unrecognized type under "other"', () => {
    const store = createActionItemStore();
    mergeActionItemDelta(store, {
      action_item_index_version: 1,
      changed_pages: {
        1: [
          item({ item_type: 'deadline' }),
          item({ item_type: 'deadline' }),
          item({ item_type: 'obligation' }),
          item({ item_type: 'condition' }),
          item({ item_type: 'something_unexpected' }),
        ],
      },
    });
    const counts = getActionItemTypeCounts(store);
    expect(counts).toEqual({ deadline: 2, obligation: 1, condition: 1, other: 1, all: 5 });
  });

  it('returns all-zero counts for an empty store', () => {
    const store = createActionItemStore();
    expect(getActionItemTypeCounts(store)).toEqual({ deadline: 0, obligation: 0, condition: 0, other: 0, all: 0 });
  });
});
