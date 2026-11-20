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

  it('deduplicates identical range entries and retains full heading tooltip', () => {
    const store = storeFromRows({
      1: {
        'manual:abc': [candidate({ key: 'manual:abc', anchor_id: 'man-leaf', page: 1, own_label: 'Very Long Manual Index Heading Title', scope_chain: [] })],
      },
    });
    const scopeRanges = [
      { key: 'manual:abc', start_page: 1, end_page: 135, depth: 0 },
      { key: 'manual:abc', start_page: 1, end_page: 135, depth: 0 },
      { key: 'manual:abc', start_page: 1, end_page: 135, depth: 0 },
      { key: 'manual:abc', start_page: 1, end_page: 135, depth: 0 },
    ];
    render(<StructureTree referenceStore={store} onSelect={vi.fn()} scopeRanges={scopeRanges} />);
    const labelEl = screen.getByText('Very Long Manual Index Heading Title');
    expect(labelEl).toHaveAttribute('title', 'Very Long Manual Index Heading Title');
    expect(labelEl.parentElement).toHaveTextContent('Very Long Manual Index Heading Title (pages 1–135)');
    expect(labelEl.parentElement.textContent).not.toContain('1–135, 1–135');
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

    // "Article 2" (depth 1) is collapsed by default — clicking it expands it to reveal "Section 3"
    fireEvent.click(screen.getByText('Article 2'));
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
    // Clicking Article 2 expands it, revealing Section 3.
    fireEvent.click(screen.getByText('Article 2'));
    expect(screen.getByText('Section 3')).toBeInTheDocument();

    // Clicking its chevron toggles it closed.
    fireEvent.click(screen.getByTestId('structure-toggle-root/annexure:a-2/article:2'));
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

  // Magnifying-glass/jump button is temporarily disabled in StructureTree.jsx — re-enable alongside it.
  it.skip('shows a chunk-jump button only for a node with a scope range, and calls onSelectContainer with its page span', () => {
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

  // Magnifying-glass/jump button is temporarily disabled in StructureTree.jsx — re-enable alongside it.
  it.skip('clicking the chunk-jump button does not also toggle the node\'s own expand/collapse state', () => {
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

  it('clicking a container row with a scope range calls onJumpContainer with its start page, without toggling expand/collapse', () => {
    const store = storeFromRows({
      427: {
        'clause:4.1': [candidate({
          key: 'clause:4.1', anchor_id: 'clause-leaf', page: 427,
          scope_chain: [{ family: 'annexure', key: 'annexure:a-2', label: 'Annexure A-2' }],
        })],
      },
    });
    const scopeRanges = [{ key: 'annexure:a-2', start_page: 427, end_page: 446, depth: 0 }];
    const onJumpContainer = vi.fn();
    render(
      <StructureTree
        referenceStore={store}
        onSelect={vi.fn()}
        onSelectContainer={vi.fn()}
        onJumpContainer={onJumpContainer}
        scopeRanges={scopeRanges}
      />
    );
    fireEvent.click(screen.getByText('Annexure A-2'));
    expect(onJumpContainer).toHaveBeenCalledWith({ startPage: 427, endPage: 446, label: 'Annexure A-2' });
    // Still expanded — a row click on a node with a range means "jump," not "toggle."
    expect(screen.getByText('Clause 4.1')).toBeInTheDocument();
  });

  it('clicking a container row with no scope range still jumps, falling back to its own earliest known page', () => {
    const sharedChain = [{ family: 'article', key: 'article:2', label: 'Article 2' }];
    const store = storeFromRows({
      2: {
        'clause:4.1': [candidate({ key: 'clause:4.1', anchor_id: 'occ-0', page: 2, occurrence_index: 0, scope_chain: sharedChain })],
      },
      5: {
        'clause:4.2': [candidate({ key: 'clause:4.2', anchor_id: 'occ-1', page: 5, occurrence_index: 0, scope_chain: sharedChain })],
      },
    });
    const onJumpContainer = vi.fn();
    render(<StructureTree referenceStore={store} onSelect={vi.fn()} onJumpContainer={onJumpContainer} />);

    // "Article 2" has no backend-computed scope range (no scopeRanges prop
    // passed at all) — the row still jumps, falling back to its own
    // earliest known page (2, from Clause 4.1) instead of doing nothing.
    fireEvent.click(screen.getByText('Article 2'));
    expect(onJumpContainer).toHaveBeenCalledWith({ startPage: 2, endPage: 2, label: 'Article 2' });
  });

  it('clicking a container row toggles expand/collapse when no onJumpContainer callback is provided at all', () => {
    const sharedChain = [{ family: 'article', key: 'article:2', label: 'Article 2' }];
    const store = storeFromRows({
      2: {
        'clause:4.1': [candidate({ key: 'clause:4.1', anchor_id: 'occ-0', page: 2, occurrence_index: 0, scope_chain: sharedChain })],
      },
      5: {
        'clause:4.2': [candidate({ key: 'clause:4.2', anchor_id: 'occ-1', page: 5, occurrence_index: 0, scope_chain: sharedChain })],
      },
    });
    render(<StructureTree referenceStore={store} onSelect={vi.fn()} />);

    // "Article 2" (depth 0) is auto-expanded, so its children are already visible.
    expect(screen.getByText('Clause 4.1')).toBeInTheDocument();
    fireEvent.click(screen.getByText('Article 2'));
    expect(screen.queryByText('Clause 4.1')).not.toBeInTheDocument();
  });

  // Magnifying-glass/jump button is temporarily disabled in StructureTree.jsx — re-enable alongside it.
  it.skip('clicking a leaf\'s magnifying-glass button calls onOpenReference without also calling onSelect', () => {
    const onSelect = vi.fn();
    const onOpenReference = vi.fn();
    const store = storeFromRows({
      2: {
        'clause:4.1': [candidate({ key: 'clause:4.1', anchor_id: 'leaf-1', page: 2, scope_chain: [] })],
      },
    });
    render(<StructureTree referenceStore={store} onSelect={onSelect} onOpenReference={onOpenReference} />);

    fireEvent.click(screen.getByTestId('structure-view-leaf-leaf-1'));

    expect(onOpenReference).toHaveBeenCalledTimes(1);
    expect(onOpenReference.mock.calls[0][0]).toMatchObject({ anchor_id: 'leaf-1', page: 2, key: 'clause:4.1' });
    expect(onSelect).not.toHaveBeenCalled();
  });

  it('does not render a leaf\'s magnifying-glass button when onOpenReference is not provided', () => {
    const store = storeFromRows({
      2: { 'clause:4.1': [candidate({ key: 'clause:4.1', anchor_id: 'leaf-1', page: 2, scope_chain: [] })] },
    });
    render(<StructureTree referenceStore={store} onSelect={vi.fn()} />);
    expect(screen.queryByTestId('structure-view-leaf-leaf-1')).not.toBeInTheDocument();
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
    // A real backend-computed scope range keeps this node in the full
    // container treatment even though it has no detected children of its
    // own — see the "shows edit/delete on a leaf row" test below for the
    // no-range, single-occurrence, no-children case instead.
    const scopeRanges = [{ key: 'annexure:a-2', start_page: 427, end_page: 446, depth: 0 }];
    const onEditItem = vi.fn();
    const onDeleteItem = vi.fn();
    render(<StructureTree referenceStore={store} onSelect={vi.fn()} scopeRanges={scopeRanges} onEditItem={onEditItem} onDeleteItem={onDeleteItem} />);

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
    expect(screen.queryByTestId('structure-edit-leaf-annexure-leaf')).not.toBeInTheDocument();
    expect(screen.queryByTestId('structure-delete-leaf-annexure-leaf')).not.toBeInTheDocument();
  });
});

describe('StructureTree — add-child affordance', () => {
  it('clicking add on a container with existing children passes the node\'s own descriptor and its current children as ordered_siblings material', () => {
    const store = storeFromRows({
      427: {
        'annexure:a-2': [candidate({ key: 'annexure:a-2', anchor_id: 'annexure-leaf', page: 427, occurrence_index: 0, scope_chain: [] })],
      },
      430: {
        'clause:4.1': [candidate({
          key: 'clause:4.1', anchor_id: 'clause-leaf', page: 430, occurrence_index: 0,
          scope_chain: [{ family: 'annexure', key: 'annexure:a-2', label: 'Annexure A-2' }],
        })],
      },
    });
    // A real scope range keeps this node in full container treatment
    // (chevron + its own row) rather than folding its single own occurrence
    // into a leaf header — same reasoning as the edit/delete tests above.
    const scopeRanges = [{ key: 'annexure:a-2', start_page: 427, end_page: 446, depth: 0 }];
    const onAddItem = vi.fn();
    render(<StructureTree referenceStore={store} onSelect={vi.fn()} scopeRanges={scopeRanges} onAddItem={onAddItem} />);

    fireEvent.click(screen.getByTestId('structure-add-child-root/annexure:a-2'));
    expect(onAddItem).toHaveBeenCalledWith({
      targetDescriptor: expect.objectContaining({ itemKey: 'annexure:a-2', page: 427, occurrenceIndex: 0 }),
      existingChildren: [{ item_key: 'clause:4.1', item_page: 430, item_occurrence_index: 0 }],
    });
  });

  it('clicking add on a container with no existing children passes an empty existingChildren list', () => {
    const store = storeFromRows({
      427: { 'annexure:a-2': [candidate({ key: 'annexure:a-2', anchor_id: 'annexure-leaf', page: 427, occurrence_index: 0, scope_chain: [] })] },
    });
    const scopeRanges = [{ key: 'annexure:a-2', start_page: 427, end_page: 446, depth: 0 }];
    const onAddItem = vi.fn();
    render(<StructureTree referenceStore={store} onSelect={vi.fn()} scopeRanges={scopeRanges} onAddItem={onAddItem} />);

    fireEvent.click(screen.getByTestId('structure-add-child-root/annexure:a-2'));
    expect(onAddItem).toHaveBeenCalledWith({
      targetDescriptor: expect.objectContaining({ itemKey: 'annexure:a-2', page: 427 }),
      existingChildren: [],
    });
  });

  it('clicking add on an individual leaf occurrence row passes that leaf\'s own descriptor and no existing children', () => {
    // Two occurrences of the same key, so each renders as its own LeafRow
    // rather than folding into a single primaryLeaf header.
    const store = storeFromRows({
      2: {
        'clause:4.1': [candidate({ key: 'clause:4.1', anchor_id: 'occ-0', page: 2, occurrence_index: 0, scope_chain: [] })],
      },
      5: {
        'clause:4.1': [candidate({ key: 'clause:4.1', anchor_id: 'occ-1', page: 5, occurrence_index: 1, scope_chain: [] })],
      },
    });
    const onAddItem = vi.fn();
    render(<StructureTree referenceStore={store} onSelect={vi.fn()} onAddItem={onAddItem} />);

    fireEvent.click(screen.getByTestId('structure-add-child-leaf-occ-1'));
    expect(onAddItem).toHaveBeenCalledWith({
      targetDescriptor: expect.objectContaining({ itemKey: 'clause:4.1', page: 5, occurrenceIndex: 1 }),
      existingChildren: [],
    });
  });

  it('hides the add button for a container node with no leaves of its own (a pure grouping artifact), same as edit/delete', () => {
    const store = storeFromRows({
      6: {
        'clause:4.1': [candidate({
          key: 'clause:4.1', anchor_id: 'clause-leaf', page: 6,
          scope_chain: [{ family: 'article', key: 'article:2', label: 'Article 2' }],
        })],
      },
    });
    render(<StructureTree referenceStore={store} onSelect={vi.fn()} onAddItem={vi.fn()} />);
    expect(screen.queryByTestId('structure-add-child-root/article:2')).not.toBeInTheDocument();
  });

  it('does not render the add button at all when onAddItem is not provided', () => {
    const store = storeFromRows({
      427: { 'annexure:a-2': [candidate({ key: 'annexure:a-2', anchor_id: 'annexure-leaf', page: 427, scope_chain: [] })] },
    });
    render(<StructureTree referenceStore={store} onSelect={vi.fn()} />);
    expect(screen.queryByTestId('structure-add-child-leaf-annexure-leaf')).not.toBeInTheDocument();
  });

  it('magnifying-glass buttons are absent while add/edit/delete still render, for both a container row and a leaf row', () => {
    const store = storeFromRows({
      427: { 'annexure:a-2': [candidate({ key: 'annexure:a-2', anchor_id: 'annexure-leaf', page: 427, occurrence_index: 0, scope_chain: [] })] },
    });
    const scopeRanges = [{ key: 'annexure:a-2', start_page: 427, end_page: 446, depth: 0 }];
    render(
      <StructureTree
        referenceStore={store}
        onSelect={vi.fn()}
        onSelectContainer={vi.fn()}
        onOpenReference={vi.fn()}
        onAddItem={vi.fn()}
        onEditItem={vi.fn()}
        onDeleteItem={vi.fn()}
        scopeRanges={scopeRanges}
      />
    );
    expect(screen.queryByTestId('structure-jump-root/annexure:a-2')).not.toBeInTheDocument();
    expect(screen.getByTestId('structure-add-child-root/annexure:a-2')).toBeInTheDocument();
    expect(screen.getByTestId('structure-edit-root/annexure:a-2')).toBeInTheDocument();
    expect(screen.getByTestId('structure-delete-root/annexure:a-2')).toBeInTheDocument();
  });
});

describe('StructureTree — universal magnifying-glass/jump target, with a fallback when there is no backend range', () => {
  it('a container-tier node with a real scope range keeps its full container treatment even with no detected children', () => {
    const store = storeFromRows({
      427: {
        'annexure:a-2': [candidate({ key: 'annexure:a-2', anchor_id: 'annexure-leaf', page: 427, scope_chain: [] })],
      },
    });
    const scopeRanges = [{ key: 'annexure:a-2', start_page: 427, end_page: 446, depth: 0 }];
    const onSelectContainer = vi.fn();
    const onJumpContainer = vi.fn();
    render(
      <StructureTree
        referenceStore={store}
        onSelect={vi.fn()}
        onSelectContainer={onSelectContainer}
        onJumpContainer={onJumpContainer}
        scopeRanges={scopeRanges}
      />
    );
    // Still a container row (not folded into a leaf) — chevron and range
    // suffix present (the container-style jump/magnifying-glass button is
    // temporarily disabled — see StructureTree.jsx).
    expect(screen.getByTestId('structure-toggle-root/annexure:a-2')).toBeInTheDocument();
    expect(screen.getByText('Annexure A-2').parentElement).toHaveTextContent('(pages 427–446)');

    fireEvent.click(screen.getByText('Annexure A-2'));
    expect(onJumpContainer).toHaveBeenCalledWith({ startPage: 427, endPage: 446, label: 'Annexure A-2' });
  });

  it('a non-ranged node with more than one occurrence keeps its chevron and expands, instead of folding', () => {
    const store = storeFromRows({
      2: {
        'clause:4.1': [
          candidate({ key: 'clause:4.1', anchor_id: 'occ-0', page: 2, occurrence_index: 0, scope_chain: [] }),
          candidate({ key: 'clause:4.1', anchor_id: 'occ-1', page: 5, occurrence_index: 1, scope_chain: [] }),
        ],
      },
    });
    render(<StructureTree referenceStore={store} onSelect={vi.fn()} />);
    expect(screen.getByTestId('structure-toggle-root/clause:4.1')).toBeInTheDocument();
    expect(screen.getByTestId('structure-leaf-occ-0')).toBeInTheDocument();
    expect(screen.getByTestId('structure-leaf-occ-1')).toBeInTheDocument();
  });

  it('a non-ranged, childless, single-occurrence node uses its own page as the jump/preview fallback, and its edit heading matches the displayed chain-formatted label', () => {
    const store = storeFromRows({
      427: { 'annexure:a-2': [candidate({ key: 'annexure:a-2', anchor_id: 'annexure-leaf', page: 427, occurrence_index: 0, scope_chain: [] })] },
    });
    const onSelect = vi.fn();
    const onOpenReference = vi.fn();
    const onEditItem = vi.fn();
    render(
      <StructureTree
        referenceStore={store}
        onSelect={onSelect}
        onOpenReference={onOpenReference}
        onEditItem={onEditItem}
      />
    );
    // No chevron — nothing left to expand.
    expect(screen.queryByTestId('structure-toggle-root/annexure:a-2')).not.toBeInTheDocument();
    expect(screen.getByText('Annexure A-2')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('structure-leaf-annexure-leaf'));
    expect(onSelect.mock.calls[0][0]).toMatchObject({ anchor_id: 'annexure-leaf', page: 427 });

    // (magnifying-glass/jump button temporarily disabled — see StructureTree.jsx)

    fireEvent.click(screen.getByTestId('structure-edit-leaf-annexure-leaf'));
    // "annexure" isn't in OWN_LABEL_FAMILIES, so a plain leafItemDescriptor
    // would say "a-2" — the displayed, chain-formatted label ("Annexure
    // A-2") is what the edit modal should actually prefill.
    expect(onEditItem).toHaveBeenCalledWith(expect.objectContaining({ heading: 'Annexure A-2', page: 427 }));
  });

  it('a non-ranged, single-occurrence node with a real nested child folds its own occurrence into the header, but keeps a chevron to reveal that child', () => {
    const store = storeFromRows({
      6: {
        'clause:4.1': [candidate({ key: 'clause:4.1', anchor_id: 'clause-leaf', page: 6, occurrence_index: 0, scope_chain: [] })],
      },
      7: {
        'point:a': [candidate({
          key: 'point:a', anchor_id: 'point-a-leaf', page: 7, own_label: 'Point (a)',
          scope_chain: [{ family: 'clause', key: 'clause:4.1', label: 'Clause 4.1' }],
        })],
      },
    });
    const onSelect = vi.fn();
    const onOpenReference = vi.fn();
    render(<StructureTree referenceStore={store} onSelect={onSelect} onOpenReference={onOpenReference} />);

    // "Clause 4.1" is depth 0, auto-expanded — no separate "Page 6" row for
    // its own occurrence, just its one real child underneath.
    expect(screen.getByTestId('structure-toggle-root/clause:4.1')).toBeInTheDocument();
    expect(screen.getByText('Point (a)')).toBeInTheDocument();
    expect(screen.queryByText('Page 6')).not.toBeInTheDocument();

    // The row's own click still carries Clause 4.1's own occurrence
    // (anchor-precise), not a generic container range. (magnifying-glass
    // button temporarily disabled — see StructureTree.jsx)
    fireEvent.click(screen.getByTestId('structure-leaf-clause-leaf'));
    expect(onSelect.mock.calls[0][0]).toMatchObject({ anchor_id: 'clause-leaf', page: 6 });
  });

  it('a genuinely zero-occurrence grouping node still gets a working jump (via its own earliest known page) but no edit/delete', () => {
    // "article:2" only ever appears as an ANCESTOR in a descendant's
    // scope_chain — it has no own detected row of its own.
    const store = storeFromRows({
      6: {
        'clause:4.1': [candidate({
          key: 'clause:4.1', anchor_id: 'clause-leaf', page: 6,
          scope_chain: [{ family: 'article', key: 'article:2', label: 'Article 2' }],
        })],
      },
    });
    const onJumpContainer = vi.fn();
    render(
      <StructureTree
        referenceStore={store}
        onSelect={vi.fn()}
        onJumpContainer={onJumpContainer}
        onEditItem={vi.fn()}
        onDeleteItem={vi.fn()}
      />
    );
    expect(screen.queryByTestId('structure-edit-root/article:2')).not.toBeInTheDocument();
    expect(screen.queryByTestId('structure-delete-root/article:2')).not.toBeInTheDocument();

    fireEvent.click(screen.getByText('Article 2'));
    expect(onJumpContainer).toHaveBeenCalledWith({ startPage: 6, endPage: 6, label: 'Article 2' });
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
    const parentOverrides = [{
      item_key: 'annexure:a-2', item_page: 50, item_occurrence_index: 0,
      new_parent_key: 'annexure:a-1', new_parent_page: 10, new_parent_occurrence_index: 0,
    }];
    render(<StructureTree referenceStore={store} onSelect={vi.fn()} parentOverrides={parentOverrides} />);

    // Annexure A-2's node keeps its original id (a stable identity, not a
    // recomputed path) but now renders nested inside Annexure A-1's
    // children. Each has exactly one occurrence of its own (and, after the
    // move, a child too), so each folds into a leaf-styled row.
    const a1Node = screen.getByTestId('structure-leaf-a1-leaf');
    const a2Node = screen.getByTestId('structure-leaf-a2-leaf');
    expect(a1Node).toContainElement(a2Node);

    // Its own child (Clause 1) came along too — collapsed by default now
    // that Annexure A-2 sits at depth 1, not depth 0, so expand it first.
    // Annexure A-2's own row click now means "jump" (it folded into a leaf
    // row), so expanding requires its chevron specifically, not its label.
    fireEvent.click(screen.getByTestId('structure-toggle-root/annexure:a-2'));
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
    const parentOverrides = [{
      item_key: 'clause:4.2', item_page: 10, item_occurrence_index: 0,
      new_parent_key: 'clause:4.1', new_parent_page: 6, new_parent_occurrence_index: 0,
    }];
    render(<StructureTree referenceStore={store} onSelect={vi.fn()} parentOverrides={parentOverrides} />);

    // Clause 4.1 has exactly one occurrence of its own (with or without a
    // child), so it folds into a leaf-styled row too — same as Clause 4.2.
    const parentNode = screen.getByTestId('structure-leaf-clause-leaf');
    const childNode = screen.getByTestId('structure-leaf-clause-4-2-leaf');
    expect(parentNode).toContainElement(childNode);
  });

  it('an orphaned parent override (target no longer exists) is silently inert', () => {
    const store = twoSiblingAnnexures();
    const parentOverrides = [{
      item_key: 'annexure:a-1', item_page: 10, item_occurrence_index: 0,
      new_parent_key: 'annexure:does-not-exist', new_parent_page: -1, new_parent_occurrence_index: -1,
    }];
    render(<StructureTree referenceStore={store} onSelect={vi.fn()} parentOverrides={parentOverrides} />);
    // Falls back to its natural (root-level) position — no crash, no vanish.
    // (A bare, childless single occurrence renders as a leaf-styled row.)
    expect(screen.getByTestId('structure-leaf-a1-leaf')).toBeInTheDocument();
  });

  it('a sort override reorders siblings, with an un-positioned sibling appended in default page order', () => {
    const store = storeFromRows({
      10: { 'annexure:a-1': [candidate({ key: 'annexure:a-1', anchor_id: 'a1-leaf', page: 10, scope_chain: [] })] },
      50: { 'annexure:a-2': [candidate({ key: 'annexure:a-2', anchor_id: 'a2-leaf', page: 50, scope_chain: [] })] },
      90: { 'annexure:a-3': [candidate({ key: 'annexure:a-3', anchor_id: 'a3-leaf', page: 90, scope_chain: [] })] },
    });
    // Default page order would be A-1, A-2, A-3 — override puts A-2 first;
    // A-3 has no explicit position and should land after the positioned ones.
    const sortOverrides = [
      { scope_key: '', scope_page: -1, scope_occurrence_index: -1, item_key: 'annexure:a-2', item_page: 50, item_occurrence_index: 0, sort_index: 0 },
      { scope_key: '', scope_page: -1, scope_occurrence_index: -1, item_key: 'annexure:a-1', item_page: 10, item_occurrence_index: 0, sort_index: 1 },
    ];
    render(<StructureTree referenceStore={store} onSelect={vi.fn()} sortOverrides={sortOverrides} />);

    // Each is a bare, childless single occurrence — rendered as a leaf-styled row.
    const nodeIds = screen.getAllByTestId(/^structure-leaf-a\d-leaf$/).map((el) => el.dataset.testid);
    expect(nodeIds).toEqual([
      'structure-leaf-a2-leaf',
      'structure-leaf-a1-leaf',
      'structure-leaf-a3-leaf',
    ]);
  });

  it('a parent override on one occurrence of a repeated key does not affect the other occurrence', () => {
    // The exact real-world bug this identity scheme fixes: the same bare
    // key ("point:5") genuinely occurs twice in one document, under two
    // different ancestors. A move targeting ONE occurrence (disambiguated
    // by page + occurrence_index) must never touch the other.
    const store = storeFromRows({
      7: {
        'point:5': [candidate({
          key: 'point:5', anchor_id: 'point5-page7', page: 7,
          scope_chain: [{ family: 'subdoc', key: 'subdoc:6', label: "Respondent's case" }],
        })],
      },
      13: {
        'point:5': [candidate({
          key: 'point:5', anchor_id: 'point5-page13', page: 13,
          scope_chain: [{ family: 'point', key: 'point:19', label: 'Point 19' }],
        })],
      },
      20: {
        'subdoc:99': [candidate({ key: 'subdoc:99', anchor_id: 'target-leaf', page: 20, scope_chain: [] })],
      },
    });
    // Move ONLY the page-13 occurrence of point:5 under subdoc:99.
    const parentOverrides = [{
      item_key: 'point:5', item_page: 13, item_occurrence_index: 0,
      new_parent_key: 'subdoc:99', new_parent_page: 20, new_parent_occurrence_index: 0,
    }];
    render(<StructureTree referenceStore={store} onSelect={vi.fn()} parentOverrides={parentOverrides} />);

    const targetNode = screen.getByTestId('structure-leaf-target-leaf');
    const movedLeaf = screen.getByTestId('structure-leaf-point5-page13');
    expect(targetNode).toContainElement(movedLeaf);

    // The page-7 occurrence is untouched — still under its original parent,
    // never swallowed or overwritten by the other occurrence's move.
    const page7Leaf = screen.getByTestId('structure-leaf-point5-page7');
    expect(targetNode).not.toContainElement(page7Leaf);
    expect(screen.getByText("Respondent's case")).toBeInTheDocument();
  });

  it('renders a "move to top level" root drop zone only when onMoveItem is provided', () => {
    const store = twoSiblingAnnexures();
    const { rerender } = render(<StructureTree referenceStore={store} onSelect={vi.fn()} />);
    expect(screen.queryByTestId('structure-tree-root-dropzone')).not.toBeInTheDocument();

    rerender(<StructureTree referenceStore={store} onSelect={vi.fn()} onMoveItem={vi.fn()} />);
    expect(screen.getByTestId('structure-tree-root-dropzone')).toBeInTheDocument();
  });
});

describe('StructureTree — Expand all / Collapse all toolbar', () => {
  function articleWithCollapsedClause() {
    const sharedChain = [{ family: 'article', key: 'article:2', label: 'Article 2' }];
    return storeFromRows({
      2: {
        'clause:4.1': [
          candidate({ key: 'clause:4.1', anchor_id: 'occ-0', page: 2, occurrence_index: 0, scope_chain: sharedChain }),
          candidate({ key: 'clause:4.1', anchor_id: 'occ-1', page: 2, occurrence_index: 1, scope_chain: sharedChain }),
        ],
      },
    });
  }

  it('renders an "Expand all" button alongside "Collapse all"', () => {
    render(<StructureTree referenceStore={articleWithCollapsedClause()} onSelect={vi.fn()} />);
    expect(screen.getByTestId('structure-expand-all')).toHaveTextContent('Expand all');
    expect(screen.getByTestId('structure-collapse-all')).toHaveTextContent('Collapse all');
  });

  it('"Expand all" reveals a collapsed depth-1 container\'s leaves without clicking it individually', () => {
    render(<StructureTree referenceStore={articleWithCollapsedClause()} onSelect={vi.fn()} />);

    // "Clause 4.1" (depth 1) is collapsed by default — its two leaf
    // occurrences aren't in the document yet, same as the existing
    // "two occurrences" test confirms for a manual click.
    expect(screen.queryByTestId('structure-leaf-occ-0')).not.toBeInTheDocument();
    expect(screen.queryByTestId('structure-leaf-occ-1')).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId('structure-expand-all'));

    expect(screen.getByTestId('structure-leaf-occ-0')).toBeInTheDocument();
    expect(screen.getByTestId('structure-leaf-occ-1')).toBeInTheDocument();
  });

  it('"Collapse all" then "Expand all" fully toggle the tree, including the auto-expanded depth-0 root', () => {
    render(<StructureTree referenceStore={articleWithCollapsedClause()} onSelect={vi.fn()} />);

    // "Article 2" (depth 0) is auto-expanded, so "Clause 4.1" is already visible.
    expect(screen.getByText('Clause 4.1')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('structure-collapse-all'));
    expect(screen.queryByText('Clause 4.1')).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId('structure-expand-all'));
    expect(screen.getByText('Clause 4.1')).toBeInTheDocument();
  });

  it('clicking one sibling index expands only that index and does not expand another sibling index', () => {
    const store = storeFromRows({
      50: {
        'point:1': [candidate({
          key: 'point:1', anchor_id: 'p1', page: 50,
          scope_chain: [
            { family: 'subdoc', key: 'subdoc:judgment', label: 'JUDGMENT' },
            { family: 'subdoc', key: 'subdoc:conclusion', label: 'Conclusion' },
          ],
        })],
      },
      60: {
        'point:2': [candidate({
          key: 'point:2', anchor_id: 'p2', page: 60,
          scope_chain: [
            { family: 'subdoc', key: 'subdoc:judgment', label: 'JUDGMENT' },
            { family: 'subdoc', key: 'subdoc:memo-of-costs', label: 'Memo of Costs' },
          ],
        })],
      },
    });
    const scopeRanges = [
      { key: 'subdoc:judgment', scope_key: '', depth: 0, start_page: 40, end_page: 71 },
      { key: 'subdoc:conclusion', scope_key: 'subdoc:judgment', depth: 1, start_page: 50, end_page: 60 },
      { key: 'subdoc:memo-of-costs', scope_key: 'subdoc:judgment', depth: 1, start_page: 60, end_page: 61 },
    ];
    const onJumpContainer = vi.fn();
    const { rerender } = render(
      <StructureTree
        referenceStore={store}
        onSelect={vi.fn()}
        onJumpContainer={onJumpContainer}
        scopeRanges={scopeRanges}
        activePage={50}
      />
    );

    // JUDGMENT (depth 0) is expanded. Conclusion and Memo of Costs (depth 1) are visible.
    expect(screen.getByText('Conclusion')).toBeInTheDocument();
    expect(screen.getByText('Memo of Costs')).toBeInTheDocument();

    // Conclusion's child is not visible yet (collapsed by default).
    expect(screen.queryByTestId('structure-node-root/subdoc:judgment/subdoc:conclusion/point:1')).not.toBeInTheDocument();

    // Clicking Memo of Costs jumps to page 60
    fireEvent.click(screen.getByText('Memo of Costs'));
    expect(onJumpContainer).toHaveBeenCalledWith({ startPage: 60, endPage: 61, label: 'Memo of Costs' });

    // Rerender with activePage=60 (as ViewerPanel does on page change)
    rerender(
      <StructureTree
        referenceStore={store}
        onSelect={vi.fn()}
        onJumpContainer={onJumpContainer}
        scopeRanges={scopeRanges}
        activePage={60}
      />
    );

    // Conclusion's child MUST STILL NOT be visible (it did not expand)!
    expect(screen.queryByTestId('structure-leaf-p1')).not.toBeInTheDocument();

    // But clicking Conclusion directly DOES expand it
    fireEvent.click(screen.getByText('Conclusion'));
    expect(onJumpContainer).toHaveBeenCalledWith({ startPage: 50, endPage: 60, label: 'Conclusion' });
    expect(screen.getByTestId('structure-leaf-p1')).toBeInTheDocument();
  });
});
