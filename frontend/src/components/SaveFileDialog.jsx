import { useEffect, useState } from 'react';

// Fallback "Save As" prompt for browsers without window.showSaveFilePicker
// (Firefox/Safari) — a web page can't show a real folder picker there at
// all (hard platform limit, not something to work around), so this just
// lets the user confirm/edit the filename before the browser's own
// default-download-folder save kicks in, same spirit as every other modal
// in this app (RetryPagesModal, StructureDeleteConfirmModal, etc.).
export default function SaveFileDialog({ open, suggestedName, onCancel, onConfirm }) {
  const [filename, setFilename] = useState(suggestedName || '');

  useEffect(() => {
    if (open) setFilename(suggestedName || '');
  }, [open, suggestedName]);

  useEffect(() => {
    if (!open) return undefined;
    function handleKeyDown(e) {
      if (e.key === 'Escape') onCancel();
    }
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [open, onCancel]);

  if (!open) return null;

  function handleSave() {
    const trimmed = filename.trim();
    if (!trimmed) return;
    onConfirm(trimmed);
  }

  return (
    <div className="modal-overlay" data-testid="save-file-dialog-backdrop" onClick={onCancel}>
      <div className="modal-panel" data-testid="save-file-dialog" onClick={(e) => e.stopPropagation()}>
        <div className="modal-title">Save As</div>
        <div className="modal-subtitle">
          This browser can't show a folder picker — the file saves to your downloads folder with this name.
        </div>
        <input
          type="text"
          className="save-file-dialog-input"
          value={filename}
          onChange={(e) => setFilename(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') handleSave(); }}
          autoFocus
          data-testid="save-file-dialog-input"
        />
        <div className="modal-actions" style={{ gap: 8 }}>
          <button className="btn btn-sm btn-secondary" data-testid="save-file-dialog-cancel" onClick={onCancel}>
            Cancel
          </button>
          <button
            className="btn btn-sm btn-primary"
            data-testid="save-file-dialog-save"
            disabled={!filename.trim()}
            onClick={handleSave}
          >
            Save
          </button>
        </div>
      </div>
    </div>
  );
}
