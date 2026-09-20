import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import ViewerPanel from './ViewerPanel';
import { createReferenceStore, mergeDelta } from '../utils/referenceStore';

// PdfCanvasViewer itself (pdf.js rendering) is tested in its own file — here
// it's stubbed so ViewerPanel's own wiring (which page is passed down, the
// onPageChange callback) can be tested without touching pdf.js/canvas at all.
vi.mock('./PdfCanvasViewer', () => ({
  default: ({ pageNum, onPageChange }) => (
    <div data-testid="mock-pdf-canvas-viewer">
      <span data-testid="mock-pdf-page">{pageNum}</span>
      <button data-testid="mock-next-page" onClick={() => onPageChange(pageNum + 1)}>next</button>
    </div>
  ),
}));

function storeWithDelta(changedPages, version = 1) {
  const store = createReferenceStore();
  mergeDelta(store, { reference_index_version: version, changed_pages: changedPages });
  return store;
}

const baseProps = {
  viewMode: 'result',
  setViewMode: vi.fn(),
  pdfSrc: null,
  pdfStatusLabel: '',
  hasSelection: true,
  onPause: vi.fn(),
  onResume: vi.fn(),
  onDownloadPdf: vi.fn(),
  onDownloadResult: vi.fn(),
  sidebarCollapsed: false,
  onToggleSidebar: vi.fn(),
};

function jobWithPages(pages) {
  return { status: 'completed', total_pages: pages.length, processed_pages: pages.length, pages_data: pages };
}

describe('ViewerPanel — per-page confidence badges', () => {
  it('renders each page as its own block with a heading', () => {
    const job = jobWithPages([
      { page: 1, text: 'First page text', confidence_pct: 95, confidence_tier: 'highly_confident' },
      { page: 2, text: 'Second page text', confidence_pct: 40, confidence_tier: 'needs_review' },
    ]);
    render(<ViewerPanel {...baseProps} jobDetails={job} />);
    expect(screen.getByTestId('page-block-1')).toBeInTheDocument();
    expect(screen.getByTestId('page-block-2')).toBeInTheDocument();
    expect(screen.getByText('First page text')).toBeInTheDocument();
    expect(screen.getByText('Second page text')).toBeInTheDocument();
  });

  it('shows the confidence percentage and tier label for a scored page', () => {
    const job = jobWithPages([{ page: 1, text: 'x', confidence_pct: 92, confidence_tier: 'very_confident' }]);
    render(<ViewerPanel {...baseProps} jobDetails={job} />);
    expect(screen.getByTestId('confidence-badge-1')).toHaveTextContent('92% · Very Confident');
  });

  it('shows no percentage for the blank_page and failed special-case tiers', () => {
    const job = jobWithPages([
      { page: 1, text: '', confidence_pct: 100, confidence_tier: 'blank_page' },
      { page: 2, text: '', confidence_pct: 0, confidence_tier: 'failed' },
    ]);
    render(<ViewerPanel {...baseProps} jobDetails={job} />);
    expect(screen.getByTestId('confidence-badge-1')).toHaveTextContent('Blank Page');
    expect(screen.getByTestId('confidence-badge-1')).not.toHaveTextContent('%');
    expect(screen.getByTestId('confidence-badge-2')).toHaveTextContent('Failed');
    expect(screen.getByTestId('confidence-badge-2')).not.toHaveTextContent('%');
  });

  it('shows no badge at all for a page with no score yet (pending/processing)', () => {
    const job = jobWithPages([{ page: 1, text: '', confidence_pct: null, confidence_tier: null }]);
    render(<ViewerPanel {...baseProps} jobDetails={job} />);
    expect(screen.queryByTestId('confidence-badge-1')).not.toBeInTheDocument();
  });

  it('falls back to the scanning placeholder when there are no pages yet', () => {
    render(<ViewerPanel {...baseProps} jobDetails={{ status: 'processing', pages_data: [] }} />);
    expect(screen.getByText('Scanning...')).toBeInTheDocument();
  });
});

