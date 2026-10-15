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
