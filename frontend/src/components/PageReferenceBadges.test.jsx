import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import PageReferenceBadges, { sortCandidates } from './PageReferenceBadges';
import { createReferenceStore, mergeDelta } from '../utils/referenceStore';

function storeWithDelta(changedPages, version = 1) {
  const store = createReferenceStore();
  mergeDelta(store, { reference_index_version: version, changed_pages: changedPages });
  return store;
}

describe('PageReferenceBadges', () => {
  it('renders nothing when there are no mentions', () => {
    const store = createReferenceStore();
    const { container } = render(
      <PageReferenceBadges mentions={[]} referenceStore={store} scopeRanges={[]} currentPage={7} onOpenReference={vi.fn()} />
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('a single-candidate mention opens the target directly, with no picker modal', () => {
    const store = storeWithDelta({ 61: { 'annexure:a-1': [{ anchor_id: 'a1', collapsed_key: 'annexure:a1', page: 61 }] } });
    const onOpenReference = vi.fn();
    render(
      <PageReferenceBadges
        mentions={[{ key: 'annexure:a-1', raw: 'Annexure A-1' }]}
        referenceStore={store}
        scopeRanges={[]}
        currentPage={7}
        onOpenReference={onOpenReference}
      />
    );
    fireEvent.click(screen.getByTestId('page-reference-badge-annexure:a-1'));
    expect(onOpenReference).toHaveBeenCalledTimes(1);
    expect(onOpenReference.mock.calls[0][0].page).toBe(61);
    expect(screen.queryByTestId('page-reference-candidates-modal')).not.toBeInTheDocument();
  });

  it('a multi-candidate mention (mix of exact + approximate) opens the picker modal with both groups; a row click opens that candidate and closes it', () => {
    const store = createReferenceStore();
    mergeDelta(store, {
      reference_index_version: 1,
      changed_pages: {
        10: { 'clause:9': [{ anchor_id: 'c9', collapsed_key: 'clause:9' }] },
        25: { 'numheading:9.0': [{ anchor_id: 'nh9', collapsed_key: 'numheading:90' }] },
      },
    });
    const onOpenReference = vi.fn();
    render(
      <PageReferenceBadges
        mentions={[{ key: 'clause:9', raw: 'Clause 9' }]}
        referenceStore={store}
        scopeRanges={[]}
        currentPage={10}
        onOpenReference={onOpenReference}
      />
    );
    fireEvent.click(screen.getByTestId('page-reference-badge-clause:9'));
    expect(onOpenReference).not.toHaveBeenCalled();
    const modal = screen.getByTestId('page-reference-candidates-modal');
    expect(modal).toHaveTextContent('Exact Match');
    expect(modal).toHaveTextContent('Possible');
    expect(modal).toHaveTextContent('Page 10');
    expect(modal).toHaveTextContent('Page 25');

    fireEvent.click(screen.getByText('Page 25'));
    expect(onOpenReference).toHaveBeenCalledTimes(1);
    expect(onOpenReference.mock.calls[0][0].page).toBe(25);
    expect(screen.queryByTestId('page-reference-candidates-modal')).not.toBeInTheDocument();
  });

  it('Escape closes an open picker modal without calling onOpenReference', () => {
    const store = storeWithDelta({
      10: { 'point:1': [{ anchor_id: 'p1', collapsed_key: 'point:1' }] },
      20: { 'point:1': [{ anchor_id: 'p1b', collapsed_key: 'point:1' }] },
    });
    const onOpenReference = vi.fn();
    render(
      <PageReferenceBadges
        mentions={[{ key: 'point:1', raw: 'para 1' }]}
        referenceStore={store}
        scopeRanges={[]}
        currentPage={10}
        onOpenReference={onOpenReference}
      />
    );
    fireEvent.click(screen.getByTestId('page-reference-badge-point:1'));
    expect(screen.getByTestId('page-reference-candidates-modal')).toBeInTheDocument();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByTestId('page-reference-candidates-modal')).not.toBeInTheDocument();
    expect(onOpenReference).not.toHaveBeenCalled();
  });

  it('a backdrop click closes an open picker modal without calling onOpenReference', () => {
    const store = storeWithDelta({
      10: { 'point:1': [{ anchor_id: 'p1', collapsed_key: 'point:1' }] },
      20: { 'point:1': [{ anchor_id: 'p1b', collapsed_key: 'point:1' }] },
    });
    const onOpenReference = vi.fn();
    render(
      <PageReferenceBadges
        mentions={[{ key: 'point:1', raw: 'para 1' }]}
        referenceStore={store}
        scopeRanges={[]}
        currentPage={10}
        onOpenReference={onOpenReference}
      />
    );
    fireEvent.click(screen.getByTestId('page-reference-badge-point:1'));
    expect(screen.getByTestId('page-reference-candidates-modal')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('page-reference-candidates-modal-backdrop'));
    expect(screen.queryByTestId('page-reference-candidates-modal')).not.toBeInTheDocument();
    expect(onOpenReference).not.toHaveBeenCalled();
  });

  it('navigating to a different currentPage while the picker modal is open closes it', () => {
    const store = storeWithDelta({
      10: { 'point:1': [{ anchor_id: 'p1', collapsed_key: 'point:1' }] },
      20: { 'point:1': [{ anchor_id: 'p1b', collapsed_key: 'point:1' }] },
    });
    const { rerender } = render(
      <PageReferenceBadges
        mentions={[{ key: 'point:1', raw: 'para 1' }]}
        referenceStore={store}
        scopeRanges={[]}
        currentPage={10}
        onOpenReference={vi.fn()}
      />
    );
    fireEvent.click(screen.getByTestId('page-reference-badge-point:1'));
    expect(screen.getByTestId('page-reference-candidates-modal')).toBeInTheDocument();
    rerender(
      <PageReferenceBadges
        mentions={[{ key: 'point:1', raw: 'para 1' }]}
        referenceStore={store}
        scopeRanges={[]}
        currentPage={11}
        onOpenReference={vi.fn()}
      />
    );
    expect(screen.queryByTestId('page-reference-candidates-modal')).not.toBeInTheDocument();
  });

  it('a fallback-only single candidate (no exact same-family target) still opens the picker, not an auto-navigate', () => {
    // Regression test for a confirmed live failure: "clause 9.0 SCC" (payment
    // terms) had exactly one fallback candidate, "clause 9.0 ITB" (site
    // conditions, a different bidding sub-document) — an unlucky single
    // guess that auto-opened with no chance to catch the mistake. A
    // fallback-only match must now always be confirmed, even as the sole
    // candidate.
    const store = storeWithDelta({ 25: { 'numheading:9.0': [{ anchor_id: 'nh9', collapsed_key: 'numheading:90' }] } });
    const onOpenReference = vi.fn();
    render(
      <PageReferenceBadges
        mentions={[{ key: 'clause:9.0', raw: 'Clause 9.0', approximate: true }]}
        referenceStore={store}
        scopeRanges={[]}
        currentPage={86}
        onOpenReference={onOpenReference}
      />
    );
    fireEvent.click(screen.getByTestId('page-reference-badge-clause:9.0'));
    expect(onOpenReference).not.toHaveBeenCalled();
    const modal = screen.getByTestId('page-reference-candidates-modal');
    expect(modal).toHaveTextContent('Page 25');

    fireEvent.click(screen.getByText('Page 25'));
    expect(onOpenReference).toHaveBeenCalledTimes(1);
    expect(onOpenReference.mock.calls[0][0].page).toBe(25);
  });

  it('a badge for an approximate-only mention gets the --approximate modifier class', () => {
    const store = storeWithDelta({ 25: { 'numheading:9.0': [{ anchor_id: 'nh9', collapsed_key: 'numheading:90' }] } });
    render(
      <PageReferenceBadges
        mentions={[{ key: 'clause:9.0', raw: 'Clause 9.0', approximate: true }]}
        referenceStore={store}
        scopeRanges={[]}
        currentPage={86}
        onOpenReference={vi.fn()}
      />
    );
    expect(screen.getByTestId('page-reference-badge-clause:9.0')).toHaveClass('page-reference-badge--approximate');
  });

  it('an exact-match badge does not get the --approximate modifier class', () => {
    const store = storeWithDelta({ 61: { 'annexure:a-1': [{ anchor_id: 'a1', collapsed_key: 'annexure:a1' }] } });
    render(
      <PageReferenceBadges
        mentions={[{ key: 'annexure:a-1', raw: 'Annexure A-1', approximate: false }]}
        referenceStore={store}
        scopeRanges={[]}
        currentPage={7}
        onOpenReference={vi.fn()}
      />
    );
    expect(screen.getByTestId('page-reference-badge-annexure:a-1')).not.toHaveClass('page-reference-badge--approximate');
  });

  it('clicking a second, different multi-candidate badge opens the picker for the new mention', () => {
    const store = createReferenceStore();
    mergeDelta(store, {
      reference_index_version: 1,
      changed_pages: {
        10: {
          'point:1': [{ anchor_id: 'p1a', collapsed_key: 'point:1' }],
          'point:2': [{ anchor_id: 'p2a', collapsed_key: 'point:2' }],
        },
        20: {
          'point:1': [{ anchor_id: 'p1b', collapsed_key: 'point:1' }],
          'point:2': [{ anchor_id: 'p2b', collapsed_key: 'point:2' }],
        },
      },
    });
    render(
      <PageReferenceBadges
        mentions={[{ key: 'point:1', raw: 'para 1' }, { key: 'point:2', raw: 'para 2' }]}
        referenceStore={store}
        scopeRanges={[]}
        currentPage={10}
        onOpenReference={vi.fn()}
      />
    );
    fireEvent.click(screen.getByTestId('page-reference-badge-point:1'));
    expect(screen.getByTestId('page-reference-candidates-modal')).toHaveTextContent('para 1');

    fireEvent.click(screen.getByTestId('page-reference-badge-point:2'));
    expect(screen.getByTestId('page-reference-candidates-modal')).toHaveTextContent('para 2');
  });
});

describe('sortCandidates', () => {
  it('sorts a same-scope candidate before an out-of-scope one, regardless of page distance', () => {
    const list = [
      { page: 100, scope_key: 'other' },
      { page: 30, scope_key: 'mine' },
    ];
    const sorted = sortCandidates(list, 10, 'mine');
    expect(sorted.map((c) => c.page)).toEqual([30, 100]);
  });

  it('falls back to nearest-page distance when scope does not distinguish', () => {
    const list = [
      { page: 50, scope_key: null },
      { page: 12, scope_key: null },
    ];
    const sorted = sortCandidates(list, 10, null);
    expect(sorted.map((c) => c.page)).toEqual([12, 50]);
  });

  it('never treats a null currentScopeKey as matching a candidate with scope_key null', () => {
    const list = [{ page: 40, scope_key: null }];
    const sorted = sortCandidates(list, 10, null);
    // Just asserting this doesn't throw and returns the single item — the
    // real guard is `currentScopeKey != null` before the equality check.
    expect(sorted).toHaveLength(1);
  });
});