describe('ViewerPanel — per-page Retry button', () => {
  it('shows Retry for failed and review pages, not for success/pending/processing', () => {
    const job = jobWithPages([
      { page: 1, text: 'x', status: 'failed', confidence_pct: 0, confidence_tier: 'failed' },
      { page: 2, text: 'x', status: 'review', confidence_pct: 40, confidence_tier: 'needs_review' },
      { page: 3, text: 'x', status: 'success', confidence_pct: 95, confidence_tier: 'highly_confident' },
      { page: 4, text: '', status: 'pending' },
      { page: 5, text: '', status: 'processing' },
    ]);
    render(<ViewerPanel {...baseProps} jobDetails={job} onRetryPage={vi.fn()} retryingPage={null} />);
    expect(screen.getByTestId('retry-page-1')).toBeInTheDocument();
    expect(screen.getByTestId('retry-page-2')).toBeInTheDocument();
    expect(screen.queryByTestId('retry-page-3')).not.toBeInTheDocument();
    expect(screen.queryByTestId('retry-page-4')).not.toBeInTheDocument();
    expect(screen.queryByTestId('retry-page-5')).not.toBeInTheDocument();
  });

  it('calls onRetryPage with that page number when clicked', () => {
    const onRetryPage = vi.fn();
    const job = jobWithPages([{ page: 7, text: 'x', status: 'failed', confidence_pct: 0, confidence_tier: 'failed' }]);
    render(<ViewerPanel {...baseProps} jobDetails={job} onRetryPage={onRetryPage} retryingPage={null} />);
    fireEvent.click(screen.getByTestId('retry-page-7'));
    expect(onRetryPage).toHaveBeenCalledWith(7);
  });

  it('shows "Retrying..." and disables every retry button while one page is mid-retry', () => {
    const job = jobWithPages([
      { page: 1, text: 'x', status: 'failed', confidence_pct: 0, confidence_tier: 'failed' },
      { page: 2, text: 'x', status: 'review', confidence_pct: 40, confidence_tier: 'needs_review' },
    ]);
    render(<ViewerPanel {...baseProps} jobDetails={job} onRetryPage={vi.fn()} retryingPage={1} />);
    expect(screen.getByTestId('retry-page-1')).toHaveTextContent('Retrying...');
    expect(screen.getByTestId('retry-page-1')).toBeDisabled();
    expect(screen.getByTestId('retry-page-2')).toBeDisabled();
  });
});

describe('ViewerPanel — bulk "Retry Pages" toolbar button + modal', () => {
  it('clicking the toolbar button opens the retry modal instead of calling onRetryPages directly', () => {
    const onRetryPages = vi.fn();
    const job = jobWithPages([{ page: 1, text: 'x', status: 'success', confidence_pct: 95, confidence_tier: 'highly_confident' }]);
    job.status = 'processing';
    render(<ViewerPanel {...baseProps} jobDetails={job} onRetryPages={onRetryPages} />);
    expect(screen.queryByTestId('retry-modal')).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId('retry-pages-btn'));

    expect(screen.getByTestId('retry-modal')).toBeInTheDocument();
    expect(onRetryPages).not.toHaveBeenCalled();
  });

  it('choosing an option in the modal calls onRetryPages with that scope and closes the modal', () => {
    const onRetryPages = vi.fn();
    const job = jobWithPages([{ page: 1, text: 'x', status: 'failed', confidence_pct: 0, confidence_tier: 'failed' }]);
    job.failed_pages = 1;
    job.review_pages = 0;
    render(<ViewerPanel {...baseProps} jobDetails={job} onRetryPages={onRetryPages} />);
    fireEvent.click(screen.getByTestId('retry-pages-btn'));

    fireEvent.click(screen.getByTestId('retry-option-failed'));

    expect(onRetryPages).toHaveBeenCalledWith('failed');
    expect(screen.queryByTestId('retry-modal')).not.toBeInTheDocument();
  });
});

