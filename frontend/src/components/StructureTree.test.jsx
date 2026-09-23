import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import StructureTree from './StructureTree';
import { createReferenceStore, mergeDelta } from '../utils/referenceStore';

function storeFromRows(rowsByPage) {
  const store = createReferenceStore();
  const changed_pages = {};
  for (const [page, byKey] of Object.entries(rowsByPage)) {
    changed_pages[page] = byKey;
  }
  mergeDelta(store, { reference_index_version: 1, changed_pages });
  return store;
}

function candidate(overrides) {
  return {
    anchor_id: 'anchor',
    snippet: '',
    truncated: false,
    continues_on_page: null,
    scope_provisional: false,
    collapsed_key: overrides.key,
    fingerprint: '',
    scope_chain: [],
    ...overrides,
  };
}

describe('StructureTree — tree built from the flat, materialized-path candidate list', () => {
  it('renders the empty state when there are no structure candidates', () => {
    const store = createReferenceStore();
    render(<StructureTree referenceStore={store} onSelect={vi.fn()} />);
    expect(screen.getByTestId('structure-tree-empty')).toBeInTheDocument();
  });

  it('excludes page:N candidates (self-reference and stamped-page shapes) from the tree entirely', () => {
    const store = storeFromRows({
      3: {
        'page:14': [candidate({ key: 'page:14', anchor_id: 'page-block-3', page: 3, scope_chain: [] })],
      },
    });
    render(<StructureTree referenceStore={store} onSelect={vi.fn()} />);
    expect(screen.getByTestId('structure-tree-empty')).toBeInTheDocument();
  });

  it('a bare top-level Article (no annexure) and an Annexure appear as sibling root branches', () => {
    const store = storeFromRows({
      2: {
        'article:9': [candidate({ key: 'article:9', anchor_id: 'article-leaf', page: 2, scope_chain: [] })],
      },
      5: {
        'annexure:a-1': [candidate({ key: 'annexure:a-1', anchor_id: 'annexure-leaf', page: 5, scope_chain: [] })],
      },
    });
    render(<StructureTree referenceStore={store} onSelect={vi.fn()} />);
    expect(screen.getByText('Article 9')).toBeInTheDocument();
    expect(screen.getByText('Annexure A-1')).toBeInTheDocument();
  });

  it('labels an Annexure node "Annexure A-1" consistently, even when its own row (client-formatted) is processed before a descendant\'s (backend-formatted) scope_chain entry', () => {
    // Page 2's row is inserted into the store first, so buildTree visits the
    // Annexure's own candidate (client-side formatKeyLabel) before the
    // clause's scope_chain entry (backend-formatted label) ever gets a
    // chance to name this node — the regression case for the casing fix.
    const store = storeFromRows({
      2: {
        'annexure:a-1': [candidate({ key: 'annexure:a-1', anchor_id: 'annexure-leaf', page: 2, scope_chain: [] })],
      },
      6: {
        'clause:4.1': [candidate({
          key: 'clause:4.1', anchor_id: 'clause-leaf', page: 6,
          scope_chain: [{ family: 'annexure', key: 'annexure:a-1', label: 'Annexure A-1' }],
        })],
      },
    });
    render(<StructureTree referenceStore={store} onSelect={vi.fn()} />);
    expect(screen.getByText('Annexure A-1')).toBeInTheDocument();
    expect(screen.queryByText('Annexure a-1')).not.toBeInTheDocument();
  });

  it('labels a numheading node with its backend-supplied own_label, never the generic "Numheading N" fallback', () => {
    const store = storeFromRows({
      4: {
        'numheading:2.0': [candidate({
          key: 'numheading:2.0', anchor_id: 'numheading-leaf', page: 4, scope_chain: [],
          own_label: '2.0 CONTRACT DOCUMENTS',
        })],
      },
    });
    render(<StructureTree referenceStore={store} onSelect={vi.fn()} />);
    expect(screen.getByText('2.0 CONTRACT DOCUMENTS')).toBeInTheDocument();
    expect(screen.queryByText('Numheading 2.0')).not.toBeInTheDocument();
  });

  it('labels a letterheading node with its backend-supplied own_label, never the generic "Letterheading A" fallback', () => {
    const store = storeFromRows({
      355: {
        'letterheading:a': [candidate({
          key: 'letterheading:a', anchor_id: 'letterheading-leaf', page: 355, scope_chain: [],
          own_label: 'A INTRODUCTION',
        })],
      },
    });
    render(<StructureTree referenceStore={store} onSelect={vi.fn()} />);
    expect(screen.getByText('A INTRODUCTION')).toBeInTheDocument();
    expect(screen.queryByText('Letterheading A')).not.toBeInTheDocument();
  });

  it('labels a headerless numbered clause with just the bare number when own_label is absent', () => {
    const store = storeFromRows({
      4: {
        'numheading:2.1': [candidate({
          key: 'numheading:2.1', anchor_id: 'headerless-leaf', page: 4, scope_chain: [],
          own_label: null,
        })],
      },
    });
    render(<StructureTree referenceStore={store} onSelect={vi.fn()} />);
    expect(screen.getByText('2.1')).toBeInTheDocument();
    expect(screen.queryByText('Numheading 2.1')).not.toBeInTheDocument();
  });

  it('renders Article 2 > Section 3 and Article 5 > Section 3 as two distinct branches, not merged', () => {
    const store = storeFromRows({
      2: {
        'clause:4.1': [candidate({
          key: 'clause:4.1', anchor_id: 'a-1', page: 2,
          scope_chain: [
            { family: 'article', key: 'article:2', label: 'Article 2' },
            { family: 'section', key: 'section:3', label: 'Section 3' },
          ],
        })],
      },
      6: {
        'clause:4.1': [candidate({
          key: 'clause:4.1', anchor_id: 'a-2', page: 6,
          scope_chain: [
            { family: 'article', key: 'article:5', label: 'Article 5' },
            { family: 'section', key: 'section:3', label: 'Section 3' },
          ],
        })],
      },
    });
    render(<StructureTree referenceStore={store} onSelect={vi.fn()} />);

    // Root-level nodes ("Article 2", "Article 5") are auto-expanded by
    // default (depth 0) — their "Section 3" child is already visible with no
    // click needed. The regression check: these must be two DISTINCT
    // "Section 3" nodes (one per Article), not collapsed onto one shared node.
    expect(screen.getByText('Article 2')).toBeInTheDocument();
    expect(screen.getByText('Article 5')).toBeInTheDocument();
    expect(screen.getAllByText('Section 3')).toHaveLength(2);
  });

  it('renders two occurrences of the same key at the same scope as two distinct sibling leaves', () => {
    const sharedChain = [{ family: 'article', key: 'article:2', label: 'Article 2' }];
    const store = storeFromRows({
      2: {
        'clause:4.1': [
          candidate({ key: 'clause:4.1', anchor_id: 'occ-0', page: 2, occurrence_index: 0, scope_chain: sharedChain }),
          candidate({ key: 'clause:4.1', anchor_id: 'occ-1', page: 2, occurrence_index: 1, scope_chain: sharedChain }),
        ],
      },
    });
    render(<StructureTree referenceStore={store} onSelect={vi.fn()} />);

    // "Article 2" is depth 0 (auto-expanded); "Clause 4.1" is depth 1
    // (collapsed by default) and needs a click to reveal its two leaves.
    fireEvent.click(screen.getByText('Clause 4.1'));

    expect(screen.getByTestId('structure-leaf-occ-0')).toBeInTheDocument();
    expect(screen.getByTestId('structure-leaf-occ-1')).toBeInTheDocument();
  });

  it('marks a low-confidence leaf distinctly, with a reasons-based tooltip', () => {
    const store = storeFromRows({
      8: {
        'section:8': [candidate({
          key: 'section:8', anchor_id: 'low-conf-leaf', page: 8, scope_chain: [],
          heading_confidence: 'low',
          heading_confidence_reasons: ['range_shaped_continuation'],
        })],
      },
    });
    render(<StructureTree referenceStore={store} onSelect={vi.fn()} />);
    const leaf = screen.getByTestId('structure-leaf-low-conf-leaf');
    expect(leaf.className).toContain('structure-tree-leaf--low-confidence');
    expect(leaf.title).toContain('range-shaped');
  });

  it('does not mark a high-confidence leaf', () => {
    const store = storeFromRows({
      2: {
        'article:9': [candidate({ key: 'article:9', anchor_id: 'high-conf-leaf', page: 2, scope_chain: [] })],
      },
    });
    render(<StructureTree referenceStore={store} onSelect={vi.fn()} />);
    const leaf = screen.getByTestId('structure-leaf-high-conf-leaf');
    expect(leaf.className).not.toContain('structure-tree-leaf--low-confidence');
  });

  it('shows a multi-page range in a node\'s label when scopeRanges covers it', () => {
    const store = storeFromRows({
      427: {
        'annexure:a-2': [candidate({ key: 'annexure:a-2', anchor_id: 'annexure-leaf', page: 427, scope_chain: [] })],
      },
    });
    const scopeRanges = [{ key: 'annexure:a-2', start_page: 427, end_page: 446, depth: 0 }];
    render(<StructureTree referenceStore={store} onSelect={vi.fn()} scopeRanges={scopeRanges} />);
    expect(screen.getByText('Annexure A-2').parentElement).toHaveTextContent('Annexure A-2 (pages 427–446)');
  });

  it('does not show a range suffix for a node whose single range is only one page', () => {
    const store = storeFromRows({
      2: { 'article:9': [candidate({ key: 'article:9', anchor_id: 'article-leaf', page: 2, scope_chain: [] })] },
    });
    const scopeRanges = [{ key: 'article:9', start_page: 2, end_page: 2, depth: 0 }];
    render(<StructureTree referenceStore={store} onSelect={vi.fn()} scopeRanges={scopeRanges} />);
    expect(screen.getByText('Article 9').parentElement).toHaveTextContent('Article 9');
    expect(screen.getByText('Article 9').parentElement.textContent).not.toContain('pages');
  });

  it('shows every disjoint span for a node with more than one range', () => {
    const store = storeFromRows({
      1: { 'schedule:2': [candidate({ key: 'schedule:2', anchor_id: 'sched-leaf', page: 1, scope_chain: [] })] },
    });
    const scopeRanges = [
      { key: 'schedule:2', start_page: 1, end_page: 1, depth: 0 },
      { key: 'schedule:2', start_page: 4, end_page: 4, depth: 0 },
    ];
    render(<StructureTree referenceStore={store} onSelect={vi.fn()} scopeRanges={scopeRanges} />);
    expect(screen.getByText('Schedule 2').parentElement).toHaveTextContent('Schedule 2 (pages 1, 4)');
  });

  it('renders normally with no range suffix when scopeRanges is not provided at all', () => {
    const store = storeFromRows({
      2: { 'article:9': [candidate({ key: 'article:9', anchor_id: 'article-leaf', page: 2, scope_chain: [] })] },
    });
    render(<StructureTree referenceStore={store} onSelect={vi.fn()} />);
    expect(screen.getByText('Article 9')).toBeInTheDocument();
  });

  it('auto-expands the ancestor chain to the node covering activePage, and marks it current', () => {
    const store = storeFromRows({
      430: {
        'clause:4.1': [candidate({
          key: 'clause:4.1', anchor_id: 'clause-leaf', page: 430,
          scope_chain: [
            { family: 'annexure', key: 'annexure:a-2', label: 'Annexure A-2' },
            { family: 'article', key: 'article:2', label: 'Article 2' },
            { family: 'section', key: 'section:3', label: 'Section 3' },
          ],
        })],
      },
    });
    const scopeRanges = [
      { key: 'annexure:a-2', scope_key: '', depth: 0, start_page: 400, end_page: 500 },
      { key: 'article:2', scope_key: 'annexure:a-2', depth: 1, start_page: 405, end_page: 460 },
      { key: 'section:3', scope_key: 'annexure:a-2/article:2', depth: 2, start_page: 410, end_page: 440 },
    ];
    render(
      <StructureTree referenceStore={store} onSelect={vi.fn()} scopeRanges={scopeRanges} activePage={430} />
    );

    // "Article 2" (depth 1) and "Section 3" (depth 2) are collapsed by
    // default — visible here with no click only because activePage's
    // ancestor chain force-expanded them.
    expect(screen.getByText('Section 3')).toBeInTheDocument();

    const sectionNode = screen.getByTestId('structure-node-root/annexure:a-2/article:2/section:3');
    expect(sectionNode.className).toContain('structure-tree-node--current');

    // Only the deepest covering node is "current" — its ancestors are
    // expanded to reveal it, but aren't themselves marked current.
    const articleNode = screen.getByTestId('structure-node-root/annexure:a-2/article:2');
    expect(articleNode.className).not.toContain('structure-tree-node--current');
  });

  it('leaves the tree at its default collapsed state when activePage falls outside every range', () => {
    const store = storeFromRows({
      430: {
        'clause:4.1': [candidate({
          key: 'clause:4.1', anchor_id: 'clause-leaf', page: 430,
          scope_chain: [
            { family: 'annexure', key: 'annexure:a-2', label: 'Annexure A-2' },
            { family: 'article', key: 'article:2', label: 'Article 2' },
            { family: 'section', key: 'section:3', label: 'Section 3' },
          ],
        })],
      },
    });
    const scopeRanges = [
      { key: 'annexure:a-2', scope_key: '', depth: 0, start_page: 400, end_page: 500 },
      { key: 'article:2', scope_key: 'annexure:a-2', depth: 1, start_page: 405, end_page: 460 },
      { key: 'section:3', scope_key: 'annexure:a-2/article:2', depth: 2, start_page: 410, end_page: 440 },
    ];
    render(
      <StructureTree referenceStore={store} onSelect={vi.fn()} scopeRanges={scopeRanges} activePage={999} />
    );
    // "Article 2" (depth 1) is visible regardless — its parent "Annexure
    // A-2" (depth 0) auto-expands by default. But "Section 3" is depth 2,
    // collapsed by default, nested inside Article 2's own children — no
    // range covers page 999, so nothing forces Article 2 open to reveal it.
    expect(screen.getByText('Article 2')).toBeInTheDocument();
    expect(screen.queryByText('Section 3')).not.toBeInTheDocument();
  });

  it('does not re-force a node back open after the reader manually collapses it, as long as activePage has not changed', () => {
    const store = storeFromRows({
      430: {
        'clause:4.1': [candidate({
          key: 'clause:4.1', anchor_id: 'clause-leaf', page: 430,
          scope_chain: [
            { family: 'annexure', key: 'annexure:a-2', label: 'Annexure A-2' },
            { family: 'article', key: 'article:2', label: 'Article 2' },
            { family: 'section', key: 'section:3', label: 'Section 3' },
          ],
        })],
      },
    });
    const scopeRanges = [
      { key: 'annexure:a-2', scope_key: '', depth: 0, start_page: 400, end_page: 500 },
      { key: 'article:2', scope_key: 'annexure:a-2', depth: 1, start_page: 405, end_page: 460 },
      { key: 'section:3', scope_key: 'annexure:a-2/article:2', depth: 2, start_page: 410, end_page: 440 },
    ];
    const { rerender } = render(
      <StructureTree referenceStore={store} onSelect={vi.fn()} scopeRanges={scopeRanges} activePage={430} />
    );
    // activePage=430 force-expanded Article 2, revealing Section 3.
    expect(screen.getByText('Section 3')).toBeInTheDocument();

    fireEvent.click(screen.getByText('Article 2'));
    expect(screen.queryByText('Section 3')).not.toBeInTheDocument();

    // Same activePage/scopeRanges — a re-render must not re-expand what the
    // reader just closed.
    rerender(
      <StructureTree referenceStore={store} onSelect={vi.fn()} scopeRanges={scopeRanges} activePage={430} />
    );
    expect(screen.queryByText('Section 3')).not.toBeInTheDocument();
  });

  it('auto-scrolls the current node into view when it becomes active', () => {
    const scrollSpy = vi.fn();
    const original = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = scrollSpy;
    try {
      const store = storeFromRows({
        430: {
          'clause:4.1': [candidate({
            key: 'clause:4.1', anchor_id: 'clause-leaf', page: 430,
            scope_chain: [{ family: 'annexure', key: 'annexure:a-2', label: 'Annexure A-2' }],
          })],
        },
      });
      const scopeRanges = [{ key: 'annexure:a-2', scope_key: '', depth: 0, start_page: 400, end_page: 500 }];
      render(
        <StructureTree referenceStore={store} onSelect={vi.fn()} scopeRanges={scopeRanges} activePage={430} />
      );
      expect(scrollSpy).toHaveBeenCalledWith({ block: 'nearest' });
    } finally {
      Element.prototype.scrollIntoView = original;
    }
  });

  it('shows a chunk-jump button only for a node with a scope range, and calls onSelectContainer with its page span', () => {
    const store = storeFromRows({
      427: {
        'annexure:a-2': [candidate({ key: 'annexure:a-2', anchor_id: 'annexure-leaf', page: 427, scope_chain: [] })],
      },
    });
    const scopeRanges = [{ key: 'annexure:a-2', start_page: 427, end_page: 446, depth: 0 }];
    const onSelectContainer = vi.fn();
    render(
      <StructureTree
        referenceStore={store}
        onSelect={vi.fn()}
        onSelectContainer={onSelectContainer}
        scopeRanges={scopeRanges}
      />
    );
    fireEvent.click(screen.getByTestId('structure-jump-root/annexure:a-2'));
    expect(onSelectContainer).toHaveBeenCalledWith({ startPage: 427, endPage: 446, label: 'Annexure A-2' });
  });

  it('does not show a chunk-jump button for a leaf-only node with no scope range', () => {
    const store = storeFromRows({
      2: {
        'clause:4.1': [candidate({ key: 'clause:4.1', anchor_id: 'leaf-1', page: 2, scope_chain: [] })],
      },
    });
    render(<StructureTree referenceStore={store} onSelect={vi.fn()} onSelectContainer={vi.fn()} />);
    expect(screen.queryByTestId('structure-jump-root/clause:4.1')).not.toBeInTheDocument();
  });

  it('clicking the chunk-jump button does not also toggle the node\'s own expand/collapse state', () => {
    const store = storeFromRows({
      427: {
        'clause:4.1': [candidate({
          key: 'clause:4.1', anchor_id: 'clause-leaf', page: 427,
          scope_chain: [{ family: 'annexure', key: 'annexure:a-2', label: 'Annexure A-2' }],
        })],
      },
    });
    const scopeRanges = [{ key: 'annexure:a-2', start_page: 427, end_page: 446, depth: 0 }];
    render(
      <StructureTree referenceStore={store} onSelect={vi.fn()} onSelectContainer={vi.fn()} scopeRanges={scopeRanges} />
    );
    // "Annexure A-2" is depth 0 (auto-expanded) — "Clause 4.1" should already
    // be visible; clicking the jump button must not collapse it.
    expect(screen.getByText('Clause 4.1')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('structure-jump-root/annexure:a-2'));
    expect(screen.getByText('Clause 4.1')).toBeInTheDocument();
  });

  it('calls onSelect with the exact candidate when a leaf is clicked', () => {
    const onSelect = vi.fn();
    const store = storeFromRows({
      2: {
        'clause:4.1': [candidate({ key: 'clause:4.1', anchor_id: 'leaf-1', page: 2, scope_chain: [] })],
      },
    });
    render(<StructureTree referenceStore={store} onSelect={onSelect} />);

    // "Clause 4.1" is a bare, root-level (depth 0) node here — already
    // auto-expanded, so its leaf is visible without any click.
    fireEvent.click(screen.getByTestId('structure-leaf-leaf-1'));

    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect.mock.calls[0][0]).toMatchObject({ anchor_id: 'leaf-1', page: 2, key: 'clause:4.1' });
  });
});

