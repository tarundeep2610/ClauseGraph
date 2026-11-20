import { useEffect } from 'react';

export default function StudioRail({
  structureOpen,
  onToggleStructure,
  indexBuilt = true,
  reviewOpen,
  onToggleReview,
  reviewCount = 0,
  actionItemsOpen,
  onToggleActionItems,
  actionItemsCount = 0,
}) {
  // Keyboard shortcuts: Ctrl+1 (Outline), Ctrl+2 (Review), Ctrl+3 (Actions)
  useEffect(() => {
    function handleKeyDown(e) {
      if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey) {
        if (e.key === '1') {
          e.preventDefault();
          onToggleStructure();
        } else if (e.key === '2') {
          e.preventDefault();
          onToggleReview();
        } else if (e.key === '3' && onToggleActionItems) {
          e.preventDefault();
          onToggleActionItems();
        }
      }
    }
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onToggleStructure, onToggleReview, onToggleActionItems]);

  return (
    <aside className="task-rail studio-rail" data-testid="studio-rail">
      <div
        className="rail-btn-wrap"
        title={indexBuilt ? 'Document Outline (Ctrl+1)' : 'No index built yet — click Build Index & Reference Links first'}
        onClick={onToggleStructure}
        data-testid="studio-rail-outline-wrap"
      >
        {/* Not a native `disabled` button — the click handler lives on the
            wrapping div above, and a disabled button would swallow clicks on
            itself before they bubble, silently eating the toast-on-click
            this is supposed to show instead of just doing nothing. */}
        <button
          type="button"
          className={`rail-icon-btn${structureOpen ? ' active' : ''}${indexBuilt ? '' : ' disabled'}`}
          aria-disabled={!indexBuilt}
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

      {onToggleActionItems && (
        <div
          className="rail-btn-wrap"
          title={`Actionable Items${actionItemsCount > 0 ? ` (${actionItemsCount})` : ''} (Ctrl+3)`}
          onClick={onToggleActionItems}
          data-testid="studio-rail-action-items-wrap"
        >
          <button
            type="button"
            className={`rail-icon-btn${actionItemsOpen ? ' active' : ''}`}
            data-testid="action-items-sidebar-toggle-btn"
            aria-label="Toggle actionable items"
          >
            <i className="fa-solid fa-list-check" />
            {actionItemsCount > 0 && (
              <span className="rail-badge-alert" data-testid="studio-rail-action-items-badge">
                {actionItemsCount}
              </span>
            )}
          </button>
          <span className="rail-btn-label">Actions</span>
        </div>
      )}
    </aside>
  );
}
