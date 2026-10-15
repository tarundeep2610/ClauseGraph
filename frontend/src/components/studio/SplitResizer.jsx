import { useState, useEffect, useRef } from 'react';

export default function SplitResizer({ splitRatio = 50, onSplitChange }) {
  const [isDragging, setIsDragging] = useState(false);
  const containerRef = useRef(null);

  useEffect(() => {
    if (!isDragging) return undefined;

    function handleMouseMove(e) {
      const container = containerRef.current?.parentElement;
      if (!container) return;
      const rect = container.getBoundingClientRect();
      const pointerX = e.clientX - rect.left;
      const pct = (pointerX / rect.width) * 100;
      // Clamp between 20% and 80%
      const clamped = Math.min(80, Math.max(20, Math.round(pct)));
      onSplitChange(clamped);
    }

    function handleMouseUp() {
      setIsDragging(false);
    }

    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);
    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
    };
  }, [isDragging, onSplitChange]);

  return (
    <div
      ref={containerRef}
      className={`split-resizer${isDragging ? ' dragging' : ''}`}
      onMouseDown={() => setIsDragging(true)}
      onDoubleClick={() => onSplitChange(50)}
      title="Drag to resize panes · Double-click to reset 50/50"
      data-testid="split-resizer"
    >
      <div className="resizer-bar" />
      <div className="resizer-pill" data-testid="split-resizer-handle">⋮</div>
      <div className="resizer-snaps" data-testid="split-resizer-snaps">
        <button
          type="button"
          className="btn btn-sm btn-outline"
          onClick={(e) => { e.stopPropagation(); onSplitChange(30); }}
          title="30% Scan / 70% Editor"
          data-testid="split-snap-30"
          style={{ padding: '2px 6px', fontSize: '10px' }}
        >
          30/70
        </button>
        <button
          type="button"
          className="btn btn-sm btn-outline"
          onClick={(e) => { e.stopPropagation(); onSplitChange(50); }}
          title="50% Scan / 50% Editor"
          data-testid="split-snap-50"
          style={{ padding: '2px 6px', fontSize: '10px' }}
        >
          50/50
        </button>
        <button
          type="button"
          className="btn btn-sm btn-outline"
          onClick={(e) => { e.stopPropagation(); onSplitChange(70); }}
          title="70% Scan / 30% Editor"
          data-testid="split-snap-70"
          style={{ padding: '2px 6px', fontSize: '10px' }}
        >
          70/30
        </button>
      </div>
    </div>
  );
}
