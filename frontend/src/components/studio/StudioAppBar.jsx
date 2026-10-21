import { useEffect, useRef } from 'react';
import ExportDropdown from './ExportDropdown';

export default function StudioAppBar({
  filename,
  currentPage,
  totalPages,
  saveStatus = 'saved',
  onBackToLibrary,
  onBuildIndex,
  reindexing = false,
  indexBuilt = false,
  onExport,
  isDualCompare = false,
  onExitDualCompare,
  searchOpen = false,
  onToggleSearch,
  searchQuery = '',
  onSearchQueryChange,
  matchIndex = 0,
  totalMatches = 0,
  onSearchPrev,
  onSearchNext,
  bothPanesSearch = true,
  onBothPanesSearchChange,
  canPause = false,
  onPause,
  canResume = false,
  onResume,
  isAdmin = false,
  onOpenRetryModal,
  onToggleSidebar,
  sidebarCollapsed = false,
}) {
  const searchInputRef = useRef(null);

  useEffect(() => {
    if (searchOpen && searchInputRef.current) {
      searchInputRef.current.focus();
      searchInputRef.current.select();
    }
  }, [searchOpen]);

  // Global Ctrl+F listener
  useEffect(() => {
    function handleKeyDown(e) {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'f') {
        e.preventDefault();
        if (onToggleSearch) onToggleSearch();
      }
    }
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onToggleSearch]);

  const saveLabel = saveStatus === 'saving'
    ? '● Saving…'
    : saveStatus === 'pending'
    ? '● Unsaved changes'
    : '✓ Saved';

  return (
    <header className="top-file-bar studio-app-bar" data-testid="studio-app-bar">
      <div className="bar-left">
        <button
          className="btn btn-sm btn-secondary btn-library-back"
          onClick={onBackToLibrary}
          data-testid="back-to-library-btn"
          title="Return to Library"
        >
          <i className="fa-solid fa-arrow-left" /> Library
        </button>

        <div className="file-meta-group">
          <span className="file-name" data-testid="studio-file-name" title={filename || 'Document'}>
            {filename || 'Document'}
          </span>
          {currentPage && totalPages ? (
            <span className="file-page-badge" data-testid="studio-page-badge">
              P. {currentPage} of {totalPages}
            </span>
          ) : null}
          <span
            className={`file-save-status file-save-status--${saveStatus}`}
            data-testid="studio-save-status"
          >
            {saveLabel}
          </span>
        </div>
      </div>

      <div className="bar-right">
        {onToggleSidebar && (
          <button
            type="button"
            className="btn btn-sm btn-secondary"
            onClick={onToggleSidebar}
            title={sidebarCollapsed ? 'Show sidebar' : 'Hide sidebar'}
            data-testid="sidebar-toggle-btn"
            style={{ display: 'none' }}
          >
            <i className={`fa-solid ${sidebarCollapsed ? 'fa-angles-right' : 'fa-angles-left'}`} />
          </button>
        )}

        {canPause && (
          <button type="button" className="btn btn-sm btn-warn" onClick={onPause} data-testid="pause-job-btn">
            <i className="fa-solid fa-pause" /> Pause
          </button>
        )}
        {canResume && (
          <button type="button" className="btn btn-sm btn-secondary" onClick={onResume} data-testid="resume-job-btn">
            <i className="fa-solid fa-rotate-right" /> Resume
          </button>
        )}
        {isAdmin && onOpenRetryModal && (
          <button
            type="button"
            className="btn btn-sm btn-secondary"
            onClick={onOpenRetryModal}
            data-testid="retry-pages-btn"
          >
            <i className="fa-solid fa-rotate-right" /> Retry Pages
          </button>
        )}

        {isDualCompare && (
          <button
            className="btn btn-sm btn-warn btn-return-editor"
            onClick={onExitDualCompare}
            data-testid="studio-return-to-editor-btn"
            title="Exit compare mode and return to WYSIWYG editor"
          >
            <i className="fa-solid fa-file-pen" /> Return to Editor
          </button>
        )}

        <button
          className={`btn-search-trigger${searchOpen ? ' active' : ''}`}
          onClick={onToggleSearch}
          title="Search in document (Ctrl+F)"
          data-testid="studio-find-btn"
        >
          <i className="fa-solid fa-magnifying-glass" /> Find <kbd>Ctrl+F</kbd>
        </button>

        {onBuildIndex && (
          <button
            className={`btn btn-sm ${indexBuilt ? 'btn-secondary' : 'btn-primary'} btn-build-index`}
            onClick={onBuildIndex}
            disabled={reindexing}
            data-testid="reindex-references-btn"
            title={
              indexBuilt
                ? 'Choose what to rebuild: the document outline, reference links, or both'
                : "Finds the document's outline and links mentions like \"see Clause 8\" to where that clause is actually defined"
            }
          >
            <i className="fa-solid fa-bolt" /> {reindexing ? 'Rebuilding…' : indexBuilt ? 'Rebuild Index & Reference Links' : 'Build Index & Reference Links'}
          </button>
        )}

        <ExportDropdown
          currentPage={currentPage}
          totalPages={totalPages}
          onExport={onExport}
        />
      </div>

      {searchOpen && (
        <div className="search-widget" data-testid="studio-search-widget">
          <i className="fa-solid fa-magnifying-glass search-widget-icon" />
          <input
            ref={searchInputRef}
            type="text"
            className="search-input"
            placeholder="Find in document…"
            value={searchQuery}
            onChange={(e) => onSearchQueryChange && onSearchQueryChange(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                if (e.shiftKey) onSearchPrev && onSearchPrev();
                else onSearchNext && onSearchNext();
              } else if (e.key === 'Escape') {
                if (onToggleSearch) onToggleSearch();
              }
            }}
            data-testid="studio-search-input"
          />
          {searchQuery && (
            <span className="search-count" data-testid="studio-search-count">
              {totalMatches > 0 ? `${matchIndex + 1} of ${totalMatches}` : '0 matches'}
            </span>
          )}
          <div className="search-nav-buttons">
            <button
              className="btn-search-nav"
              onClick={onSearchPrev}
              title="Previous match (Shift+Enter)"
              disabled={totalMatches === 0}
              data-testid="studio-search-prev-btn"
            >
              ▲
            </button>
            <button
              className="btn-search-nav"
              onClick={onSearchNext}
              title="Next match (Enter)"
              disabled={totalMatches === 0}
              data-testid="studio-search-next-btn"
            >
              ▼
            </button>
          </div>
          <div className="search-divider" />
          <label className="search-both-panes-label" title="Search both PDF scan and extracted text simultaneously">
            <input
              type="checkbox"
              checked={bothPanesSearch}
              onChange={(e) => onBothPanesSearchChange && onBothPanesSearchChange(e.target.checked)}
              data-testid="studio-search-both-panes"
            />
            Both Panes
          </label>
          <button
            className="btn-search-close"
            onClick={onToggleSearch}
            title="Close search (Esc)"
            data-testid="studio-search-close-btn"
          >
            ✕
          </button>
        </div>
      )}
    </header>
  );
}
