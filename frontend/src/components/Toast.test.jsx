import { describe, it, expect, vi } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import Toast from './Toast';

describe('Toast', () => {
  it('renders nothing when message is falsy', () => {
    render(<Toast message={null} onDismiss={vi.fn()} />);
    expect(screen.queryByTestId('toast')).not.toBeInTheDocument();
  });

  it('renders the message when set', () => {
    render(<Toast message="Hello" onDismiss={vi.fn()} />);
    expect(screen.getByTestId('toast')).toHaveTextContent('Hello');
  });

  it('calls onDismiss automatically after the duration', () => {
    vi.useFakeTimers();
    const onDismiss = vi.fn();
    render(<Toast message="Hello" onDismiss={onDismiss} duration={1000} />);
    expect(onDismiss).not.toHaveBeenCalled();
    act(() => { vi.advanceTimersByTime(1000); });
    expect(onDismiss).toHaveBeenCalledOnce();
    vi.useRealTimers();
  });

  it('still auto-dismisses even if the parent re-renders with a brand-new onDismiss function on every tick (e.g. a caller polling faster than `duration`) — regression test for a real bug found via Playwright: onDismiss used to be a timer-effect dependency, so a fresh function reference on every poll cancelled and restarted the timeout before it ever fired, making the toast effectively permanent', () => {
    vi.useFakeTimers();
    const { rerender } = render(<Toast message="Hello" onDismiss={() => {}} duration={1000} />);

    // Simulate a parent re-rendering every 250ms (faster than duration) with
    // a brand-new inline onDismiss each time, same as ViewerPanel.jsx's
    // 2.5s job-details poll does in practice. 3 ticks = 750ms, still under
    // the 1000ms duration.
    let dismissed = false;
    for (let i = 0; i < 3; i++) {
      act(() => { vi.advanceTimersByTime(250); });
      rerender(<Toast message="Hello" onDismiss={() => { dismissed = true; }} duration={1000} />);
    }
    expect(dismissed).toBe(false); // only 750ms of the 1000ms has elapsed

    act(() => { vi.advanceTimersByTime(300); }); // crosses the 1000ms mark
    expect(dismissed).toBe(true);
    vi.useRealTimers();
  });
});
