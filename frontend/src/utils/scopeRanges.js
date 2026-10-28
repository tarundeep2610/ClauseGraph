// Client-side helpers for GET /api/jobs/{id}/scope-ranges (Part B). Each
// range means "the contiguous physical pages for which this node is the
// active structural ancestor" — not "the pages its own heading text
// happens to appear on" — see reference_index.py's compute_scope_ranges
// for the full semantics this mirrors.

// Groups the flat range list by key. A key can legitimately have more than
// one disjoint range (it closed, then reopened later) — each entry is an
// array, never merged into one.
export function buildRangeIndex(ranges) {
  const index = new Map();
  const seenByKey = new Map();
  for (const r of ranges || []) {
    if (!index.has(r.key)) {
      index.set(r.key, []);
      seenByKey.set(r.key, new Set());
    }
    const spanKey = `${r.start_page}::${r.end_page}`;
    const seen = seenByKey.get(r.key);
    if (!seen.has(spanKey)) {
      seen.add(spanKey);
      index.get(r.key).push(r);
    }
  }
  return index;
}

// The *deepest* range containing `page` (depth is the correctness-bearing
// tie-break, not array order) — used both for proximity-ranking fallback
// (getPageScopeKey) and for the sidebar's active-page focus. On boundary
// pages where an outgoing section ends on `page` and a new section begins
// on `page`, the range starting on this page takes precedence over the closing range.
export function findRangeForPage(ranges, page) {
  let best = null;
  for (const r of ranges || []) {
    if (page >= r.start_page && page <= r.end_page) {
      if (!best) {
        best = r;
        continue;
      }
      const rStarts = r.start_page === page;
      const bestStarts = best.start_page === page;
      if (rStarts && !bestStarts) {
        best = r;
      } else if (rStarts === bestStarts) {
        if (r.depth > best.depth) {
          best = r;
        }
      }
    }
  }
  return best;
}

// The full chain of bare keys from the root down to (and including) the
// deepest node covering `page` — e.g. ["annexure:a-2", "article:2"] — used
// by the sidebar to auto-expand exactly the ancestor chain to the node the
// reader is currently inside, rather than the whole tree. `scope_key` is
// already "/".join(ancestor keys) per compute_scope_ranges, so this is just
// that plus the range's own key; null when no range covers the page at all
// (e.g. scopeRanges hasn't loaded yet).
export function findActivePathKeys(ranges, page) {
  const range = findRangeForPage(ranges, page);
  if (!range) return null;
  const ancestorKeys = range.scope_key ? range.scope_key.split('/') : [];
  return [...ancestorKeys, range.key];
}
