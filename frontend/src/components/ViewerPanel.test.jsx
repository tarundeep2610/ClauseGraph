import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import ViewerPanel from './ViewerPanel';
import { createReferenceStore, mergeDelta } from '../utils/referenceStore';
import { getPageMentions } from '../utils/markdown';

// Defaults to admin so every existing test below (which predates role-gating
// and asserts on retry wiring, not on who can see it) keeps working
// unchanged. The dedicated "Retry controls are admin-gated" suite further
// down overrides this per-test to isAdmin: false.
let mockAuth = { isAdmin: true };
vi.mock('../utils/AuthContext', () => ({
  useAuth: () => mockAuth,
}));

beforeEach(() => {
  mockAuth = { isAdmin: true };
});

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

// Wraps the real getPageMentions in a spy (not a stub — ViewerPanel's badge
// rendering still needs real mentions) purely so the memoization test below
// can assert it isn't called more often than the page/store actually change.
vi.mock('../utils/markdown', async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, getPageMentions: vi.fn(actual.getPageMentions) };
});

function storeWithDelta(changedPages, version = 1, changedMentions = {}) {
  const store = createReferenceStore();
  mergeDelta(store, { reference_index_version: version, changed_pages: changedPages, changed_mentions: changedMentions });
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
  onSavePageText: vi.fn(),
  onExport: vi.fn(),
  sidebarCollapsed: false,
  onToggleSidebar: vi.fn(),
};

function jobWithPages(pages) {
  // reference_index_version defaults to "already built" (> 0) so every
  // existing test below (written before citation badges became gated on it)
  // keeps exercising PageReferenceBadges unchanged — the dedicated
  // "on-demand indexing" describe block further down explicitly sets it to 0
  // to test the gated-off default state. This is the real gating signal
  // (ViewerPanel.jsx's `indexBuilt`) — NOT index_built_at, which is only
  // ever stamped by the explicit rebuild endpoint and stays null forever for
  // a job indexed under the old automatic architecture, even though it has
  // real data (the round-7 bug fix this file's "stale gating signal" tests
  // cover). index_built_at is still included here for realism/parity with a
  // real API response, but nothing in ViewerPanel should read it anymore.
  return {
    status: 'completed', total_pages: pages.length, processed_pages: pages.length, pages_data: pages,
    reference_index_version: 1, index_built_at: '2026-01-01T00:00:00Z',
  };
}

