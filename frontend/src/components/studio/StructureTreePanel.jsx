import StructureTree from '../StructureTree';

// Sprint 4 ("Structure Tree Drawer") shell: a thin, fixed-width (280px,
// matching the mockup's actual slide-7 markup) presentational wrapper around
// the existing, fully-tested StructureTree — purely layout (header + "+ Add"
// button + content area + slide transition). It deliberately does not touch
// StructureTree's own props/behavior at all; every prop other than
// `open`/`onAddClick` is forwarded straight through unchanged. `onAddClick`
// is this panel's own header "+ Add" button handler (root-level add) —
// distinct from the `onAddItem` prop callers pass through `...treeProps`,
// which is StructureTree's own per-node "add child" handler.
//
// Deliberately does NOT use the generic `.view-box` card chrome for its
// outer container (10px radius + tinted background) — the mockup's drawer
// sits flush against the rail (flat border-right only, no radius, no
// background distinct from the app surface, no shadow), not as a floating
// card nested in the row. `.view-box-header`/`.view-box-content` are still
// reused for internal layout (padding/flex/scroll), which is generic and
// mockup-neutral, not "card" styling.
export default function StructureTreePanel({ open, onAddClick, onClose, ...treeProps }) {
  if (!open) return null;

  return (
    <div className="structure-sidebar" data-testid="structure-sidebar">
      <div className="view-box-header">
        <span><i className="fa-solid fa-sitemap" /> Document Structure</span>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          {onAddClick && (
            <button className="btn btn-sm btn-secondary" onClick={onAddClick} data-testid="structure-add-btn">
              <i className="fa-solid fa-plus" /> Add
            </button>
          )}
          {onClose && (
            <button
              type="button"
              className="drawer-close-btn"
              onClick={onClose}
              title="Close Outline Drawer"
              data-testid="structure-close-btn"
              aria-label="Close Outline Drawer"
            >
              <i className="fa-solid fa-xmark" />
            </button>
          )}
        </div>
      </div>
      <div className="view-box-content">
        <StructureTree {...treeProps} />
      </div>
    </div>
  );
}
