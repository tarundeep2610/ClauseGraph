// Client-side accumulator for the backend's page-delta actionable-items
// index (GET /api/jobs/{id}/action-items?since_version=V). Each delta
// whole-page-replaces that page's item list; everything else already known
// stays untouched. Deliberately much simpler than referenceStore.js — action
// items are a flat per-page list with no key/scope/collapsed-key resolution
// concept at all.

export function createActionItemStore() {
  return {
    version: 0,
    pageIndex: new Map(), // page -> [item, ...] (as received from the server)
  };
}

// Merges an /action-items response (or an equivalent
// {action_item_index_version, changed_pages} object) into the store in
// place. Mutates and returns `store`.
export function mergeActionItemDelta(store, delta) {
  if (!delta) return store;
  const changed = delta.changed_pages || {};
  for (const [pageStr, items] of Object.entries(changed)) {
    store.pageIndex.set(Number(pageStr), items || []);
  }
  if (typeof delta.action_item_index_version === 'number') {
    store.version = delta.action_item_index_version;
  }
  return store;
}

const TYPE_SORT_RANK = { deadline: 0, obligation: 1, condition: 2, other: 3 };

// Flat, sorted list for the panel. sortMode: 'page' (document order, the
// default) or 'date' (date_iso ascending, items without a date_iso last,
// stable within each group by page/occurrence).
export function getAllActionItems(store, sortMode = 'page') {
  const all = [];
  for (const [page, items] of store.pageIndex.entries()) {
    for (const item of items) all.push({ ...item, page });
  }
  if (sortMode === 'date') {
    all.sort((a, b) => {
      if (a.date_iso && b.date_iso) return a.date_iso.localeCompare(b.date_iso) || a.page - b.page;
      if (a.date_iso) return -1;
      if (b.date_iso) return 1;
      return a.page - b.page || a.occurrence_index - b.occurrence_index;
    });
  } else {
    all.sort((a, b) => a.page - b.page || a.occurrence_index - b.occurrence_index);
  }
  return all;
}

// Counts per item_type, for the filter pills — { deadline: n, obligation: n,
// condition: n, other: n, all: n }. The "other" count is what the panel uses
// to decide whether to render the "Other" pill at all (expected to be rare/
// absent for tender documents, common for litigation ones).
export function getActionItemTypeCounts(store) {
  const counts = { deadline: 0, obligation: 0, condition: 0, other: 0, all: 0 };
  for (const items of store.pageIndex.values()) {
    for (const item of items) {
      const t = counts[item.item_type] !== undefined ? item.item_type : 'other';
      counts[t] += 1;
      counts.all += 1;
    }
  }
  return counts;
}

export function sortTypeRank(itemType) {
  return TYPE_SORT_RANK[itemType] ?? 3;
}
