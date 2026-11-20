import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import ViewerPanel from './ViewerPanel';

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
