import { describe, it, expect } from 'vitest';
import {
  renderPageMarkdown,
  getPageMentions,
  normalizeDocrefCode,
  docrefKey,
  isPlausibleDocrefCode,
  normalizeTermKey,
  termKey,
} from './markdown';
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

  it('includes an undefined mention with undefined: true', () => {
    const store = storeWithDelta(
      {},
      1,
      { 5: [{ key: 'docref:xyz123', raw_text: 'Tender Enquiry No. XYZ-123', resolution: 'undefined', suppressed_reason: 'unresolved' }] }
    );
    const mentions = getPageMentions('Tender Enquiry No. XYZ-123 is cited.', 5, store);
    expect(mentions).toEqual([
      { key: 'docref:xyz123', raw: 'Tender Enquiry No. XYZ-123', approximate: false, undefined: true },
    ]);
  });
});

describe('docref and term normalization and plausibility', () => {
  it('normalizes hyphen/space variants of docref codes', () => {
    expect(normalizeDocrefCode('REC-064')).toBe('REC064');
    expect(normalizeDocrefCode('REC 064')).toBe('REC064');
    expect(normalizeDocrefCode('REC-064.')).toBe('REC064');
    expect(docrefKey('REC-064')).toBe('docref:rec064');
  });

  it('validates plausibility of docref codes', () => {
    expect(isPlausibleDocrefCode('REC-064', 'Technical Specification')).toBe(true);
    expect(isPlausibleDocrefCode('1', 'Claim')).toBe(true);
    expect(isPlausibleDocrefCode('5', 'File')).toBe(false); // bare numeral rejected for non-whitelisted labels
    expect(isPlausibleDocrefCode('4', 'Page')).toBe(false); // structural label rejected
    expect(isPlausibleDocrefCode('5', 'Clause')).toBe(false); // structural label rejected
    expect(isPlausibleDocrefCode('ABC', 'Tender')).toBe(false); // no digits rejected
    expect(isPlausibleDocrefCode('<|det|>REC-064<|/det|>', 'Specification')).toBe(false); // tag fragments rejected
  });

  it('normalizes term keys by stripping leading "the" and lowering whitespace', () => {
    expect(normalizeTermKey('Contractor')).toBe('contractor');
    expect(normalizeTermKey('the Contractor')).toBe('contractor');
    expect(normalizeTermKey('  The   Works  ')).toBe('works');
    expect(termKey('NIT')).toBe('term:nit');
  });
});

describe('renderPageMarkdown — docref linkifying', () => {
  it('links a docref mention that resolves to an exact candidate', () => {
    const store = storeWithDelta({
      64: { 'docref:rec064': [{ anchor_id: 'target-rec064', collapsed_key: 'docref:rec064' }] },
    });
    const html = renderPageMarkdown('See Technical Specification No. REC-064 for requirements.', 10, store);
    expect(html).toContain('data-xref-key="docref:rec064"');
    expect(html).toContain('class="xref-link"');
    expect(html).toContain('Technical Specification No. REC-064');
  });

  it('leaves a single-page or undefined docref mention as plain text in the markdown body', () => {
    const store = storeWithDelta(
      {},
      1,
      { 5: [{ key: 'docref:single123', raw_text: 'Tender No. SINGLE-123', resolution: 'undefined' }] }
    );
    const html = renderPageMarkdown('Tender No. SINGLE-123 appears only here.', 5, store);
    expect(html).not.toContain('<a');
    expect(html).toContain('Tender No. SINGLE-123');
  });

  it('links a fuzzy-folded docref mention using the server verdict resolution', () => {
    const store = storeWithDelta(
      { 64: { 'docref:rec064': [{ anchor_id: 't1', collapsed_key: 'docref:rec064' }] } },
      1,
      { 12: [{ key: 'docref:rec064', raw_text: 'Technical Specification No. REC-054', resolution: 'approximate' }] }
    );
    const html = renderPageMarkdown('Technical Specification No. REC-054 has been reviewed.', 12, store);
    expect(html).toContain('class="xref-link xref-link--approximate"');
    expect(html).toContain('data-xref-key="docref:rec064"');
    expect(html).toContain('Technical Specification No. REC-054');
  });

  it('links docref mentions inside layout-aware formatted_text headings and lists', () => {
    const store = storeWithDelta({
      64: { 'docref:rec064': [{ anchor_id: 'target-rec064', collapsed_key: 'docref:rec064' }] },
    });
    const formatted = '## 3.3 Technical Specification No. REC-064\n\n- Refer to Technical Specification No. REC-064';
    const html = renderPageMarkdown(formatted, 10, store);
    expect(html).toContain('<h2>3.3 <a href="#" class="xref-link" data-xref-key="docref:rec064">Technical Specification No. REC-064</a></h2>');
    expect(html).toContain('<li>Refer to <a href="#" class="xref-link" data-xref-key="docref:rec064">Technical Specification No. REC-064</a></li>');
  });
});

describe('renderPageMarkdown — defined term linkifying', () => {
  it('links an exact defined term mention like NIT or Contractor', () => {
    const store = storeWithDelta({
      2: {
        'term:contractor': [{ anchor_id: 't-contractor', collapsed_key: 'term:contractor' }],
        'term:nit': [{ anchor_id: 't-nit', collapsed_key: 'term:nit' }],
      },
    });
    const html = renderPageMarkdown('The Contractor submitted the bid under the NIT terms.', 10, store);
    expect(html).toContain('data-xref-key="term:contractor"');
    expect(html).toContain('data-xref-key="term:nit"');
  });

  it('does not link lowercase prose for short abbreviation terms', () => {
    const store = storeWithDelta({
      2: { 'term:nit': [{ anchor_id: 't-nit', collapsed_key: 'term:nit' }] },
    });
    const html = renderPageMarkdown('Do not nit pick on small details.', 10, store);
    expect(html).not.toContain('data-xref-key="term:nit"');
    expect(html).not.toContain('<a');
  });

  it('links defined terms inside layout-aware formatted_text bolding and lists', () => {
    const store = storeWithDelta({
      2: { 'term:contractor': [{ anchor_id: 't-contractor', collapsed_key: 'term:contractor' }] },
    });
    const formatted = '**Contractor** obligations:\n\n1. The Contractor must deliver the equipment.';
    const html = renderPageMarkdown(formatted, 10, store);
    expect(html).toContain('<strong><a href="#" class="xref-link" data-xref-key="term:contractor">Contractor</a></strong>');
    expect(html).toContain('<a href="#" class="xref-link" data-xref-key="term:contractor">Contractor</a>');
  });
});
