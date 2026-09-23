import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import StructureDeleteConfirmModal from './StructureDeleteConfirmModal';

describe('StructureDeleteConfirmModal', () => {
  it('renders nothing when closed', () => {
    render(<StructureDeleteConfirmModal open={false} onClose={vi.fn()} onConfirm={vi.fn()} />);
    expect(screen.queryByTestId('structure-delete-modal')).not.toBeInTheDocument();
  });

  it('shows the label in the confirmation message', () => {
    render(<StructureDeleteConfirmModal open label="Clause 4.1" onClose={vi.fn()} onConfirm={vi.fn()} />);
    expect(screen.getByText(/Remove "Clause 4.1"/)).toBeInTheDocument();
  });

  it('Confirm calls onConfirm; Cancel/backdrop/Escape call onClose without confirming', () => {
    const onConfirm = vi.fn();
    const onClose = vi.fn();
    render(<StructureDeleteConfirmModal open label="Clause 4.1" onClose={onClose} onConfirm={onConfirm} />);

    fireEvent.click(screen.getByTestId('structure-delete-modal-cancel'));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onConfirm).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId('structure-delete-modal-backdrop'));
    expect(onClose).toHaveBeenCalledTimes(2);

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(3);

    fireEvent.click(screen.getByTestId('structure-delete-modal-confirm'));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it('shows the deleting state and disables confirm while submitting', () => {
    render(<StructureDeleteConfirmModal open label="x" onClose={vi.fn()} onConfirm={vi.fn()} submitting />);
    expect(screen.getByTestId('structure-delete-modal-confirm')).toBeDisabled();
    expect(screen.getByTestId('structure-delete-modal-confirm')).toHaveTextContent('Deleting…');
  });
});
