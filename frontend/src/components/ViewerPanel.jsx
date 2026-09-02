import { renderMarkdown } from '../utils/markdown';

// Presentational only — the thresholds themselves live entirely on the backend
// (confidence.py); this just maps the already-computed tier string to a label/color
// so the frontend never has its own, possibly-drifting copy of the boundaries.
const TIER_DISPLAY = {
  highly_confident: { label: 'Highly Confident', color: 'var(--success)' },
  very_confident: { label: 'Very Confident', color: '#4ade80' },
  confident: { label: 'Confident', color: '#facc15' },
  needs_review: { label: 'Needs Review', color: 'var(--error)' },
  blank_page: { label: 'Blank Page', color: 'var(--muted)' },
  failed: { label: 'Failed', color: '#991b1b' },
};

function ConfidenceBadge({ page }) {
  if (page.confidence_pct === null || page.confidence_pct === undefined || !page.confidence_tier) return null;
  const display = TIER_DISPLAY[page.confidence_tier];
  if (!display) return null;
  const showPct = page.confidence_tier !== 'blank_page' && page.confidence_tier !== 'failed';
  return (
    <span
      className="confidence-badge"
      style={{
        display: 'inline-block', marginLeft: 10, padding: '2px 9px', borderRadius: 999,
        fontSize: '0.7rem', fontWeight: 600, color: display.color,
        border: `1px solid ${display.color}`, verticalAlign: 'middle',
      }}
      data-testid={`confidence-badge-${page.page}`}
    >
      {showPct ? `${page.confidence_pct}% · ${display.label}` : display.label}
    </span>
  );
}

export default function ViewerPanel({
  viewMode,
  setViewMode,
  pdfSrc,
  pdfStatusLabel,
  jobDetails,
  hasSelection,
  onPause,
  onResume,
  onDownloadPdf,
  onDownloadResult,
  sidebarCollapsed,
  onToggleSidebar,
}) {
  const showPdf = viewMode !== 'result';
  const showResult = viewMode !== 'pdf';

  let progressLabel = 'Ready';
  let progressColor = 'var(--accent)';
  if (jobDetails) {
    if (jobDetails.status === 'processing') progressLabel = `Processing (${jobDetails.processed_pages}/${jobDetails.total_pages} pages)...`;
    else if (jobDetails.status === 'completed') { progressLabel = `Done (${jobDetails.total_pages} pages)`; progressColor = 'var(--success)'; }
    else if (jobDetails.status === 'paused') { progressLabel = `Paused (${jobDetails.processed_pages}/${jobDetails.total_pages} pages extracted)`; progressColor = '#67e8f9'; }
    else if (jobDetails.status === 'failed') { progressLabel = 'Failed'; progressColor = 'var(--error)'; }
    else progressLabel = 'Queued...';
  }

  const pages = jobDetails?.pages_data || [];
  let resultHtml;
  let pageBlocks = null;
  if (!pages.length) {
    if (jobDetails?.status === 'processing') {
      resultHtml = '<div style="text-align:center;padding-top:60px;">Scanning...</div>';
    } else if (jobDetails?.status === 'failed') {
      resultHtml = `<div style="color:var(--error);padding:18px;">Extraction failed: ${jobDetails.error_message || ''}</div>`;
    } else {
      resultHtml = '<div style="text-align:center;padding-top:60px;color:var(--muted)">Waiting for first page...</div>';
    }
  } else {
    // Rendered per-page (not one concatenated blob) so each page can carry its own
    // confidence badge. Pages with no score yet (still pending/processing) show no
    // badge rather than a fabricated one.
    pageBlocks = pages.map((p) => ({
      page: p.page,
      bodyHtml: renderMarkdown(p.text || ''),
      data: p,
    }));
  }

  const canPause = jobDetails?.status === 'processing';
  const canResume = jobDetails?.status === 'paused' || jobDetails?.status === 'failed';

  return (
    <div className="panel viewer-panel">
      <div className="viewer-toolbar">
        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          <button
            className="btn btn-sm btn-secondary"
            onClick={onToggleSidebar}
            title={sidebarCollapsed ? 'Show sidebar' : 'Hide sidebar'}
            data-testid="sidebar-toggle-btn"
          >
            <i className={`fa-solid ${sidebarCollapsed ? 'fa-angles-right' : 'fa-angles-left'}`} />
          </button>
          <div className="view-toggles">
            <button className={`view-toggle-btn ${viewMode === 'split' ? 'active' : ''}`} onClick={() => setViewMode('split')}>
              <i className="fa-solid fa-columns" /> Split
            </button>
            <button className={`view-toggle-btn ${viewMode === 'pdf' ? 'active' : ''}`} onClick={() => setViewMode('pdf')}>
              <i className="fa-regular fa-file-pdf" /> PDF Only
            </button>
            <button className={`view-toggle-btn ${viewMode === 'result' ? 'active' : ''}`} onClick={() => setViewMode('result')}>
              <i className="fa-solid fa-file-lines" /> Result Only
            </button>
          </div>
        </div>
        {hasSelection && (
          <div className="download-group">
            {canPause && <button className="btn btn-sm btn-warn" onClick={onPause}><i className="fa-solid fa-pause" /> Pause</button>}
            {canResume && <button className="btn btn-sm btn-secondary" onClick={onResume}><i className="fa-solid fa-rotate-right" /> Resume</button>}
            <button className="btn btn-sm btn-secondary" onClick={onDownloadPdf}><i className="fa-solid fa-file-arrow-down" /> PDF</button>
            <button className="btn btn-sm btn-primary" onClick={onDownloadResult}><i className="fa-solid fa-download" /> Result (.md)</button>
          </div>
        )}
      </div>

      <div className="dual-viewport">
        {showPdf && (
          <div className="view-box">
            <div className="view-box-header">
              <span><i className="fa-regular fa-file-pdf" /> Document Viewer</span>
              <span style={{ fontSize: '0.73rem', color: 'var(--muted)' }}>{pdfStatusLabel}</span>
            </div>
            <div className="view-box-content" style={{ padding: 0 }}>
              <iframe className="pdf-frame" src={pdfSrc || 'about:blank'} title="document" />
            </div>
          </div>
        )}
        {showResult && (
          <div className="view-box">
            <div className="view-box-header">
              <span><i className="fa-solid fa-align-left" /> Extracted OCR Markdown</span>
              <span style={{ fontSize: '0.73rem', color: progressColor }}>{progressLabel}</span>
            </div>
            {hasSelection && pageBlocks ? (
              <div className="view-box-content markdown-body">
                {pageBlocks.map(({ page, bodyHtml, data }) => (
                  <div key={page} className="ocr-page-block" data-testid={`page-block-${page}`}>
                    <h3 style={{ display: 'flex', alignItems: 'center', margin: '0 0 8px' }}>
                      Page {page}
                      <ConfidenceBadge page={data} />
                    </h3>
                    <div dangerouslySetInnerHTML={{ __html: bodyHtml }} />
                    <hr />
                  </div>
                ))}
              </div>
            ) : (
              <div
                className="view-box-content markdown-body"
                dangerouslySetInnerHTML={{
                  __html: hasSelection ? resultHtml : '<div style="text-align:center;color:var(--muted);padding-top:90px;">Select a document or upload a new file to view results.</div>',
                }}
              />
            )}
          </div>
        )}
      </div>
    </div>
  );
}
