import { useEffect, useState } from 'react';

// Shared page navigation for the PDF pane and Result pane (and the
// StructureReadingModal, in local page-range mode) — always operates in
// whatever numbering its caller hands it (global document pages for the
// main viewer, local 1..N pages for a reading-modal range), never guesses.
// Clamps an out-of-range jump to [minPage, maxPage] rather than rejecting it
// outright, with a brief visible hint so the clamp isn't silently confusing.
export default function PageNavBar({ currentPage, minPage = 1, maxPage, onNavigate }) {
  const [inputValue, setInputValue] = useState(String(currentPage));
  const [hint, setHint] = useState('');

  useEffect(() => {
    setInputValue(String(currentPage));
  }, [currentPage]);

  function commit(raw) {
    const n = parseInt(raw, 10);
    if (Number.isNaN(n)) {
      setInputValue(String(currentPage));
      return;
    }
    const hi = maxPage || n;
    const clamped = Math.min(Math.max(n, minPage), hi);
    setInputValue(String(clamped));
    if (clamped !== n) {
      setHint(`Clamped to ${clamped}`);
      setTimeout(() => setHint(''), 2000);
    }
    if (clamped !== currentPage) onNavigate(clamped);
  }

  return (
    <div className="page-nav-bar" data-testid="page-nav-bar">
      <button
        className="btn btn-sm btn-secondary"
        onClick={() => onNavigate(Math.max(minPage, currentPage - 1))}
        disabled={currentPage <= minPage}
        data-testid="page-nav-prev"
      >
        <i className="fa-solid fa-chevron-left" />
      </button>
      <span className="page-nav-jump">
        <input
          type="number"
          className="page-nav-jump-input"
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') commit(inputValue); }}
          onBlur={() => commit(inputValue)}
          data-testid="page-nav-jump-input"
        />
        {maxPage ? <span className="page-nav-total"> / {maxPage}</span> : null}
        <button
          className="btn btn-sm btn-secondary"
          onClick={() => commit(inputValue)}
          data-testid="page-nav-jump-btn"
        >
          Go
        </button>
      </span>
      <button
        className="btn btn-sm btn-secondary"
        onClick={() => onNavigate(Math.min(maxPage || currentPage + 1, currentPage + 1))}
        disabled={!!maxPage && currentPage >= maxPage}
        data-testid="page-nav-next"
      >
        <i className="fa-solid fa-chevron-right" />
      </button>
      {hint && <span className="page-nav-hint" data-testid="page-nav-hint">{hint}</span>}
    </div>
  );
}
