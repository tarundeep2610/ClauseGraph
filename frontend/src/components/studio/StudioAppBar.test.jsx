import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import StudioAppBar from './StudioAppBar';

describe('StudioAppBar component', () => {
  it('renders filename, page badge, and saved status', () => {
    render(
      <StudioAppBar
        filename="contract.pdf"
        currentPage={3}
        totalPages={10}
        saveStatus="saved"
      />
    );

    expect(screen.getByTestId('studio-file-name')).toHaveTextContent('contract.pdf');
    expect(screen.getByTestId('studio-page-badge')).toHaveTextContent('P. 3 of 10');
    expect(screen.getByTestId('studio-save-status')).toHaveTextContent('✓ Saved');
  });

  it('reflects saving and unsaved status labels', () => {
    const { rerender } = render(<StudioAppBar saveStatus="saving" />);
    expect(screen.getByTestId('studio-save-status')).toHaveTextContent('● Saving…');

    rerender(<StudioAppBar saveStatus="pending" />);
    expect(screen.getByTestId('studio-save-status')).toHaveTextContent('● Unsaved changes');
  });

  it('triggers onBackToLibrary when clicking the Library back button', () => {
    const onBack = vi.fn();
    render(<StudioAppBar onBackToLibrary={onBack} />);

    fireEvent.click(screen.getByTestId('back-to-library-btn'));
    expect(onBack).toHaveBeenCalledTimes(1);
  });

  it('handles build index / reindexing button actions', () => {
    const onBuild = vi.fn();
    const { rerender } = render(
      <StudioAppBar onBuildIndex={onBuild} reindexing={false} indexBuilt={false} />
    );

    const btn = screen.getByTestId('reindex-references-btn');
    expect(btn).toHaveTextContent('Build Index & Reference Links');
    fireEvent.click(btn);
    expect(onBuild).toHaveBeenCalledTimes(1);

    rerender(<StudioAppBar onBuildIndex={onBuild} reindexing={true} indexBuilt={true} />);
    expect(screen.getByTestId('reindex-references-btn')).toHaveTextContent('Rebuilding…');
    expect(screen.getByTestId('reindex-references-btn')).toBeDisabled();
  });

  it('renders return to editor button only when isDualCompare is true', () => {
    const onExit = vi.fn();
    const { rerender } = render(<StudioAppBar isDualCompare={false} onExitDualCompare={onExit} />);
    expect(screen.queryByTestId('studio-return-to-editor-btn')).not.toBeInTheDocument();

    rerender(<StudioAppBar isDualCompare={true} onExitDualCompare={onExit} />);
    const returnBtn = screen.getByTestId('studio-return-to-editor-btn');
    expect(returnBtn).toBeInTheDocument();
    fireEvent.click(returnBtn);
    expect(onExit).toHaveBeenCalledTimes(1);
  });

  it('opens search input and handles search keystrokes and navigation', () => {
    const onToggle = vi.fn();
    const onQueryChange = vi.fn();
    const onNext = vi.fn();
    const onPrev = vi.fn();

    const { rerender } = render(
      <StudioAppBar
        searchOpen={false}
        onToggleSearch={onToggle}
      />
    );

    fireEvent.click(screen.getByTestId('studio-find-btn'));
    expect(onToggle).toHaveBeenCalledTimes(1);

    rerender(
      <StudioAppBar
        searchOpen={true}
        onToggleSearch={onToggle}
        searchQuery="liability"
        onSearchQueryChange={onQueryChange}
        onSearchNext={onNext}
        onSearchPrev={onPrev}
      />
    );

    const input = screen.getByPlaceholderText('Find in document…');
    expect(input.value).toBe('liability');

    fireEvent.change(input, { target: { value: 'indemnity' } });
    expect(onQueryChange).toHaveBeenCalledWith('indemnity');

    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onNext).toHaveBeenCalledTimes(1);

    fireEvent.keyDown(input, { key: 'Enter', shiftKey: true });
    expect(onPrev).toHaveBeenCalledTimes(1);

    fireEvent.keyDown(input, { key: 'Escape' });
    expect(onToggle).toHaveBeenCalledTimes(2);
  });

  it('renders pause, resume, and retry pages buttons when provided', () => {
    const onPause = vi.fn();
    const onResume = vi.fn();
    const onRetryModal = vi.fn();

    render(
      <StudioAppBar
        canPause={true}
        onPause={onPause}
        canResume={true}
        onResume={onResume}
        isAdmin={true}
        onOpenRetryModal={onRetryModal}
      />
    );

    fireEvent.click(screen.getByTestId('pause-job-btn'));
    expect(onPause).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByTestId('resume-job-btn'));
    expect(onResume).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByTestId('retry-pages-btn'));
    expect(onRetryModal).toHaveBeenCalledTimes(1);
  });
});
