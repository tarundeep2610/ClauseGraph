import ActionItemsList from '../ActionItemsList';

// Drawer shell, structurally copying StructureTreePanel.jsx's header/close-
// button pattern. Unlike Outline, this drawer always opens on rail click
// regardless of build state (see StudioRail.jsx/ViewerPanel.jsx for why —
// the extraction trigger lives INSIDE this panel's own header, so gating
// the drawer open on "already built" would make that trigger unreachable).
// "Nothing extracted yet" is communicated by the empty state below, never
// by refusing to open.
export default function ActionableItemsPanel({
  open, onClose, onRebuild, rebuilding = false, built = false,
  items = [], typeCounts, sortMode, onSortModeChange, activePage, onSelect,
}) {
  if (!open) return null;

  return (
    <div className="structure-sidebar" data-testid="action-items-sidebar">
      <div className="view-box-header">
        <span><i className="fa-solid fa-list-check" /> Actionable Items</span>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          {onRebuild && (
            <button
              className={`btn btn-sm ${built ? 'btn-secondary' : 'btn-primary'}`}
              onClick={onRebuild}
              disabled={rebuilding}
              data-testid="action-items-rebuild-btn"
              title={built
                ? 'Re-extract deadlines, obligations, and conditions from this document'
                : 'Extract deadlines, obligations, and conditions from this document'}
            >
              <i className="fa-solid fa-bolt" /> {rebuilding ? 'Extracting…' : built ? 'Re-extract' : 'Extract Action Items'}
            </button>
          )}
          {onClose && (
            <button
              type="button"
              className="drawer-close-btn"
              onClick={onClose}
              title="Close Actionable Items Drawer"
              data-testid="action-items-close-btn"
              aria-label="Close Actionable Items Drawer"
            >
              <i className="fa-solid fa-xmark" />
            </button>
          )}
        </div>
      </div>
      <div className="view-box-content">
        {!built ? (
          <div className="action-items-empty-state">
            <i className="fa-solid fa-list-check action-items-empty-icon" />
            <p className="action-items-empty-title">No action items extracted yet</p>
            <p className="action-items-empty-description">
              Scan this document to find deadlines, obligations, and conditional triggers
              (bid dates, notice periods, penalty/termination clauses) and link each one to its page.
            </p>
          </div>
        ) : !items.length ? (
          <div className="action-items-empty-state">
            <i className="fa-solid fa-circle-check action-items-empty-icon" />
            <p className="action-items-empty-title">No action items found</p>
            <p className="action-items-empty-description">
              This document was scanned but no deadlines, obligations, or conditions were detected.
            </p>
          </div>
        ) : (
          <ActionItemsList
            items={items}
            typeCounts={typeCounts}
            sortMode={sortMode}
            onSortModeChange={onSortModeChange}
            activePage={activePage}
            onSelect={onSelect}
          />
        )}
      </div>
    </div>
  );
}
