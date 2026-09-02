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
