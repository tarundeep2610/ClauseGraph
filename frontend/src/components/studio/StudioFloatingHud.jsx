import { useEffect, useRef, useState } from 'react';

export default function StudioFloatingHud({
  currentPage = 1,
  totalPages = 1,
  onNavigate,
  viewMode = 'split',
  onViewModeChange,
  zoomLevel = 1,
  onZoomIn,
  onZoomOut,
  onZoomReset,
  isDualCompare = false,
}) {
  const [pageInput, setPageInput] = useState(String(currentPage));
  const [minimized, setMinimized] = useState(false);
  const [customPos, setCustomPos] = useState(null);
  const [isDragging, setIsDragging] = useState(false);
  const hudRef = useRef(null);
  const dragRef = useRef(null);

  useEffect(() => {
    setPageInput(String(currentPage));
  }, [currentPage]);

  const handleResetPosition = () => {
    setCustomPos(null);
  };

  const handlePointerDown = (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();

    const hudEl = hudRef.current;
    if (!hudEl) return;

    const parent = hudEl.offsetParent || hudEl.parentElement || document.body;
    const parentRect = parent.getBoundingClientRect();
    const hudRect = hudEl.getBoundingClientRect();

    dragRef.current = {
      startX: e.clientX,
      startY: e.clientY,
      startLeft: hudRect.left - parentRect.left,
      startTop: hudRect.top - parentRect.top,
      parentWidth: parentRect.width,
      parentHeight: parentRect.height,
      hudWidth: hudRect.width,
      hudHeight: hudRect.height,
    };

    setIsDragging(true);
  };

  useEffect(() => {
    if (!isDragging) return;

    const MARGIN = 10;

    const handlePointerMove = (e) => {
      if (!dragRef.current) return;
      const { startX, startY, startLeft, startTop, parentWidth, parentHeight, hudWidth, hudHeight } = dragRef.current;
      const dx = e.clientX - startX;
      const dy = e.clientY - startY;

      const maxLeft = Math.max(MARGIN, parentWidth - hudWidth - MARGIN);
      const maxTop = Math.max(MARGIN, parentHeight - hudHeight - MARGIN);

      const clampedLeft = Math.max(MARGIN, Math.min(startLeft + dx, maxLeft));
      const clampedTop = Math.max(MARGIN, Math.min(startTop + dy, maxTop));

      setCustomPos({ left: Math.round(clampedLeft), top: Math.round(clampedTop) });
    };

    const handlePointerUp = () => {
      setIsDragging(false);
      dragRef.current = null;
    };

    window.addEventListener('pointermove', handlePointerMove);
    window.addEventListener('pointerup', handlePointerUp);
    window.addEventListener('pointercancel', handlePointerUp);
    window.addEventListener('mousemove', handlePointerMove);
    window.addEventListener('mouseup', handlePointerUp);

    return () => {
      window.removeEventListener('pointermove', handlePointerMove);
      window.removeEventListener('pointerup', handlePointerUp);
      window.removeEventListener('pointercancel', handlePointerUp);
      window.removeEventListener('mousemove', handlePointerMove);
      window.removeEventListener('mouseup', handlePointerUp);
    };
  }, [isDragging]);

  useEffect(() => {
    if (!customPos || !hudRef.current) return;

    const handleResize = () => {
      const hudEl = hudRef.current;
      if (!hudEl) return;
      const parent = hudEl.offsetParent || hudEl.parentElement || document.body;
      const parentRect = parent.getBoundingClientRect();
      const hudRect = hudEl.getBoundingClientRect();
      const MARGIN = 10;
      const maxLeft = Math.max(MARGIN, parentRect.width - hudRect.width - MARGIN);
      const maxTop = Math.max(MARGIN, parentRect.height - hudRect.height - MARGIN);

      setCustomPos((prev) => {
        if (!prev) return null;
        const newLeft = Math.max(MARGIN, Math.min(prev.left, maxLeft));
        const newTop = Math.max(MARGIN, Math.min(prev.top, maxTop));
        if (newLeft === prev.left && newTop === prev.top) return prev;
        return { left: Math.round(newLeft), top: Math.round(newTop) };
      });
    };

    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, [customPos]);

  function handleCommit() {
    const val = parseInt(pageInput, 10);
    if (!isNaN(val) && val >= 1) {
      const clamped = Math.min(val, totalPages || val);
      setPageInput(String(clamped));
      if (onNavigate) onNavigate(clamped);
    } else {
      setPageInput(String(currentPage));
    }
  }

  function handleKeyDown(e) {
    if (e.key === 'Enter') {
      handleCommit();
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      const next = Math.min(currentPage + 1, totalPages || currentPage + 1);
      if (onNavigate) onNavigate(next);
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      const prev = Math.max(currentPage - 1, 1);
      if (onNavigate) onNavigate(prev);
    }
  }

  const effectiveTotal = totalPages || 1;

  const customStyle = customPos
    ? {
        left: `${customPos.left}px`,
        top: `${customPos.top}px`,
        bottom: 'auto',
        transform: 'none',
      }
    : undefined;

  return (
    <div
      ref={hudRef}
      className={`floating-nav-hud${minimized ? ' minimized' : ''}${isDragging ? ' is-dragging' : ''}`}
      style={customStyle}
      data-testid="studio-floating-hud"
    >
      <div
        className="hud-drag-handle"
        title="Drag to reposition • Double-click to reset"
        onPointerDown={handlePointerDown}
        onMouseDown={handlePointerDown}
        onDoubleClick={handleResetPosition}
        data-testid="hud-drag-handle"
        role="button"
        tabIndex={0}
        aria-label="Reposition navigation HUD"
      >
        <i className="fa-solid fa-grip-vertical" />
      </div>
      <div className="hud-cluster hud-page-cluster">
        <button
          type="button"
          className="hud-btn"
          onClick={() => onNavigate && onNavigate(Math.max(currentPage - 1, 1))}
          disabled={currentPage <= 1}
          title="Previous page (PageUp or Down)"
          data-testid="hud-prev-page-btn"
        >
          <i className="fa-solid fa-chevron-left" />
        </button>

        <div className="hud-page-box" data-testid="hud-page-box">
          <span className="hud-page-label">Page</span>
          <input
            type="text"
            className="hud-page-input"
            value={pageInput}
            onChange={(e) => setPageInput(e.target.value)}
            onBlur={handleCommit}
            onKeyDown={handleKeyDown}
            data-testid="hud-page-input"
            aria-label="Current page number"
          />
          <span className="hud-page-total">of {effectiveTotal}</span>
        </div>

        <button
          type="button"
          className="hud-btn"
          onClick={() => onNavigate && onNavigate(Math.min(currentPage + 1, effectiveTotal))}
          disabled={currentPage >= effectiveTotal}
          title="Next page (PageDown or Up)"
          data-testid="hud-next-page-btn"
        >
          <i className="fa-solid fa-chevron-right" />
        </button>
      </div>

      {!minimized && <div className="hud-divider hud-divider-opt" />}

      {!minimized && onViewModeChange && !isDualCompare && (
        <div className="hud-cluster hud-view-toggle" role="group" aria-label="View mode">
          <button
            type="button"
            className={`hud-view-btn${viewMode === 'split' ? ' active' : ''}`}
            onClick={() => onViewModeChange('split')}
            data-testid="hud-view-split"
            title="Split: 50% Scan + 50% Editor"
          >
            <i className="fa-solid fa-columns" /> Split
          </button>
          <button
            type="button"
            className={`hud-view-btn${viewMode === 'pdf' ? ' active' : ''}`}
            onClick={() => onViewModeChange('pdf')}
            data-testid="hud-view-pdf"
            title="Scan: Full-width PDF document view"
            aria-label="PDF Only"
          >
            <i className="fa-regular fa-file-pdf" /> Scan
          </button>
          <button
            type="button"
            className={`hud-view-btn${viewMode === 'result' ? ' active' : ''}`}
            onClick={() => onViewModeChange('result')}
            data-testid="hud-view-result"
            title="Editor: Full-width WYSIWYG rich text editor"
            aria-label="Result Only"
          >
            <i className="fa-solid fa-file-lines" /> Editor
          </button>
        </div>
      )}

      {!minimized && onZoomIn && onZoomOut && (
        <>
          <div className="hud-divider hud-divider-opt" />
          <div className="hud-cluster hud-zoom-cluster">
            <button
              type="button"
              className="hud-btn"
              onClick={onZoomOut}
              title="Zoom out"
              data-testid="hud-zoom-out"
            >
              <i className="fa-solid fa-minus" />
            </button>
            {onZoomReset && (
              <button
                type="button"
                className="hud-btn"
                onClick={onZoomReset}
                title="Reset zoom to fit width"
                data-testid="hud-zoom-reset"
              >
                {Math.round((zoomLevel || 1) * 100)}%
              </button>
            )}
            <button
              type="button"
              className="hud-btn"
              onClick={onZoomIn}
              title="Zoom in"
              data-testid="hud-zoom-in"
            >
              <i className="fa-solid fa-plus" />
            </button>
          </div>
        </>
      )}

      <div className="hud-divider" />

      {customPos !== null && (
        <button
          type="button"
          className="hud-btn hud-reset-pos-btn"
          onClick={handleResetPosition}
          title="Reset dock to default bottom position"
          data-testid="hud-reset-pos-btn"
          aria-label="Reset dock position"
        >
          <i className="fa-solid fa-arrow-rotate-left" />
        </button>
      )}

      <button
        type="button"
        className="hud-btn hud-dock-btn"
        onClick={() => setMinimized((v) => !v)}
        title={minimized ? 'Expand navigation HUD' : 'Dock navigation HUD to compact pill'}
        data-testid={minimized ? 'hud-expand-btn' : 'hud-dock-btn'}
      >
        <i className={`fa-solid ${minimized ? 'fa-chevron-up' : 'fa-chevron-down'}`} />
        <span style={{ fontSize: '10px', marginLeft: 3 }}>{minimized ? 'Expand' : 'Dock'}</span>
      </button>
    </div>
  );
}
