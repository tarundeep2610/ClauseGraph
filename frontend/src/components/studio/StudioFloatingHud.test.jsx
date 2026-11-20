import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import StudioFloatingHud from './StudioFloatingHud';

describe('StudioFloatingHud component', () => {
  it('renders page info, navigation buttons, and handles click navigation', () => {
    const onNavigate = vi.fn();

    render(
      <StudioFloatingHud
        currentPage={5}
        totalPages={20}
        onNavigate={onNavigate}
      />
    );

    expect(screen.getByTestId('hud-page-input')).toHaveValue('5');
    expect(screen.getByText(/of 20/)).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('hud-prev-page-btn'));
    expect(onNavigate).toHaveBeenCalledWith(4);

    fireEvent.click(screen.getByTestId('hud-next-page-btn'));
    expect(onNavigate).toHaveBeenCalledWith(6);
  });

  it('allows direct page typing and Enter commit', () => {
    const onNavigate = vi.fn();

    render(
      <StudioFloatingHud
        currentPage={5}
        totalPages={20}
        onNavigate={onNavigate}
      />
    );

    const input = screen.getByTestId('hud-page-input');
    fireEvent.change(input, { target: { value: '14' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    expect(onNavigate).toHaveBeenCalledWith(14);
  });

  it('switches view mode when clicking Split, Scan, or Editor buttons', () => {
    const onViewModeChange = vi.fn();

    render(
      <StudioFloatingHud
        currentPage={1}
        totalPages={10}
        viewMode="split"
        onViewModeChange={onViewModeChange}
      />
    );

    expect(screen.getByTestId('hud-view-split')).toHaveClass('active');

    fireEvent.click(screen.getByTestId('hud-view-pdf'));
    expect(onViewModeChange).toHaveBeenCalledWith('pdf');

    fireEvent.click(screen.getByTestId('hud-view-result'));
    expect(onViewModeChange).toHaveBeenCalledWith('result');
  });

  it('handles dock/minimize toggle', () => {
    render(
      <StudioFloatingHud
        currentPage={1}
        totalPages={10}
      />
    );

    const hud = screen.getByTestId('studio-floating-hud');
    expect(hud).not.toHaveClass('minimized');

    fireEvent.click(screen.getByTestId('hud-dock-btn'));
    expect(hud).toHaveClass('minimized');
    expect(screen.getByTestId('hud-expand-btn')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('hud-expand-btn'));
    expect(hud).not.toHaveClass('minimized');
  });

  it('allows repositioning HUD by dragging and resetting on double-click', () => {
    render(
      <div style={{ width: 1000, height: 800 }}>
        <StudioFloatingHud currentPage={1} totalPages={10} />
      </div>
    );

    const hud = screen.getByTestId('studio-floating-hud');
    const handle = screen.getByTestId('hud-drag-handle');

    vi.spyOn(hud, 'getBoundingClientRect').mockReturnValue({
      left: 400,
      top: 700,
      width: 200,
      height: 40,
      right: 600,
      bottom: 740,
    });
    vi.spyOn(hud.parentElement, 'getBoundingClientRect').mockReturnValue({
      left: 0,
      top: 0,
      width: 1000,
      height: 800,
      right: 1000,
      bottom: 800,
    });

    fireEvent.mouseDown(handle, { button: 0, clientX: 400, clientY: 700 });
    expect(hud).toHaveClass('is-dragging');

    fireEvent.mouseMove(window, { clientX: 450, clientY: 650 });
    expect(hud.style.left).toBe('450px');
    expect(hud.style.top).toBe('650px');

    fireEvent.mouseUp(window);
    expect(hud).not.toHaveClass('is-dragging');

    const resetBtn = screen.getByTestId('hud-reset-pos-btn');
    expect(resetBtn).toBeInTheDocument();

    fireEvent.doubleClick(handle);
    expect(hud.style.left).toBe('');
    expect(screen.queryByTestId('hud-reset-pos-btn')).not.toBeInTheDocument();
  });

  it('resets position when clicking the reset position button', () => {
    render(
      <div style={{ width: 1000, height: 800 }}>
        <StudioFloatingHud currentPage={1} totalPages={10} />
      </div>
    );

    const hud = screen.getByTestId('studio-floating-hud');
    const handle = screen.getByTestId('hud-drag-handle');

    vi.spyOn(hud, 'getBoundingClientRect').mockReturnValue({
      left: 400,
      top: 700,
      width: 200,
      height: 40,
    });
    vi.spyOn(hud.parentElement, 'getBoundingClientRect').mockReturnValue({
      left: 0,
      top: 0,
      width: 1000,
      height: 800,
    });

    fireEvent.mouseDown(handle, { button: 0, clientX: 400, clientY: 700 });
    fireEvent.mouseMove(window, { clientX: 420, clientY: 600 });
    fireEvent.mouseUp(window);

    const resetBtn = screen.getByTestId('hud-reset-pos-btn');
    fireEvent.click(resetBtn);

    expect(hud.style.left).toBe('');
    expect(screen.queryByTestId('hud-reset-pos-btn')).not.toBeInTheDocument();
  });
});

describe('StudioFloatingHud — PDF (scan) zoom cluster', () => {
  it('is absent when no zoom handlers are passed', () => {
    render(<StudioFloatingHud currentPage={1} totalPages={10} />);
    expect(screen.queryByTestId('hud-zoom-in')).not.toBeInTheDocument();
  });

  it('calls onZoomIn/onZoomOut/onZoomReset and shows the current percentage', () => {
    const onZoomIn = vi.fn();
    const onZoomOut = vi.fn();
    const onZoomReset = vi.fn();
    render(
      <StudioFloatingHud
        currentPage={1} totalPages={10}
        zoomLevel={1.15} onZoomIn={onZoomIn} onZoomOut={onZoomOut} onZoomReset={onZoomReset}
      />
    );
    expect(screen.getByTestId('hud-zoom-reset')).toHaveTextContent('115%');
    fireEvent.click(screen.getByTestId('hud-zoom-in'));
    expect(onZoomIn).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByTestId('hud-zoom-out'));
    expect(onZoomOut).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByTestId('hud-zoom-reset'));
    expect(onZoomReset).toHaveBeenCalledOnce();
  });

  it('is hidden in dual-compare mode, even with handlers passed — the scan pane it would drive is unmounted then', () => {
    render(
      <StudioFloatingHud
        currentPage={1} totalPages={10}
        onZoomIn={vi.fn()} onZoomOut={vi.fn()} onZoomReset={vi.fn()}
        isDualCompare
      />
    );
    expect(screen.queryByTestId('hud-zoom-in')).not.toBeInTheDocument();
  });
});

