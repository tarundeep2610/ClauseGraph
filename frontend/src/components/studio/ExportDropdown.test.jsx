import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import ExportDropdown from './ExportDropdown';

describe('ExportDropdown', () => {
  it('is closed by default and opens the panel on click', () => {
    render(<ExportDropdown currentPage={3} totalPages={42} onExport={vi.fn()} />);
    expect(screen.queryByTestId('export-dropdown-panel')).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId('export-dropdown-toggle'));
    expect(screen.getByTestId('export-dropdown-panel')).toBeInTheDocument();
  });

  it('defaults to docx + full document + include metadata, and calls onExport with that', () => {
    const onExport = vi.fn();
    render(<ExportDropdown currentPage={3} totalPages={42} onExport={onExport} />);
    fireEvent.click(screen.getByTestId('export-dropdown-toggle'));
    fireEvent.click(screen.getByTestId('export-confirm-btn'));
    expect(onExport).toHaveBeenCalledWith({ format: 'docx', scope: 'full', includeMetadata: true, pageNum: undefined, pageRange: undefined });
  });

  it('switching format and scope changes what onExport is called with', () => {
    const onExport = vi.fn();
    render(<ExportDropdown currentPage={5} totalPages={42} onExport={onExport} />);
    fireEvent.click(screen.getByTestId('export-dropdown-toggle'));
    fireEvent.click(screen.getByTestId('export-format-pdf'));
    fireEvent.click(screen.getByLabelText(/Current Page Only/));
    fireEvent.click(screen.getByTestId('export-confirm-btn'));
    expect(onExport).toHaveBeenCalledWith({ format: 'pdf', scope: 'page', includeMetadata: true, pageNum: 5, pageRange: undefined });
  });

  it('custom range requires a non-empty range before exporting', () => {
    const onExport = vi.fn();
    render(<ExportDropdown currentPage={5} totalPages={42} onExport={onExport} />);
    fireEvent.click(screen.getByTestId('export-dropdown-toggle'));
    fireEvent.click(screen.getByLabelText(/Custom Range/));
    fireEvent.click(screen.getByTestId('export-confirm-btn'));
    expect(onExport).not.toHaveBeenCalled();
    expect(screen.getByTestId('export-range-error')).toBeInTheDocument();

    fireEvent.change(screen.getByTestId('export-range-input'), { target: { value: '1-5,12' } });
    fireEvent.click(screen.getByTestId('export-confirm-btn'));
    expect(onExport).toHaveBeenCalledWith({ format: 'docx', scope: 'range', includeMetadata: true, pageNum: undefined, pageRange: '1-5,12' });
  });

  it('unchecking the metadata toggle is reflected in the export call', () => {
    const onExport = vi.fn();
    render(<ExportDropdown currentPage={3} totalPages={42} onExport={onExport} />);
    fireEvent.click(screen.getByTestId('export-dropdown-toggle'));
    fireEvent.click(screen.getByLabelText(/Include review warnings/));
    fireEvent.click(screen.getByTestId('export-confirm-btn'));
    expect(onExport).toHaveBeenCalledWith(expect.objectContaining({ includeMetadata: false }));
  });

  it('cancel closes the panel without exporting', () => {
    const onExport = vi.fn();
    render(<ExportDropdown currentPage={3} totalPages={42} onExport={onExport} />);
    fireEvent.click(screen.getByTestId('export-dropdown-toggle'));
    fireEvent.click(screen.getByText('Cancel'));
    expect(screen.queryByTestId('export-dropdown-panel')).not.toBeInTheDocument();
    expect(onExport).not.toHaveBeenCalled();
  });
});
