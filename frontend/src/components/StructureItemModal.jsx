import { useEffect, useState } from 'react';

// Shared add/edit form for a Structure-tree entry. `mode` decides the title
// and whether the submit payload is a create or an update; `initialValues`
// (heading/startPage/endPage) seeds the fields for edit, or sensible
// defaults for add. Not optimistic — the caller is expected to keep the
// modal open (via `submitting`) until the server round-trip resolves, then
// close it once the version-bump-driven refetch has updated the tree.
export default function StructureItemModal({ open, mode = 'add', initialValues, onClose, onSubmit, submitting, error }) {
  const [heading, setHeading] = useState('');
  const [startPage, setStartPage] = useState('');
  const [endPage, setEndPage] = useState('');
  const [endPageTouched, setEndPageTouched] = useState(false);

  useEffect(() => {
    if (!open) return;
    setHeading(initialValues?.heading || '');
    setStartPage(initialValues?.startPage != null ? String(initialValues.startPage) : '');
    setEndPage(initialValues?.endPage != null ? String(initialValues.endPage) : '');
    setEndPageTouched(false);
  }, [open, initialValues]);

  useEffect(() => {
    if (!open) return;
    function handleKeyDown(e) {
      if (e.key === 'Escape') onClose();
    }
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [open, onClose]);

  if (!open) return null;

  function handleStartPageChange(value) {
    setStartPage(value);
    // Until the reader explicitly edits end-page themselves, keep it
    // mirroring start-page — avoids forcing every single-page edit to
    // retype the same number twice.
    if (!endPageTouched) setEndPage(value);
  }

  function handleSubmit(e) {
    e.preventDefault();
    const sp = parseInt(startPage, 10);
    const ep = parseInt(endPage, 10);
    if (!heading.trim() || Number.isNaN(sp) || Number.isNaN(ep)) return;
    onSubmit({ heading: heading.trim(), startPage: sp, endPage: ep });
  }

  const title = mode === 'edit' ? 'Edit Structure Entry' : 'Add Structure Entry';

  return (
    <div className="modal-overlay" data-testid="structure-item-modal-backdrop" onClick={onClose}>
      <div className="modal-panel" data-testid="structure-item-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-title">{title}</div>
        <div className="modal-subtitle">Heading name and the page range it covers.</div>
        <form onSubmit={handleSubmit}>
          <div className="form-group">
            <label>Heading</label>
            <input
              type="text"
              value={heading}
              onChange={(e) => setHeading(e.target.value)}
              data-testid="structure-item-heading-input"
              autoFocus
            />
          </div>
          <div className="params-grid">
            <div className="form-group">
              <label>Start page</label>
              <input
                type="number"
                min="1"
                value={startPage}
                onChange={(e) => handleStartPageChange(e.target.value)}
                data-testid="structure-item-start-page-input"
              />
            </div>
            <div className="form-group">
              <label>End page</label>
              <input
                type="number"
                min="1"
                value={endPage}
                onChange={(e) => { setEndPage(e.target.value); setEndPageTouched(true); }}
                data-testid="structure-item-end-page-input"
              />
            </div>
          </div>
          {error && <div style={{ color: 'var(--error)', fontSize: '0.8rem', marginTop: 6 }} data-testid="structure-item-modal-error">{error}</div>}
          <div className="modal-actions" style={{ marginTop: 16, gap: 8 }}>
            <button type="button" className="btn btn-sm btn-secondary" onClick={onClose} data-testid="structure-item-modal-cancel">
              Cancel
            </button>
            <button type="submit" className="btn btn-sm btn-primary" disabled={!!submitting} data-testid="structure-item-modal-submit">
              {submitting ? 'Saving…' : 'Save'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