describe('ViewerPanel — loading placeholder while switching jobs', () => {
  it('shows "Loading document..." when jobDetails is null but a job is selected', () => {
    render(<ViewerPanel {...baseProps} jobDetails={null} hasSelection={true} />);
    expect(screen.getByText('Loading document...')).toBeInTheDocument();
  });

  it('still shows the "select a document" placeholder when nothing is selected at all', () => {
    render(<ViewerPanel {...baseProps} jobDetails={null} hasSelection={false} />);
    expect(screen.getByText(/Select a document or upload a new file/)).toBeInTheDocument();
    expect(screen.queryByText('Loading document...')).not.toBeInTheDocument();
  });

  it('still shows "Waiting for first page..." for a real job with no pages yet (not "Loading document...")', () => {
    render(<ViewerPanel {...baseProps} jobDetails={{ status: 'pending', pages_data: [] }} hasSelection={true} />);
    expect(screen.getByText('Waiting for first page...')).toBeInTheDocument();
  });

  it('still shows "Scanning..." for a processing job with no pages yet', () => {
    render(<ViewerPanel {...baseProps} jobDetails={{ status: 'processing', pages_data: [] }} hasSelection={true} />);
    expect(screen.getByText('Scanning...')).toBeInTheDocument();
  });
});

describe('ViewerPanel — completed progress label', () => {
  it('shows a flat page count when nothing failed', () => {
    const job = jobWithPages([{ page: 1, text: 'x', status: 'success' }]);
    job.status = 'completed';
    job.total_pages = 40;
    job.failed_pages = 0;
    render(<ViewerPanel {...baseProps} jobDetails={job} />);
    expect(screen.getByText('Done (40 pages)')).toBeInTheDocument();
  });

  it('breaks out passed vs failed counts when some pages failed', () => {
    const job = jobWithPages([{ page: 1, text: 'x', status: 'failed' }]);
    job.status = 'completed';
    job.total_pages = 40;
    job.failed_pages = 5;
    render(<ViewerPanel {...baseProps} jobDetails={job} />);
    expect(screen.getByText('Done (35 passed, 5 failed)')).toBeInTheDocument();
  });
});

describe('ViewerPanel — Rebuild Cross-References button', () => {
  it('is shown and enabled for a completed job, and calls onReindexReferences when clicked', () => {
    const onReindexReferences = vi.fn();
    const job = jobWithPages([{ page: 1, text: 'x', status: 'success' }]);
    render(<ViewerPanel {...baseProps} jobDetails={job} onReindexReferences={onReindexReferences} />);
    const btn = screen.getByTestId('reindex-references-btn');
    expect(btn).toBeInTheDocument();
    expect(btn).not.toBeDisabled();
    fireEvent.click(btn);
    expect(onReindexReferences).toHaveBeenCalledTimes(1);
  });

  it('is hidden while the job is actively processing or escalating', () => {
    const processingJob = jobWithPages([{ page: 1, text: 'x' }]);
    processingJob.status = 'processing';
    const { rerender } = render(<ViewerPanel {...baseProps} jobDetails={processingJob} onReindexReferences={vi.fn()} />);
    expect(screen.queryByTestId('reindex-references-btn')).not.toBeInTheDocument();

    const escalatingJob = jobWithPages([{ page: 1, text: 'x' }]);
    escalatingJob.status = 'escalating';
    rerender(<ViewerPanel {...baseProps} jobDetails={escalatingJob} onReindexReferences={vi.fn()} />);
    expect(screen.queryByTestId('reindex-references-btn')).not.toBeInTheDocument();
  });

  it('shows "Rebuilding…" and disables itself while a rebuild is in flight', () => {
    const job = jobWithPages([{ page: 1, text: 'x' }]);
    render(<ViewerPanel {...baseProps} jobDetails={job} onReindexReferences={vi.fn()} reindexing />);
    const btn = screen.getByTestId('reindex-references-btn');
    expect(btn).toHaveTextContent('Rebuilding…');
    expect(btn).toBeDisabled();
  });
});

