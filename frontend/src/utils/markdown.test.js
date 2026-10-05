import { describe, it, expect } from 'vitest';
import { renderPageMarkdown, getPageMentions } from './markdown';
import { createReferenceStore, mergeDelta, resolveMention, getPageScopeKey } from './referenceStore';

function storeWithDelta(changedPages, version = 1, changedMentions = {}) {
  const store = createReferenceStore();
  mergeDelta(store, { reference_index_version: version, changed_pages: changedPages, changed_mentions: changedMentions });
  return store;
}

describe('referenceStore', () => {
  it('accumulates candidates across multiple delta merges without losing earlier pages', () => {
    const store = createReferenceStore();
    mergeDelta(store, { reference_index_version: 1, changed_pages: { 3: { 'clause:4.2': [{ anchor_id: 'a1', collapsed_key: 'clause:42' }] } } });
    mergeDelta(store, { reference_index_version: 2, changed_pages: { 5: { 'clause:8': [{ anchor_id: 'a2', collapsed_key: 'clause:8' }] } } });
    expect(resolveMention(store, 'clause:4.2').exact).toHaveLength(1);
    expect(resolveMention(store, 'clause:8').exact).toHaveLength(1);
    expect(store.version).toBe(2);
  });

  it('a page-delta replaces only that page\'s prior contribution', () => {
    const store = createReferenceStore();
    mergeDelta(store, { reference_index_version: 1, changed_pages: { 3: { 'clause:4.2': [{ anchor_id: 'a1', collapsed_key: 'clause:42' }] } } });
    mergeDelta(store, { reference_index_version: 2, changed_pages: { 3: { 'clause:9': [{ anchor_id: 'a2', collapsed_key: 'clause:9' }] } } });
    expect(resolveMention(store, 'clause:4.2').exact).toHaveLength(0);
    expect(resolveMention(store, 'clause:9').exact).toHaveLength(1);
  });

  it('surfaces a real distinct key as approximate only when a genuine collapsed-key collision exists', () => {
    const store = storeWithDelta({
      3: { 'clause:11': [{ anchor_id: 'a1', collapsed_key: 'clause:11' }] },
    });
    // Clean case: no collapsed-key collision -> zero approximate noise.
    expect(resolveMention(store, 'clause:11').approximate).toHaveLength(0);

    mergeDelta(store, { reference_index_version: 2, changed_pages: { 7: { 'clause:1.1': [{ anchor_id: 'a2', collapsed_key: 'clause:11' }] } } });
    const resolved = resolveMention(store, 'clause:11');
    expect(resolved.exact).toHaveLength(1);
    expect(resolved.approximate).toHaveLength(1);
    expect(resolved.approximate[0].key).toBe('clause:1.1');
  });

  it('getPageScopeKey reads a representative scope_key from that page\'s own candidates', () => {
    const store = storeWithDelta({ 9: { 'clause:2': [{ anchor_id: 'a1', collapsed_key: 'clause:2', scope_key: '1:article-2' }] } });
    expect(getPageScopeKey(store, 9)).toBe('1:article-2');
    expect(getPageScopeKey(store, 999)).toBeNull();
  });
});

describe('renderPageMarkdown — mention linkifying', () => {
  it('links a mention that resolves to an exact candidate', () => {
    const store = storeWithDelta({ 12: { 'clause:8': [{ anchor_id: 'xref-target-clause-8-p12-0', collapsed_key: 'clause:8' }] } });
    const html = renderPageMarkdown('See Clause 8 for the force majeure terms.', 5, store);
    expect(html).toContain('data-xref-key="clause:8"');
    expect(html).toContain('class="xref-link"');
    expect(html).not.toContain('xref-link--approximate');
  });

  it('leaves a mention with zero candidates as plain text', () => {
    const store = createReferenceStore();
    const html = renderPageMarkdown('See Clause 99 for details.', 5, store);
    expect(html).not.toContain('<a');
    expect(html).toContain('Clause 99');
  });

  it('marks an approximate-only resolution with the --approximate modifier class', () => {
    const store = storeWithDelta({ 7: { 'clause:1.1': [{ anchor_id: 'a1', collapsed_key: 'clause:11' }] } });
    const html = renderPageMarkdown('Refer to Clause 11 above.', 5, store);
    expect(html).toContain('xref-link--approximate');
    expect(html).toContain('data-xref-key="clause:11"');
  });

  it('never linkifies text inside an existing markdown link or code span', () => {
    const store = storeWithDelta({ 12: { 'clause:8': [{ anchor_id: 'a1', collapsed_key: 'clause:8' }] } });
    const html = renderPageMarkdown('See [Clause 8](https://example.com) and `Clause 8` too.', 5, store);
    // The real link stays a real link; the code span keeps its text un-linked.
    expect(html).toContain('href="https://example.com"');
    expect(html).toContain('<code>Clause 8</code>');
  });

  it('roman numerals in a mention normalize to the same key as the arabic form', () => {
    const store = storeWithDelta({ 3: { 'article:2': [{ anchor_id: 'a1', collapsed_key: 'article:2' }] } });
    const html = renderPageMarkdown('As per Article II above.', 5, store);
    expect(html).toContain('data-xref-key="article:2"');
  });

  it('suppresses linkifying when the server marked the mention as suppressed', () => {
    const store = storeWithDelta(
      { 56: { 'numheading:34': [{ anchor_id: 'nh34', collapsed_key: 'numheading:34' }] } },
      1,
      { 7: [{ key: 'section:34', raw_text: 'section 34', resolution: 'suppressed', suppressed_reason: 'external_law' }] }
    );
    const html = renderPageMarkdown('objection filed under section 34 of the Arbitration Act, 1996.', 7, store);
    expect(html).not.toContain('data-xref-key="section:34"');
    expect(html).not.toContain('<a');
  });
});

