import { useState } from 'react';

const FORMATS = [
  { value: 'docx', label: '.docx', sub: 'Microsoft Word', icon: 'fa-file-word', recommended: true },
  { value: 'pdf', label: '.pdf', sub: 'Print-ready', icon: 'fa-file-pdf' },
  { value: 'md', label: '.md', sub: 'GitHub Markdown', icon: 'fa-file-lines' },
];

// Slide 08's multi-format export modal: format cards, a page-scope selector
// (full / current page / a custom "1-5,12,19-24" range), and a review-
// metadata toggle. Purely presentational — `onExport({format, scope,
// pageNum, pageRange, includeMetadata})` does the actual authenticated
// download (same blob-fetch pattern every other download in this app uses),
// so this component has no network code of its own.
export default function ExportDropdown({ currentPage, totalPages, onExport }) {
  const [open, setOpen] = useState(false);
  const [format, setFormat] = useState('docx');
  const [scope, setScope] = useState('full');
  const [pageRange, setPageRange] = useState('');
  const [includeMetadata, setIncludeMetadata] = useState(true);
  const [rangeError, setRangeError] = useState('');

  function handleExport() {
    if (scope === 'range' && !pageRange.trim()) {
      setRangeError('Enter a page range, e.g. 1-5,12,19-24');
      return;
    }
    setRangeError('');
    onExport({
      format, scope, includeMetadata,
      pageNum: scope === 'page' ? currentPage : undefined,
      pageRange: scope === 'range' ? pageRange.trim() : undefined,
    });
    setOpen(false);
  }

  return (
    <div className="export-dropdown">
      <button
        className="btn btn-sm btn-primary"
        onClick={() => setOpen((v) => !v)}
        data-testid="export-dropdown-toggle"
      >
        <i className="fa-solid fa-download" /> Export Document <i className="fa-solid fa-caret-down" />
      </button>

      {open && (
        <div className="export-dropdown-panel" data-testid="export-dropdown-panel">
          <div className="export-format-cards">
            {FORMATS.map((f) => (
              <button
                key={f.value}
                type="button"
                className={`export-format-card${format === f.value ? ' active' : ''}`}
                onClick={() => setFormat(f.value)}
                data-testid={`export-format-${f.value}`}
              >
                <i className={`fa-solid ${f.icon}`} />
                <span className="export-format-label">{f.label}</span>
                <span className="export-format-sub">{f.sub}{f.recommended ? ' · Recommended' : ''}</span>
              </button>
            ))}
          </div>

          <div className="export-scope-group" role="radiogroup" aria-label="Export scope">
            <label className="export-scope-option">
              <input type="radio" name="export-scope" value="full" checked={scope === 'full'} onChange={() => setScope('full')} />
              Entire Document{totalPages ? ` (${totalPages} pages)` : ''}
            </label>
            <label className="export-scope-option">
              <input type="radio" name="export-scope" value="page" checked={scope === 'page'} onChange={() => setScope('page')} />
              Current Page Only{currentPage ? ` (P.${currentPage})` : ''}
            </label>
            <label className="export-scope-option">
              <input type="radio" name="export-scope" value="range" checked={scope === 'range'} onChange={() => setScope('range')} />
              Custom Range
            </label>
            {scope === 'range' && (
              <input
                type="text"
                className="export-range-input"
                placeholder="e.g. 1-5, 12, 19-24"
                value={pageRange}
                onChange={(e) => { setPageRange(e.target.value); setRangeError(''); }}
                data-testid="export-range-input"
              />
            )}
            {rangeError && <div className="export-range-error" data-testid="export-range-error">{rangeError}</div>}
          </div>

          <label className="export-metadata-toggle">
            <input type="checkbox" checked={includeMetadata} onChange={(e) => setIncludeMetadata(e.target.checked)} />
            Include review warnings &amp; confidence metadata
          </label>

          <div className="export-dropdown-actions">
            <button className="btn btn-sm btn-secondary" onClick={() => setOpen(false)}>Cancel</button>
            <button className="btn btn-sm btn-primary" onClick={handleExport} data-testid="export-confirm-btn">
              Download ({format}) <i className="fa-solid fa-arrow-right" />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
