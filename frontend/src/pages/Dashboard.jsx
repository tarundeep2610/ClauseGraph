import { useCallback, useEffect, useRef, useState } from 'react';
import { apiFetch } from '../utils/api';
import { useAuth } from '../utils/AuthContext';
import Header from '../components/Header';
import IdleBanner from '../components/IdleBanner';
import ControlBar from '../components/ControlBar';
import UploadPanel from '../components/UploadPanel';
import AdvancedSettingsPanel from '../components/AdvancedSettingsPanel';
import HistoryPanel from '../components/HistoryPanel';
import ViewerPanel from '../components/ViewerPanel';

const DEFAULT_FORM = {
  engine: 'unlimited',
  scale: '1.5',
  promptMode: 'document',
  customPrompt: '',
  pageRange: '',
  glmTiling: true,
  glmTemperature: 0.0,
  glmNumCtx: '8192',
  glmNumPredict: '2048',
  glmRepeatPenalty: 1.2,
  unlimitedBaseSize: '1024',
};

export default function Dashboard() {
  const { isAdmin } = useAuth();

  const [form, setForm] = useState(DEFAULT_FORM);
  const updateForm = useCallback((patch) => setForm((f) => ({ ...f, ...patch })), []);

  const [file, setFile] = useState(null);
  const [localPreviewUrl, setLocalPreviewUrl] = useState(null);
  const [submitting, setSubmitting] = useState(false);

  const [prompts, setPrompts] = useState({});
  const [defaults, setDefaults] = useState({});
  const [settings, setSettings] = useState({ output_path: '', idle_timeout_minutes: 15 });

  const [jobs, setJobs] = useState([]);
  const [activeJobId, setActiveJobId] = useState(null);
  const [jobDetails, setJobDetails] = useState(null);
  const [viewMode, setViewMode] = useState('split');

  const pollRef = useRef(null);
  const lastUserActionRef = useRef(Date.now());
  const [idle, setIdle] = useState({ text: 'Session active — idle timeout: --:--', className: '' });
  const [idleBanner, setIdleBanner] = useState({ visible: false, expired: false, message: '' });

  const hasActiveJobs = jobs.some((j) => j.status === 'processing' || j.status === 'pausing');

  // ── Load prompts + settings + jobs on mount ────────────────────────────
  useEffect(() => {
    (async () => {
      const resp = await apiFetch('/api/prompts');
      if (resp.ok) {
        const data = await resp.json();
        setPrompts(data.prompts || {});
        setDefaults(data.defaults || {});
        updateForm({ customPrompt: (data.prompts || {}).document || '' });
      }
    })();
    if (isAdmin) {
      (async () => {
        const resp = await apiFetch('/api/settings');
        if (resp.ok) setSettings(await resp.json());
      })();
    }
    loadJobsList();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isAdmin]);

  // ── Track user activity for the idle timer ─────────────────────────────
  useEffect(() => {
    const mark = () => { lastUserActionRef.current = Date.now(); };
    ['mousedown', 'keydown', 'touchstart', 'scroll', 'click'].forEach((evt) =>
      document.addEventListener(evt, mark, { passive: true })
    );
    return () => ['mousedown', 'keydown', 'touchstart', 'scroll', 'click'].forEach((evt) =>
      document.removeEventListener(evt, mark)
    );
  }, []);

  useEffect(() => {
    const interval = setInterval(() => {
      const timeoutMins = settings.idle_timeout_minutes ?? 15;
      if (timeoutMins <= 0) {
        setIdle({ text: 'Idle timeout disabled', className: '' });
        return;
      }
      if (hasActiveJobs) {
        lastUserActionRef.current = Date.now();
        setIdle({ text: 'OCR running — idle timer paused', className: '' });
        setIdleBanner({ visible: false, expired: false, message: '' });
        return;
      }
      const idleMs = Date.now() - lastUserActionRef.current;
      const totalMs = timeoutMins * 60 * 1000;
      const remainMs = Math.max(0, totalMs - idleMs);
      const remainSecs = Math.floor(remainMs / 1000);
      const m = Math.floor(remainSecs / 60);
      const s = remainSecs % 60;
      const label = `${m}:${String(s).padStart(2, '0')}`;

      if (remainMs <= 0) {
        if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
        setIdle({ text: 'Session paused — server shutting down', className: 'expired' });
        setIdleBanner({ visible: true, expired: true, message: `No activity for ${timeoutMins} minutes. Polling stopped. The server will shut down shortly.` });
      } else if (remainMs <= 2 * 60 * 1000) {
        setIdle({ text: `Idle shutdown in ${label}`, className: 'warn' });
        setIdleBanner({ visible: true, expired: false, message: `Idle for ${timeoutMins - m - 1}m ${60 - s}s — server will shut down in ${label}` });
      } else {
        setIdle({ text: `Session active — idle shutdown in ${label}`, className: '' });
        setIdleBanner({ visible: false, expired: false, message: '' });
      }
    }, 5000);
    return () => clearInterval(interval);
  }, [settings.idle_timeout_minutes, hasActiveJobs]);

  function handleKeepAlive() {
    lastUserActionRef.current = Date.now();
    setIdleBanner({ visible: false, expired: false, message: '' });
    apiFetch('/api/idle-status').catch(() => {});
    if (!pollRef.current) startAutoPolling();
  }

  // ── Job list + polling ──────────────────────────────────────────────────
  const loadJobsList = useCallback(async () => {
    const resp = await apiFetch('/api/jobs');
    if (!resp.ok) return;
    const data = await resp.json();
    setJobs(data);
    setActiveJobId((prev) => prev ?? (data.length ? data[0].job_id : null));
  }, []);

  function startAutoPolling() {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(() => {
      loadJobsList();
    }, 2500);
  }

  useEffect(() => {
    if (hasActiveJobs && !pollRef.current) startAutoPolling();
    else if (!hasActiveJobs && pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasActiveJobs]);

  useEffect(() => {
    if (!activeJobId) { setJobDetails(null); return; }
    fetchJobDetails(activeJobId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeJobId]);

  useEffect(() => {
    if (!activeJobId || !pollRef.current) return;
    const t = setInterval(() => fetchJobDetails(activeJobId), 2500);
    return () => clearInterval(t);
  }, [activeJobId, hasActiveJobs]);

  async function fetchJobDetails(jobId) {
    const resp = await apiFetch(`/api/jobs/${jobId}`);
    if (!resp.ok) return;
    setJobDetails(await resp.json());
  }

  // ── File selection ──────────────────────────────────────────────────────
  function handleFileSelected(f) {
    setFile(f);
    if (localPreviewUrl) URL.revokeObjectURL(localPreviewUrl);
    const url = URL.createObjectURL(f);
    setLocalPreviewUrl(url);
    updateForm({ pageRange: '' });
  }

  function handleClearFile() {
    setFile(null);
    if (localPreviewUrl) { URL.revokeObjectURL(localPreviewUrl); setLocalPreviewUrl(null); }
  }

  // ── Submit job ───────────────────────────────────────────────────────────
  async function handleSubmit() {
    if (!file) return;
    lastUserActionRef.current = Date.now();
    setSubmitting(true);

    const params = new URLSearchParams({ engine: form.engine, scale: form.scale, prompt_mode: form.promptMode });
    if (form.pageRange) params.set('page_range', form.pageRange);
    if (form.customPrompt && form.customPrompt !== (defaults[form.promptMode] || '')) {
      params.set('custom_prompt', form.customPrompt);
    }
    if (form.engine === 'glm') {
      params.set('tiling', form.glmTiling ? 'true' : 'false');
      params.set('glm_temperature', String(form.glmTemperature));
      params.set('glm_num_ctx', form.glmNumCtx);
      params.set('glm_num_predict', form.glmNumPredict);
      params.set('glm_repeat_penalty', String(form.glmRepeatPenalty));
    } else {
      params.set('tiling', 'false');
      params.set('unlimited_base_size', form.unlimitedBaseSize);
    }

    const body = new FormData();
    body.append('file', file);

    try {
      const resp = await apiFetch(`/api/jobs?${params.toString()}`, { method: 'POST', body });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        alert(`Failed: ${err.detail || resp.statusText}`);
        return;
      }
      const res = await resp.json();
      handleClearFile();
      await loadJobsList();
      setActiveJobId(res.job_id);
    } catch (e) {
      alert(`Network error: ${e.message}`);
    } finally {
      setSubmitting(false);
    }
  }

  // ── Job actions ──────────────────────────────────────────────────────────
  async function handlePause(jobId) {
    await apiFetch(`/api/jobs/${jobId}/pause`, { method: 'POST' });
    loadJobsList();
  }
  async function handleRetry(jobId) {
    await apiFetch(`/api/jobs/${jobId}/retry`, { method: 'POST' });
    loadJobsList();
  }
  async function handleDelete(jobId) {
    if (!confirm('Delete this document and its results?')) return;
    await apiFetch(`/api/jobs/${jobId}`, { method: 'DELETE' });
    if (activeJobId === jobId) setActiveJobId(null);
    loadJobsList();
  }
  function _filenameFromContentDisposition(header, fallback) {
    if (!header) return fallback;
    const match = /filename="?([^";]+)"?/i.exec(header);
    return match ? match[1] : fallback;
  }

  async function _downloadViaAuthedFetch(url, fallbackFilename) {
    // window.open()/plain navigation never carries the Authorization header
    // apiFetch attaches, so authenticated downloads have to go through a
    // real fetch and be saved from the resulting blob instead.
    const resp = await apiFetch(url);
    if (!resp.ok) {
      alert(`Download failed: ${resp.status} ${resp.statusText}`);
      return;
    }
    const blob = await resp.blob();
    const filename = _filenameFromContentDisposition(resp.headers.get('Content-Disposition'), fallbackFilename);
    const blobUrl = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = blobUrl;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(blobUrl);
  }

  function handleDownloadFile(jobId) {
    _downloadViaAuthedFetch(`/api/jobs/${jobId}/file?download=true`, `${jobId}`);
  }
  function handleDownloadResult(jobId) {
    _downloadViaAuthedFetch(`/api/jobs/${jobId}/download-result`, `${jobId}_ocr_result.md`);
  }
  async function handleStopAll() {
    if (!confirm('Stop all running OCR extractions? Pages extracted so far are preserved.')) return;
    await apiFetch('/api/jobs/stop-all', { method: 'POST' });
    setTimeout(loadJobsList, 600);
  }
  async function handleExit() {
    if (!confirm('Shut down the OCR server?')) return;
    try { await apiFetch('/api/shutdown', { method: 'POST' }); } catch (e) {}
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
  }

  async function handleSavePrompt(mode, text) {
    const resp = await apiFetch(`/api/prompts/${mode}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
    if (resp.ok) setPrompts((p) => ({ ...p, [mode]: text }));
  }
  async function handleResetPrompt(mode) {
    const resp = await apiFetch(`/api/prompts/${mode}`, { method: 'DELETE' });
    if (resp.ok) {
      const data = await resp.json();
      setPrompts((p) => ({ ...p, [mode]: data.text }));
      return data.text;
    }
    return form.customPrompt;
  }
  async function handleSaveOutputPath(path) {
    const resp = await apiFetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ output_path: path }),
    });
    if (resp.ok) setSettings((s) => ({ ...s, output_path: path }));
  }
  async function handleSaveIdleTimeout(mins) {
    const resp = await apiFetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ idle_timeout_minutes: mins }),
    });
    if (resp.ok) setSettings((s) => ({ ...s, idle_timeout_minutes: mins }));
  }

  const pdfSrc = file ? localPreviewUrl : (activeJobId ? `/api/jobs/${activeJobId}/file` : null);
  const pdfStatusLabel = file ? `${file.name} (local preview)` : (jobDetails ? `${jobDetails.filename} (${jobDetails.total_pages}p)` : 'No document selected');

  return (
    <div className="container">
      <Header idleState={idle} onExit={handleExit} />
      <IdleBanner visible={idleBanner.visible} expired={idleBanner.expired} message={idleBanner.message} onKeepAlive={handleKeepAlive} />
      <ControlBar visible={hasActiveJobs} onPauseCurrent={() => activeJobId && handlePause(activeJobId)} onStopAll={handleStopAll} />

      <div className="dashboard">
        <div>
          <UploadPanel
            file={file}
            onFileSelected={handleFileSelected}
            onClearFile={handleClearFile}
            form={form}
            updateForm={updateForm}
            onSubmit={handleSubmit}
            submitting={submitting}
          />
          <AdvancedSettingsPanel
            form={form}
            updateForm={updateForm}
            prompts={prompts}
            defaults={defaults}
            onSavePrompt={handleSavePrompt}
            onResetPrompt={handleResetPrompt}
            settings={settings}
            onSaveOutputPath={handleSaveOutputPath}
            onSaveIdleTimeout={handleSaveIdleTimeout}
          />
          <HistoryPanel
            jobs={jobs}
            activeJobId={activeJobId}
            onSelect={setActiveJobId}
            onPause={handlePause}
            onRetry={handleRetry}
            onDownloadFile={handleDownloadFile}
            onDownloadResult={handleDownloadResult}
            onDelete={handleDelete}
            onRefresh={loadJobsList}
          />
        </div>

        <ViewerPanel
          viewMode={viewMode}
          setViewMode={setViewMode}
          pdfSrc={pdfSrc}
          pdfStatusLabel={pdfStatusLabel}
          jobDetails={jobDetails}
          hasSelection={!!(file || activeJobId)}
          onPause={() => activeJobId && handlePause(activeJobId)}
          onResume={() => activeJobId && handleRetry(activeJobId)}
          onDownloadPdf={() => activeJobId && handleDownloadFile(activeJobId)}
          onDownloadResult={() => activeJobId && handleDownloadResult(activeJobId)}
        />
      </div>
    </div>
  );
}
