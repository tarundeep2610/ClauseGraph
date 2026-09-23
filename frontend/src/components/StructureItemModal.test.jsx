import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import StructureItemModal from './StructureItemModal';

describe('StructureItemModal', () => {
  it('renders nothing when closed', () => {
    render(<StructureItemModal open={false} onClose={vi.fn()} onSubmit={vi.fn()} />);
    expect(screen.queryByTestId('structure-item-modal')).not.toBeInTheDocument();
  });

  it('add mode starts with blank fields and submits the entered values', () => {
    const onSubmit = vi.fn();
    render(<StructureItemModal open mode="add" initialValues={{ heading: '', startPage: 5, endPage: 5 }} onClose={vi.fn()} onSubmit={onSubmit} />);
    expect(screen.getByTestId('structure-item-start-page-input')).toHaveValue(5);

    fireEvent.change(screen.getByTestId('structure-item-heading-input'), { target: { value: 'New Section' } });
    fireEvent.change(screen.getByTestId('structure-item-end-page-input'), { target: { value: '8' } });
    fireEvent.click(screen.getByTestId('structure-item-modal-submit'));

    expect(onSubmit).toHaveBeenCalledWith({ heading: 'New Section', startPage: 5, endPage: 8 });
  });

  it('typing a start page mirrors it into end page until the reader edits end page themselves', () => {
    render(<StructureItemModal open mode="add" initialValues={{ heading: '', startPage: '', endPage: '' }} onClose={vi.fn()} onSubmit={vi.fn()} />);
    fireEvent.change(screen.getByTestId('structure-item-start-page-input'), { target: { value: '12' } });
    expect(screen.getByTestId('structure-item-end-page-input')).toHaveValue(12);

    fireEvent.change(screen.getByTestId('structure-item-end-page-input'), { target: { value: '20' } });
    fireEvent.change(screen.getByTestId('structure-item-start-page-input'), { target: { value: '13' } });
    // End page was explicitly touched — no longer auto-mirrors.
    expect(screen.getByTestId('structure-item-end-page-input')).toHaveValue(20);
  });

  it('edit mode pre-fills from initialValues', () => {
    render(
      <StructureItemModal
        open mode="edit"
        initialValues={{ heading: 'Clause 4.1', startPage: 6, endPage: 6 }}
        onClose={vi.fn()} onSubmit={vi.fn()}
      />
    );
    expect(screen.getByTestId('structure-item-heading-input')).toHaveValue('Clause 4.1');
    expect(screen.getByTestId('structure-item-start-page-input')).toHaveValue(6);
    expect(screen.getByText('Edit Structure Entry')).toBeInTheDocument();
  });

  it('does not submit with an empty heading', () => {
    const onSubmit = vi.fn();
    render(<StructureItemModal open mode="add" initialValues={{ heading: '', startPage: 1, endPage: 1 }} onClose={vi.fn()} onSubmit={onSubmit} />);
    fireEvent.click(screen.getByTestId('structure-item-modal-submit'));
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('shows the submitting state and disables the submit button', () => {
    render(<StructureItemModal open mode="add" initialValues={{ heading: 'x', startPage: 1, endPage: 1 }} onClose={vi.fn()} onSubmit={vi.fn()} submitting />);
    expect(screen.getByTestId('structure-item-modal-submit')).toBeDisabled();
    expect(screen.getByTestId('structure-item-modal-submit')).toHaveTextContent('Saving…');
  });

  it('shows a passed-in error message', () => {
    render(<StructureItemModal open mode="add" initialValues={{ heading: 'x', startPage: 1, endPage: 1 }} onClose={vi.fn()} onSubmit={vi.fn()} error="end_page must be >= start_page" />);
    expect(screen.getByTestId('structure-item-modal-error')).toHaveTextContent('end_page must be >= start_page');
  });

  it('Cancel, backdrop click, and Escape all call onClose', () => {
    const onClose = vi.fn();
    render(<StructureItemModal open mode="add" initialValues={{ heading: '', startPage: 1, endPage: 1 }} onClose={onClose} onSubmit={vi.fn()} />);

    fireEvent.click(screen.getByTestId('structure-item-modal-cancel'));
    expect(onClose).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByTestId('structure-item-modal-backdrop'));
    expect(onClose).toHaveBeenCalledTimes(2);

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(3);
  });

  it('clicking inside the panel does not close it', () => {
    const onClose = vi.fn();
    render(<StructureItemModal open mode="add" initialValues={{ heading: '', startPage: 1, endPage: 1 }} onClose={onClose} onSubmit={vi.fn()} />);
    fireEvent.click(screen.getByTestId('structure-item-modal'));
    expect(onClose).not.toHaveBeenCalled();
  });
});
