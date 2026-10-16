import { useEffect, useRef } from 'react';

// Minimal, generic toast — no reusable notification system existed anywhere
// in this app before (StructureTree.jsx has a one-off local drag-feedback
// div, not a portable component). Single-message, auto-dismissing; a
// stacking/queueing system can be built later if more than one concurrent
// use case ever needs it.
export default function Toast({ message, onDismiss, duration = 3200 }) {
  // onDismiss must NOT be a dependency of the timer effect below: callers
  // that re-render on an interval (e.g. ViewerPanel.jsx's 2.5s job-details
  // poll while Studio is open) pass a fresh inline arrow function every
  // render. With onDismiss in the deps array, every poll tick (faster than
  // `duration`) cancels and restarts the timeout before it ever fires,
  // making the toast effectively permanent for as long as polling continues
  // — confirmed as a real bug via Playwright, not just a theoretical one.
  const onDismissRef = useRef(onDismiss);
  onDismissRef.current = onDismiss;

  useEffect(() => {
    if (!message) return undefined;
    const t = setTimeout(() => onDismissRef.current(), duration);
    return () => clearTimeout(t);
  }, [message, duration]);

  if (!message) return null;

  return (
    <div className="toast" role="status" data-testid="toast">
      {message}
    </div>
  );
}