describe('ViewerPanel — sidebar collapse toggle', () => {
  it('renders the toggle even when there is no selection (it is a layout control, not a document action)', () => {
    render(<ViewerPanel {...baseProps} hasSelection={false} />);
    expect(screen.getByTestId('sidebar-toggle-btn')).toBeInTheDocument();
    expect(screen.queryByText('PDF')).not.toBeInTheDocument(); // download-group is hidden without a selection
  });

  it('calls onToggleSidebar exactly once when clicked', () => {
    const onToggleSidebar = vi.fn();
    render(<ViewerPanel {...baseProps} onToggleSidebar={onToggleSidebar} />);
    fireEvent.click(screen.getByTestId('sidebar-toggle-btn'));
    expect(onToggleSidebar).toHaveBeenCalledTimes(1);
  });

  it('shows a "hide" icon/title when the sidebar is expanded, and a "show" icon/title when collapsed', () => {
    const { rerender } = render(<ViewerPanel {...baseProps} sidebarCollapsed={false} />);
    let btn = screen.getByTestId('sidebar-toggle-btn');
    expect(btn.title).toBe('Hide sidebar');
    expect(btn.querySelector('i.fa-angles-left')).toBeTruthy();

    rerender(<ViewerPanel {...baseProps} sidebarCollapsed={true} />);
    btn = screen.getByTestId('sidebar-toggle-btn');
    expect(btn.title).toBe('Show sidebar');
    expect(btn.querySelector('i.fa-angles-right')).toBeTruthy();
  });
});

describe('ViewerPanel — Result pane is display-only', () => {
  it('clicking a rendered .xref-link in the Result pane is inert (no navigation, no popover)', () => {
    // The PDF pane's reference badge/popover (the only thing that ever
    // turned a mention click into a jump or a disambiguation popover) was
    // removed as part of the pdf.js migration — the Structure sidebar is
    // now the primary way to reach a reference. This just confirms a
    // Result-pane link click doesn't do anything surprising (e.g. navigate
    // via its href="#"), not that it opens anything.
    const store = storeWithDelta({ 1: { 'clause:8': [{ anchor_id: 'a1', collapsed_key: 'clause:8' }] } });
    const job = jobWithPages([{ page: 1, text: 'See Clause 8 for details.' }]);
    render(<ViewerPanel {...baseProps} viewMode="result" jobDetails={job} referenceStore={store} />);
    const link = screen.getByText('Clause 8');
    expect(link.tagName).toBe('A');
    fireEvent.click(link);
    expect(screen.queryByTestId('xref-popover')).not.toBeInTheDocument();
  });
});

describe('ViewerPanel — PDF pane wiring', () => {
  it('passes the current page and total pages down to PdfCanvasViewer', () => {
    const job = jobWithPages([{ page: 1, text: 'See Clause 8 for details.' }]);
    render(<ViewerPanel {...baseProps} viewMode="split" pdfSrc="blob:fake-url" jobDetails={job} />);
    expect(screen.getByTestId('mock-pdf-page')).toHaveTextContent('1');
  });

  it('onPageChange from PdfCanvasViewer updates the current page', () => {
    const job = jobWithPages([
      { page: 1, text: 'See Clause 8 for details.' },
      { page: 2, text: 'See Ground 1 above.' },
    ]);
    render(<ViewerPanel {...baseProps} viewMode="split" pdfSrc="blob:fake-url" jobDetails={job} />);
    fireEvent.click(screen.getByTestId('mock-next-page'));
    expect(screen.getByTestId('mock-pdf-page')).toHaveTextContent('2');
  });
});

