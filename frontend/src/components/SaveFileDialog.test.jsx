import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import SaveFileDialog from './SaveFileDialog';

describe('SaveFileDialog', () => {
  it('renders nothing when closed', () => {
    render(<SaveFileDialog open={false} suggestedName="report.docx" onCancel={vi.fn()} onConfirm={vi.fn()} />);
    expect(screen.queryByTestId('save-file-dialog')).not.toBeInTheDocument();
  });

  it('pre-fills the input with the suggested filename', () => {
    render(<SaveFileDialog open suggestedName="report.docx" onCancel={vi.fn()} onConfirm={vi.fn()} />);
    expect(screen.getByTestId('save-file-dialog-input')).toHaveValue('report.docx');
  });

  it('Cancel closes without calling onConfirm', () => {
    const onCancel = vi.fn();
    const onConfirm = vi.fn();
    render(<SaveFileDialog open suggestedName="report.docx" onCancel={onCancel} onConfirm={onConfirm} />);
    fireEvent.click(screen.getByTestId('save-file-dialog-cancel'));
    expect(onCancel).toHaveBeenCalledOnce();
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it('Save calls onConfirm with the (possibly edited) filename', () => {
    const onConfirm = vi.fn();
    render(<SaveFileDialog open suggestedName="report.docx" onCancel={vi.fn()} onConfirm={onConfirm} />);
    const input = screen.getByTestId('save-file-dialog-input');
    fireEvent.change(input, { target: { value: 'my-renamed-file.docx' } });
    fireEvent.click(screen.getByTestId('save-file-dialog-save'));
    expect(onConfirm).toHaveBeenCalledWith('my-renamed-file.docx');
  });

  it('Enter in the input also confirms', () => {
    const onConfirm = vi.fn();
    render(<SaveFileDialog open suggestedName="report.docx" onCancel={vi.fn()} onConfirm={onConfirm} />);
    fireEvent.keyDown(screen.getByTestId('save-file-dialog-input'), { key: 'Enter' });
    expect(onConfirm).toHaveBeenCalledWith('report.docx');
  });

  it('Escape key cancels', () => {
    const onCancel = vi.fn();
    render(<SaveFileDialog open suggestedName="report.docx" onCancel={onCancel} onConfirm={vi.fn()} />);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onCancel).toHaveBeenCalledOnce();
  });

  it('Save is disabled when the filename is emptied out', () => {
    render(<SaveFileDialog open suggestedName="report.docx" onCancel={vi.fn()} onConfirm={vi.fn()} />);
    const input = screen.getByTestId('save-file-dialog-input');
    fireEvent.change(input, { target: { value: '   ' } });
    expect(screen.getByTestId('save-file-dialog-save')).toBeDisabled();
  });

  it('resets to the new suggested name when reopened with a different one', () => {
    const { rerender } = render(
      <SaveFileDialog open suggestedName="report.docx" onCancel={vi.fn()} onConfirm={vi.fn()} />
    );
    rerender(<SaveFileDialog open={false} suggestedName="report.docx" onCancel={vi.fn()} onConfirm={vi.fn()} />);
    rerender(<SaveFileDialog open suggestedName="other.pdf" onCancel={vi.fn()} onConfirm={vi.fn()} />);
    expect(screen.getByTestId('save-file-dialog-input')).toHaveValue('other.pdf');
  });
});
