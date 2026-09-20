import { useEffect, useMemo, useState } from 'react';
import { renderMarkdown, renderPageMarkdown } from '../utils/markdown';
import RetryPagesModal from './RetryPagesModal';
import PdfCanvasViewer from './PdfCanvasViewer';
import StructureTree from './StructureTree';

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
  onRetryPage,
  retryingPage,
  onRetryPages,
  onReindexReferences,
  reindexing,
  referenceStore,
  scopeRanges,
  onCurrentPageChange,
  chunkView,
  onSelectChunk,
  onExitChunk,
}) {
  const showPdf = viewMode === 'split' || viewMode === 'pdf';
  const showResult = viewMode === 'split' || viewMode === 'result';
  const [retryModalOpen, setRetryModalOpen] = useState(false);
  const [pdfPageOverride, setPdfPageOverride] = useState(null);
  const [pendingJump, setPendingJump] = useState(null); // candidate to jump to once a viewable pane is showing
  // The Structure tree is a persistent sidebar (collapsed by default), not a
  // competing view mode — it stays available no matter which of
  // Split/PDF/Result the reader has open, since jumping to a reference and
  // seeing the document it points to are both things a reader wants at once.
  const [structureSidebarOpen, setStructureSidebarOpen] = useState(false);

  let progressLabel = 'Ready';
  let progressColor = 'var(--accent)';
  if (jobDetails) {
    if (jobDetails.status === 'processing') progressLabel = `Processing (${jobDetails.processed_pages}/${jobDetails.total_pages} pages)...`;
    else if (jobDetails.status === 'completed') {
      const failed = jobDetails.failed_pages || 0;
      const passed = jobDetails.total_pages - failed;
      progressLabel = failed > 0 ? `Done (${passed} passed, ${failed} failed)` : `Done (${jobDetails.total_pages} pages)`;
      progressColor = failed > 0 ? 'var(--warning)' : 'var(--success)';
    }
    else if (jobDetails.status === 'paused') { progressLabel = `Paused (${jobDetails.processed_pages}/${jobDetails.total_pages} pages extracted)`; progressColor = '#67e8f9'; }
    else if (jobDetails.status === 'failed') { progressLabel = 'Failed'; progressColor = 'var(--error)'; }
    else progressLabel = 'Queued...';
  }

  // Part C3: while viewing a reference-triggered chunk, both panes read from
  // the chunk's own (much smaller) pages_data instead of the main windowed
  // fetch — Dashboard.jsx resolves chunkView (PDF blob + pages_data) and
  // hands it down already-fetched, same as it does for the main pdfSrc.
  const inChunkMode = !!chunkView;
  const pages = inChunkMode ? (chunkView.pagesData || []) : (jobDetails?.pages_data || []);
  let resultHtml;
  let pageBlocks = null;
  if (!pages.length) {
    if (!jobDetails && hasSelection) {
      // A job switch (new upload or selecting a different history entry) has
      // already updated pdfSrc/hasSelection, but jobDetails is cleared until
      // the fresh fetch resolves - show a neutral loading state instead of
      // falling through to "Waiting for first page", which would be
      // misleading for a job that's actually already completed.
      resultHtml = '<div style="text-align:center;padding-top:60px;color:var(--muted)">Loading document...</div>';
    } else if (jobDetails?.status === 'processing') {
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
      bodyHtml: referenceStore ? renderPageMarkdown(p.text || '', p.page, referenceStore) : renderMarkdown(p.text || ''),
      data: p,
    }));
  }

  const pageMap = useMemo(() => new Map(pages.map((p) => [p.page, p])), [pages]);

  // Always the GLOBAL physical page — the rest of the app (Dashboard's
  // windowed fetch, the Structure sidebar's activePage) keeps thinking in
  // real document page numbers regardless of whether a chunk is showing;
  // only the PDF pane itself is handed a locally-translated number below.
  const currentPdfPage = pdfPageOverride || 1;

  // Reports the current page up to Dashboard.jsx so its windowed
  // pages_data poll (Part C1) can center on wherever the reader actually
  // is — pdf.js always knows this exactly (one page at a time, no scroll
  // drift), so this fires only on genuine page changes, never guesses.
  useEffect(() => {
    if (onCurrentPageChange) onCurrentPageChange(currentPdfPage);
  }, [currentPdfPage, onCurrentPageChange]);

  // Part C3 local<->global page-number translation. PdfCanvasViewer always
  // renders one page (pdf.js is single-page-at-a-time, see the plan's C0
  // section) — in chunk mode it's handed the chunk's own local numbering
  // (1..chunkSize) so its own clamping/prev-next logic doesn't need to know
  // about the physical document at all; its onPageChange is translated back
  // to a global page here before touching the shared pdfPageOverride state.
  const chunkPageCount = inChunkMode ? chunkView.endPage - chunkView.startPage + 1 : 0;
  const pdfPaneSrc = inChunkMode ? chunkView.pdfUrl : pdfSrc;
  const pdfPaneTotalPages = inChunkMode ? chunkPageCount : jobDetails?.total_pages;
  const pdfPaneCurrentPage = inChunkMode
    ? Math.min(Math.max(currentPdfPage - chunkView.startPage + 1, 1), chunkPageCount || 1)
    : currentPdfPage;

  function handlePdfPageChange(localOrGlobalPage) {
    setPdfPageOverride(inChunkMode ? chunkView.startPage + localOrGlobalPage - 1 : localOrGlobalPage);
  }

  // Clicking a container's chunk-jump button in the Structure sidebar (a
  // whole Annexure/Article/Section span, per compute_scope_ranges) — unlike
  // handleStructureSelect below (a single leaf page, jumped to within
  // whatever's already loaded), this fetches a standalone chunk PDF+text
  // and switches both panes to it. Jumps the global page to the chunk's
  // start immediately so the Structure sidebar's activePage/current-node
  // highlight updates right away, without waiting on the fetch in Dashboard.
  function handleChunkRequest(range) {
    setPdfPageOverride(range.startPage);
    if (onSelectChunk) onSelectChunk(range);
  }

  const canPause = jobDetails?.status === 'processing';
  const canResume = jobDetails?.status === 'paused' || jobDetails?.status === 'failed';

  // Result-pane-only concern: precisely scrolls a candidate's own anchor
  // into view. Requires the Result pane to actually be mounted (its DOM
  // doesn't exist at all in PDF-only mode) and the target page's OCR text
  // to already be loaded — neither is needed for the PDF page jump itself
  // (handleStructureSelect below), which only needs the page number.
  // Returns whether the anchor was actually found.
  function scrollToCandidate(candidate) {
    const el = candidate.anchor_id ? document.getElementById(candidate.anchor_id) : null;
    if (!el) return false;
    el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    return true;
  }

  // Clicking a Structure-sidebar leaf is explicit navigation intent. The
  // PDF page is jumped immediately and unconditionally — the page number
  // is already known, so this never waits on the Result pane's OCR text
  // being loaded or its anchor being in the DOM (that DOM lookup is what
  // silently deadlocked jumps to a page far from the currently-loaded
  // window on long documents: the anchor was never in the DOM until the
  // page jumped, but the page never jumped until the anchor was found).
  // The current view mode is left as-is — a reader in PDF-only mode stays
  // in PDF-only mode; the Result pane is never force-opened just to serve
  // a jump it doesn't need.
  function handleStructureSelect(candidate) {
    if (!pageMap.get(candidate.page)?.is_boundary) {
      setPdfPageOverride(candidate.page);
    }
    if (showResult) {
      setPendingJump(candidate);
    }
  }

  useEffect(() => {
    if (!pendingJump || !showResult) return;
    if (scrollToCandidate(pendingJump)) {
      setPendingJump(null);
    }
    // Re-checked whenever fresh page data arrives, in case the target
    // wasn't in the DOM yet on the first attempt.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingJump, pages, showResult]);

  // The Result pane is display-only — this only swallows the click so an
  // `<a href="#">` doesn't jump the page to top. Clicking a reference
  // mention directly from the document view (PDF or Result pane) has no
  // trigger left as of the pdf.js migration — the PDF pane's reference
  // badge (the only thing that ever opened the ambiguous-match popover)
  // was removed as part of that change, per an explicit decision to let
  // the Structure sidebar be the primary way to reach a reference instead.
  // See the plan file if this needs restoring via a different trigger.
  function handleMarkdownClick(e) {
    const link = e.target.closest('a.xref-link');
    if (!link) return;
    e.preventDefault();
  }

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
          <button
            className={`btn btn-sm btn-secondary${structureSidebarOpen ? ' active' : ''}`}
            onClick={() => setStructureSidebarOpen((v) => !v)}
            title={structureSidebarOpen ? 'Hide document structure' : 'Show document structure'}
            data-testid="structure-sidebar-toggle-btn"
          >
            <i className="fa-solid fa-sitemap" /> Structure
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
            <button className="btn btn-sm btn-secondary" onClick={() => setRetryModalOpen(true)} data-testid="retry-pages-btn"><i className="fa-solid fa-rotate-right" /> Retry Pages</button>
            {jobDetails?.status !== 'processing' && jobDetails?.status !== 'escalating' && (
              <button
                className="btn btn-sm btn-secondary"
                disabled={!!reindexing}
                onClick={onReindexReferences}
                data-testid="reindex-references-btn"
              >
                <i className="fa-solid fa-diagram-project" />
                {reindexing ? 'Rebuilding…' : 'Rebuild Cross-References'}
              </button>
            )}
            <button className="btn btn-sm btn-secondary" onClick={onDownloadPdf}><i className="fa-solid fa-file-arrow-down" /> PDF</button>
            <button className="btn btn-sm btn-primary" onClick={onDownloadResult}><i className="fa-solid fa-download" /> Result (.md)</button>
          </div>
        )}
      </div>

      {chunkView && (
        <div className="chunk-view-banner" data-testid="chunk-view-banner">
          <span>
            <i className="fa-solid fa-diagram-project" /> Viewing: {chunkView.label} (pages {chunkView.startPage}–{chunkView.endPage})
          </span>
          <button className="btn btn-sm btn-secondary" onClick={onExitChunk} data-testid="exit-chunk-btn">
            <i className="fa-solid fa-arrow-left" /> View complete PDF
          </button>
        </div>
      )}

      <div className="dual-viewport">
        {structureSidebarOpen && (
          <div className="view-box structure-sidebar" data-testid="structure-sidebar">
            <div className="view-box-header">
              <span><i className="fa-solid fa-sitemap" /> Document Structure</span>
            </div>
            <div className="view-box-content">
              <StructureTree
                referenceStore={referenceStore}
                onSelect={handleStructureSelect}
                onSelectContainer={handleChunkRequest}
                scopeRanges={scopeRanges}
                activePage={currentPdfPage}
              />
            </div>
          </div>
        )}
        {showPdf && (
          <div className="view-box">
            <div className="view-box-header">
              <span><i className="fa-regular fa-file-pdf" /> Document Viewer</span>
              <span style={{ fontSize: '0.73rem', color: 'var(--muted)' }}>{pdfStatusLabel}</span>
            </div>
            <div className="view-box-content" style={{ padding: 0, overflow: 'hidden' }}>
              {pdfPaneSrc ? (
                <PdfCanvasViewer
                  pdfSrc={pdfPaneSrc}
                  pageNum={pdfPaneCurrentPage}
                  totalPages={pdfPaneTotalPages}
                  onPageChange={handlePdfPageChange}
                />
              ) : (
                <div style={{ textAlign: 'center', paddingTop: 60, color: 'var(--muted)' }}>
                  No document selected
                </div>
              )}
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
              <div className="view-box-content markdown-body" onClick={handleMarkdownClick}>
                {pageBlocks.map(({ page, bodyHtml, data }) => (
                  <div key={page} id={`page-block-${page}`} className="ocr-page-block" data-testid={`page-block-${page}`}>
                    <h3 style={{ display: 'flex', alignItems: 'center', margin: '0 0 8px' }}>
                      Page {page}
                      <ConfidenceBadge page={data} />
                      {(data.status === 'failed' || data.status === 'review') && (
                        <button
                          className="btn btn-sm btn-secondary"
                          style={{ marginLeft: 10 }}
                          disabled={retryingPage !== null && retryingPage !== undefined}
                          onClick={() => onRetryPage(page)}
                          data-testid={`retry-page-${page}`}
                        >
                          <i className="fa-solid fa-rotate-right" /> {retryingPage === page ? 'Retrying...' : 'Retry'}
                        </button>
                      )}
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

      <RetryPagesModal
        open={retryModalOpen}
        onClose={() => setRetryModalOpen(false)}
        onConfirm={(scope) => { setRetryModalOpen(false); onRetryPages(scope); }}
        counts={{
          failed: jobDetails?.failed_pages ?? 0,
          review: jobDetails?.review_pages ?? 0,
          total: jobDetails?.total_pages ?? 0,
        }}
      />
    </div>
  );
}