describe('ViewerPanel — Structure sidebar (Part C2)', () => {
  it('is collapsed (not rendered) by default', () => {
    render(<ViewerPanel {...baseProps} viewMode="split" jobDetails={jobWithPages([{ page: 1, text: 'x' }])} />);
    expect(screen.queryByTestId('structure-sidebar')).not.toBeInTheDocument();
  });

  it('toggles open via its own toolbar button, alongside whichever of Split/PDF/Result is active — not a competing mode', () => {
    render(<ViewerPanel {...baseProps} viewMode="result" jobDetails={jobWithPages([{ page: 1, text: 'x' }])} />);
    fireEvent.click(screen.getByTestId('structure-sidebar-toggle-btn'));
    expect(screen.getByTestId('structure-sidebar')).toBeInTheDocument();
    // The Result pane is still showing alongside it, not replaced by it.
    expect(screen.getByText('x')).toBeInTheDocument();
  });

  it('passes the current pdf.js-reported page down to the Structure tree as activePage', () => {
    const store = storeWithDelta({
      1: {
        'clause:4.1': [{
          anchor_id: 'clause-leaf', collapsed_key: 'clause:4.1', key: 'clause:4.1', page: 1,
          scope_chain: [{ family: 'annexure', key: 'annexure:a-2', label: 'Annexure A-2' }],
        }],
      },
    });
    const job = jobWithPages([{ page: 1, text: 'x' }]);
    render(
      <ViewerPanel
        {...baseProps}
        viewMode="split"
        pdfSrc="blob:fake-url"
        jobDetails={job}
        referenceStore={store}
        scopeRanges={[{ key: 'annexure:a-2', scope_key: '', depth: 0, start_page: 1, end_page: 500 }]}
      />
    );
    fireEvent.click(screen.getByTestId('structure-sidebar-toggle-btn'));
    // pdf.js reports page 1 by default (no navigation yet) — the Annexure's
    // range covers page 1, so it should already be marked current.
    expect(screen.getByTestId('structure-node-root/annexure:a-2').className).toContain('structure-tree-node--current');
  });
});

describe('ViewerPanel — Structure sidebar leaf jump-to-page', () => {
  it('jumps the PDF immediately even when the target page is far outside the currently-loaded pages_data window', () => {
    const store = storeWithDelta({
      800: {
        'clause:9.1': [{ anchor_id: 'far-leaf', collapsed_key: 'clause:9.1', key: 'clause:9.1', page: 800, scope_chain: [] }],
      },
    });
    // Only page 1 is loaded — the ±15-page windowed fetch on a 900+ page
    // document would never include page 800 while the reader is on page 1.
    const job = { ...jobWithPages([{ page: 1, text: 'x' }]), total_pages: 900 };
    render(
      <ViewerPanel
        {...baseProps}
        viewMode="split"
        pdfSrc="blob:full-doc"
        jobDetails={job}
        referenceStore={store}
        scopeRanges={[]}
      />
    );
    fireEvent.click(screen.getByTestId('structure-sidebar-toggle-btn'));
    fireEvent.click(screen.getByTestId('structure-leaf-far-leaf'));
    expect(screen.getByTestId('mock-pdf-page')).toHaveTextContent('800');
  });

  it('does not force-switch view mode — clicking a leaf in PDF-only mode jumps the page and stays in PDF-only mode', () => {
    const store = storeWithDelta({
      5: {
        'clause:2.1': [{ anchor_id: 'pdf-only-leaf', collapsed_key: 'clause:2.1', key: 'clause:2.1', page: 5, scope_chain: [] }],
      },
    });
    const setViewMode = vi.fn();
    const job = jobWithPages([{ page: 1, text: 'x' }]);
    render(
      <ViewerPanel
        {...baseProps}
        viewMode="pdf"
        setViewMode={setViewMode}
        pdfSrc="blob:full-doc"
        jobDetails={job}
        referenceStore={store}
        scopeRanges={[]}
      />
    );
    fireEvent.click(screen.getByTestId('structure-sidebar-toggle-btn'));
    fireEvent.click(screen.getByTestId('structure-leaf-pdf-only-leaf'));
    expect(screen.getByTestId('mock-pdf-page')).toHaveTextContent('5');
    expect(setViewMode).not.toHaveBeenCalled();
  });
});