describe('StructureTree — manual entries render like any other node', () => {
  it('renders a manual entry\'s own heading verbatim, not through the family/number formatter', () => {
    const store = storeFromRows({
      12: {
        'manual:abc123': [candidate({
          key: 'manual:abc123', anchor_id: 'manual-leaf', page: 12, scope_chain: [],
          own_label: 'My Custom Section', detection_source: 'manual',
        })],
      },
    });
    render(<StructureTree referenceStore={store} onSelect={vi.fn()} />);
    expect(screen.getByText('My Custom Section')).toBeInTheDocument();
    expect(screen.getByTestId('structure-leaf-manual-leaf')).toHaveTextContent('Manual');
  });
});

describe('StructureTree — edit/delete affordances', () => {
  it('shows edit/delete on a container node with its own occurrence, calling back with the right descriptor', () => {
    const store = storeFromRows({
      427: {
        'annexure:a-2': [candidate({ key: 'annexure:a-2', anchor_id: 'annexure-leaf', page: 427, occurrence_index: 0, scope_chain: [] })],
      },
    });
    const onEditItem = vi.fn();
    const onDeleteItem = vi.fn();
    render(<StructureTree referenceStore={store} onSelect={vi.fn()} onEditItem={onEditItem} onDeleteItem={onDeleteItem} />);

    fireEvent.click(screen.getByTestId('structure-edit-root/annexure:a-2'));
    expect(onEditItem).toHaveBeenCalledWith(expect.objectContaining({ itemKey: 'annexure:a-2', page: 427, occurrenceIndex: 0, isManual: false }));

    fireEvent.click(screen.getByTestId('structure-delete-root/annexure:a-2'));
    expect(onDeleteItem).toHaveBeenCalledWith(expect.objectContaining({ itemKey: 'annexure:a-2', page: 427 }));
  });

  it('hides edit/delete for a container node with no leaves of its own (a pure grouping artifact)', () => {
    // "article:2" only ever appears as an ANCESTOR in a descendant's
    // scope_chain — it has no own detected row, so no addressable override
    // target.
    const store = storeFromRows({
      6: {
        'clause:4.1': [candidate({
          key: 'clause:4.1', anchor_id: 'clause-leaf', page: 6,
          scope_chain: [{ family: 'article', key: 'article:2', label: 'Article 2' }],
        })],
      },
    });
    render(<StructureTree referenceStore={store} onSelect={vi.fn()} onEditItem={vi.fn()} onDeleteItem={vi.fn()} />);
    expect(screen.queryByTestId('structure-edit-root/article:2')).not.toBeInTheDocument();
    expect(screen.queryByTestId('structure-delete-root/article:2')).not.toBeInTheDocument();
  });

  it('shows edit/delete on a leaf row, calling back without opening/toggling the parent node', () => {
    const store = storeFromRows({
      6: {
        'clause:4.1': [candidate({
          key: 'clause:4.1', anchor_id: 'clause-leaf', page: 6, occurrence_index: 0,
          scope_chain: [{ family: 'annexure', key: 'annexure:a-1', label: 'Annexure A-1' }],
        })],
      },
    });
    const onEditItem = vi.fn();
    const onDeleteItem = vi.fn();
    render(<StructureTree referenceStore={store} onSelect={vi.fn()} onEditItem={onEditItem} onDeleteItem={onDeleteItem} />);

    // "Annexure A-1" (depth 0) is auto-expanded; "Clause 4.1" (depth 1) is
    // collapsed by default and needs a click to reveal its own leaf row.
    fireEvent.click(screen.getByText('Clause 4.1'));

    fireEvent.click(screen.getByTestId('structure-edit-leaf-clause-leaf'));
    expect(onEditItem).toHaveBeenCalledWith(expect.objectContaining({ itemKey: 'clause:4.1', page: 6, occurrenceIndex: 0 }));
    fireEvent.click(screen.getByTestId('structure-delete-leaf-clause-leaf'));
    expect(onDeleteItem).toHaveBeenCalledWith(expect.objectContaining({ itemKey: 'clause:4.1', page: 6 }));
  });

  it('does not render edit/delete buttons at all when the callbacks are not provided', () => {
    const store = storeFromRows({
      427: { 'annexure:a-2': [candidate({ key: 'annexure:a-2', anchor_id: 'annexure-leaf', page: 427, scope_chain: [] })] },
    });
    render(<StructureTree referenceStore={store} onSelect={vi.fn()} />);
    expect(screen.queryByTestId('structure-edit-root/annexure:a-2')).not.toBeInTheDocument();
    expect(screen.queryByTestId('structure-delete-root/annexure:a-2')).not.toBeInTheDocument();
  });
});

