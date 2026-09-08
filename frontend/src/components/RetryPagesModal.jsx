import { useEffect } from 'react';

const OPTIONS = [
  { scope: 'failed', label: 'Failed only', countKey: 'failed' },
  { scope: 'review', label: 'Low Confidence only', countKey: 'review' },
  { scope: 'all', label: 'All pages', countKey: 'total' },
];

export default function RetryPagesModal({ open, onClose, onConfirm, counts }) {
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
    <div className="modal-overlay" data-testid="retry-modal-backdrop" onClick={onClose}>
      <div className="modal-panel" data-testid="retry-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-title">Retry Pages</div>
        <div className="modal-subtitle">Choose which pages to re-run at escalation quality.</div>
        <div className="modal-options">
          {OPTIONS.map(({ scope, label, countKey }) => {
            const count = counts?.[countKey] ?? 0;
            return (
              <button
                key={scope}
                className="modal-option-btn"
                data-testid={`retry-option-${scope}`}
                disabled={count === 0}
                onClick={() => onConfirm(scope)}
              >
                <span>{label}</span>
                <span className="modal-option-count">{count}</span>
              </button>
            );
          })}
        </div>
        <div className="modal-actions">
          <button className="btn btn-sm btn-secondary" data-testid="retry-modal-cancel" onClick={onClose}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