describe('ViewerPanel — reference-triggered chunk view (Part C3)', () => {
  it('renders no chunk banner and passes the full pdfSrc/totalPages through when chunkView is absent', () => {
    const job = jobWithPages([{ page: 1, text: 'x' }]);
    render(<ViewerPanel {...baseProps} viewMode="split" pdfSrc="blob:full-doc" jobDetails={{ ...job, total_pages: 900 }} />);
    expect(screen.queryByTestId('chunk-view-banner')).not.toBeInTheDocument();
    expect(screen.getByTestId('mock-pdf-page')).toHaveTextContent('1');
  });

  it('clicking a Structure container chunk-jump button calls onSelectChunk with the container\'s page span', () => {
    const store = storeWithDelta({
      427: {
        'annexure:a-2': [{ anchor_id: 'a-leaf', collapsed_key: 'annexure:a-2', key: 'annexure:a-2', page: 427, scope_chain: [] }],
      },
    });
    const onSelectChunk = vi.fn();
    render(
      <ViewerPanel
        {...baseProps}
        viewMode="split"
        pdfSrc="blob:full-doc"
        jobDetails={jobWithPages([{ page: 427, text: 'x' }])}
        referenceStore={store}
        scopeRanges={[{ key: 'annexure:a-2', scope_key: '', depth: 0, start_page: 427, end_page: 446 }]}
        onSelectChunk={onSelectChunk}
      />
    );
    fireEvent.click(screen.getByTestId('structure-sidebar-toggle-btn'));
    fireEvent.click(screen.getByTestId('structure-jump-root/annexure:a-2'));
    expect(onSelectChunk).toHaveBeenCalledWith({ startPage: 427, endPage: 446, label: 'Annexure A-2' });
  });

  it('while a chunkView is set, the PDF pane gets the chunk pdfUrl/locally-translated page, the Result pane reads chunk pagesData, and a banner offers a way back', () => {
    const chunkView = {
      startPage: 427, endPage: 429, label: 'Annexure A-2',
      pdfUrl: 'blob:chunk-url',
      pagesData: [{ page: 428, text: 'Chunk page text' }],
    };
    const onExitChunk = vi.fn();
    render(
      <ViewerPanel
        {...baseProps}
        viewMode="split"
        pdfSrc="blob:full-doc"
        jobDetails={{ ...jobWithPages([{ page: 1, text: 'main doc text' }]), total_pages: 900 }}
        chunkView={chunkView}
        onExitChunk={onExitChunk}
      />
    );

    expect(screen.getByTestId('chunk-view-banner')).toHaveTextContent('Annexure A-2');
    expect(screen.getByTestId('chunk-view-banner')).toHaveTextContent('427');
    expect(screen.getByTestId('chunk-view-banner')).toHaveTextContent('429');

    // The PDF pane is pointed at the chunk's own small PDF, not the main doc.
    // ViewerPanel starts at global page 1 by default, which is before the
    // chunk's own span — clamped to local page 1 (the chunk's first page).
    expect(screen.getByTestId('mock-pdf-page')).toHaveTextContent('1');

    // The Result pane renders the chunk's own pagesData, not the main
    // windowed pages_data ("main doc text" would come from the latter).
    expect(screen.getByText('Chunk page text')).toBeInTheDocument();
    expect(screen.queryByText('main doc text')).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId('exit-chunk-btn'));
    expect(onExitChunk).toHaveBeenCalledTimes(1);
  });

  it('translates a local PdfCanvasViewer page change back to a global page', () => {
    const chunkView = {
      startPage: 427, endPage: 429, label: 'Annexure A-2',
      pdfUrl: 'blob:chunk-url',
      pagesData: [{ page: 427, text: 'p427' }, { page: 428, text: 'p428' }],
    };
    render(
      <ViewerPanel
        {...baseProps}
        viewMode="split"
        pdfSrc="blob:full-doc"
        jobDetails={{ ...jobWithPages([{ page: 1, text: 'x' }]), total_pages: 900 }}
        chunkView={chunkView}
      />
    );
    // mock-next-page fires onPageChange(pageNum + 1) — local page 1 -> 2,
    // which should translate to the chunk's global page 428.
    fireEvent.click(screen.getByTestId('mock-next-page'));
    expect(screen.getByText('p428')).toBeInTheDocument();
  });
});