describe('renderPageMarkdown — target-anchor tagging via fingerprint', () => {
  it('tags the matching block with the candidate\'s anchor_id', () => {
    const store = storeWithDelta({
      5: {
        'clause:4.2': [{
          anchor_id: 'xref-target-clause-4-2-p5-0',
          collapsed_key: 'clause:42',
          fingerprint: 'Clause 4.2 The contractor',
        }],
      },
    });
    const html = renderPageMarkdown('Clause 4.2 The contractor shall complete the works.', 5, store);
    expect(html).toContain('id="xref-target-clause-4-2-p5-0"');
  });

  it('does not tag a block whose text does not match the fingerprint', () => {
    const store = storeWithDelta({
      5: { 'clause:4.2': [{ anchor_id: 'anchor-1', collapsed_key: 'clause:42', fingerprint: 'Something totally different' }] },
    });
    const html = renderPageMarkdown('Clause 4.2 The contractor shall complete the works.', 5, store);
    expect(html).not.toContain('id="anchor-1"');
  });

  it('tags a target that lives inside a markdown list item', () => {
    const store = storeWithDelta({
      9: { 'ground:1': [{ anchor_id: 'anchor-ground-1', collapsed_key: 'ground:1', fingerprint: '1. The learned judge' }] },
    });
    const html = renderPageMarkdown('1. The learned judge erred in law.\n\n2. Another ground.', 9, store);
    expect(html).toContain('id="anchor-ground-1"');
  });
});

describe('getPageMentions', () => {
  it('returns a mention that resolves to an exact candidate from store.mentionsByPage', () => {
    const store = storeWithDelta(
      { 12: { 'clause:8': [{ anchor_id: 'a1', collapsed_key: 'clause:8' }] } },
      1,
      { 5: [{ key: 'clause:8', raw_text: 'Clause 8', resolution: 'exact' }] }
    );
    const mentions = getPageMentions('See Clause 8 for the force majeure terms.', 5, store);
    expect(mentions).toEqual([{ key: 'clause:8', raw: 'Clause 8', approximate: false }]);
  });

  it('omits a mention marked suppressed by the server', () => {
    const store = storeWithDelta(
      {},
      1,
      { 5: [{ key: 'clause:99', raw_text: 'Clause 99', resolution: 'suppressed', suppressed_reason: 'unresolved' }] }
    );
    const mentions = getPageMentions('See Clause 99 for details.', 5, store);
    expect(mentions).toEqual([]);
  });

  it('includes an approximate resolution with approximate: true', () => {
    const store = storeWithDelta(
      { 7: { 'clause:1.1': [{ anchor_id: 'a1', collapsed_key: 'clause:11' }] } },
      1,
      { 5: [{ key: 'clause:11', raw_text: 'Clause 11', resolution: 'approximate' }] }
    );
    const mentions = getPageMentions('Refer to Clause 11 above.', 5, store);
    expect(mentions).toEqual([{ key: 'clause:11', raw: 'Clause 11', approximate: true }]);
  });

  it('dedupes repeated mentions of the same key on one page', () => {
    const store = storeWithDelta(
      { 12: { 'clause:8': [{ anchor_id: 'a1', collapsed_key: 'clause:8' }] } },
      1,
      { 5: [
        { key: 'clause:8', raw_text: 'Clause 8', resolution: 'exact' },
        { key: 'clause:8', raw_text: 'Clause 8', resolution: 'exact' },
      ] }
    );
    const mentions = getPageMentions('Clause 8 applies here. Clause 8 applies there too.', 5, store);
    expect(mentions).toEqual([{ key: 'clause:8', raw: 'Clause 8', approximate: false }]);
  });

  it('returns empty array when page has no mentions recorded in store', () => {
    const store = createReferenceStore();
    const mentions = getPageMentions('Some text with no mentions.', 5, store);
    expect(mentions).toEqual([]);
  });
});
