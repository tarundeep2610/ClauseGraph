import { useEffect } from 'react';

// Small yes/no confirm — RetryPagesModal's own layout is a multi-option
// list, not a plain confirm, so this is a fresh (but visually consistent)
// component rather than a repurposing of it.
export default function StructureDeleteConfirmModal({ open, label, onClose, onConfirm, submitting }) {
  useEffect(() => {
    if (!open) return;
    function handleKeyDown(e) {
      if (e.key === 'Escape') onClose();
    }
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="modal-overlay" data-testid="structure-delete-modal-backdrop" onClick={onClose}>
      <div className="modal-panel" data-testid="structure-delete-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-title">Delete Structure Entry</div>
        <div className="modal-subtitle">
          {label ? `Remove "${label}" from the document structure?` : 'Remove this entry from the document structure?'}
        </div>
        <div className="modal-actions" style={{ gap: 8 }}>
          <button className="btn btn-sm btn-secondary" onClick={onClose} data-testid="structure-delete-modal-cancel">
            Cancel
          </button>
          <button
            className="btn btn-sm btn-danger"
            onClick={onConfirm}
            disabled={!!submitting}
            data-testid="structure-delete-modal-confirm"
          >
            {submitting ? 'Deleting…' : 'Delete'}
          </button>
        </div>
      </div>
    </div>
  );
}