describe('ViewerPanel — per-page confidence badges', () => {
  it('renders only the current page as a block, and switches to the new page (and away from the old one) on navigation', () => {
    const job = jobWithPages([
      { page: 1, text: 'First page text', confidence_pct: 95, confidence_tier: 'highly_confident' },
      { page: 2, text: 'Second page text', confidence_pct: 40, confidence_tier: 'needs_review' },
    ]);
    render(<ViewerPanel {...baseProps} viewMode="split" pdfSrc="blob:fake-url" jobDetails={job} />);
    expect(screen.getByTestId('page-block-1')).toBeInTheDocument();
    expect(screen.queryByTestId('page-block-2')).not.toBeInTheDocument();
    expect(screen.getByText('First page text')).toBeInTheDocument();
    expect(screen.queryByText('Second page text')).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId('mock-next-page'));

    expect(screen.queryByTestId('page-block-1')).not.toBeInTheDocument();
    expect(screen.getByTestId('page-block-2')).toBeInTheDocument();
    expect(screen.getByText('Second page text')).toBeInTheDocument();
    expect(screen.queryByText('First page text')).not.toBeInTheDocument();
  });

  it('shows the confidence percentage and tier label for a scored page', () => {
    const job = jobWithPages([{ page: 1, text: 'x', confidence_pct: 92, confidence_tier: 'very_confident' }]);
    render(<ViewerPanel {...baseProps} jobDetails={job} />);
    expect(screen.getByTestId('confidence-badge-1')).toHaveTextContent('92% · Very Confident');
  });

  it('shows no percentage for the blank_page and failed special-case tiers', () => {
    const job = jobWithPages([{ page: 1, text: '', confidence_pct: 100, confidence_tier: 'blank_page' }]);
    render(<ViewerPanel {...baseProps} jobDetails={job} />);
    expect(screen.getByTestId('confidence-badge-1')).toHaveTextContent('Blank Page');
    expect(screen.getByTestId('confidence-badge-1')).not.toHaveTextContent('%');
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

  it('shows a per-page loading placeholder (not the job-level placeholder) when the current page is not yet in the loaded window', () => {
    const job = jobWithPages([{ page: 5, text: 'only page 5 loaded' }]);
    render(<ViewerPanel {...baseProps} jobDetails={job} />);
    // pdfPageOverride defaults to null -> currentPdfPage 1, which isn't loaded.
    expect(screen.getByText('Loading page 1…')).toBeInTheDocument();
    expect(screen.queryByText('only page 5 loaded')).not.toBeInTheDocument();
  });
});

describe('ViewerPanel — formatted_text (layout-aware rendering) fallback', () => {
  it('renders formatted_text instead of text when present', () => {
    const job = jobWithPages([
      { page: 1, text: '3.3 TIME SCHEDULE', formatted_text: '## 3.3 TIME SCHEDULE' },
    ]);
    render(<ViewerPanel {...baseProps} jobDetails={job} />);
    expect(screen.getByRole('heading', { name: '3.3 TIME SCHEDULE' })).toBeInTheDocument();
  });

  it('falls back to plain text when formatted_text is null (non-grounded page)', () => {
    const job = jobWithPages([
      { page: 1, text: 'Plain ungrounded page text', formatted_text: null },
    ]);
    render(<ViewerPanel {...baseProps} jobDetails={job} />);
    expect(screen.getByText('Plain ungrounded page text')).toBeInTheDocument();
    // Markdown is rendered as a plain <p>, not promoted to a heading — the
    // page's own "Page 1" label (an <h3> in the component's chrome) is
    // unrelated to this and still present regardless.
    expect(screen.getByText('Plain ungrounded page text').tagName).toBe('P');
  });

  it('falls back to plain text when formatted_text is absent entirely', () => {
    const job = jobWithPages([{ page: 1, text: 'Page with no formatted_text key at all' }]);
    render(<ViewerPanel {...baseProps} jobDetails={job} />);
    expect(screen.getByText('Page with no formatted_text key at all')).toBeInTheDocument();
  });

  // REWRITTEN (premise obsolete): the Result pane is now the editable
  // WysiwygEditor, which parses formatted_text fresh from Markdown via
  // TipTap rather than through renderPageMarkdown's annotated HTML — so the
  // `data-xref-key` inline mention-links renderPageMarkdown used to inject
  // no longer appear in this pane. PageReferenceBadges (top-of-pane citation
  // pills, see the "on-page reference badges" describe block) is the
  // surviving mechanism for reference discovery, per the Studio redesign.
  it('no longer linkifies docref/term mentions inline — the editor shows the real text plainly, and PageReferenceBadges surfaces the same mentions instead', () => {
    const store = storeWithDelta(
      {
        1: {
          'term:contractor': [{ anchor_id: 't-contractor', collapsed_key: 'term:contractor', page: 1 }],
          'docref:rec064': [{ anchor_id: 't-rec064', collapsed_key: 'docref:rec064', page: 1 }],
        },
      },
      1,
      {
        1: [
          { key: 'docref:rec064', raw_text: 'Technical Specification No. REC-064', resolution: 'exact' },
          { key: 'term:contractor', raw_text: 'Contractor', resolution: 'exact' },
        ],
      }
    );
    const job = jobWithPages([
      {
        page: 1,
        text: 'Plain fallback',
        formatted_text: '## Technical Specification No. REC-064\n\n**Contractor** obligations.',
      },
    ]);
    render(<ViewerPanel {...baseProps} viewMode="split" pdfSrc="blob:fake-url" jobDetails={job} referenceStore={store} scopeRanges={[]} />);

    // The editor still shows formatted_text's real content...
    expect(screen.getByRole('heading', { name: 'Technical Specification No. REC-064' })).toBeInTheDocument();
    // ...but with no inline xref-link anchors any more.
    expect(document.querySelector('a[data-xref-key]')).not.toBeInTheDocument();

    // PageReferenceBadges (not an inline link) is what surfaces these mentions now.
    expect(screen.getByTestId('page-reference-badge-docref:rec064')).toBeInTheDocument();
    expect(screen.getByTestId('page-reference-badge-term:contractor')).toBeInTheDocument();
  });
});

describe('ViewerPanel — per-page Retry button', () => {
  it('shows Retry for the failed/review page, not for success/pending/processing, as the reader navigates through them', () => {
    const job = jobWithPages([
      { page: 1, text: 'x', status: 'failed', confidence_pct: 0, confidence_tier: 'failed' },
      { page: 2, text: 'x', status: 'review', confidence_pct: 40, confidence_tier: 'needs_review' },
      { page: 3, text: 'x', status: 'success', confidence_pct: 95, confidence_tier: 'highly_confident' },
      { page: 4, text: '', status: 'pending' },
      { page: 5, text: '', status: 'processing' },
    ]);
    render(<ViewerPanel {...baseProps} viewMode="split" pdfSrc="blob:fake-url" jobDetails={job} onRetryPage={vi.fn()} retryingPage={null} />);
    expect(screen.getByTestId('retry-page-1')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('mock-next-page'));
    expect(screen.getByTestId('retry-page-2')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('mock-next-page'));
    expect(screen.queryByTestId('retry-page-3')).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId('mock-next-page'));
    expect(screen.queryByTestId('retry-page-4')).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId('mock-next-page'));
    expect(screen.queryByTestId('retry-page-5')).not.toBeInTheDocument();
  });

  it('calls onRetryPage with that page number when clicked', () => {
    const onRetryPage = vi.fn();
    const job = jobWithPages([{ page: 1, text: 'x', status: 'failed', confidence_pct: 0, confidence_tier: 'failed' }]);
    render(<ViewerPanel {...baseProps} jobDetails={job} onRetryPage={onRetryPage} retryingPage={null} />);
    fireEvent.click(screen.getByTestId('retry-page-1'));
    expect(onRetryPage).toHaveBeenCalledWith(1);
  });

  it('shows "Retrying..." and disables the retry button for the page being retried, and disables the current page\'s retry button while any retry is in flight', () => {
    const job = jobWithPages([
      { page: 1, text: 'x', status: 'failed', confidence_pct: 0, confidence_tier: 'failed' },
      { page: 2, text: 'x', status: 'review', confidence_pct: 40, confidence_tier: 'needs_review' },
    ]);
    render(<ViewerPanel {...baseProps} viewMode="split" pdfSrc="blob:fake-url" jobDetails={job} onRetryPage={vi.fn()} retryingPage={1} />);
    expect(screen.getByTestId('retry-page-1')).toHaveTextContent('Retrying...');
    expect(screen.getByTestId('retry-page-1')).toBeDisabled();

    fireEvent.click(screen.getByTestId('mock-next-page'));
    expect(screen.getByTestId('retry-page-2')).toBeDisabled();
    expect(screen.getByTestId('retry-page-2')).not.toHaveTextContent('Retrying...');
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

describe('ViewerPanel — Retry controls are admin-gated', () => {
  it('hides the per-page Retry button for a non-admin user even on a failed/review page', () => {
    mockAuth = { isAdmin: false };
    const job = jobWithPages([{ page: 1, text: 'x', status: 'failed', confidence_pct: 0, confidence_tier: 'failed' }]);
    render(<ViewerPanel {...baseProps} jobDetails={job} onRetryPage={vi.fn()} retryingPage={null} />);
    expect(screen.queryByTestId('retry-page-1')).not.toBeInTheDocument();
  });

  it('shows the per-page Retry button for an admin on a failed/review page', () => {
    mockAuth = { isAdmin: true };
    const job = jobWithPages([{ page: 1, text: 'x', status: 'failed', confidence_pct: 0, confidence_tier: 'failed' }]);
    render(<ViewerPanel {...baseProps} jobDetails={job} onRetryPage={vi.fn()} retryingPage={null} />);
    expect(screen.getByTestId('retry-page-1')).toBeInTheDocument();
  });

  it('hides the bulk "Retry Pages" toolbar button for a non-admin user', () => {
    mockAuth = { isAdmin: false };
    const job = jobWithPages([{ page: 1, text: 'x', status: 'success' }]);
    render(<ViewerPanel {...baseProps} jobDetails={job} onRetryPages={vi.fn()} />);
    expect(screen.queryByTestId('retry-pages-btn')).not.toBeInTheDocument();
  });

  it('shows the bulk "Retry Pages" toolbar button for an admin', () => {
    mockAuth = { isAdmin: true };
    const job = jobWithPages([{ page: 1, text: 'x', status: 'success' }]);
    render(<ViewerPanel {...baseProps} jobDetails={job} onRetryPages={vi.fn()} />);
    expect(screen.getByTestId('retry-pages-btn')).toBeInTheDocument();
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

describe('ViewerPanel — Build/Rebuild Index & Reference Links button', () => {
  it('is shown and enabled for a completed job, and builds directly (no modal) when no index exists yet', () => {
    const onReindexReferences = vi.fn();
    const job = jobWithPages([{ page: 1, text: 'x', status: 'success' }]);
    job.reference_index_version = 0;
    render(<ViewerPanel {...baseProps} jobDetails={job} onReindexReferences={onReindexReferences} onBuildReferenceLinksOnly={vi.fn()} />);
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

describe('ViewerPanel — Export Document (ExportDropdown replaces the old "Result (.md)" button)', () => {
  it('renders the ExportDropdown toggle instead of the old single-format button, and forwards onExport with the chosen options', () => {
    const onExport = vi.fn();
    const job = jobWithPages([{ page: 1, text: 'x' }]);
    job.total_pages = 12;
    render(<ViewerPanel {...baseProps} jobDetails={job} onExport={onExport} />);

    expect(screen.queryByText('Result (.md)')).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId('export-dropdown-toggle'));
    fireEvent.click(screen.getByTestId('export-format-pdf'));
    fireEvent.click(screen.getByLabelText(/Current Page Only/));
    fireEvent.click(screen.getByTestId('export-confirm-btn'));

    expect(onExport).toHaveBeenCalledWith({ format: 'pdf', scope: 'page', includeMetadata: true, pageNum: 1, pageRange: undefined });
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

// REWRITTEN (premise obsolete): the Result pane is no longer display-only —
// the Studio redesign (plan §2) replaces it with the editable WysiwygEditor
// as the general user's primary review action. These tests replace the old
// "display-only" suite with coverage of the pane actually being editable,
// plus confirmation that reference discovery is PageReferenceBadges' job now
// (not an inline link inside the editor).
describe('ViewerPanel — Result pane is editable (WysiwygEditor)', () => {
  it('renders the page text in an editable rich-text editor, not a read-only block', () => {
    const job = jobWithPages([{ page: 1, text: 'See Clause 8 for details.' }]);
    render(<ViewerPanel {...baseProps} viewMode="result" jobDetails={job} />);
    const editable = document.querySelector('[contenteditable]');
    expect(editable).toBeInTheDocument();
    expect(editable).toHaveAttribute('contenteditable', 'true');
    expect(screen.getByText('See Clause 8 for details.')).toBeInTheDocument();
  });

  it('calls onSavePageText with the current page number and the edited Markdown, debounced ~800ms after the user stops typing', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const onSavePageText = vi.fn();
      const job = jobWithPages([{ page: 1, text: 'Original text' }]);
      render(<ViewerPanel {...baseProps} viewMode="result" jobDetails={job} onSavePageText={onSavePageText} />);

      // Edit via the Source toggle (a raw-Markdown textarea) — the same
      // edit path WysiwygEditor's own test suite uses, since jsdom has no
      // real caret/selection model to type into the rich-text view directly.
      fireEvent.click(screen.getByTestId('editor-source-toggle-btn'));
      fireEvent.change(screen.getByTestId('editor-source-textarea'), { target: { value: 'Edited text' } });
      fireEvent.click(screen.getByTestId('editor-source-toggle-btn'));

      await act(async () => { await vi.advanceTimersByTimeAsync(900); });
      expect(onSavePageText).toHaveBeenCalledWith(1, 'Edited text');
    } finally {
      vi.useRealTimers();
    }
  });

  it('mention discovery is PageReferenceBadges\' job now, not an inline .xref-link inside the editor', () => {
    const store = storeWithDelta(
      { 1: { 'clause:8': [{ anchor_id: 'a1', collapsed_key: 'clause:8', page: 1 }] } },
      1,
      { 1: [{ key: 'clause:8', raw_text: 'Clause 8', resolution: 'exact' }] }
    );
    const job = jobWithPages([{ page: 1, text: 'See Clause 8 for details.' }]);
    render(<ViewerPanel {...baseProps} viewMode="split" pdfSrc="blob:fake-url" jobDetails={job} referenceStore={store} scopeRanges={[]} />);

    expect(screen.getByText('See Clause 8 for details.')).toBeInTheDocument();
    expect(document.querySelector('a.xref-link')).not.toBeInTheDocument();
    expect(screen.getByTestId('page-reference-badge-clause:8')).toBeInTheDocument();
  });
});

describe('ViewerPanel — on-demand indexing (Zero Indexing Overhead by Default)', () => {
  it('hides PageReferenceBadges and labels the action "Build Index & Reference Links" before the index has ever been built', () => {
    const store = storeWithDelta(
      { 1: { 'clause:8': [{ anchor_id: 'a1', collapsed_key: 'clause:8', page: 1 }] } },
      1,
      { 1: [{ key: 'clause:8', raw_text: 'Clause 8', resolution: 'exact' }] }
    );
    const job = jobWithPages([{ page: 1, text: 'See Clause 8 for details.' }]);
    job.reference_index_version = 0;
    render(
      <ViewerPanel {...baseProps} viewMode="split" pdfSrc="blob:fake-url" jobDetails={job} referenceStore={store} scopeRanges={[]} onReindexReferences={vi.fn()} />
    );

    // Even with referenceStore/scopeRanges data passed in directly (as this
    // test does, bypassing the real fetch), badges must not surface until
    // the index has actually been built (reference_index_version > 0) — that
    // data existing is not the same as the user having asked for reference
    // links. In the real app, post round-6, this data wouldn't exist yet
    // either: indexing is fully opt-in now, nothing runs automatically
    // during OCR.
    expect(screen.queryByTestId('page-reference-badge-clause:8')).not.toBeInTheDocument();

    const btn = screen.getByTestId('reindex-references-btn');
    expect(btn).toHaveTextContent('Build Index & Reference Links');
    expect(btn).toHaveClass('btn-primary');
  });

  it('shows PageReferenceBadges and labels the action "Rebuild Index & Reference Links" once the index has been built', () => {
    const store = storeWithDelta(
      { 1: { 'clause:8': [{ anchor_id: 'a1', collapsed_key: 'clause:8', page: 1 }] } },
      1,
      { 1: [{ key: 'clause:8', raw_text: 'Clause 8', resolution: 'exact' }] }
    );
    const job = jobWithPages([{ page: 1, text: 'See Clause 8 for details.' }]); // reference_index_version: 1 set by the jobWithPages default
    render(
      <ViewerPanel {...baseProps} viewMode="split" pdfSrc="blob:fake-url" jobDetails={job} referenceStore={store} scopeRanges={[]} onReindexReferences={vi.fn()} />
    );

    expect(screen.getByTestId('page-reference-badge-clause:8')).toBeInTheDocument();
    const btn = screen.getByTestId('reindex-references-btn');
    expect(btn).toHaveTextContent('Rebuild Index & Reference Links');
    expect(btn).toHaveClass('btn-secondary');
  });

  // Regression coverage for the exact bug the user reported: a job indexed
  // under the old (removed) automatic architecture has real data —
  // reference_index_version > 0 — but index_built_at permanently null, since
  // that column is only ever stamped by the explicit rebuild endpoint and
  // was never backfilled. Badges/Outline must key off reference_index_version,
  // not index_built_at, or every pre-existing job wrongly looks un-indexed.
  it('treats a job with reference_index_version > 0 but index_built_at null as already indexed (the old-job gating bug)', () => {
    const store = storeWithDelta(
      { 1: { 'clause:8': [{ anchor_id: 'a1', collapsed_key: 'clause:8', page: 1 }] } },
      1,
      { 1: [{ key: 'clause:8', raw_text: 'Clause 8', resolution: 'exact' }] }
    );
    const job = jobWithPages([{ page: 1, text: 'See Clause 8 for details.' }]);
    job.index_built_at = null; // never stamped — old automatic architecture
    job.reference_index_version = 3; // but real data exists
    render(
      <ViewerPanel {...baseProps} studioMode={true} viewMode="split" pdfSrc="blob:fake-url" jobDetails={job} referenceStore={store} scopeRanges={[]} onReindexReferences={vi.fn()} />
    );

    expect(screen.getByTestId('page-reference-badge-clause:8')).toBeInTheDocument();
    expect(screen.getByTestId('reindex-references-btn')).toHaveTextContent('Rebuild Index & Reference Links');
    expect(screen.getByTestId('structure-sidebar-toggle-btn')).not.toHaveClass('disabled');
  });

  // The Outline rail button only exists in the real, studioMode-rendered
  // shell (StudioRail.jsx) — the legacy, non-studio branch exercised by the
  // tests above (dead code in production, kept for its own test coverage)
  // has a different, ungated toolbar button instead.
  it('disables the Outline rail button and shows a toast instead of opening the drawer when no index has been built', () => {
    const job = jobWithPages([{ page: 1, text: 'See Clause 8 for details.' }]);
    job.reference_index_version = 0;
    render(
      <ViewerPanel {...baseProps} studioMode={true} viewMode="split" pdfSrc="blob:fake-url" jobDetails={job} referenceStore={null} scopeRanges={[]} onReindexReferences={vi.fn()} />
    );

    expect(screen.getByTestId('structure-sidebar-toggle-btn')).toHaveClass('disabled');
    fireEvent.click(screen.getByTestId('studio-rail-outline-wrap'));
    expect(screen.getByTestId('toast')).toHaveTextContent(/no index built yet/i);
    expect(screen.queryByTestId('structure-sidebar')).not.toBeInTheDocument();
  });

  it('opens the Outline drawer normally once an index has been built, with no toast', () => {
    const job = jobWithPages([{ page: 1, text: 'See Clause 8 for details.' }]);
    render(
      <ViewerPanel {...baseProps} studioMode={true} viewMode="split" pdfSrc="blob:fake-url" jobDetails={job} referenceStore={null} scopeRanges={[]} onReindexReferences={vi.fn()} />
    );

    expect(screen.getByTestId('structure-sidebar-toggle-btn')).not.toHaveClass('disabled');
    fireEvent.click(screen.getByTestId('studio-rail-outline-wrap'));
    expect(screen.queryByTestId('toast')).not.toBeInTheDocument();
    expect(screen.getByTestId('structure-sidebar')).toBeInTheDocument();
  });
});

describe('ViewerPanel — Rebuild Index & Reference Links modal (shown only when an index already exists)', () => {
  it('a never-indexed job builds directly on click, with no modal', () => {
    const onReindexReferences = vi.fn();
    const job = jobWithPages([{ page: 1, text: 'x' }]);
    job.reference_index_version = 0;
    render(<ViewerPanel {...baseProps} jobDetails={job} onReindexReferences={onReindexReferences} onBuildReferenceLinksOnly={vi.fn()} />);

    fireEvent.click(screen.getByTestId('reindex-references-btn'));
    expect(onReindexReferences).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId('rebuild-index-modal')).not.toBeInTheDocument();
  });

  it('a job that already has an index opens the modal instead of rebuilding directly', () => {
    const onReindexReferences = vi.fn();
    const job = jobWithPages([{ page: 1, text: 'x' }]); // reference_index_version: 1 by default
    render(<ViewerPanel {...baseProps} jobDetails={job} onReindexReferences={onReindexReferences} onBuildReferenceLinksOnly={vi.fn()} />);

    fireEvent.click(screen.getByTestId('reindex-references-btn'));
    expect(onReindexReferences).not.toHaveBeenCalled();
    expect(screen.getByTestId('rebuild-index-modal')).toBeInTheDocument();
  });

  it('confirming with "Index" checked (the default) calls onReindexReferences, the full rebuild', () => {
    const onReindexReferences = vi.fn();
    const onBuildReferenceLinksOnly = vi.fn();
    const job = jobWithPages([{ page: 1, text: 'x' }]);
    render(<ViewerPanel {...baseProps} jobDetails={job} onReindexReferences={onReindexReferences} onBuildReferenceLinksOnly={onBuildReferenceLinksOnly} />);

    fireEvent.click(screen.getByTestId('reindex-references-btn'));
    fireEvent.click(screen.getByTestId('rebuild-modal-confirm'));

    expect(onReindexReferences).toHaveBeenCalledTimes(1);
    expect(onBuildReferenceLinksOnly).not.toHaveBeenCalled();
    expect(screen.queryByTestId('rebuild-index-modal')).not.toBeInTheDocument();
  });

  it('unchecking "Index" and confirming with only "Reference Links" calls onBuildReferenceLinksOnly, not the full rebuild', () => {
    const onReindexReferences = vi.fn();
    const onBuildReferenceLinksOnly = vi.fn();
    const job = jobWithPages([{ page: 1, text: 'x' }]);
    render(<ViewerPanel {...baseProps} jobDetails={job} onReindexReferences={onReindexReferences} onBuildReferenceLinksOnly={onBuildReferenceLinksOnly} />);

    fireEvent.click(screen.getByTestId('reindex-references-btn'));
    fireEvent.click(screen.getByTestId('rebuild-checkbox-index'));
    fireEvent.click(screen.getByTestId('rebuild-modal-confirm'));

    expect(onBuildReferenceLinksOnly).toHaveBeenCalledTimes(1);
    expect(onReindexReferences).not.toHaveBeenCalled();
  });

  it('cancelling the modal calls neither handler', () => {
    const onReindexReferences = vi.fn();
    const onBuildReferenceLinksOnly = vi.fn();
    const job = jobWithPages([{ page: 1, text: 'x' }]);
    render(<ViewerPanel {...baseProps} jobDetails={job} onReindexReferences={onReindexReferences} onBuildReferenceLinksOnly={onBuildReferenceLinksOnly} />);

    fireEvent.click(screen.getByTestId('reindex-references-btn'));
    fireEvent.click(screen.getByTestId('rebuild-modal-cancel'));

    expect(screen.queryByTestId('rebuild-index-modal')).not.toBeInTheDocument();
    expect(onReindexReferences).not.toHaveBeenCalled();
    expect(onBuildReferenceLinksOnly).not.toHaveBeenCalled();
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

describe('ViewerPanel — shared PageNavBar keeps both panes in sync', () => {
  it('renders the shared nav bar in every view mode, including Result-only (which had no page nav at all before)', () => {
    const job = jobWithPages([{ page: 1, text: 'x' }]);
    render(<ViewerPanel {...baseProps} viewMode="result" jobDetails={job} />);
    expect(screen.getByTestId('page-nav-bar')).toBeInTheDocument();
  });

  it('moves both the PDF pane and the Result pane together', () => {
    const job = jobWithPages([
      { page: 1, text: 'First page text' },
      { page: 2, text: 'Second page text' },
    ]);
    render(<ViewerPanel {...baseProps} viewMode="split" pdfSrc="blob:fake-url" jobDetails={job} />);
    expect(screen.getByText('First page text')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('page-nav-next'));

    expect(screen.getByTestId('mock-pdf-page')).toHaveTextContent('2');
    expect(screen.getByText('Second page text')).toBeInTheDocument();
    expect(screen.queryByText('First page text')).not.toBeInTheDocument();
  });

  it('jump-to-page clamps an out-of-range value and shows a hint', () => {
    const job = jobWithPages([{ page: 1, text: 'x' }, { page: 2, text: 'y' }]);
    render(<ViewerPanel {...baseProps} viewMode="split" pdfSrc="blob:fake-url" jobDetails={job} />);
    const input = screen.getByTestId('page-nav-jump-input');
    fireEvent.change(input, { target: { value: '99' } });
    fireEvent.click(screen.getByTestId('page-nav-jump-btn'));
    expect(screen.getByTestId('mock-pdf-page')).toHaveTextContent('2');
    expect(screen.getByTestId('page-nav-hint')).toHaveTextContent('Clamped to 2');
  });

  it('disables prev on page 1 and next on the last page', () => {
    const job = jobWithPages([{ page: 1, text: 'x' }]);
    render(<ViewerPanel {...baseProps} viewMode="split" pdfSrc="blob:fake-url" jobDetails={job} />);
    expect(screen.getByTestId('page-nav-prev')).toBeDisabled();
    expect(screen.getByTestId('page-nav-next')).toBeDisabled();
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

describe('ViewerPanel — Structure sidebar leaf click jumps the main panel', () => {
  it('moves the main PDF pane straight to the leaf\'s page, even far outside the loaded window, without opening the reading modal', () => {
    const store = storeWithDelta({
      800: {
        'clause:9.1': [{ anchor_id: 'far-leaf', collapsed_key: 'clause:9.1', key: 'clause:9.1', page: 800, scope_chain: [] }],
      },
    });
    // Only page 1 is loaded — a windowed fetch on a 900+ page document
    // would never include page 800 while the reader is on page 1; the jump
    // itself only needs to move currentPdfPage, exactly like PageNavBar does.
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
    expect(screen.queryByTestId('structure-reading-modal')).not.toBeInTheDocument();
  });

  it('does not force-switch view mode — clicking a leaf in PDF-only mode jumps the page without touching the current view mode', () => {
    const store = storeWithDelta({
      5: {
        'clause:2.1': [{ anchor_id: 'pdf-only-leaf', collapsed_key: 'clause:2.1', key: 'clause:2.1', page: 5, scope_chain: [] }],
      },
    });
    const setViewMode = vi.fn();
    const job = jobWithPages([{ page: 1, text: 'x' }, { page: 5, text: 'page five text' }]);
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
    expect(screen.queryByTestId('structure-reading-modal')).not.toBeInTheDocument();
  });

  // REWRITTEN (premise obsolete): exact in-page anchor scrolling relied on
  // renderPageMarkdown's injectAnchor() stamping an `#xref-anchor` span into
  // the dangerouslySetInnerHTML'd body at the matched block's position. The
  // Result pane's body is now the editable WysiwygEditor, which parses the
  // page's Markdown fresh via TipTap rather than rendering that annotated
  // HTML — so there's no anchor span left for this effect to scroll to. The
  // leaf jump itself (moving the main viewer to the target page) still
  // works unchanged; only the sub-page scroll precision is gone.
  it('still moves the main viewer straight to the leaf\'s page (exact in-page anchor scrolling no longer applies now that the Result pane is the editable WysiwygEditor)', () => {
    const store = storeWithDelta({
      5: {
        'clause:2.1': [{
          anchor_id: 'anchor-leaf', collapsed_key: 'clause:2.1', key: 'clause:2.1', page: 5, scope_chain: [],
          fingerprint: 'Clause 2.1 page five text',
        }],
      },
    });
    const job = jobWithPages([{ page: 1, text: 'x' }, { page: 5, text: 'Clause 2.1 page five text.' }]);
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
    fireEvent.click(screen.getByTestId('structure-leaf-anchor-leaf'));

    expect(screen.getByTestId('mock-pdf-page')).toHaveTextContent('5');
    expect(screen.getByText('Clause 2.1 page five text.')).toBeInTheDocument();
  });

  // Magnifying-glass/jump button is temporarily disabled in StructureTree.jsx — re-enable alongside it.
  it.skip('clicking a leaf\'s magnifying-glass button opens the reading modal for that page without moving the main PDF pane', () => {
    const store = storeWithDelta({
      800: {
        'clause:9.1': [{ anchor_id: 'far-leaf', collapsed_key: 'clause:9.1', key: 'clause:9.1', page: 800, scope_chain: [] }],
      },
    });
    const onRequestStructureContent = vi.fn();
    const job = { ...jobWithPages([{ page: 1, text: 'x' }]), total_pages: 900 };
    render(
      <ViewerPanel
        {...baseProps}
        viewMode="split"
        pdfSrc="blob:full-doc"
        jobDetails={job}
        referenceStore={store}
        scopeRanges={[]}
        onRequestStructureContent={onRequestStructureContent}
      />
    );
    fireEvent.click(screen.getByTestId('structure-sidebar-toggle-btn'));
    fireEvent.click(screen.getByTestId('structure-view-leaf-far-leaf'));

    // Main PDF pane is untouched.
    expect(screen.getByTestId('mock-pdf-page')).toHaveTextContent('1');
    // The reading modal opened for that leaf's page instead.
    expect(screen.getByTestId('structure-reading-modal')).toBeInTheDocument();
    expect(onRequestStructureContent).toHaveBeenCalledWith({ startPage: 800, endPage: 800, label: 'Page 800', anchorId: 'far-leaf' });
  });
});

describe('ViewerPanel — Structure container jump opens the reading modal', () => {
  it('clicking a container row (not its magnifying-glass button) jumps the main PDF pane to the section\'s start page, without opening the modal', () => {
    const store = storeWithDelta({
      427: {
        'annexure:a-2': [{ anchor_id: 'a-leaf', collapsed_key: 'annexure:a-2', key: 'annexure:a-2', page: 427, scope_chain: [] }],
      },
    });
    const job = { ...jobWithPages([{ page: 1, text: 'x' }]), total_pages: 900 };
    render(
      <ViewerPanel
        {...baseProps}
        viewMode="split"
        pdfSrc="blob:full-doc"
        jobDetails={job}
        referenceStore={store}
        scopeRanges={[{ key: 'annexure:a-2', scope_key: '', depth: 0, start_page: 427, end_page: 446 }]}
      />
    );
    fireEvent.click(screen.getByTestId('structure-sidebar-toggle-btn'));
    fireEvent.click(screen.getByText('Annexure A-2'));

    expect(screen.getByTestId('mock-pdf-page')).toHaveTextContent('427');
    expect(screen.queryByTestId('structure-reading-modal')).not.toBeInTheDocument();
  });

  it('renders no reading modal by default', () => {
    const job = jobWithPages([{ page: 1, text: 'x' }]);
    render(<ViewerPanel {...baseProps} viewMode="split" pdfSrc="blob:full-doc" jobDetails={{ ...job, total_pages: 900 }} />);
    expect(screen.queryByTestId('structure-reading-modal')).not.toBeInTheDocument();
    expect(screen.getByTestId('mock-pdf-page')).toHaveTextContent('1');
  });

  // Magnifying-glass/jump button is temporarily disabled in StructureTree.jsx — re-enable alongside it
  // (this is currently the only way the Structure tab opens the reading modal, so every test below
  // that depends on it is skipped too, not just this one).
  it.skip('clicking a Structure container jump button calls onRequestStructureContent with the container\'s page span and opens the modal, without moving the main PDF pane', () => {
    const store = storeWithDelta({
      427: {
        'annexure:a-2': [{ anchor_id: 'a-leaf', collapsed_key: 'annexure:a-2', key: 'annexure:a-2', page: 427, scope_chain: [] }],
      },
    });
    const onRequestStructureContent = vi.fn();
    render(
      <ViewerPanel
        {...baseProps}
        viewMode="split"
        pdfSrc="blob:full-doc"
        jobDetails={jobWithPages([{ page: 1, text: 'x' }])}
        referenceStore={store}
        scopeRanges={[{ key: 'annexure:a-2', scope_key: '', depth: 0, start_page: 427, end_page: 446 }]}
        onRequestStructureContent={onRequestStructureContent}
      />
    );
    fireEvent.click(screen.getByTestId('structure-sidebar-toggle-btn'));
    fireEvent.click(screen.getByTestId('structure-jump-root/annexure:a-2'));

    expect(onRequestStructureContent).toHaveBeenCalledWith({ startPage: 427, endPage: 446, label: 'Annexure A-2' });
    expect(screen.getByTestId('structure-reading-modal')).toBeInTheDocument();
    expect(screen.getByTestId('mock-pdf-page')).toHaveTextContent('1');
  });

  // Depends on the temporarily-disabled magnifying-glass/jump button (see above) to open the modal.
  it.skip('shows a loading state until structureContent resolves to "ready", then renders the PDF+text content', () => {
    const store = storeWithDelta({
      427: { 'annexure:a-2': [{ anchor_id: 'a-leaf', collapsed_key: 'annexure:a-2', key: 'annexure:a-2', page: 427, scope_chain: [] }] },
    });
    const props = {
      ...baseProps,
      viewMode: 'split',
      pdfSrc: 'blob:full-doc',
      jobDetails: jobWithPages([{ page: 1, text: 'x' }]),
      referenceStore: store,
      scopeRanges: [{ key: 'annexure:a-2', scope_key: '', depth: 0, start_page: 427, end_page: 429 }],
    };
    const { rerender } = render(<ViewerPanel {...props} structureContent={null} />);
    fireEvent.click(screen.getByTestId('structure-sidebar-toggle-btn'));
    fireEvent.click(screen.getByTestId('structure-jump-root/annexure:a-2'));
    // No matching content yet — the staleness guard keeps the modal in its
    // loading state.
    expect(screen.getByTestId('structure-reading-modal-loading')).toBeInTheDocument();

    rerender(
      <ViewerPanel
        {...props}
        structureContent={{
          startPage: 427, endPage: 429, label: 'Annexure A-2', status: 'ready',
          pdfUrl: 'blob:chunk-url', pagesData: [{ page: 427, text: 'Chunk page text' }],
        }}
      />
    );
    expect(screen.getByText('Chunk page text')).toBeInTheDocument();
  });

  // Depends on the temporarily-disabled magnifying-glass/jump button (see above) to open the modal.
  it.skip('an error status shows the error message and a working Retry that re-requests the same target', () => {
    const store = storeWithDelta({
      427: { 'annexure:a-2': [{ anchor_id: 'a-leaf', collapsed_key: 'annexure:a-2', key: 'annexure:a-2', page: 427, scope_chain: [] }] },
    });
    const onRequestStructureContent = vi.fn();
    render(
      <ViewerPanel
        {...baseProps}
        viewMode="split"
        pdfSrc="blob:full-doc"
        jobDetails={jobWithPages([{ page: 1, text: 'x' }])}
        referenceStore={store}
        scopeRanges={[{ key: 'annexure:a-2', scope_key: '', depth: 0, start_page: 427, end_page: 429 }]}
        onRequestStructureContent={onRequestStructureContent}
        structureContent={{ startPage: 427, endPage: 429, label: 'Annexure A-2', status: 'error', message: 'Failed to load this section.' }}
      />
    );
    fireEvent.click(screen.getByTestId('structure-sidebar-toggle-btn'));
    fireEvent.click(screen.getByTestId('structure-jump-root/annexure:a-2'));
    expect(screen.getByTestId('structure-reading-modal-error')).toHaveTextContent('Failed to load this section.');

    fireEvent.click(screen.getByTestId('structure-reading-modal-retry'));
    expect(onRequestStructureContent).toHaveBeenLastCalledWith({ startPage: 427, endPage: 429, label: 'Annexure A-2' });
  });

  // Depends on the temporarily-disabled magnifying-glass/jump button (see above) to open the modal.
  it.skip('closing the modal (Escape) leaves the main viewer untouched', () => {
    const store = storeWithDelta({
      427: { 'annexure:a-2': [{ anchor_id: 'a-leaf', collapsed_key: 'annexure:a-2', key: 'annexure:a-2', page: 427, scope_chain: [] }] },
    });
    render(
      <ViewerPanel
        {...baseProps}
        viewMode="split"
        pdfSrc="blob:full-doc"
        jobDetails={{ ...jobWithPages([{ page: 1, text: 'main doc text' }]), total_pages: 900 }}
        referenceStore={store}
        scopeRanges={[{ key: 'annexure:a-2', scope_key: '', depth: 0, start_page: 427, end_page: 429 }]}
        structureContent={{
          startPage: 427, endPage: 429, label: 'Annexure A-2', status: 'ready',
          pdfUrl: 'blob:chunk-url', pagesData: [{ page: 427, text: 'Chunk page text' }],
        }}
      />
    );
    fireEvent.click(screen.getByTestId('structure-sidebar-toggle-btn'));
    fireEvent.click(screen.getByTestId('structure-jump-root/annexure:a-2'));
    expect(screen.getByTestId('structure-reading-modal')).toBeInTheDocument();

    fireEvent.keyDown(document, { key: 'Escape' });

    expect(screen.queryByTestId('structure-reading-modal')).not.toBeInTheDocument();
    expect(screen.getByTestId('mock-pdf-page')).toHaveTextContent('1');
    expect(screen.getByText('main doc text')).toBeInTheDocument();
  });

});

describe('ViewerPanel — Structure add/edit/delete modals', () => {
  it('the Add button only renders when onAddStructureItem is provided, and opens the add modal seeded with the current page', () => {
    const job = jobWithPages([{ page: 3, text: 'x' }]);
    const { rerender } = render(<ViewerPanel {...baseProps} viewMode="split" jobDetails={job} />);
    fireEvent.click(screen.getByTestId('structure-sidebar-toggle-btn'));
    expect(screen.queryByTestId('structure-add-btn')).not.toBeInTheDocument();

    rerender(<ViewerPanel {...baseProps} viewMode="split" jobDetails={job} onAddStructureItem={vi.fn()} />);
    fireEvent.click(screen.getByTestId('structure-add-btn'));
    expect(screen.getByTestId('structure-item-modal')).toBeInTheDocument();
    expect(screen.getByText('Add Structure Entry')).toBeInTheDocument();
  });

  it('submitting the add modal calls onAddStructureItem and closes on success', async () => {
    const onAddStructureItem = vi.fn().mockResolvedValue();
    const job = jobWithPages([{ page: 1, text: 'x' }]);
    render(<ViewerPanel {...baseProps} viewMode="split" jobDetails={job} onAddStructureItem={onAddStructureItem} />);
    fireEvent.click(screen.getByTestId('structure-sidebar-toggle-btn'));
    fireEvent.click(screen.getByTestId('structure-add-btn'));

    fireEvent.change(screen.getByTestId('structure-item-heading-input'), { target: { value: 'New Section' } });
    fireEvent.click(screen.getByTestId('structure-item-modal-submit'));

    // Root add has no target context (that's only set for the per-node add
    // button, via StructureTree's onAddItem) — the second arg is undefined.
    await waitFor(() => expect(onAddStructureItem).toHaveBeenCalledWith({ heading: 'New Section', startPage: 1, endPage: 1 }, undefined));
    await waitFor(() => expect(screen.queryByTestId('structure-item-modal')).not.toBeInTheDocument());
  });

  it('a rejected add submission shows the error and keeps the modal open', async () => {
    const onAddStructureItem = vi.fn().mockRejectedValue(new Error('start_page out of range'));
    const job = jobWithPages([{ page: 1, text: 'x' }]);
    render(<ViewerPanel {...baseProps} viewMode="split" jobDetails={job} onAddStructureItem={onAddStructureItem} />);
    fireEvent.click(screen.getByTestId('structure-sidebar-toggle-btn'));
    fireEvent.click(screen.getByTestId('structure-add-btn'));
    fireEvent.change(screen.getByTestId('structure-item-heading-input'), { target: { value: 'x' } });
    fireEvent.click(screen.getByTestId('structure-item-modal-submit'));

    expect(await screen.findByTestId('structure-item-modal-error')).toHaveTextContent('start_page out of range');
    expect(screen.getByTestId('structure-item-modal')).toBeInTheDocument();
  });

  it('clicking a container\'s edit icon opens the modal pre-filled, and submitting calls onEditStructureItem with the descriptor', async () => {
    const store = storeWithDelta({
      427: { 'annexure:a-2': [{ anchor_id: 'a-leaf', collapsed_key: 'annexure:a-2', key: 'annexure:a-2', page: 427, occurrence_index: 0, scope_chain: [] }] },
    });
    const onEditStructureItem = vi.fn().mockResolvedValue();
    render(
      <ViewerPanel
        {...baseProps} viewMode="split" jobDetails={jobWithPages([{ page: 427, text: 'x' }])}
        referenceStore={store} scopeRanges={[{ key: 'annexure:a-2', scope_key: '', depth: 0, start_page: 427, end_page: 446 }]}
        onEditStructureItem={onEditStructureItem}
      />
    );
    fireEvent.click(screen.getByTestId('structure-sidebar-toggle-btn'));
    fireEvent.click(screen.getByTestId('structure-edit-root/annexure:a-2'));

    expect(screen.getByTestId('structure-item-heading-input')).toHaveValue('Annexure A-2');
    fireEvent.change(screen.getByTestId('structure-item-heading-input'), { target: { value: 'Renamed Annexure' } });
    fireEvent.click(screen.getByTestId('structure-item-modal-submit'));

    await waitFor(() => expect(onEditStructureItem).toHaveBeenCalledWith(
      expect.objectContaining({ itemKey: 'annexure:a-2', page: 427, occurrenceIndex: 0 }),
      { heading: 'Renamed Annexure', startPage: 427, endPage: 446 },
    ));
  });

  it('clicking a leaf\'s delete icon opens the confirm modal, and confirming calls onDeleteStructureItem', async () => {
    const store = storeWithDelta({
      6: { 'clause:4.1': [{ anchor_id: 'clause-leaf', collapsed_key: 'clause:4.1', key: 'clause:4.1', page: 6, occurrence_index: 0, scope_chain: [] }] },
    });
    const onDeleteStructureItem = vi.fn().mockResolvedValue();
    render(
      <ViewerPanel
        {...baseProps} viewMode="split" jobDetails={jobWithPages([{ page: 6, text: 'x' }])}
        referenceStore={store} onDeleteStructureItem={onDeleteStructureItem}
      />
    );
    fireEvent.click(screen.getByTestId('structure-sidebar-toggle-btn'));
    fireEvent.click(screen.getByTestId('structure-delete-leaf-clause-leaf'));

    expect(screen.getByTestId('structure-delete-modal')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('structure-delete-modal-confirm'));

    await waitFor(() => expect(onDeleteStructureItem).toHaveBeenCalledWith(
      expect.objectContaining({ itemKey: 'clause:4.1', page: 6, occurrenceIndex: 0 }),
    ));
    await waitFor(() => expect(screen.queryByTestId('structure-delete-modal')).not.toBeInTheDocument());
  });

  it('drag-and-drop reordering wiring: onMoveStructureItem is passed straight through to StructureTree as onMoveItem', () => {
    const store = storeWithDelta({
      10: { 'annexure:a-1': [{ anchor_id: 'a1', collapsed_key: 'annexure:a-1', key: 'annexure:a-1', page: 10, scope_chain: [] }] },
    });
    const onMoveStructureItem = vi.fn();
    render(
      <ViewerPanel
        {...baseProps} viewMode="split" jobDetails={jobWithPages([{ page: 10, text: 'x' }])}
        referenceStore={store} onMoveStructureItem={onMoveStructureItem}
      />
    );
    fireEvent.click(screen.getByTestId('structure-sidebar-toggle-btn'));
    // Presence of the root drop zone is the observable proof that
    // onMoveItem reached StructureTree (it only renders when onMoveItem is
    // truthy) — real drag gesture simulation is covered by StructureTree's
    // own override-application tests, not a full dnd-kit pointer sequence.
    expect(screen.getByTestId('structure-tree-root-dropzone')).toBeInTheDocument();
  });

  // Depends on the temporarily-disabled magnifying-glass/jump button (see above) to open the modal.
  it.skip('clicking the modal backdrop closes it, but clicking inside the panel does not', () => {
    const store = storeWithDelta({
      427: { 'annexure:a-2': [{ anchor_id: 'a-leaf', collapsed_key: 'annexure:a-2', key: 'annexure:a-2', page: 427, scope_chain: [] }] },
    });
    render(
      <ViewerPanel
        {...baseProps}
        viewMode="split"
        pdfSrc="blob:full-doc"
        jobDetails={jobWithPages([{ page: 1, text: 'x' }])}
        referenceStore={store}
        scopeRanges={[{ key: 'annexure:a-2', scope_key: '', depth: 0, start_page: 427, end_page: 429 }]}
        structureContent={{ startPage: 427, endPage: 429, label: 'Annexure A-2', status: 'loading' }}
      />
    );
    fireEvent.click(screen.getByTestId('structure-sidebar-toggle-btn'));
    fireEvent.click(screen.getByTestId('structure-jump-root/annexure:a-2'));

    fireEvent.click(screen.getByTestId('structure-reading-modal'));
    expect(screen.getByTestId('structure-reading-modal')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('structure-reading-modal-backdrop'));
    expect(screen.queryByTestId('structure-reading-modal')).not.toBeInTheDocument();
  });
});

describe('ViewerPanel — on-page reference badges', () => {
  it('clicking a reference badge pins an inline second PDF pane at the target page (not the reading modal)', () => {
    const store = storeWithDelta(
      {
        61: { 'annexure:a-1': [{ anchor_id: 'a1', collapsed_key: 'annexure:a1', key: 'annexure:a-1', page: 61 }] },
        90: { 'annexure:a-2': [{ anchor_id: 'a2', collapsed_key: 'annexure:a2', key: 'annexure:a-2', page: 90 }] },
      },
      1,
      {
        1: [
          { key: 'annexure:a-1', raw_text: 'Annexure A-1', resolution: 'exact' },
          { key: 'annexure:a-2', raw_text: 'Annexure A-2', resolution: 'exact' },
        ],
      }
    );
    const onRequestStructureContent = vi.fn();
    const job = jobWithPages([{ page: 1, text: 'Annexure A-1 and Annexure A-2, which are very essential.' }]);
    render(
      <ViewerPanel
        {...baseProps}
        viewMode="split"
        pdfSrc="blob:fake-url"
        jobDetails={job}
        referenceStore={store}
        scopeRanges={[]}
        onRequestStructureContent={onRequestStructureContent}
      />
    );
    fireEvent.click(screen.getByTestId('page-reference-badge-annexure:a-1'));

    // Main PDF pane is untouched, the modal is never opened — unlike the
    // Structure-tree leaf's own magnifier button, a badge click replaces the
    // markdown pane with a second, independent PDF view instead.
    expect(onRequestStructureContent).not.toHaveBeenCalled();
    expect(screen.queryByTestId('structure-reading-modal')).not.toBeInTheDocument();
    expect(screen.queryByText('Extracted OCR Markdown')).not.toBeInTheDocument();
    let pages = screen.getAllByTestId('mock-pdf-page').map((el) => el.textContent);
    expect(pages).toEqual(['1', '61']);
    expect(screen.getByText('Reference: Page 61')).toBeInTheDocument();

    // A second badge click re-pins to its own target page.
    fireEvent.click(screen.getByTestId('page-reference-badge-annexure:a-2'));
    pages = screen.getAllByTestId('mock-pdf-page').map((el) => el.textContent);
    expect(pages).toEqual(['1', '90']);

    // The pane's own Close button clears it, restoring the markdown pane.
    fireEvent.click(screen.getByTestId('pinned-pdf-close-btn'));
    expect(screen.queryByText(/Reference: Page/)).not.toBeInTheDocument();
    expect(screen.getByText('Extracted OCR Markdown')).toBeInTheDocument();
  });

  it('pins the second pane even in PDF-only mode, and a view-mode toggle click clears it', () => {
    const store = storeWithDelta(
      { 61: { 'annexure:a-1': [{ anchor_id: 'a1', collapsed_key: 'annexure:a1', key: 'annexure:a-1', page: 61 }] } },
      1,
      { 1: [{ key: 'annexure:a-1', raw_text: 'Annexure A-1', resolution: 'exact' }] }
    );
    const job = jobWithPages([{ page: 1, text: 'Annexure A-1, which is very essential.' }]);
    render(
      <ViewerPanel {...baseProps} viewMode="pdf" pdfSrc="blob:fake-url" jobDetails={job} referenceStore={store} scopeRanges={[]} />
    );
    expect(screen.getAllByTestId('mock-pdf-page')).toHaveLength(1);

    fireEvent.click(screen.getByTestId('page-reference-badge-annexure:a-1'));
    expect(screen.getAllByTestId('mock-pdf-page')).toHaveLength(2);
    expect(screen.getByText('Reference: Page 61')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /PDF Only/i }));
    expect(screen.getAllByTestId('mock-pdf-page')).toHaveLength(1);
  });

  it('shows no badge on a page with no resolvable mentions', () => {
    const store = createReferenceStore();
    const job = jobWithPages([{ page: 1, text: 'Nothing here refers to anything else.' }]);
    render(<ViewerPanel {...baseProps} viewMode="split" pdfSrc="blob:fake-url" jobDetails={job} referenceStore={store} scopeRanges={[]} />);
    expect(screen.queryByTestId('page-reference-badges')).not.toBeInTheDocument();
  });
});

describe('ViewerPanel — pageMentions memoization', () => {
  it('does not recompute pageMentions on a render where only unrelated state changes', () => {
    const store = storeWithDelta(
      { 61: { 'annexure:a-1': [{ anchor_id: 'a1', collapsed_key: 'annexure:a1', key: 'annexure:a-1', page: 61 }] } },
      1,
      { 1: [{ key: 'annexure:a-1', raw_text: 'Annexure A-1', resolution: 'exact' }] }
    );
    const job = jobWithPages([{ page: 1, text: 'Annexure A-1 to A-5.' }]);
    getPageMentions.mockClear();
    render(
      <ViewerPanel {...baseProps} viewMode="split" pdfSrc="blob:fake-url" jobDetails={job} referenceStore={store} scopeRanges={[]} />
    );
    const callsAfterMount = getPageMentions.mock.calls.length;
    expect(callsAfterMount).toBeGreaterThan(0);

    // Toggling the structure sidebar re-renders ViewerPanel without changing
    // currentPdfPage, the page text, or the referenceStore — the useMemo
    // dependency fix must keep getPageMentions from re-running here.
    fireEvent.click(screen.getByTestId('structure-sidebar-toggle-btn'));
    expect(getPageMentions.mock.calls.length).toBe(callsAfterMount);
  });
});

describe('ViewerPanel — Studio split group containment', () => {
  it('wraps scan pane, split resizer, and editor pane inside studio-split-group in studioMode split view', () => {
    const job = jobWithPages([{ page: 1, text: 'Sample text' }]);
    render(
      <ViewerPanel
        {...baseProps}
        studioMode={true}
        viewMode="split"
        pdfSrc="blob:fake-url"
        jobDetails={job}
      />
    );
    const splitGroup = document.querySelector('.studio-split-group');
    expect(splitGroup).toBeInTheDocument();
    expect(splitGroup.querySelector('.studio-scan-pane')).toBeInTheDocument();
    expect(splitGroup.querySelector('.split-resizer')).toBeInTheDocument();
    expect(splitGroup.querySelector('.studio-editor-pane')).toBeInTheDocument();
  });
});
