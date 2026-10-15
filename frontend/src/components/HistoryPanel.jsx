import { useMemo, useState } from 'react';

function badgeFor(job) {
  if (job.status === 'completed') return { cls: 'completed', text: 'completed' };
  if (job.status === 'processing') return { cls: 'processing', text: `${job.processed_pages}/${job.total_pages}` };
  if (job.status === 'pausing') return { cls: 'processing', text: 'pausing...' };
  if (job.status === 'paused') return { cls: 'paused', text: 'paused' };
  if (job.status === 'failed') return { cls: 'failed', text: 'failed' };
  return { cls: 'pending', text: job.status };
}

// Status pills per the redesign plan (§7): "Needs Review" is a cross-cutting
// signal (review_pages > 0), independent of — and combinable with — the
// job's own status, not a 5th value of the status enum. "Processing" groups
// both 'processing' and 'pausing' statuses, matching the visual grouping
// badgeFor() already uses (both render the same badge class).
const STATUS_FILTERS = [
  { key: 'all', label: 'All' },
  { key: 'completed', label: 'Completed' },
  { key: 'review', label: 'Needs Review' },
  { key: 'processing', label: 'Processing' },
];

function matchesStatusFilter(job, filterKey) {
  if (filterKey === 'all') return true;
  if (filterKey === 'review') return (job.review_pages || 0) > 0;
  if (filterKey === 'completed') return job.status === 'completed';
  if (filterKey === 'processing') return job.status === 'processing' || job.status === 'pausing';
  return true;
}

// Rebuilt as a plain document table (Document / Pages / Status / Engine /
// Actions), matching the mockup's Library screen — a simple table, not the
// heavier card-list chrome this used before the round-4 Library rebuild.
// Search bar + status filter pills are kept (the mockup's own table has
// neither) since they're a real usability improvement over it; just reskinned
// flat to match.
export default function HistoryPanel({ jobs, activeJobId, onSelect, onPause, onRetry, onDownloadFile, onDownloadResult, onDelete, onRefresh }) {
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');

  const filteredJobs = useMemo(() => {
    const q = search.trim().toLowerCase();
    return jobs.filter((j) => {
      if (q && !(j.filename || '').toLowerCase().includes(q)) return false;
      return matchesStatusFilter(j, statusFilter);
    });
  }, [jobs, search, statusFilter]);

  return (
    <div className="library-doc-section">
      <div className="history-title">
        <span><i className="fa-solid fa-list-check" /> Documents</span>
        <button className="btn btn-sm btn-outline" title="Refresh" onClick={onRefresh}>
          <i className="fa-solid fa-rotate-right" />
        </button>
      </div>

      {!!jobs.length && (
        <div className="history-filters">
          <input
            type="text"
            className="history-search-input"
            placeholder="Search by filename…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            data-testid="history-search-input"
          />
          <div className="history-status-pills" role="group" aria-label="Filter by status">
            {STATUS_FILTERS.map((f) => (
              <button
                type="button"
                key={f.key}
                className={`status-pill${statusFilter === f.key ? ' active' : ''}`}
                onClick={() => setStatusFilter(f.key)}
                data-testid={`status-filter-${f.key}`}
                aria-pressed={statusFilter === f.key}
              >
                {f.label}
              </button>
            ))}
          </div>
        </div>
      )}

      {!jobs.length && (
        <div className="history-empty-state">No documents yet.</div>
      )}
      {!!jobs.length && !filteredJobs.length && (
        <div className="history-empty-state">No documents match your search/filter.</div>
      )}

      {!!filteredJobs.length && (
        <table className="history-table" data-testid="history-list">
          <thead>
            <tr>
              <th>Document</th>
              <th>Pages</th>
              <th>Status</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {filteredJobs.map((j) => {
              const badge = badgeFor(j);
              const canPause = j.status === 'processing';
              const canResume = j.status === 'paused' || j.status === 'failed';
              return (
                <tr
                  key={j.job_id}
                  className={`history-card ${j.job_id === activeJobId ? 'active' : ''}`}
                  data-testid="history-card"
                  onClick={() => onSelect(j.job_id)}
                >
                  <td className="history-filename" title={j.filename}>{j.filename}</td>
                  <td className="history-cell-muted">{j.total_pages}p</td>
                  <td><span className={`status-badge ${badge.cls}`}>{badge.text}</span></td>
                  <td className="history-actions" onClick={(e) => e.stopPropagation()}>
                    {canPause && <button className="btn btn-sm btn-warn" onClick={() => onPause(j.job_id)} title="Pause"><i className="fa-solid fa-pause" /></button>}
                    {canResume && <button className="btn btn-sm btn-secondary" onClick={() => onRetry(j.job_id)} title="Resume"><i className="fa-solid fa-rotate-right" /></button>}
                    <button className="btn btn-sm btn-outline" onClick={() => onDownloadFile(j.job_id)} title="Download PDF"><i className="fa-solid fa-file-pdf" /></button>
                    <button className="btn btn-sm btn-outline" onClick={() => onDownloadResult(j.job_id)} title="Download Markdown"><i className="fa-solid fa-file-lines" /></button>
                    <button className="btn btn-sm btn-danger" onClick={() => onDelete(j.job_id)} title="Delete"><i className="fa-solid fa-trash-can" /></button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}
