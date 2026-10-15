import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import DualPdfCompare from './DualPdfCompare';

vi.mock('../PdfCanvasViewer', () => ({
  default: ({ pageNum }) => (
    <div data-testid="mock-pdf-viewer">Page: {pageNum}</div>
  ),
}));

describe('DualPdfCompare component', () => {
  it('renders side-by-side comparison panes and target clause badge', () => {
    const onClose = vi.fn();
    render(
      <DualPdfCompare
        pdfSrc="fake-src"
        currentPage={3}
        targetPage={12}
        totalPages={42}
        onClose={onClose}
      />
    );

    expect(screen.getByTestId('dual-pdf-compare')).toBeInTheDocument();
    expect(screen.getByText(/Document Scan \(Current Page 3\)/)).toBeInTheDocument();
    expect(screen.getByTestId('target-clause-badge')).toHaveTextContent(/TARGET CLAUSE/);

    const closeBtn = screen.getByTestId('pinned-pdf-close-btn');
    fireEvent.click(closeBtn);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('renders target tabs when multiple targets exist and fires onSelectTargetIndex', () => {
    const onSelect = vi.fn();
    const targets = [
      { page: 12, section: '4.2 Security Deposit' },
      { page: 27, section: 'Annexure A-1' },
    ];

    render(
      <DualPdfCompare
        pdfSrc="fake-src"
        currentPage={3}
        targetPage={12}
        targets={targets}
        activeTargetIndex={0}
        onSelectTargetIndex={onSelect}
        totalPages={42}
      />
    );

    expect(screen.getByTestId('dual-pdf-target-tabs')).toBeInTheDocument();
    const tab2 = screen.getByTestId('target-tab-1');
    expect(tab2).toHaveTextContent('Annexure A-1');

    fireEvent.click(tab2);
    expect(onSelect).toHaveBeenCalledWith(1);
  });

  it('toggles sync zoom state', () => {
    render(
      <DualPdfCompare
        pdfSrc="fake-src"
        currentPage={3}
        targetPage={12}
        totalPages={42}
      />
    );

    const syncBtn = screen.getByTestId('sync-zoom-btn');
    expect(syncBtn).toHaveTextContent('Sync Zoom: OFF');

    fireEvent.click(syncBtn);
    expect(syncBtn).toHaveTextContent('Sync Zoom: ON');
  });
});
