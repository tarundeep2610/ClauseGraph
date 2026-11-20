import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import HistoryPanel from './HistoryPanel';

const noop = vi.fn();
const baseHandlers = {
  onSelect: noop, onPause: noop, onRetry: noop, onDownloadFile: noop,
  onDownloadResult: noop, onDelete: noop, onRefresh: noop,
};

describe('HistoryPanel', () => {
  it('shows an empty state when there are no jobs', () => {
    render(<HistoryPanel jobs={[]} activeJobId={null} {...baseHandlers} />);
    expect(screen.getByText(/No documents yet/i)).toBeInTheDocument();
  });

  it('renders one status badge per job, matching each job\'s status', () => {
    const jobs = [
      { job_id: '1', filename: 'a.pdf', status: 'completed', total_pages: 3, processed_pages: 3, engine: 'unlimited', created_at: new Date().toISOString() },
      { job_id: '2', filename: 'b.pdf', status: 'processing', total_pages: 5, processed_pages: 2, engine: 'glm', created_at: new Date().toISOString() },
      { job_id: '3', filename: 'c.pdf', status: 'failed', total_pages: 1, processed_pages: 0, engine: 'glm', created_at: new Date().toISOString() },
    ];
    render(<HistoryPanel jobs={jobs} activeJobId={null} {...baseHandlers} />);

    const cards = screen.getAllByTestId('history-card');
    expect(cards).toHaveLength(3);
    expect(screen.getByText('completed')).toBeInTheDocument();
    expect(screen.getByText('2/5')).toBeInTheDocument(); // processing shows progress, not literal "processing"
    expect(screen.getByText('failed')).toBeInTheDocument();
  });

  it('shows a pause action only for processing jobs, and a resume (retry) action only for paused/failed jobs', () => {
    const jobs = [
      { job_id: '1', filename: 'a.pdf', status: 'processing', total_pages: 5, processed_pages: 2, engine: 'glm', created_at: new Date().toISOString() },
      { job_id: '2', filename: 'b.pdf', status: 'paused', total_pages: 5, processed_pages: 2, engine: 'glm', created_at: new Date().toISOString() },
    ];
    render(<HistoryPanel jobs={jobs} activeJobId={null} {...baseHandlers} />);
    const cards = screen.getAllByTestId('history-card');
    expect(cards[0].querySelector('.btn-warn')).toBeInTheDocument(); // pause on the processing job
    expect(cards[1].querySelector('.btn-warn')).not.toBeInTheDocument();
  });

  it('does not render an Engine column or eye icon button in actions', () => {
    const jobs = [
      { job_id: '1', filename: 'a.pdf', status: 'completed', total_pages: 3, processed_pages: 3, engine: 'unlimited', created_at: new Date().toISOString() },
    ];
    render(<HistoryPanel jobs={jobs} activeJobId={null} {...baseHandlers} />);
    expect(screen.queryByText('Engine')).not.toBeInTheDocument();
    expect(document.querySelector('.fa-eye')).not.toBeInTheDocument();
  });
});
