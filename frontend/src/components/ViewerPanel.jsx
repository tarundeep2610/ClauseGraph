import { useEffect, useMemo, useState } from 'react';
import { renderMarkdown, renderPageMarkdown } from '../utils/markdown';
import RetryPagesModal from './RetryPagesModal';
import PdfCanvasViewer from './PdfCanvasViewer';
import StructureTree from './StructureTree';
import PageNavBar from './PageNavBar';
import StructureReadingModal from './StructureReadingModal';
import StructureItemModal from './StructureItemModal';
import StructureDeleteConfirmModal from './StructureDeleteConfirmModal';

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
  structureContent,
  onRequestStructureContent,
  parentOverrides,
  sortOverrides,
  onAddStructureItem,
  onEditStructureItem,
  onDeleteStructureItem,
  onMoveStructureItem,
}) {
  const showPdf = viewMode === 'split' || viewMode === 'pdf';
  const showResult = viewMode === 'split' || viewMode === 'result';
  const [retryModalOpen, setRetryModalOpen] = useState(false);
  const [pdfPageOverride, setPdfPageOverride] = useState(null);
  // The Structure tree is a persistent sidebar (collapsed by default), not a
  // competing view mode — it stays available no matter which of
  // Split/PDF/Result the reader has open, since jumping to a reference and
  // seeing the document it points to are both things a reader wants at once.
  const [structureSidebarOpen, setStructureSidebarOpen] = useState(false);
  // The reading modal opened by a Structure-tab click — { startPage,
  // endPage, label, anchorId? } or null when closed. Deliberately never
  // touches pdfPageOverride: this is what guarantees the main viewer sits
  // exactly where it was left once the modal closes.
  const [readingTarget, setReadingTarget] = useState(null);
  // Add/Edit structure-item modal: { mode: 'add'|'edit', descriptor?, initialValues } or null.
  // `descriptor` (edit only) carries the addressing info (itemKey/page/
  // occurrenceIndex/isManual) the submit handler needs to call the right
  // update endpoint; add has no descriptor since it always creates fresh.
  const [itemModal, setItemModal] = useState(null);
  const [itemModalSubmitting, setItemModalSubmitting] = useState(false);
  const [itemModalError, setItemModalError] = useState('');
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [deleteSubmitting, setDeleteSubmitting] = useState(false);
  // A/B toggle for the "Rebuild Cross-References" button: whether the AI
  // reads Unlimited-OCR's raw, <|det|>-tagged text or the cleaned text. Only
  // has any effect on a job that ran with grounding enabled — see
  // ai_reference_index.reindex_job_with_ai's text_source param.
  const [useRawText, setUseRawText] = useState(false);
  const groundingEnabled = !!jobDetails?.engine_params?.grounding_enabled;

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

  const pages = jobDetails?.pages_data || [];
  const pageMap = useMemo(() => new Map(pages.map((p) => [p.page, p])), [pages]);

  // The single source of truth for "what page is showing," read by BOTH
  // panes — the PDF pane via its `pageNum` prop, the Result pane via a
  // pageMap lookup below. Always the global physical page number.
  const currentPdfPage = pdfPageOverride || 1;

  let resultHtml;
  let currentPageBlock = null;
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
    const data = pageMap.get(currentPdfPage);
    if (data) {
      currentPageBlock = {
        page: currentPdfPage,
        bodyHtml: referenceStore ? renderPageMarkdown(data.text || '', data.page, referenceStore) : renderMarkdown(data.text || ''),
        data,
      };
    } else {
      // Normal transient state now (not an edge case): the windowed fetch
      // around currentPdfPage hasn't landed yet — distinct from the
      // job-level "Scanning..."/"Waiting for first page" states above,
      // which mean the job itself has no data at all yet.
      resultHtml = `<div style="text-align:center;padding-top:60px;color:var(--muted)">Loading page ${currentPdfPage}…</div>`;
    }
  }

  // Reports the current page up to Dashboard.jsx so its windowed
  // pages_data fetch (Part A) can center on wherever the reader actually
  // is — pdf.js always knows this exactly (one page at a time, no scroll
  // drift), so this fires only on genuine page changes, never guesses.
  useEffect(() => {
    if (onCurrentPageChange) onCurrentPageChange(currentPdfPage);
  }, [currentPdfPage, onCurrentPageChange]);

  function handlePdfPageChange(page) {
    setPdfPageOverride(page);
  }

  // Clicking a Structure-sidebar leaf or a container's jump button both open
  // the reading modal instead of moving the main viewer — the main pane's
  // pdfPageOverride is left completely untouched, so closing the modal
  // leaves the reader exactly where they were.
  function handleStructureSelect(candidate) {
    const target = { startPage: candidate.page, endPage: candidate.page, label: `Page ${candidate.page}`, anchorId: candidate.anchor_id };
    setReadingTarget(target);
    if (onRequestStructureContent) onRequestStructureContent(target);
  }

  function handleStructureContainerJump(range) {
    const target = { startPage: range.startPage, endPage: range.endPage, label: range.label };
    setReadingTarget(target);
    if (onRequestStructureContent) onRequestStructureContent(target);
  }

  function openAddItemModal() {
    setItemModalError('');
    setItemModal({ mode: 'add', initialValues: { heading: '', startPage: currentPdfPage, endPage: currentPdfPage } });
  }

  function openEditItemModal(descriptor) {
    setItemModalError('');
    setItemModal({
      mode: 'edit',
      descriptor,
      initialValues: { heading: descriptor.heading, startPage: descriptor.startPage, endPage: descriptor.endPage },
    });
  }

  async function handleItemModalSubmit(values) {
    setItemModalSubmitting(true);
    setItemModalError('');
    try {
      if (itemModal.mode === 'add') {
        if (onAddStructureItem) await onAddStructureItem(values);
      } else if (onEditStructureItem) {
        await onEditStructureItem(itemModal.descriptor, values);
      }
      setItemModal(null);
    } catch (e) {
      setItemModalError(e?.message || 'Failed to save this entry.');
    } finally {
      setItemModalSubmitting(false);
    }
  }

  async function handleDeleteConfirm() {
    setDeleteSubmitting(true);
    try {
      if (onDeleteStructureItem) await onDeleteStructureItem(deleteTarget);
      setDeleteTarget(null);
    } finally {
      setDeleteSubmitting(false);
    }
  }

  const canPause = jobDetails?.status === 'processing';
  const canResume = jobDetails?.status === 'paused' || jobDetails?.status === 'failed';

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
              <label
                className="btn btn-sm btn-secondary"
                style={{ opacity: groundingEnabled ? 1 : 0.5, cursor: groundingEnabled ? 'pointer' : 'default' }}
                title={groundingEnabled
                  ? "Use Unlimited-OCR's raw, layout-tagged text as the AI indexer's input instead of the cleaned text"
                  : 'This job was not run with grounding enabled — toggling this has no effect'}
                data-testid="reindex-raw-text-toggle"
              >
                <input
                  type="checkbox"
                  checked={useRawText}
                  disabled={!groundingEnabled}
                  onChange={(e) => setUseRawText(e.target.checked)}
                  style={{ marginRight: 6 }}
                />
                Raw tagged text
              </label>
            )}
            {jobDetails?.status !== 'processing' && jobDetails?.status !== 'escalating' && (
              <button
                className="btn btn-sm btn-secondary"
                disabled={!!reindexing}
                onClick={() => onReindexReferences(useRawText ? 'raw' : 'clean')}
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

      {hasSelection && jobDetails?.total_pages > 0 && (
        <PageNavBar
          currentPage={currentPdfPage}
          minPage={1}
          maxPage={jobDetails.total_pages}
          onNavigate={handlePdfPageChange}
        />
      )}

      <div className="dual-viewport">
        {structureSidebarOpen && (
          <div className="view-box structure-sidebar" data-testid="structure-sidebar">
            <div className="view-box-header">
              <span><i className="fa-solid fa-sitemap" /> Document Structure</span>
              {onAddStructureItem && (
                <button className="btn btn-sm btn-secondary" onClick={openAddItemModal} data-testid="structure-add-btn">
                  <i className="fa-solid fa-plus" /> Add
                </button>
              )}
            </div>
            <div className="view-box-content">
              <StructureTree
                referenceStore={referenceStore}
                onSelect={handleStructureSelect}
                onSelectContainer={handleStructureContainerJump}
                scopeRanges={scopeRanges}
                activePage={currentPdfPage}
                onEditItem={onEditStructureItem ? openEditItemModal : undefined}
                onDeleteItem={onDeleteStructureItem ? setDeleteTarget : undefined}
                onMoveItem={onMoveStructureItem}
                parentOverrides={parentOverrides}
                sortOverrides={sortOverrides}
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
              {pdfSrc ? (
                <PdfCanvasViewer
                  pdfSrc={pdfSrc}
                  pageNum={currentPdfPage}
                  totalPages={jobDetails?.total_pages}
                  onPageChange={handlePdfPageChange}
                  showPageNav={false}
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
            {hasSelection && currentPageBlock ? (
              <div className="view-box-content markdown-body" onClick={handleMarkdownClick}>
                <div id={`page-block-${currentPageBlock.page}`} className="ocr-page-block" data-testid={`page-block-${currentPageBlock.page}`}>
                  <h3 style={{ display: 'flex', alignItems: 'center', margin: '0 0 8px' }}>
                    Page {currentPageBlock.page}
                    <ConfidenceBadge page={currentPageBlock.data} />
                    {(currentPageBlock.data.status === 'failed' || currentPageBlock.data.status === 'review') && (
                      <button
                        className="btn btn-sm btn-secondary"
                        style={{ marginLeft: 10 }}
                        disabled={retryingPage !== null && retryingPage !== undefined}
                        onClick={() => onRetryPage(currentPageBlock.page)}
                        data-testid={`retry-page-${currentPageBlock.page}`}
                      >
                        <i className="fa-solid fa-rotate-right" /> {retryingPage === currentPageBlock.page ? 'Retrying...' : 'Retry'}
                      </button>
                    )}
                  </h3>
                  <div dangerouslySetInnerHTML={{ __html: currentPageBlock.bodyHtml }} />
                </div>
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

      <StructureReadingModal
        target={readingTarget}
        content={structureContent}
        onClose={() => setReadingTarget(null)}
        onRetry={() => readingTarget && onRequestStructureContent && onRequestStructureContent(readingTarget)}
        referenceStore={referenceStore}
      />

      <StructureItemModal
        open={!!itemModal}
        mode={itemModal?.mode}
        initialValues={itemModal?.initialValues}
        onClose={() => setItemModal(null)}
        onSubmit={handleItemModalSubmit}
        submitting={itemModalSubmitting}
        error={itemModalError}
      />

      <StructureDeleteConfirmModal
        open={!!deleteTarget}
        label={deleteTarget?.heading}
        onClose={() => setDeleteTarget(null)}
        onConfirm={handleDeleteConfirm}
        submitting={deleteSubmitting}
      />
    </div>
  );
}
