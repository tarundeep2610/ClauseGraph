import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import ReviewQueuePanel from './ReviewQueuePanel';

const PAGES = [
  { page_num: 2, confidence_pct: 40, review_reason: 'repetition_loop' },
  { page_num: 5, confidence_pct: 55, review_reason: 'low_confidence' },
];

describe('ReviewQueuePanel — empty state', () => {
  it('shows an "all verified" message when there are no flagged pages', () => {
    render(<ReviewQueuePanel pages={[]} currentPageNum={1} isAdmin={false} onSelectPage={vi.fn()} onConfirmCorrect={vi.fn()} onRerunPage={vi.fn()} />);
    expect(screen.getByTestId('review-queue-empty')).toBeInTheDocument();
    expect(screen.queryByTestId('review-mark-verified-btn')).not.toBeInTheDocument();
  });
});

describe('ReviewQueuePanel — listing and selection', () => {
  it('lists every flagged page with its confidence and reason', () => {
    render(<ReviewQueuePanel pages={PAGES} currentPageNum={2} isAdmin={false} onSelectPage={vi.fn()} onConfirmCorrect={vi.fn()} onRerunPage={vi.fn()} />);
    expect(screen.getByTestId('review-queue-item-2')).toHaveTextContent('Page 2');
    expect(screen.getByTestId('review-queue-item-2')).toHaveTextContent('40%');
    expect(screen.getByTestId('review-queue-item-2')).toHaveTextContent('repetition_loop');
    expect(screen.getByTestId('review-queue-item-5')).toHaveTextContent('55%');
  });

  it('marks the row matching currentPageNum as active', () => {
    render(<ReviewQueuePanel pages={PAGES} currentPageNum={5} isAdmin={false} onSelectPage={vi.fn()} onConfirmCorrect={vi.fn()} onRerunPage={vi.fn()} />);
    expect(screen.getByTestId('review-queue-item-5')).toHaveClass('active');
    expect(screen.getByTestId('review-queue-item-2')).not.toHaveClass('active');
  });

  it('clicking a row calls onSelectPage with that page number', () => {
    const onSelectPage = vi.fn();
    render(<ReviewQueuePanel pages={PAGES} currentPageNum={2} isAdmin={false} onSelectPage={onSelectPage} onConfirmCorrect={vi.fn()} onRerunPage={vi.fn()} />);
    fireEvent.click(screen.getByTestId('review-queue-item-5'));
    expect(onSelectPage).toHaveBeenCalledWith(5);
  });
});

describe('ReviewQueuePanel — Mark Verified & Next', () => {
  it('confirms the currently-active page, not just the first in the list', () => {
    const onConfirmCorrect = vi.fn();
    render(<ReviewQueuePanel pages={PAGES} currentPageNum={5} isAdmin={false} onSelectPage={vi.fn()} onConfirmCorrect={onConfirmCorrect} onRerunPage={vi.fn()} />);
    fireEvent.click(screen.getByTestId('review-mark-verified-btn'));
    expect(onConfirmCorrect).toHaveBeenCalledWith(5);
  });

  it('falls back to the first page when currentPageNum matches none in the queue', () => {
    const onConfirmCorrect = vi.fn();
    render(<ReviewQueuePanel pages={PAGES} currentPageNum={99} isAdmin={false} onSelectPage={vi.fn()} onConfirmCorrect={onConfirmCorrect} onRerunPage={vi.fn()} />);
    fireEvent.click(screen.getByTestId('review-mark-verified-btn'));
    expect(onConfirmCorrect).toHaveBeenCalledWith(2);
  });
});

describe('ReviewQueuePanel — Rerun is admin-gated', () => {
  it('hides the Rerun button entirely for a non-admin', () => {
    render(<ReviewQueuePanel pages={PAGES} currentPageNum={2} isAdmin={false} onSelectPage={vi.fn()} onConfirmCorrect={vi.fn()} onRerunPage={vi.fn()} />);
    expect(screen.queryByTestId('review-rerun-btn')).not.toBeInTheDocument();
  });

  it('shows Rerun for an admin and calls onRerunPage with the active page', () => {
    const onRerunPage = vi.fn();
    render(<ReviewQueuePanel pages={PAGES} currentPageNum={5} isAdmin={true} onSelectPage={vi.fn()} onConfirmCorrect={vi.fn()} onRerunPage={onRerunPage} />);
    fireEvent.click(screen.getByTestId('review-rerun-btn'));
    expect(onRerunPage).toHaveBeenCalledWith(5);
  });

  it('disables Rerun while a rerun is already in flight for this page', () => {
    render(<ReviewQueuePanel pages={PAGES} currentPageNum={5} isAdmin={true} onSelectPage={vi.fn()} onConfirmCorrect={vi.fn()} onRerunPage={vi.fn()} rerunningPage={5} />);
    expect(screen.getByTestId('review-rerun-btn')).toBeDisabled();
    expect(screen.getByTestId('review-rerun-btn')).toHaveTextContent('Rerunning…');
  });
});
