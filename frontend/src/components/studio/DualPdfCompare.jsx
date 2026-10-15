import { useState } from 'react';
import PdfCanvasViewer from '../PdfCanvasViewer';

export default function DualPdfCompare({
  pdfSrc,
  currentPage,
  targetPage,
  targets = [],
  activeTargetIndex = 0,
  onSelectTargetIndex,
  onTargetPageChange,
  totalPages,
  onClose,
  pdfStatusLabel,
}) {
  const [syncZoom, setSyncZoom] = useState(false);
  const [sharedZoom, setSharedZoom] = useState(1);

  const currentTarget = targets[activeTargetIndex] || {
    page: targetPage,
    section: targetPage ? `Page ${targetPage}` : '',
  };

  const effectiveTargetPage = currentTarget.page || targetPage;

  return (
    <div className="dual-pdf-compare-container" data-testid="dual-pdf-compare">
      <div className="dual-pdf-pane dual-pdf-left">
        <div className="view-box-header">
          <span>
            <i className="fa-regular fa-file-pdf" /> Document Scan (Current Page {currentPage})
          </span>
          <span style={{ fontSize: '0.73rem', color: 'var(--muted)' }}>{pdfStatusLabel}</span>
        </div>
        <div className="view-box-content" style={{ padding: 0, overflow: 'hidden' }}>
          <PdfCanvasViewer
            pdfSrc={pdfSrc}
            pageNum={currentPage}
            totalPages={totalPages}
            showPageNav={false}
          />
        </div>
      </div>

      <div className="dual-pdf-pane dual-pdf-right">
        <div className="view-box-header dual-pdf-target-header">
          <div className="dual-pdf-target-info">
            <span className="target-clause-badge" data-testid="target-clause-badge">
              🎯 TARGET CLAUSE {currentTarget.section ? `(§ ${currentTarget.section})` : `(Page ${effectiveTargetPage})`}
            </span>

            {targets.length > 1 && (
              <div className="dual-pdf-target-tabs" data-testid="dual-pdf-target-tabs">
                {targets.map((t, idx) => (
                  <button
                    key={idx}
                    type="button"
                    className={`target-tab-btn${idx === activeTargetIndex ? ' active' : ''}`}
                    onClick={() => onSelectTargetIndex && onSelectTargetIndex(idx)}
                    data-testid={`target-tab-${idx}`}
                  >
                    Target {idx + 1}: {t.section || `P.${t.page}`}
                  </button>
                ))}
              </div>
            )}
          </div>

          <div className="dual-pdf-target-actions">
            <button
              type="button"
              className={`btn btn-sm btn-outline btn-sync-zoom${syncZoom ? ' active' : ''}`}
              onClick={() => setSyncZoom((v) => !v)}
              title="Synchronize zoom between both comparison panes"
              data-testid="sync-zoom-btn"
            >
              <i className="fa-solid fa-link" /> Sync Zoom: {syncZoom ? 'ON' : 'OFF'}
            </button>
            <button
              type="button"
              className="btn btn-sm btn-secondary"
              onClick={onClose}
              title="Close compare mode and return to editor"
              data-testid="pinned-pdf-close-btn"
            >
              <i className="fa-solid fa-xmark" /> Close
            </button>
          </div>
        </div>

        <div className="view-box-content" style={{ padding: 0, overflow: 'hidden' }}>
          <PdfCanvasViewer
            pdfSrc={pdfSrc}
            pageNum={effectiveTargetPage}
            totalPages={totalPages}
            onPageChange={onTargetPageChange}
            showPageNav={true}
          />
        </div>
      </div>
    </div>
  );
}
