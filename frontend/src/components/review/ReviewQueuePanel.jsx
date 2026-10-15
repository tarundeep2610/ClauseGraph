// Slide 03's Guided Review Queue: a focused list of this job's flagged
// (needs_review) pages — confidence-ascending, worst first, exactly as
// GET /api/jobs/{id}/review-pages already returns them — so a reviewer works
// through the handful of pages that actually need attention instead of
// paging through the whole document. Purely presentational: `pages` is
// supplied by the parent (already fetched), and every action is a callback —
// this component owns no network calls of its own, consistent with
// RetryPagesModal/ExportDropdown elsewhere in this codebase.
//
// Per the redesign's own later revision (plan §8.2.6, "Pruned noisy
// word-level dashed underlines and pixel-crop popovers to match backend
// reality"), there is no per-token/word confidence or bounding-box data
// anywhere in the backend — confidence is tracked per PAGE only. This panel
// does not attempt word-level "suspect token" highlighting for that reason;
// proofreading happens by editing the page's text directly in the
// WysiwygEditor (the Result pane), which already becomes the active editor
// the moment a queue row is selected and the viewer jumps to that page.
export default function ReviewQueuePanel({
  pages,
  currentPageNum,
  isAdmin,
  onSelectPage,
  onConfirmCorrect,
  onRerunPage,
  rerunningPage,
  onClose,
}) {
  const list = pages || [];

  if (list.length === 0) {
    return (
      <div className="review-queue-panel" data-testid="review-queue-panel">
        <div className="view-box-header review-queue-header">
          <span><i className="fa-solid fa-circle-check" /> Review Queue</span>
          {onClose && (
            <button
              type="button"
              className="drawer-close-btn"
              onClick={onClose}
              title="Close Review Queue"
              data-testid="review-close-btn"
              aria-label="Close Review Queue"
            >
              <i className="fa-solid fa-xmark" />
            </button>
          )}
        </div>
        <div className="review-queue-empty" data-testid="review-queue-empty">
          All pages verified — nothing left to review.
        </div>
      </div>
    );
  }

  const currentIndex = list.findIndex((p) => p.page_num === currentPageNum);
  const activePage = currentIndex >= 0 ? list[currentIndex] : list[0];

  function handleMarkVerifiedAndNext() {
    if (!activePage) return;
    onConfirmCorrect(activePage.page_num);
  }

  function handleRerun() {
    if (!activePage) return;
    onRerunPage(activePage.page_num);
  }

  return (
    <div className="review-queue-panel" data-testid="review-queue-panel">
      <div className="view-box-header review-queue-header">
        <span><i className="fa-solid fa-triangle-exclamation" /> Review Queue ({list.length})</span>
        {onClose && (
          <button
            type="button"
            className="drawer-close-btn"
            onClick={onClose}
            title="Close Review Queue"
            data-testid="review-close-btn"
            aria-label="Close Review Queue"
          >
            <i className="fa-solid fa-xmark" />
          </button>
        )}
      </div>
      <div className="review-queue-list">
        {list.map((p) => (
          <button
            key={p.page_num}
            type="button"
            className={`review-queue-item${p.page_num === activePage?.page_num ? ' active' : ''}`}
            onClick={() => onSelectPage(p.page_num)}
            data-testid={`review-queue-item-${p.page_num}`}
          >
            <span className="review-queue-item-page">Page {p.page_num}</span>
            <span className="review-queue-item-confidence">
              {p.confidence_pct != null ? `${p.confidence_pct}%` : '—'}
            </span>
            <span className="review-queue-item-reason">{p.review_reason || 'Needs Review'}</span>
          </button>
        ))}
      </div>
      <div className="review-queue-actions">
        <button
          type="button"
          className="btn btn-sm btn-primary"
          onClick={handleMarkVerifiedAndNext}
          data-testid="review-mark-verified-btn"
        >
          <i className="fa-solid fa-check" /> Mark Verified &amp; Next
        </button>
        {isAdmin && (
          <button
            type="button"
            className="btn btn-sm btn-secondary"
            disabled={rerunningPage != null}
            onClick={handleRerun}
            data-testid="review-rerun-btn"
            title="Admin only: re-run OCR extraction for this page"
          >
            <i className="fa-solid fa-bolt" /> {rerunningPage === activePage?.page_num ? 'Rerunning…' : 'Rerun'}
            <span className="admin-badge">Admin</span>
          </button>
        )}
      </div>
    </div>
  );
}
