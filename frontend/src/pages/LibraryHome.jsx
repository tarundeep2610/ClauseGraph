import { useNavigate, useOutletContext } from 'react-router-dom';
import { useAuth } from '../utils/AuthContext';
import UploadPanel from '../components/UploadPanel';
import QualityPresetPicker from '../components/QualityPresetPicker';
import HistoryPanel from '../components/HistoryPanel';

// LibraryHome ("/") is the landing page: a hero upload dropzone followed by
// a plain document table, matching the mockup's Library screen layout — a
// single centered column, not the old sidebar-with-settings-card layout.
// Advanced/admin settings (engine, prompt, output, indexing, session) moved
// into SettingsModal (opened from the unified header, see Header.jsx) since
// they're persistent app settings, not something that belongs on this page.
// QualityPresetPicker stays here, next to the upload dropzone, because it's
// a per-job choice made at upload time, not a persistent setting. All
// state/handlers come from AppShell (the parent layout route) via
// useOutletContext(), so none of it is lost when navigating to/from Studio.
export default function LibraryHome() {
  const navigate = useNavigate();
  const { isAdmin } = useAuth();
  const {
    file, onFileSelected, onClearFile, form, updateForm, onSubmit, submitting,
    jobs, activeJobId, onPause, onRetry, onDownloadFile, onDownloadResult, onDelete, onRefresh,
    sidebarCollapsed,
  } = useOutletContext();

  // Selecting (or resuming) a document from history now navigates into
  // Studio instead of flipping a local `activeJobId` state directly —
  // StudioView reads the job id back out of the URL (`:docId`) and syncs it
  // into AppShell's activeJobId itself. Resuming additionally has to kick
  // off the actual resume API call (onRetry, from AppShell) before/while
  // navigating, since that's the part of "resume" that isn't just routing.
  function handleSelect(jobId) {
    navigate(`/studio/${jobId}`);
  }

  function handleResume(jobId) {
    onRetry(jobId);
    navigate(`/studio/${jobId}`);
  }

  return (
    <div className={`dashboard library-view${sidebarCollapsed ? ' sidebar-collapsed' : ''}`}>
      <div className="sidebar">
        <UploadPanel
          file={file}
          onFileSelected={onFileSelected}
          onClearFile={onClearFile}
          form={form}
          updateForm={updateForm}
          onSubmit={onSubmit}
          submitting={submitting}
        />
        {!isAdmin && <QualityPresetPicker form={form} updateForm={updateForm} />}
        <HistoryPanel
          jobs={jobs}
          activeJobId={activeJobId}
          onSelect={handleSelect}
          onPause={onPause}
          onRetry={handleResume}
          onDownloadFile={onDownloadFile}
          onDownloadResult={onDownloadResult}
          onDelete={onDelete}
          onRefresh={onRefresh}
        />
      </div>
    </div>
  );
}
