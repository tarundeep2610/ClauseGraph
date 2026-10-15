import { useEffect } from 'react';

export default function StudioRail({
  structureOpen,
  onToggleStructure,
  reviewOpen,
  onToggleReview,
  reviewCount = 0,
}) {
  // Keyboard shortcuts: Ctrl+1 (Outline) and Ctrl+2 (Review)
  useEffect(() => {
    function handleKeyDown(e) {
      if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey) {
        if (e.key === '1') {
          e.preventDefault();
          onToggleStructure();
        } else if (e.key === '2') {
          e.preventDefault();
          onToggleReview();
        }
      }
    }
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onToggleStructure, onToggleReview]);

  return (
    <aside className="task-rail studio-rail" data-testid="studio-rail">
      <div
        className="rail-btn-wrap"
        title="Document Outline (Ctrl+1)"
        onClick={onToggleStructure}
        data-testid="studio-rail-outline-wrap"
      >
        <button
          type="button"
          className={`rail-icon-btn${structureOpen ? ' active' : ''}`}
          data-testid="structure-sidebar-toggle-btn"
          aria-label="Toggle document structure tree"
        >
          <i className="fa-solid fa-sitemap" />
        </button>
        <span className="rail-btn-label">Outline</span>
      </div>

      <div
        className="rail-btn-wrap"
        title={`Quality Review Flags${reviewCount > 0 ? ` (${reviewCount})` : ''} (Ctrl+2)`}
        onClick={onToggleReview}
        data-testid="studio-rail-review-wrap"
      >
        <button
          type="button"
          className={`rail-icon-btn${reviewOpen ? ' active' : ''}`}
          data-testid="review-sidebar-toggle-btn"
          aria-label="Toggle quality review queue"
        >
          <i className="fa-solid fa-triangle-exclamation" />
          {reviewCount > 0 && (
            <span className="rail-badge-alert" data-testid="studio-rail-review-badge">
              {reviewCount}
            </span>
          )}
        </button>
        <span className="rail-btn-label">Review</span>
      </div>
    </aside>
  );
}
