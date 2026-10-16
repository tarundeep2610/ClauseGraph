import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import RebuildIndexModal from './RebuildIndexModal';

describe('RebuildIndexModal', () => {
  it('renders nothing when closed', () => {
    render(<RebuildIndexModal open={false} onClose={vi.fn()} onConfirm={vi.fn()} />);
    expect(screen.queryByTestId('rebuild-index-modal')).not.toBeInTheDocument();
  });

  it('both checkboxes are checked by default, and Reference Links is disabled while Index is checked', () => {
    render(<RebuildIndexModal open={true} onClose={vi.fn()} onConfirm={vi.fn()} />);
    expect(screen.getByTestId('rebuild-checkbox-index')).toBeChecked();
    expect(screen.getByTestId('rebuild-checkbox-links')).toBeChecked();
    expect(screen.getByTestId('rebuild-checkbox-links')).toBeDisabled();
  });

  it('unchecking Index re-enables the Reference Links checkbox, which stays checked', () => {
    render(<RebuildIndexModal open={true} onClose={vi.fn()} onConfirm={vi.fn()} />);
    fireEvent.click(screen.getByTestId('rebuild-checkbox-index'));
    expect(screen.getByTestId('rebuild-checkbox-index')).not.toBeChecked();
    expect(screen.getByTestId('rebuild-checkbox-links')).not.toBeDisabled();
    expect(screen.getByTestId('rebuild-checkbox-links')).toBeChecked();
  });

  it('Rebuild is disabled once both boxes are unchecked', () => {
    render(<RebuildIndexModal open={true} onClose={vi.fn()} onConfirm={vi.fn()} />);
    fireEvent.click(screen.getByTestId('rebuild-checkbox-index'));
    fireEvent.click(screen.getByTestId('rebuild-checkbox-links'));
    expect(screen.getByTestId('rebuild-modal-confirm')).toBeDisabled();
  });

  // Regression test for a real bug found by the user: ViewerPanel.jsx (the
  // real caller) re-renders every ~2.5s for as long as Studio is open
  // (AppShell's job-details poll), passing a brand-new inline `onClose`
  // function each time. The checkbox-reset effect used to depend on
  // `onClose`, so every poll tick silently reset the user's in-progress
  // choice back to "both checked" — reported as "I checked Reference Links
  // Only and it reverted back to Index checked automatically." Simulated
  // here by re-rendering with a fresh onClose, same as a real poll tick.
  it('a parent re-render with a brand-new onClose function does not reset the checkboxes mid-choice', () => {
    const { rerender } = render(<RebuildIndexModal open={true} onClose={() => {}} onConfirm={vi.fn()} />);

    fireEvent.click(screen.getByTestId('rebuild-checkbox-index'));
    expect(screen.getByTestId('rebuild-checkbox-index')).not.toBeChecked();

    // Simulate several poll-driven re-renders, each with a fresh onClose.
    for (let i = 0; i < 3; i++) {
      rerender(<RebuildIndexModal open={true} onClose={() => {}} onConfirm={vi.fn()} />);
    }

    expect(screen.getByTestId('rebuild-checkbox-index')).not.toBeChecked();
    expect(screen.getByTestId('rebuild-checkbox-links')).toBeChecked();
  });

  it('confirming calls onConfirm with "full" when Index is checked', () => {
    const onConfirm = vi.fn();
    render(<RebuildIndexModal open={true} onClose={vi.fn()} onConfirm={onConfirm} />);
    fireEvent.click(screen.getByTestId('rebuild-modal-confirm'));
    expect(onConfirm).toHaveBeenCalledWith('full');
  });

  it('confirming calls onConfirm with "links-only" when only Reference Links is checked', () => {
    const onConfirm = vi.fn();
    render(<RebuildIndexModal open={true} onClose={vi.fn()} onConfirm={onConfirm} />);
    fireEvent.click(screen.getByTestId('rebuild-checkbox-index'));
    fireEvent.click(screen.getByTestId('rebuild-modal-confirm'));
    expect(onConfirm).toHaveBeenCalledWith('links-only');
  });

  it('calls onClose when Cancel is clicked', () => {
    const onClose = vi.fn();
    render(<RebuildIndexModal open={true} onClose={onClose} onConfirm={vi.fn()} />);
    fireEvent.click(screen.getByTestId('rebuild-modal-cancel'));
    expect(onClose).toHaveBeenCalledOnce();
  });
});
