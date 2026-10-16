import { useEffect, useMemo, useRef, useState } from 'react';
import { useAuth } from '../utils/AuthContext';
import { getPageMentions } from '../utils/markdown';
import RetryPagesModal from './RetryPagesModal';
import RebuildIndexModal from './RebuildIndexModal';
import PdfCanvasViewer from './PdfCanvasViewer';
import PageNavBar from './PageNavBar';
import StructureReadingModal from './StructureReadingModal';
import StructureItemModal from './StructureItemModal';
import StructureDeleteConfirmModal from './StructureDeleteConfirmModal';
import PageReferenceBadges from './PageReferenceBadges';
import WysiwygEditor from './editor/WysiwygEditor';
import ExportDropdown from './studio/ExportDropdown';
import StructureTreePanel from './studio/StructureTreePanel';
import ReviewQueuePanel from './review/ReviewQueuePanel';
import StudioAppBar from './studio/StudioAppBar';
import StudioRail from './studio/StudioRail';
import StudioFloatingHud from './studio/StudioFloatingHud';
import SplitResizer from './studio/SplitResizer';
import DualPdfCompare from './studio/DualPdfCompare';
import Toast from './Toast';

// Presentational only — the thresholds themselves live entirely on the backend
// (confidence.py); this just maps the already-computed tier string to a label/color
// so the frontend never has its own, possibly-drifting copy of the boundaries.
const TIER_DISPLAY = {
  highly_confident: { label: 'Highly Confident', color: 'var(--success)' },
  very_confident: { label: 'Very Confident', color: 'var(--tier-very-confident)' },
  confident: { label: 'Confident', color: 'var(--tier-confident)' },
  needs_review: { label: 'Needs Review', color: 'var(--error)' },
  blank_page: { label: 'Blank Page', color: 'var(--muted)' },
  failed: { label: 'Failed', color: 'var(--tier-failed)' },
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
  studioMode = false,
  initialReviewOpen = false,
  onBackToLibrary,
  viewMode,
  setViewMode,
  pdfSrc,
  pdfStatusLabel,
  jobDetails,
  hasSelection,
  fileLoading = false,
  onPause,
  onResume,
  onDownloadPdf,
  onSavePageText,
  onExport,
  sidebarCollapsed,
  onToggleSidebar,
  onRetryPage,
  retryingPage,
  onRetryPages,
  reviewPages,
  onConfirmCorrectPage,
  onReindexReferences,
  onBuildReferenceLinksOnly,
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
  onOpenSettings,
}) {
  const { isAdmin } = useAuth();
  const showPdf = viewMode === 'split' || viewMode === 'pdf';
  const [retryModalOpen, setRetryModalOpen] = useState(false);
  const [rebuildModalOpen, setRebuildModalOpen] = useState(false);
  const [pdfPageOverride, setPdfPageOverride] = useState(null);
  // Set by a cross-reference badge click — pins a second, independent PDF
  // view (same pdfSrc, its own page) to the reference's target page, taking
  // over whichever slot would otherwise show the OCR markdown. null when
  // inactive. Reset below whenever the document changes or the reader
  // manually picks a view mode; also closable via its own header button.
  const [pinnedPdfPage, setPinnedPdfPage] = useState(null);
  const [pinnedReference, setPinnedReference] = useState(null);
  const showPinnedPdf = pinnedPdfPage != null && !!pdfSrc;
  const showResult = !showPinnedPdf && (viewMode === 'split' || viewMode === 'result');
  // Set by a Structure-sidebar leaf jump that carries an anchor_id — cleared
  // once the scroll-to-anchor effect below has acted on it (or immediately,
  // for a jump with no anchor, e.g. a container jump).
  const [pendingAnchorId, setPendingAnchorId] = useState(null);
  const resultContentRef = useRef(null);
  // The Structure tree is a persistent sidebar (collapsed by default), not a
  // competing view mode — it stays available no matter which of
  // Split/PDF/Result the reader has open, since jumping to a reference and
  // seeing the document it points to are both things a reader wants at once.
  const [structureSidebarOpen, setStructureSidebarOpen] = useState(false);
  // Guided Review Queue drawer (Sprint 6) — same "persistent, collapsed by
  // default" treatment as the Structure sidebar above, toggled independently.
  const [reviewSidebarOpen, setReviewSidebarOpen] = useState(initialReviewOpen);
  // Indexing (structure + reference links) is fully opt-in now — see
  // StudioRail's disabled Outline button below — so clicking it with no
  // index built shows this instead of silently opening an empty drawer.
  const [toastMessage, setToastMessage] = useState(null);
  // `index_built_at` is NOT the right signal here — it's only ever stamped by
  // the explicit rebuild endpoint, so a job indexed under the old (removed)
  // automatic architecture has real data but permanently null index_built_at.
  // reference_index_version is bumped by every path that has ever written
  // structure/target data, old automatic or new explicit alike, and is
  // confirmed 0 for a genuinely fresh, never-indexed job — the correct,
  // data-presence-based signal instead of a separate flag that can drift
  // from reality.
  const indexBuilt = (jobDetails?.reference_index_version ?? 0) > 0;
  function handleToggleStructure() {
    if (!indexBuilt) {
      setToastMessage('No index built yet — click "Build Index & Reference Links" first.');
      return;
    }
    setStructureSidebarOpen((v) => !v);
  }
  // Nothing built yet: build both directly, "Reference Links Only" has no
  // value with nothing to link against. Something already built: let the
  // user choose what to rebuild (RebuildIndexModal) rather than silently
  // redoing everything every time.
  function handleBuildIndexClick() {
    if (!indexBuilt) {
      onReindexReferences();
      return;
    }
    setRebuildModalOpen(true);
  }
  function handleConfirmRebuild(choice) {
    setRebuildModalOpen(false);
    if (choice === 'links-only') onBuildReferenceLinksOnly();
    else onReindexReferences();
  }
  const [splitRatio, setSplitRatio] = useState(50);
  const [editorSaveStatus, setEditorSaveStatus] = useState('saved');
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [bothPanesSearch, setBothPanesSearch] = useState(true);
  const [searchMatchIndex, setSearchMatchIndex] = useState(0);

  useEffect(() => {
    if (initialReviewOpen) setReviewSidebarOpen(true);
  }, [initialReviewOpen]);

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
    else if (jobDetails.status === 'paused') { progressLabel = `Paused (${jobDetails.processed_pages}/${jobDetails.total_pages} pages extracted)`; progressColor = 'var(--fg-accent-soft)'; }
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
      // formatted_text (headings/tables/columns, built from the OCR
      // engine's raw layout tags) is preferred when present — NULL for any
      // page OCR'd without grounding, or before this capture existed, which
      // falls back to the plain text exactly as before. The Result pane is
      // now an editable WysiwygEditor (Studio redesign) rather than
      // dangerouslySetInnerHTML'd markdown — it ingests/persists this same
      // Markdown string directly (see WysiwygEditor.jsx/MarkdownBridge.js),
      // so there is no HTML to pre-render here any more. One accepted
      // consequence: the inline cross-reference mention-linkification
      // renderPageMarkdown used to inject (data-xref-key anchors turning
      // "Clause 8" into a clickable link) no longer appears inside this
      // editable body text, since the editor builds its own DOM from parsed
      // Markdown, not from that annotated HTML — PageReferenceBadges (top-
      // of-pane citation pills) remains the mechanism for reference
      // navigation going forward.
      const pageText = data.formatted_text || data.text || '';
      currentPageBlock = {
        page: currentPdfPage,
        pageText,
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

  // `currentPageBlock` above is a fresh object literal every render (it's
  // built inline, not itself memoized) — depending on it here would re-run
  // getPageMentions' marked.lexer() call (uncached, unlike renderPageMarkdown
  // which has its own _renderCache) on every render, including ones with no
  // relevant change (scroll, sidebar toggle). Depend on the primitive page
  // text instead.
  const currentPageText = pageMap.get(currentPdfPage)?.text || '';
  const pageMentions = useMemo(
    () => (referenceStore && currentPageText ? getPageMentions(currentPageText, currentPdfPage, referenceStore) : []),
    [referenceStore, referenceStore?.version, currentPageText, currentPdfPage]
  );

  // Reports the current page up to AppShell.jsx so its windowed
  // pages_data fetch (Part A) can center on wherever the reader actually
  // is — pdf.js always knows this exactly (one page at a time, no scroll
  // drift), so this fires only on genuine page changes, never guesses.
  useEffect(() => {
    if (onCurrentPageChange) onCurrentPageChange(currentPdfPage);
  }, [currentPdfPage, onCurrentPageChange]);

  // A pinned reference page only makes sense against the document that was
  // open when it was pinned — switching documents/jobs changes pdfSrc, so
  // drop it rather than show a stale page number against new content.
  useEffect(() => {
    setPinnedPdfPage(null);
  }, [pdfSrc]);

  // Scrolls the Result pane to a leaf jump's exact position once its page's
  // content has actually rendered — the same xref-anchor span the reading
  // modal already scrolls to (see StructureReadingModal.jsx), scoped to this
  // pane's own subtree since the same anchor id can exist in both places at
  // once. Waits out the "Loading page N…" transient naturally: this effect
  // just re-checks whenever currentPageBlock changes.
  useEffect(() => {
    if (!pendingAnchorId || !currentPageBlock) return;
    const el = resultContentRef.current?.querySelector(`#${CSS.escape(pendingAnchorId)}`);
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    setPendingAnchorId(null);
  }, [pendingAnchorId, currentPageBlock]);

  function handlePdfPageChange(page) {
    setPdfPageOverride(page);
  }

  // "[✓ Mark Verified & Next]" — compute the next queue entry BEFORE
  // confirming (the parent's reviewPages prop won't reflect the removal
  // until its next render), then jump the viewer there once the confirm
  // call resolves. Falls back to the previous entry if the confirmed page
  // was the last one in the list.
  async function handleConfirmCorrectAndAdvance(pageNum) {
    const idx = (reviewPages || []).findIndex((p) => p.page_num === pageNum);
    const next = idx >= 0 ? (reviewPages[idx + 1] || reviewPages[idx - 1]) : undefined;
    await onConfirmCorrectPage(pageNum);
    if (next) handlePdfPageChange(next.page_num);
  }

  // A Structure-sidebar row click now moves the main viewer itself — a leaf
  // jumps straight to its page (and, once that page's content is on screen,
  // scrolls to its exact anchor position via the effect below); a container
  // jumps to its range's first page. The reading modal (opened only via each
  // row's dedicated magnifying-glass button, see handleStructureSelect /
  // handleStructureContainerJump below) remains the way to preview a page or
  // section without disturbing where the reader actually is.
  function handleStructureJumpToPage(candidate) {
    setPdfPageOverride(candidate.page);
    setPendingAnchorId(candidate.anchor_id || null);
  }

  function handleStructureJumpContainer(range) {
    setPdfPageOverride(range.startPage);
    setPendingAnchorId(null);
  }

  // Opens the reading modal instead of moving the main viewer — the main
  // pane's pdfPageOverride is left completely untouched, so closing the
  // modal leaves the reader exactly where they were.
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

  // A cross-reference badge click pins an inline second PDF pane to the
  // target page instead — unlike handleStructureSelect, this never opens
  // the reading modal and reuses the already-loaded main pdfSrc rather than
  // a separate windowed fetch.
  function handleBadgeOpenReference(candidate) {
    setPinnedPdfPage(candidate.page);
    setPinnedReference({
      clauseLabel: candidate.label || candidate.section || (candidate.page ? `Page ${candidate.page}` : ''),
      snippet: candidate.snippet || '',
      targets: candidate.targets || [{ page: candidate.page, section: candidate.label || candidate.section }],
      activeTargetIndex: 0,
    });
  }

  function openAddItemModal() {
    setItemModalError('');
    setItemModal({ mode: 'add', initialValues: { heading: '', startPage: currentPdfPage, endPage: currentPdfPage } });
  }

  // Per-node "add" button (StructureTree's onAddItem) — same modal, but the
  // new entry is placed as a child of the clicked node afterward instead of
  // landing at the tree root. `targetContext` ({targetDescriptor,
  // existingChildren}, built by StructureTree.handleAddChildClick) rides
  // along in itemModal state and is handed back to onAddStructureItem on
  // submit, unchanged from how it arrived — this component doesn't need to
  // understand its shape, only carry it through. Defaults the page range to
  // the target node's own page rather than whatever's in the main PDF
  // viewport right now, since that's almost always the more useful starting
  // point for something being added under a specific section.
  function openAddChildModal({ targetDescriptor, existingChildren }) {
    setItemModalError('');
    setItemModal({
      mode: 'add',
      targetContext: { targetDescriptor, existingChildren },
      initialValues: { heading: '', startPage: targetDescriptor.page, endPage: targetDescriptor.page },
    });
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
        if (onAddStructureItem) await onAddStructureItem(values, itemModal.targetContext);
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

  return (
    <div className={`panel viewer-panel${studioMode ? ' studio-mode' : ''}`}>
      {studioMode ? (
        <StudioAppBar
          filename={jobDetails?.original_filename || jobDetails?.filename || jobDetails?.job_id}
          currentPage={currentPdfPage}
          totalPages={jobDetails?.total_pages}
          saveStatus={editorSaveStatus}
          onBackToLibrary={onBackToLibrary}
          canPause={canPause}
          onPause={onPause}
          canResume={canResume}
          onResume={onResume}
          isAdmin={isAdmin}
          onOpenRetryModal={() => setRetryModalOpen(true)}
          onToggleSidebar={onToggleSidebar}
          sidebarCollapsed={sidebarCollapsed}
          onBuildIndex={
            jobDetails?.status !== 'processing' && jobDetails?.status !== 'escalating'
              ? handleBuildIndexClick
              : undefined
          }
          reindexing={reindexing}
          indexBuilt={indexBuilt}
          onExport={(opts) => onExport && onExport(opts)}
          isDualCompare={showPinnedPdf}
          onExitDualCompare={() => setPinnedPdfPage(null)}
          searchOpen={searchOpen}
          onToggleSearch={() => setSearchOpen((v) => !v)}
          searchQuery={searchQuery}
          onSearchQueryChange={setSearchQuery}
          matchIndex={searchMatchIndex}
          totalMatches={0}
          bothPanesSearch={bothPanesSearch}
          onBothPanesSearchChange={setBothPanesSearch}
          onOpenSettings={onOpenSettings}
        />
      ) : (
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
            {reviewPages?.length > 0 && (
              <button
                className={`btn btn-sm btn-secondary${reviewSidebarOpen ? ' active' : ''}`}
                onClick={() => setReviewSidebarOpen((v) => !v)}
                title={reviewSidebarOpen ? 'Hide review queue' : 'Show review queue'}
                data-testid="review-sidebar-toggle-btn"
              >
                <i className="fa-solid fa-triangle-exclamation" /> Review ({reviewPages.length})
              </button>
            )}
            <div className="view-toggles">
              <button className={`view-toggle-btn ${viewMode === 'split' ? 'active' : ''}`} onClick={() => { setViewMode('split'); setPinnedPdfPage(null); }}>
                <i className="fa-solid fa-columns" /> Split
              </button>
              <button className={`view-toggle-btn ${viewMode === 'pdf' ? 'active' : ''}`} onClick={() => { setViewMode('pdf'); setPinnedPdfPage(null); }}>
                <i className="fa-regular fa-file-pdf" /> PDF Only
              </button>
              <button className={`view-toggle-btn ${viewMode === 'result' ? 'active' : ''}`} onClick={() => { setViewMode('result'); setPinnedPdfPage(null); }}>
                <i className="fa-solid fa-file-lines" /> Result Only
              </button>
            </div>
          </div>
          {hasSelection && (
            <div className="download-group">
              {canPause && <button className="btn btn-sm btn-warn" onClick={onPause}><i className="fa-solid fa-pause" /> Pause</button>}
              {canResume && <button className="btn btn-sm btn-secondary" onClick={onResume}><i className="fa-solid fa-rotate-right" /> Resume</button>}
              {isAdmin && (
                <button className="btn btn-sm btn-secondary" onClick={() => setRetryModalOpen(true)} data-testid="retry-pages-btn"><i className="fa-solid fa-rotate-right" /> Retry Pages</button>
              )}
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
                  className={`btn btn-sm ${indexBuilt ? 'btn-secondary' : 'btn-primary'}`}
                  disabled={!!reindexing}
                  onClick={handleBuildIndexClick}
                  data-testid="reindex-references-btn"
                  title={
                    indexBuilt
                      ? 'Choose what to rebuild: the document outline, reference links, or both'
                      : "Finds the document's outline and links mentions like \"see Clause 8\" to where that clause is actually defined"
                  }
                >
                  <i className="fa-solid fa-diagram-project" />
                  {reindexing ? 'Rebuilding…' : indexBuilt ? 'Rebuild Index & Reference Links' : 'Build Index & Reference Links'}
                </button>
              )}
              <button className="btn btn-sm btn-secondary" onClick={onDownloadPdf}><i className="fa-solid fa-file-arrow-down" /> PDF</button>
              <ExportDropdown
                currentPage={currentPdfPage}
                totalPages={jobDetails?.total_pages}
                onExport={(opts) => onExport && onExport(opts)}
              />
            </div>
          )}
        </div>
      )}

      {!studioMode && hasSelection && jobDetails?.total_pages > 0 && (
        <PageNavBar
          currentPage={currentPdfPage}
          minPage={1}
          maxPage={jobDetails.total_pages}
          onNavigate={handlePdfPageChange}
        />
      )}

      {studioMode ? (
        <div className="studio-main-body" style={{ display: 'flex', flex: 1, minHeight: 0, position: 'relative', overflow: 'hidden' }}>
          <StudioRail
            structureOpen={structureSidebarOpen}
            onToggleStructure={handleToggleStructure}
            indexBuilt={indexBuilt}
            reviewOpen={reviewSidebarOpen}
            onToggleReview={() => setReviewSidebarOpen((v) => !v)}
            reviewCount={reviewPages?.length || 0}
          />
          <Toast message={toastMessage} onDismiss={() => setToastMessage(null)} />
          <div className="dual-viewport studio-viewport" style={{ flex: 1, minWidth: 0, position: 'relative', display: 'flex' }}>
            <StructureTreePanel
              open={structureSidebarOpen}
              onClose={() => setStructureSidebarOpen(false)}
              onAddClick={onAddStructureItem ? openAddItemModal : undefined}
              referenceStore={referenceStore}
              onSelect={handleStructureJumpToPage}
              onOpenReference={handleStructureSelect}
              onSelectContainer={handleStructureContainerJump}
              onJumpContainer={handleStructureJumpContainer}
              scopeRanges={scopeRanges}
              activePage={currentPdfPage}
              onEditItem={onEditStructureItem ? openEditItemModal : undefined}
              onDeleteItem={onDeleteStructureItem ? setDeleteTarget : undefined}
              onMoveItem={onMoveStructureItem}
              onAddItem={onAddStructureItem ? openAddChildModal : undefined}
              parentOverrides={parentOverrides}
              sortOverrides={sortOverrides}
            />
            {reviewSidebarOpen && (
              <ReviewQueuePanel
                pages={reviewPages}
                currentPageNum={currentPdfPage}
                isAdmin={isAdmin}
                onSelectPage={handlePdfPageChange}
                onConfirmCorrect={handleConfirmCorrectAndAdvance}
                onRerunPage={onRetryPage}
                rerunningPage={retryingPage}
                onClose={() => setReviewSidebarOpen(false)}
              />
            )}
            {showPinnedPdf ? (
              <DualPdfCompare
                pdfSrc={pdfSrc}
                currentPage={currentPdfPage}
                targetPage={pinnedPdfPage}
                targets={pinnedReference?.targets || [{ page: pinnedPdfPage, section: pinnedReference?.clauseLabel }]}
                activeTargetIndex={pinnedReference?.activeTargetIndex || 0}
                onSelectTargetIndex={(idx) => {
                  const t = pinnedReference?.targets?.[idx];
                  if (t) {
                    setPinnedPdfPage(t.page);
                    setPinnedReference((prev) => ({ ...prev, activeTargetIndex: idx }));
                  }
                }}
                onTargetPageChange={setPinnedPdfPage}
                totalPages={jobDetails?.total_pages}
                onClose={() => setPinnedPdfPage(null)}
                pdfStatusLabel={pdfStatusLabel}
              />
            ) : (
              <div className="studio-split-group" style={{ flex: 1, minWidth: 0, display: 'flex', position: 'relative' }}>
                {showPdf && (
                  <div
                    className="view-box studio-pane studio-scan-pane"
                    style={{
                      flex: viewMode === 'split' ? `0 0 ${splitRatio}%` : '1 1 100%',
                      width: viewMode === 'split' ? `${splitRatio}%` : '100%',
                      minWidth: 200,
                    }}
                  >
                    <div className="view-box-header">
                      <span><i className="fa-regular fa-file-pdf" /> Document Viewer</span>
                      <span style={{ fontSize: '0.73rem', color: 'var(--muted)' }}>{jobDetails?.total_pages ? `${jobDetails.total_pages} pages` : ''}</span>
                    </div>
                    <div className="view-box-content" style={{ padding: 0, overflow: 'hidden', display: 'flex', flexDirection: 'column', position: 'relative' }}>
                      {fileLoading && (
                        // Shown regardless of whether pdfSrc is currently stale
                        // (a previous document's blob, kept around until the
                        // new one replaces it) or null — positive, immediate
                        // feedback that the click registered and a new
                        // document is loading, instead of the click appearing
                        // to do nothing (see AppShell.jsx's fileLoading state).
                        <div className="pdf-pane-loading-overlay" data-testid="pdf-pane-loading">
                          <div className="spinner" /> Loading document…
                        </div>
                      )}
                      {pdfSrc ? (
                        <>
                          {indexBuilt && (
                            <PageReferenceBadges
                              mentions={pageMentions}
                              referenceStore={referenceStore}
                              scopeRanges={scopeRanges}
                              currentPage={currentPdfPage}
                              onOpenReference={handleBadgeOpenReference}
                            />
                          )}
                          <div style={{ flex: 1, minHeight: 0 }}>
                            <PdfCanvasViewer
                              pdfSrc={pdfSrc}
                              pageNum={currentPdfPage}
                              totalPages={jobDetails?.total_pages}
                              onPageChange={handlePdfPageChange}
                              showPageNav={false}
                            />
                          </div>
                        </>
                      ) : (
                        !fileLoading && (
                          <div style={{ textAlign: 'center', paddingTop: 60, color: 'var(--muted)' }}>
                            No document selected
                          </div>
                        )
                      )}
                    </div>
                  </div>
                )}

                {viewMode === 'split' && (
                  <SplitResizer splitRatio={splitRatio} onSplitChange={setSplitRatio} />
                )}

                {showResult && (
                  <div
                    className="view-box studio-pane studio-editor-pane"
                    style={{
                      flex: viewMode === 'split' ? `0 0 ${100 - splitRatio}%` : '1 1 100%',
                      width: viewMode === 'split' ? `${100 - splitRatio}%` : '100%',
                      minWidth: 200,
                    }}
                  >
                    <div className="view-box-header">
                      <span><i className="fa-solid fa-align-left" /> Extracted OCR Markdown</span>
                      <span style={{ fontSize: '0.73rem', color: progressColor }}>{progressLabel}</span>
                    </div>
                    {hasSelection && currentPageBlock ? (
                      <div ref={resultContentRef} className="view-box-content markdown-body">
                        <div id={`page-block-${currentPageBlock.page}`} className="ocr-page-block" data-testid={`page-block-${currentPageBlock.page}`}>
                          <h3 style={{ display: 'flex', alignItems: 'center', margin: '0 0 8px' }}>
                            Page {currentPageBlock.page}
                            <ConfidenceBadge page={currentPageBlock.data} />
                            {isAdmin && (currentPageBlock.data.status === 'failed' || currentPageBlock.data.status === 'review') && (
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
                          <WysiwygEditor
                            key={currentPageBlock.page}
                            value={currentPageBlock.pageText}
                            onChange={(md) => onSavePageText && onSavePageText(currentPageBlock.page, md)}
                            onSaveStatusChange={setEditorSaveStatus}
                          />
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
            )}
          </div>
          <StudioFloatingHud
            currentPage={currentPdfPage}
            totalPages={jobDetails?.total_pages || 1}
            onNavigate={handlePdfPageChange}
            viewMode={viewMode}
            onViewModeChange={(m) => { setViewMode(m); setPinnedPdfPage(null); }}
            isDualCompare={showPinnedPdf}
          />
        </div>
      ) : (
        <div className="dual-viewport">
          <StructureTreePanel
            open={structureSidebarOpen}
            onAddClick={onAddStructureItem ? openAddItemModal : undefined}
            referenceStore={referenceStore}
            onSelect={handleStructureJumpToPage}
            onOpenReference={handleStructureSelect}
            onSelectContainer={handleStructureContainerJump}
            onJumpContainer={handleStructureJumpContainer}
            scopeRanges={scopeRanges}
            activePage={currentPdfPage}
            onEditItem={onEditStructureItem ? openEditItemModal : undefined}
            onDeleteItem={onDeleteStructureItem ? setDeleteTarget : undefined}
            onMoveItem={onMoveStructureItem}
            onAddItem={onAddStructureItem ? openAddChildModal : undefined}
            parentOverrides={parentOverrides}
            sortOverrides={sortOverrides}
          />
          {reviewSidebarOpen && (
            <ReviewQueuePanel
              pages={reviewPages}
              currentPageNum={currentPdfPage}
              isAdmin={isAdmin}
              onSelectPage={handlePdfPageChange}
              onConfirmCorrect={handleConfirmCorrectAndAdvance}
              onRerunPage={onRetryPage}
              rerunningPage={retryingPage}
            />
          )}
          {showPdf && (
            <div className="view-box">
              <div className="view-box-header">
                <span><i className="fa-regular fa-file-pdf" /> Document Viewer</span>
                <span style={{ fontSize: '0.73rem', color: 'var(--muted)' }}>{pdfStatusLabel}</span>
              </div>
              <div className="view-box-content" style={{ padding: 0, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
                {pdfSrc ? (
                  <>
                    {indexBuilt && (
                      <PageReferenceBadges
                        mentions={pageMentions}
                        referenceStore={referenceStore}
                        scopeRanges={scopeRanges}
                        currentPage={currentPdfPage}
                        onOpenReference={handleBadgeOpenReference}
                      />
                    )}
                    <div style={{ flex: 1, minHeight: 0 }}>
                      <PdfCanvasViewer
                        pdfSrc={pdfSrc}
                        pageNum={currentPdfPage}
                        totalPages={jobDetails?.total_pages}
                        onPageChange={handlePdfPageChange}
                        showPageNav={false}
                      />
                    </div>
                  </>
                ) : (
                  <div style={{ textAlign: 'center', paddingTop: 60, color: 'var(--muted)' }}>
                    No document selected
                  </div>
                )}
              </div>
            </div>
          )}
          {showPinnedPdf ? (
            <div className="view-box">
              <div className="view-box-header">
                <span><i className="fa-regular fa-file-pdf" /> Reference: Page {pinnedPdfPage}</span>
                <button
                  className="btn btn-sm btn-secondary"
                  onClick={() => setPinnedPdfPage(null)}
                  title="Close reference pane"
                  data-testid="pinned-pdf-close-btn"
                >
                  <i className="fa-solid fa-xmark" /> Close
                </button>
              </div>
              <div className="view-box-content" style={{ padding: 0, overflow: 'hidden' }}>
                <PdfCanvasViewer
                  pdfSrc={pdfSrc}
                  pageNum={pinnedPdfPage}
                  totalPages={jobDetails?.total_pages}
                  onPageChange={setPinnedPdfPage}
                />
              </div>
            </div>
          ) : showResult && (
            <div className="view-box">
              <div className="view-box-header">
                <span><i className="fa-solid fa-align-left" /> Extracted OCR Markdown</span>
                <span style={{ fontSize: '0.73rem', color: progressColor }}>{progressLabel}</span>
              </div>
              {hasSelection && currentPageBlock ? (
                <div ref={resultContentRef} className="view-box-content markdown-body">
                  <div id={`page-block-${currentPageBlock.page}`} className="ocr-page-block" data-testid={`page-block-${currentPageBlock.page}`}>
                    <h3 style={{ display: 'flex', alignItems: 'center', margin: '0 0 8px' }}>
                      Page {currentPageBlock.page}
                      <ConfidenceBadge page={currentPageBlock.data} />
                      {isAdmin && (currentPageBlock.data.status === 'failed' || currentPageBlock.data.status === 'review') && (
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
                    <WysiwygEditor
                      key={currentPageBlock.page}
                      value={currentPageBlock.pageText}
                      onChange={(md) => onSavePageText && onSavePageText(currentPageBlock.page, md)}
                      onSaveStatusChange={setEditorSaveStatus}
                    />
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
      )}

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

      <RebuildIndexModal
        open={rebuildModalOpen}
        onClose={() => setRebuildModalOpen(false)}
        onConfirm={handleConfirmRebuild}
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
