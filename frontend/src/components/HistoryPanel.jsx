function badgeFor(job) {
  if (job.status === 'completed') return { cls: 'completed', text: 'completed' };
  if (job.status === 'processing') return { cls: 'processing', text: `${job.processed_pages}/${job.total_pages}` };
  if (job.status === 'pausing') return { cls: 'processing', text: 'pausing...' };
  if (job.status === 'paused') return { cls: 'paused', text: 'paused' };
  if (job.status === 'failed') return { cls: 'failed', text: 'failed' };
  return { cls: 'pending', text: job.status };
}

export default function HistoryPanel({ jobs, activeJobId, onSelect, onPause, onRetry, onDownloadFile, onDownloadResult, onDelete, onRefresh }) {
  return (
    <div className="panel">
      <div className="history-title">
        <span><i className="fa-solid fa-list-check" /> Documents</span>
        <button className="btn btn-sm btn-outline" title="Refresh" onClick={onRefresh}>
          <i className="fa-solid fa-rotate-right" />
        </button>
      </div>
      <div className="history-list" data-testid="history-list">
        {!jobs.length && (
          <div style={{ textAlign: 'center', color: 'var(--muted)', padding: 18, fontSize: '0.83rem' }}>No documents yet.</div>
        )}
        {jobs.map((j) => {
          const badge = badgeFor(j);
          const canPause = j.status === 'processing';
          const canResume = j.status === 'paused' || j.status === 'failed';
          return (
            <div
              key={j.job_id}
              className={`history-card ${j.job_id === activeJobId ? 'active' : ''}`}
              data-testid="history-card"
              onClick={() => onSelect(j.job_id)}
            >
              <div className="history-header">
                <span className="history-filename" title={j.filename}>{j.filename}</span>
                <span className={`status-badge ${badge.cls}`}>{badge.text}</span>
              </div>
              <div className="history-meta">
                <span><i className="fa-regular fa-file" /> {j.total_pages}p · {j.engine.toUpperCase()}</span>
                <span>{new Date(j.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
              </div>
              <div className="history-actions" onClick={(e) => e.stopPropagation()}>
                <button className="btn btn-sm btn-secondary" onClick={() => onSelect(j.job_id)}><i className="fa-solid fa-eye" /></button>
                {canPause && <button className="btn btn-sm btn-warn" onClick={() => onPause(j.job_id)}><i className="fa-solid fa-pause" /></button>}
                {canResume && <button className="btn btn-sm btn-secondary" onClick={() => onRetry(j.job_id)}><i className="fa-solid fa-rotate-right" /></button>}
                <button className="btn btn-sm btn-outline" onClick={() => onDownloadFile(j.job_id)}><i className="fa-solid fa-file-pdf" /></button>
                <button className="btn btn-sm btn-outline" onClick={() => onDownloadResult(j.job_id)}><i className="fa-solid fa-file-lines" /></button>
                <button className="btn btn-sm btn-danger" onClick={() => onDelete(j.job_id)}><i className="fa-solid fa-trash-can" /></button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