describe('StudioFloatingHud — editor zoom cluster', () => {
  it('is absent when no editor zoom handlers are passed', () => {
    render(<StudioFloatingHud currentPage={1} totalPages={10} />);
    expect(screen.queryByTestId('hud-editor-zoom-in')).not.toBeInTheDocument();
  });

  it('calls onEditorZoomIn/onEditorZoomOut/onEditorZoomReset independently of the PDF zoom cluster', () => {
    const onZoomIn = vi.fn();
    const onEditorZoomIn = vi.fn();
    const onEditorZoomOut = vi.fn();
    const onEditorZoomReset = vi.fn();
    render(
      <StudioFloatingHud
        currentPage={1} totalPages={10}
        zoomLevel={1} onZoomIn={onZoomIn} onZoomOut={vi.fn()} onZoomReset={vi.fn()}
        editorZoomLevel={1.3} onEditorZoomIn={onEditorZoomIn} onEditorZoomOut={onEditorZoomOut} onEditorZoomReset={onEditorZoomReset}
      />
    );
    expect(screen.getByTestId('hud-editor-zoom-reset')).toHaveTextContent('130%');
    fireEvent.click(screen.getByTestId('hud-editor-zoom-in'));
    expect(onEditorZoomIn).toHaveBeenCalledOnce();
    expect(onZoomIn).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId('hud-editor-zoom-out'));
    expect(onEditorZoomOut).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByTestId('hud-editor-zoom-reset'));
    expect(onEditorZoomReset).toHaveBeenCalledOnce();
  });

  it('is hidden in dual-compare mode — the editor pane it would drive isn\'t on screen then', () => {
    render(
      <StudioFloatingHud
        currentPage={1} totalPages={10}
        onEditorZoomIn={vi.fn()} onEditorZoomOut={vi.fn()} onEditorZoomReset={vi.fn()}
        isDualCompare
      />
    );
    expect(screen.queryByTestId('hud-editor-zoom-in')).not.toBeInTheDocument();
  });
});

describe('StudioFloatingHud — Fit Both', () => {
  it('is absent when onFitBoth is not passed', () => {
    render(<StudioFloatingHud currentPage={1} totalPages={10} />);
    expect(screen.queryByTestId('hud-fit-both-btn')).not.toBeInTheDocument();
  });

  it('calls onFitBoth when clicked', () => {
    const onFitBoth = vi.fn();
    render(<StudioFloatingHud currentPage={1} totalPages={10} onFitBoth={onFitBoth} />);
    fireEvent.click(screen.getByTestId('hud-fit-both-btn'));
    expect(onFitBoth).toHaveBeenCalledOnce();
  });

  it('is hidden in dual-compare mode — "fit both panes" doesn\'t make sense when one of them is a second PDF pane, not the editor', () => {
    render(<StudioFloatingHud currentPage={1} totalPages={10} onFitBoth={vi.fn()} isDualCompare />);
    expect(screen.queryByTestId('hud-fit-both-btn')).not.toBeInTheDocument();
  });
});

describe('StudioFloatingHud — zoom controls reappear once dual-compare closes', () => {
  it('shows all three zoom controls again when isDualCompare goes back to false', () => {
    const props = {
      currentPage: 1, totalPages: 10,
      onZoomIn: vi.fn(), onZoomOut: vi.fn(),
      onEditorZoomIn: vi.fn(), onEditorZoomOut: vi.fn(),
      onFitBoth: vi.fn(),
    };
    const { rerender } = render(<StudioFloatingHud {...props} isDualCompare />);
    expect(screen.queryByTestId('hud-zoom-in')).not.toBeInTheDocument();
    expect(screen.queryByTestId('hud-editor-zoom-in')).not.toBeInTheDocument();
    expect(screen.queryByTestId('hud-fit-both-btn')).not.toBeInTheDocument();

    rerender(<StudioFloatingHud {...props} isDualCompare={false} />);
    expect(screen.getByTestId('hud-zoom-in')).toBeInTheDocument();
    expect(screen.getByTestId('hud-editor-zoom-in')).toBeInTheDocument();
    expect(screen.getByTestId('hud-fit-both-btn')).toBeInTheDocument();
  });
});
