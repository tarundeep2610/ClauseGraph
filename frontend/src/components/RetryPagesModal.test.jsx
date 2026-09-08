import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import RetryPagesModal from './RetryPagesModal';

const baseCounts = { failed: 3, review: 5, total: 40 };

describe('RetryPagesModal', () => {
  it('renders nothing when closed', () => {
    render(<RetryPagesModal open={false} onClose={vi.fn()} onConfirm={vi.fn()} counts={baseCounts} />);
    expect(screen.queryByTestId('retry-modal')).not.toBeInTheDocument();
  });

  it('renders all three options with their counts when open', () => {
    render(<RetryPagesModal open={true} onClose={vi.fn()} onConfirm={vi.fn()} counts={baseCounts} />);
    expect(screen.getByTestId('retry-option-failed')).toHaveTextContent('3');
    expect(screen.getByTestId('retry-option-review')).toHaveTextContent('5');
    expect(screen.getByTestId('retry-option-all')).toHaveTextContent('40');
  });

  it.each([
    ['retry-option-failed', 'failed'],
    ['retry-option-review', 'review'],
    ['retry-option-all', 'all'],
  ])('clicking %s calls onConfirm with scope %s', (testId, scope) => {
    const onConfirm = vi.fn();
    render(<RetryPagesModal open={true} onClose={vi.fn()} onConfirm={onConfirm} counts={baseCounts} />);
    fireEvent.click(screen.getByTestId(testId));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(onConfirm).toHaveBeenCalledWith(scope);
  });

  it('disables an option with a zero count and does not call onConfirm when clicked', () => {
    const onConfirm = vi.fn();
    render(<RetryPagesModal open={true} onClose={vi.fn()} onConfirm={onConfirm} counts={{ failed: 0, review: 5, total: 40 }} />);
    const btn = screen.getByTestId('retry-option-failed');
    expect(btn).toBeDisabled();
    fireEvent.click(btn);
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it('clicking Cancel calls onClose and not onConfirm', () => {
    const onClose = vi.fn();
    const onConfirm = vi.fn();
    render(<RetryPagesModal open={true} onClose={onClose} onConfirm={onConfirm} counts={baseCounts} />);
    fireEvent.click(screen.getByTestId('retry-modal-cancel'));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it('clicking the backdrop calls onClose and not onConfirm', () => {
    const onClose = vi.fn();
    const onConfirm = vi.fn();
    render(<RetryPagesModal open={true} onClose={onClose} onConfirm={onConfirm} counts={baseCounts} />);
    fireEvent.click(screen.getByTestId('retry-modal-backdrop'));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it('clicking inside the panel does not close the modal', () => {
    const onClose = vi.fn();
    render(<RetryPagesModal open={true} onClose={onClose} onConfirm={vi.fn()} counts={baseCounts} />);
    fireEvent.click(screen.getByTestId('retry-modal'));
    expect(onClose).not.toHaveBeenCalled();
  });

  it('pressing Escape calls onClose', () => {
    const onClose = vi.fn();
    render(<RetryPagesModal open={true} onClose={onClose} onConfirm={vi.fn()} counts={baseCounts} />);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
