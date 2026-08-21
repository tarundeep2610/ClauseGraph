import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import UploadPanel from './UploadPanel';

const baseProps = {
  file: null,
  onFileSelected: vi.fn(),
  onClearFile: vi.fn(),
  form: { pageRange: '' },
  updateForm: vi.fn(),
  onSubmit: vi.fn(),
  submitting: false,
};

describe('UploadPanel', () => {
  it('disables the submit button when no file is selected', () => {
    render(<UploadPanel {...baseProps} />);
    expect(screen.getByTestId('submit-btn')).toBeDisabled();
  });

  it('enables the submit button once a file is selected', () => {
    const file = new File(['content'], 'doc.pdf', { type: 'application/pdf' });
    render(<UploadPanel {...baseProps} file={file} />);
    expect(screen.getByTestId('submit-btn')).not.toBeDisabled();
  });

  it('shows the page-range input only for PDF files', () => {
    const pdf = new File(['content'], 'doc.pdf', { type: 'application/pdf' });
    const { rerender } = render(<UploadPanel {...baseProps} file={pdf} />);
    expect(screen.getByPlaceholderText(/Pages to extract/i)).toBeInTheDocument();

    const png = new File(['content'], 'image.png', { type: 'image/png' });
    rerender(<UploadPanel {...baseProps} file={png} />);
    expect(screen.queryByPlaceholderText(/Pages to extract/i)).not.toBeInTheDocument();
  });

  it('disables the submit button while a submission is in flight', () => {
    const file = new File(['content'], 'doc.pdf', { type: 'application/pdf' });
    render(<UploadPanel {...baseProps} file={file} submitting />);
    expect(screen.getByTestId('submit-btn')).toBeDisabled();
  });

  it('calls onSubmit when the submit button is clicked', () => {
    const onSubmit = vi.fn();
    const file = new File(['content'], 'doc.pdf', { type: 'application/pdf' });
    render(<UploadPanel {...baseProps} file={file} onSubmit={onSubmit} />);
    fireEvent.click(screen.getByTestId('submit-btn'));
    expect(onSubmit).toHaveBeenCalledOnce();
  });
});
