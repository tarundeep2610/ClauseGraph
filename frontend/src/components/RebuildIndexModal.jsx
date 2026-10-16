import { useEffect, useState } from 'react';

// Shown only when a job already has an index (see ViewerPanel.jsx's
// indexBuilt check) — a brand-new, never-indexed job skips this entirely and
// builds both outline + reference links directly, since "Reference Links
// Only" has no value with nothing to link against yet.
//
// Multi-select, not either/or: structure detection (the outline) and
// cross-reference *target* detection happen in the same per-page pass, so
// there's no cheaper way to rebuild the outline without reference links
// coming along in that same pass — checking "Index" always refreshes both.
// The genuinely separable, cheaper path is reference links *alone*, against
// targets that already exist (POST .../references/build-mentions) — that's
// only possible when Index is left unchecked.
export default function RebuildIndexModal({ open, onClose, onConfirm }) {
  const [rebuildIndex, setRebuildIndex] = useState(true);
  const [rebuildLinks, setRebuildLinks] = useState(true);

  // Reset to the "rebuild everything" default each time the modal opens,
  // rather than remembering the previous choice across different jobs.
  // Deliberately depends on ONLY `open`, not `onClose`: ViewerPanel re-renders
  // every ~2.5s (AppShell's job-details poll) for as long as Studio stays
  // open, passing a brand-new inline `onClose` function each time. With
  // `onClose` in this effect's deps, every poll tick re-ran it and silently
  // reset both checkboxes back to checked out from under the user mid-choice
  // — confirmed as a real bug (checking "Reference Links Only" would revert
  // to "Index" checked within a couple of seconds), same root cause as an
  // earlier bug in Toast.jsx's dismiss timer.
  useEffect(() => {
    if (!open) return;
    setRebuildIndex(true);
    setRebuildLinks(true);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    function handleKeyDown(e) {
      if (e.key === 'Escape') onClose();
    }
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [open, onClose]);

  if (!open) return null;

  const nothingChecked = !rebuildIndex && !rebuildLinks;

  function handleConfirm() {
    if (nothingChecked) return;
    // "Index" checked (with or without "Links") always means a full
    // rebuild — there's no cheaper structure-only call on the backend.
    // Only "Links" alone takes the genuinely cheaper build-mentions path.
    onConfirm(rebuildIndex ? 'full' : 'links-only');
  }

  return (
    <div className="modal-overlay" data-testid="rebuild-index-modal-backdrop" onClick={onClose}>
      <div className="modal-panel" data-testid="rebuild-index-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-title">Rebuild Index & Reference Links</div>
        <div className="modal-subtitle">Choose what to rebuild.</div>
        <div className="modal-options">
          <label className="modal-option-btn" data-testid="rebuild-option-index">
            <input
              type="checkbox"
              checked={rebuildIndex}
              onChange={(e) => setRebuildIndex(e.target.checked)}
              data-testid="rebuild-checkbox-index"
            />
            <span style={{ flex: 1, marginLeft: 10 }}>
              <span style={{ display: 'block' }}>Rebuild Index (Outline)</span>
              <span style={{ display: 'block', fontSize: '0.74rem', color: 'var(--muted)', fontWeight: 400 }}>
                Re-scans the document to rebuild its outline. Reference links are updated too.
              </span>
            </span>
          </label>
          <label className="modal-option-btn" data-testid="rebuild-option-links">
            <input
              type="checkbox"
              checked={rebuildLinks}
              onChange={(e) => setRebuildLinks(e.target.checked)}
              disabled={rebuildIndex}
              data-testid="rebuild-checkbox-links"
            />
            <span style={{ flex: 1, marginLeft: 10 }}>
              <span style={{ display: 'block' }}>Rebuild Reference Links</span>
              <span style={{ display: 'block', fontSize: '0.74rem', color: 'var(--muted)', fontWeight: 400 }}>
                {rebuildIndex
                  ? 'Already included above.'
                  : 'Only updates the reference links (like "see Clause 8") — quicker, and keeps your outline as is.'}
              </span>
            </span>
          </label>
        </div>
        <div className="modal-actions">
          <button className="btn btn-sm btn-secondary" data-testid="rebuild-modal-cancel" onClick={onClose}>
            Cancel
          </button>
          <button
            className="btn btn-sm btn-primary"
            style={{ width: 'auto', marginLeft: 8 }}
            data-testid="rebuild-modal-confirm"
            disabled={nothingChecked}
            onClick={handleConfirm}
          >
            Rebuild
          </button>
        </div>
      </div>
    </div>
  );
}
