import { renderMarkdown } from '../utils/markdown';

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
  if (!pages.length) {
    if (jobDetails?.status === 'processing') {
      resultHtml = '<div style="text-align:center;padding-top:60px;">Scanning...</div>';
    } else if (jobDetails?.status === 'failed') {
      resultHtml = `<div style="color:var(--error);padding:18px;">Extraction failed: ${jobDetails.error_message || ''}</div>`;
    } else {
      resultHtml = '<div style="text-align:center;padding-top:60px;color:var(--muted)">Waiting for first page...</div>';
    }
  } else {
    let md = '';
    pages.forEach((p) => { md += `### Page ${p.page}\n\n${p.text || ''}\n\n---\n\n`; });
    resultHtml = renderMarkdown(md);
  }

  const canPause = jobDetails?.status === 'processing';
  const canResume = jobDetails?.status === 'paused' || jobDetails?.status === 'failed';

  return (
    <div className="panel viewer-panel">
      <div className="viewer-toolbar">
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
            <div className="view-box-content markdown-body" dangerouslySetInnerHTML={{ __html: hasSelection ? resultHtml : '<div style="text-align:center;color:var(--muted);padding-top:90px;">Select a document or upload a new file to view results.</div>' }} />
          </div>
        )}
      </div>
    </div>
  );
}
