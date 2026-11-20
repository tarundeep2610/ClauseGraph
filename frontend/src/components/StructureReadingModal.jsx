import { useEffect, useMemo, useRef, useState } from 'react';
import { renderMarkdown, renderPageMarkdown } from '../utils/markdown';
import PdfCanvasViewer from './PdfCanvasViewer';
import PageNavBar from './PageNavBar';

// Opened by any Structure-tab click (leaf or container) — shows that item's
// page or page range in a large overlay, entirely independent of the main
// viewer's own page state. It owns its own local page (1..totalPages of the
// target range), never touching the main viewer's pdfPageOverride, which is
// what guarantees the background viewer is exactly where it was left once
// this closes.
export default function StructureReadingModal({ target, content, onClose, onRetry, referenceStore }) {
  const contentRef = useRef(null);
  const [page, setPage] = useState(1);

  useEffect(() => {
    if (!target) return;
    setPage(1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target?.startPage, target?.endPage, target?.label]);

  useEffect(() => {
    if (!target) return;
    function handleKeyDown(e) {
      if (e.key === 'Escape') onClose();
    }
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [target, onClose]);

  // Only trust `content` once it actually matches the currently-open target
  // — guards against a stale response for a previous target landing after
  // the reader has already opened a different one.
  const isCurrent = !!(target && content && content.startPage === target.startPage && content.endPage === target.endPage);
  const status = isCurrent ? content.status : 'loading';

  const totalPages = target ? target.endPage - target.startPage + 1 : 0;
  const globalPage = target ? target.startPage + page - 1 : 0;

  const pageMap = useMemo(() => {
    const map = new Map();
    if (isCurrent && content.pagesData) {
      for (const p of content.pagesData) map.set(p.page, p);
    }
    return map;
  }, [isCurrent, content]);

  const pageData = pageMap.get(globalPage);
  const bodyHtml = pageData
    ? (referenceStore ? renderPageMarkdown(pageData.text || '', pageData.page, referenceStore) : renderMarkdown(pageData.text || ''))
    : '';

  // Scoped to this modal's own DOM subtree, not document.getElementById —
  // the same anchor id can exist in the still-mounted main Result pane too.
  useEffect(() => {
    if (status !== 'ready' || !target?.anchorId || !contentRef.current) return;
    const el = contentRef.current.querySelector(`#${CSS.escape(target.anchorId)}`);
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, [status, target?.anchorId, globalPage]);

  if (!target) return null;

  const title = target.label || (totalPages > 1 ? `Pages ${target.startPage}–${target.endPage}` : `Page ${target.startPage}`);

  return (
    <div className="modal-overlay" data-testid="structure-reading-modal-backdrop" onClick={onClose}>
      <div className="modal-panel reading-modal-panel" data-testid="structure-reading-modal" onClick={(e) => e.stopPropagation()}>
        <div className="reading-modal-header">
          <span className="modal-title reading-modal-title">{title}</span>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            {totalPages > 1 && status === 'ready' && (
              <PageNavBar currentPage={page} minPage={1} maxPage={totalPages} onNavigate={setPage} />
            )}
            <button className="btn btn-sm btn-secondary" onClick={onClose} data-testid="structure-reading-modal-close">
              <i className="fa-solid fa-xmark" /> Close
            </button>
          </div>
        </div>
        <div className="reading-modal-body" ref={contentRef}>
          {status === 'loading' && (
            <div className="view-box" style={{ flex: 1 }}>
              <div className="view-box-content" data-testid="structure-reading-modal-loading">
                <div style={{ textAlign: 'center', paddingTop: 60, color: 'var(--muted)' }}>
                  Loading {totalPages > 1 ? `pages ${target.startPage}–${target.endPage}` : `page ${target.startPage}`}…
                </div>
              </div>
            </div>
          )}
          {status === 'error' && (
            <div className="view-box" style={{ flex: 1 }}>
              <div className="view-box-content" data-testid="structure-reading-modal-error">
                <div style={{ textAlign: 'center', paddingTop: 60, color: 'var(--error)' }}>
                  {content?.message || 'Failed to load this section.'}
                  <div style={{ marginTop: 14 }}>
                    <button className="btn btn-sm btn-secondary" onClick={onRetry} data-testid="structure-reading-modal-retry">
                      <i className="fa-solid fa-rotate-right" /> Retry
                    </button>
                  </div>
                </div>
              </div>
            </div>
          )}
          {status === 'ready' && (
            <>
              <div className="view-box">
                <div className="view-box-header"><span><i className="fa-regular fa-file-pdf" /> Document</span></div>
                <div className="view-box-content" style={{ padding: 0, overflow: 'hidden' }}>
                  <PdfCanvasViewer pdfSrc={content.pdfUrl} pageNum={page} totalPages={totalPages} onPageChange={setPage} showPageNav={false} />
                </div>
              </div>
              <div className="view-box">
                <div className="view-box-header"><span><i className="fa-solid fa-align-left" /> Extracted Text</span></div>
                <div className="view-box-content markdown-body">
                  {pageData ? (
                    <div id={`page-block-${globalPage}`} data-testid={`page-block-${globalPage}`}>
                      <h3>Page {globalPage}</h3>
                      <div dangerouslySetInnerHTML={{ __html: bodyHtml }} />
                    </div>
                  ) : (
                    <div style={{ textAlign: 'center', paddingTop: 60, color: 'var(--muted)' }}>No text for this page.</div>
                  )}
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