describe('StructureTree — hierarchy overrides (drag-and-drop reparent/reorder)', () => {
  function twoSiblingAnnexures() {
    return storeFromRows({
      10: { 'annexure:a-1': [candidate({ key: 'annexure:a-1', anchor_id: 'a1-leaf', page: 10, scope_chain: [] })] },
      50: { 'annexure:a-2': [candidate({ key: 'annexure:a-2', anchor_id: 'a2-leaf', page: 50, scope_chain: [] })] },
    });
  }

  it('a parent override moves a node (and its whole subtree) under its new parent', () => {
    const store = storeFromRows({
      10: { 'annexure:a-1': [candidate({ key: 'annexure:a-1', anchor_id: 'a1-leaf', page: 10, scope_chain: [] })] },
      50: { 'annexure:a-2': [candidate({ key: 'annexure:a-2', anchor_id: 'a2-leaf', page: 50, scope_chain: [] })] },
      55: {
        'clause:1': [candidate({
          key: 'clause:1', anchor_id: 'clause-leaf', page: 55,
          scope_chain: [{ family: 'annexure', key: 'annexure:a-2', label: 'Annexure A-2' }],
        })],
      },
    });
    // Move Annexure A-2 (and its child Clause 1) to become a child of Annexure A-1.
    const parentOverrides = { 'annexure:a-2': 'annexure:a-1' };
    render(<StructureTree referenceStore={store} onSelect={vi.fn()} parentOverrides={parentOverrides} />);

    // Annexure A-2's node keeps its original id (a stable identity, not a
    // recomputed path) but now renders nested inside Annexure A-1's
    // children.
    const a1Node = screen.getByTestId('structure-node-root/annexure:a-1');
    const a2Node = screen.getByTestId('structure-node-root/annexure:a-2');
    expect(a1Node).toContainElement(a2Node);

    // Its own child (Clause 1) came along too — collapsed by default now
    // that Annexure A-2 sits at depth 1, not depth 0, so expand it first.
    fireEvent.click(screen.getByText('Annexure A-2'));
    expect(screen.getByText('Clause 1')).toBeInTheDocument();
  });

  it('moving a node under a previously-childless leaf-shaped node nests it there (every node already carries a children slot)', () => {
    const store = storeFromRows({
      6: {
        'clause:4.1': [candidate({ key: 'clause:4.1', anchor_id: 'clause-leaf', page: 6, scope_chain: [] })],
      },
      10: {
        'clause:4.2': [candidate({ key: 'clause:4.2', anchor_id: 'clause-4-2-leaf', page: 10, scope_chain: [] })],
      },
    });
    // Move Clause 4.2 to become a child of Clause 4.1 (previously a bare leaf).
    const parentOverrides = { 'clause:4.2': 'clause:4.1' };
    render(<StructureTree referenceStore={store} onSelect={vi.fn()} parentOverrides={parentOverrides} />);

    const parentNode = screen.getByTestId('structure-node-root/clause:4.1');
    const childNode = screen.getByTestId('structure-node-root/clause:4.2');
    expect(parentNode).toContainElement(childNode);
  });

  it('an orphaned parent override (target no longer exists) is silently inert', () => {
    const store = twoSiblingAnnexures();
    const parentOverrides = { 'annexure:a-1': 'annexure:does-not-exist' };
    render(<StructureTree referenceStore={store} onSelect={vi.fn()} parentOverrides={parentOverrides} />);
    // Falls back to its natural (root-level) position — no crash, no vanish.
    expect(screen.getByTestId('structure-node-root/annexure:a-1')).toBeInTheDocument();
  });

  it('a sort override reorders siblings, with an un-positioned sibling appended in default page order', () => {
    const store = storeFromRows({
      10: { 'annexure:a-1': [candidate({ key: 'annexure:a-1', anchor_id: 'a1-leaf', page: 10, scope_chain: [] })] },
      50: { 'annexure:a-2': [candidate({ key: 'annexure:a-2', anchor_id: 'a2-leaf', page: 50, scope_chain: [] })] },
      90: { 'annexure:a-3': [candidate({ key: 'annexure:a-3', anchor_id: 'a3-leaf', page: 90, scope_chain: [] })] },
    });
    // Default page order would be A-1, A-2, A-3 — override puts A-2 first;
    // A-3 has no explicit position and should land after the positioned ones.
    const sortOverrides = { '': ['annexure:a-2', 'annexure:a-1'] };
    render(<StructureTree referenceStore={store} onSelect={vi.fn()} sortOverrides={sortOverrides} />);

    const nodeIds = screen.getAllByTestId(/^structure-node-root\/annexure:a-\d$/).map((el) => el.dataset.testid);
    expect(nodeIds).toEqual([
      'structure-node-root/annexure:a-2',
      'structure-node-root/annexure:a-1',
      'structure-node-root/annexure:a-3',
    ]);
  });

  it('renders a "move to top level" root drop zone only when onMoveItem is provided', () => {
    const store = twoSiblingAnnexures();
    const { rerender } = render(<StructureTree referenceStore={store} onSelect={vi.fn()} />);
    expect(screen.queryByTestId('structure-tree-root-dropzone')).not.toBeInTheDocument();

    rerender(<StructureTree referenceStore={store} onSelect={vi.fn()} onMoveItem={vi.fn()} />);
    expect(screen.getByTestId('structure-tree-root-dropzone')).toBeInTheDocument();
  });
});
