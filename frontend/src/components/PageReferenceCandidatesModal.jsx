import { useEffect } from 'react';

// Opened by PageReferenceBadges when a mention has more than one candidate
// (e.g. "para 1" — this document restarts pleading-point numbering in nearly
// every affidavit, so point:1 alone exists on several pages). Modeled
// directly on StructureDeleteConfirmModal's structure/Escape handling; a
// modal reads far better than the small anchored dropdown this replaced.
export default function PageReferenceCandidatesModal({ target, onClose, onSelect }) {
  useEffect(() => {
    if (!target) return undefined;
    function handleKeyDown(e) {
      if (e.key === 'Escape') onClose();
    }
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [target, onClose]);

  if (!target) return null;

  function renderGroup(label, candidates) {
    if (!candidates.length) return null;
    return (
      <div key={label}>
        <div className="modal-subtitle" style={{ marginBottom: 8 }}>{label}</div>
        <div className="modal-options">
          {candidates.map((c, i) => (
            <button
              key={`${label}-${i}`}
              type="button"
              className="modal-option-btn"
              onClick={() => onSelect(c)}
              data-testid={`page-reference-candidate-${label === 'Exact Match' ? 'exact' : 'approx'}-${i}`}
            >
              <span>Page {c.page}</span>
              {(c.scope_label || c.own_label) && (
                <span className="modal-option-count">{c.scope_label || c.own_label}</span>
              )}
            </button>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="modal-overlay" data-testid="page-reference-candidates-modal-backdrop" onClick={onClose}>
      <div className="modal-panel" data-testid="page-reference-candidates-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-title">{target.mentionRaw}</div>
        {renderGroup('Exact Match', target.exact)}
        {renderGroup('≈ Possible', target.approximate)}
      </div>
    </div>
  );
}
