import { useCallback, useEffect, useRef, useState } from 'react';
import { apiFetch } from '../utils/api';
import { useAuth } from '../utils/AuthContext';
import { loadPersistedSettings, savePersistedSettings } from '../utils/persistedSettings';
import Header from '../components/Header';
import IdleBanner from '../components/IdleBanner';
import ControlBar from '../components/ControlBar';
import UploadPanel from '../components/UploadPanel';
import AdvancedSettingsPanel from '../components/AdvancedSettingsPanel';
import HistoryPanel from '../components/HistoryPanel';
import ViewerPanel from '../components/ViewerPanel';
import { createReferenceStore, mergeDelta } from '../utils/referenceStore';

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
  processingMode: 'single',
  batchSize: '5',
  enableOverlap: true,
  escalateBeforeFlagging: true,
};

export default function Dashboard() {
  const { isAdmin } = useAuth();

  const [form, setForm] = useState(() => {
    const persisted = loadPersistedSettings().form || {};
    const { customPrompt, pageRange, ...persistedRest } = persisted;
    return { ...DEFAULT_FORM, ...persistedRest };
  });
  const updateForm = useCallback((patch) => setForm((f) => ({ ...f, ...patch })), []);

  const [file, setFile] = useState(null);
  const [localPreviewUrl, setLocalPreviewUrl] = useState(null);
  const [remoteFileUrl, setRemoteFileUrl] = useState(null);
  const [submitting, setSubmitting] = useState(false);

  const [prompts, setPrompts] = useState({});
  const [defaults, setDefaults] = useState({});
  const [settings, setSettings] = useState({ output_path: '', idle_timeout_minutes: 15 });

  const [jobs, setJobs] = useState([]);
  const [activeJobId, setActiveJobId] = useState(null);
  const [jobDetails, setJobDetails] = useState(null);
  const [viewMode, setViewMode] = useState(() => loadPersistedSettings().viewMode || 'split');
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => loadPersistedSettings().sidebarCollapsed || false);

  // Cross-reference index: a persistent, locally-accumulated store (not
  // replaced wholesale per fetch) merged from page-delta responses. Kept in a
  // ref since it mutates in place; referenceStoreTick forces ViewerPanel to
  // re-render when new data merges in.
  const referenceStoreRef = useRef(createReferenceStore());
  const [referenceStoreTick, setReferenceStoreTick] = useState(0);

  // Part B: container page-spans, fetched once per job load and re-fetched
  // whenever reference_index_version moves (the endpoint itself is cached
  // server-side by that same version, so this is cheap to re-issue).
  const [scopeRanges, setScopeRanges] = useState([]);

  // Part C3/C4: the reference-triggered chunk currently showing (null when
  // viewing the complete document) — { startPage, endPage, label, pdfUrl,
  // pagesData } once resolved. chunkCacheRef is a bounded, in-memory cache
  // keyed by "start-end" so bouncing between a couple of already-seen
  // references doesn't re-fetch/re-generate the same chunk PDF every time;
  // capped and LRU-evicted (oldest Map entry, since re-gets move an entry to
  // the end) with URL.revokeObjectURL on eviction so a long session doesn't
  // accumulate unbounded blob URLs.
  const [chunkView, setChunkView] = useState(null);
  const chunkCacheRef = useRef(new Map());
  const CHUNK_CACHE_MAX = 10;

  // Part C1: the page currently showing in PdfCanvasViewer, reported up via
  // ViewerPanel's onCurrentPageChange — drives the windowed pages_data fetch
  // below so a 900+ page job doesn't re-transfer every page's OCR text on
  // every 2.5s poll tick. Always known exactly (no scroll-drift risk): pdf.js
  // renders one page at a time, so this only ever changes on an explicit
  // page-change event (prev/next, or a parent-driven jump), never silently.
  const [currentPdfPage, setCurrentPdfPage] = useState(1);
  const currentPdfPageRef = useRef(1);
  useEffect(() => { currentPdfPageRef.current = currentPdfPage; }, [currentPdfPage]);
  const PAGE_WINDOW = 15;

  // Locally-tracked "rebuild in flight" state — reindex_job() just re-parses
  // already-stored text, so for anything but a very large job it can start
  // AND finish between two 2.5s poll ticks. Relying solely on the server's
  // reference_index_reindexing flag (only true while a task is actually
  // mid-flight) means the UI can miss the whole thing. Cleared once
  // reference_index_version has advanced past its value at click time -
  // every rebuilt page bumps this by at least 1, so it's a reliable signal
  // regardless of how fast the rebuild actually was.
  const [reindexState, setReindexState] = useState(null); // { jobId, startVersion, startedAt }
  const REINDEX_MAX_WAIT_MS = 2 * 60 * 1000;

  useEffect(() => {
    const { customPrompt, pageRange, ...formToPersist } = form;
    savePersistedSettings({ form: formToPersist, viewMode, sidebarCollapsed });
  }, [form, viewMode, sidebarCollapsed]);

  const pollRef = useRef(null);
  const lastUserActionRef = useRef(Date.now());
  const [idle, setIdle] = useState({ text: 'Session active — idle timeout: --:--', className: '' });
  const [idleBanner, setIdleBanner] = useState({ visible: false, expired: false, message: '' });
  const [gpuStatus, setGpuStatus] = useState(null);
  const [retryingPage, setRetryingPage] = useState(null);

  const hasActiveJobs = jobs.some((j) => j.status === 'processing' || j.status === 'pausing');

  // ── GPU service status (backs the Header pill) — independent of job
  // polling so it keeps working with no job selected/active, unlike the
  // job-status poll which the resume-polling-deadlock fix intentionally
  // still gates on job activity.
  useEffect(() => {
    let cancelled = false;
    async function pollGpuHealth() {
      try {
        const resp = await apiFetch('/health');
        if (!cancelled && resp.ok) setGpuStatus(await resp.json());
      } catch (e) {
        // ignore - next poll will retry
      }
    }
    pollGpuHealth();
    const interval = setInterval(pollGpuHealth, 15000);
    return () => { cancelled = true; clearInterval(interval); };
  }, []);

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
    // Always clear immediately on any switch (including to a different,
    // already-selected job, not just to "none selected") - otherwise the
    // previous job's pages_data stays displayed for however long the fresh
    // fetch below takes, showing stale/mismatched OCR text next to the
    // newly-selected document.
    setJobDetails(null);
    if (!activeJobId) return;
    // Polls whenever a job is selected, independent of pollRef/hasActiveJobs -
    // gating this on "the list poll happened to already be running" was the
    // other half of the resume-polling-deadlock bug: setActiveJobId(jobId) in
    // handleRetry is a no-op re-render when resuming the job you're already
    // viewing (the common case - e.g. resuming from the viewer's own button),
    // so an effect keyed on [activeJobId, hasActiveJobs] never re-ran to pick
    // up pollRef.current having just been set.
    // A different document means a different reference index entirely - start
    // the accumulator over rather than merging a new job's deltas onto a
    // stale previous job's candidates.
    referenceStoreRef.current = createReferenceStore();
    setReferenceStoreTick(0);
    setScopeRanges([]);
    setReindexState(null);
    setCurrentPdfPage(1);
    currentPdfPageRef.current = 1;
    // A different job means its chunk PDFs/text are meaningless (and its
    // page numbers may not even exist in the new document) - drop the
    // active chunk view and the whole cache, revoking every cached blob URL
    // so switching documents repeatedly doesn't leak memory.
    setChunkView(null);
    for (const entry of chunkCacheRef.current.values()) {
      if (entry.pdfUrl) URL.revokeObjectURL(entry.pdfUrl);
    }
    chunkCacheRef.current.clear();
    fetchJobDetails(activeJobId, 1);
    const t = setInterval(() => fetchJobDetails(activeJobId, currentPdfPageRef.current), 2500);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeJobId]);

  useEffect(() => {
    // The existing 2.5s job-details poll already carries this cheap version
    // field - only fetch the (potentially large, page-delta) /references
    // response when it's actually moved past what's locally accumulated so far.
    if (!activeJobId) return;
    const serverVersion = jobDetails?.reference_index_version || 0;
    if (serverVersion <= referenceStoreRef.current.version) return;
    let cancelled = false;
    (async () => {
      const sinceVersion = referenceStoreRef.current.version;
      const resp = await apiFetch(`/api/jobs/${activeJobId}/references?since_version=${sinceVersion}`);
      if (!resp.ok || cancelled) return;
      const delta = await resp.json();
      mergeDelta(referenceStoreRef.current, delta);
      if (!cancelled) setReferenceStoreTick((v) => v + 1);
    })();
    return () => { cancelled = true; };
  }, [activeJobId, jobDetails?.reference_index_version]);

  useEffect(() => {
    // Same cheap-re-fetch-on-version-change pattern as the references delta
    // above — GET /scope-ranges is itself cached server-side by
    // reference_index_version, so re-issuing it whenever that version moves
    // is inexpensive (a cache hit) rather than a full recompute each time.
    if (!activeJobId) { setScopeRanges([]); return; }
    let cancelled = false;
    (async () => {
      const resp = await apiFetch(`/api/jobs/${activeJobId}/scope-ranges`);
      if (!resp.ok || cancelled) return;
      const body = await resp.json();
      if (!cancelled) setScopeRanges(body.ranges || []);
    })();
    return () => { cancelled = true; };
  }, [activeJobId, jobDetails?.reference_index_version]);

  useEffect(() => {
    if (!reindexState || reindexState.jobId !== activeJobId) return;
    const currentVersion = jobDetails?.reference_index_version || 0;
    if (currentVersion > reindexState.startVersion) {
      setReindexState(null);
    } else if (Date.now() - reindexState.startedAt > REINDEX_MAX_WAIT_MS) {
      // Safety net only - a version bump should always arrive well before
      // this for any real rebuild; this just prevents the button getting
      // stuck showing "Rebuilding..." forever if something went wrong.
      setReindexState(null);
    }
  }, [reindexState, activeJobId, jobDetails?.reference_index_version]);

  async function fetchJobDetails(jobId, aroundPage) {
    const params = new URLSearchParams();
    if (aroundPage) {
      params.set('page_from', String(Math.max(1, aroundPage - PAGE_WINDOW)));
      params.set('page_to', String(aroundPage + PAGE_WINDOW));
    }
    const qs = params.toString();
    const resp = await apiFetch(`/api/jobs/${jobId}${qs ? `?${qs}` : ''}`);
    if (!resp.ok) return;
    setJobDetails(await resp.json());
  }

  // Part C3: fetches (or serves from the C4 cache) both the chunk PDF and
  // its own pages_data in parallel, for the container range a Structure
  // sidebar chunk-jump click resolved. Selecting a different reference
  // while already viewing one just replaces chunkView (a single piece of
  // state, not a stack) - never nests.
  function evictOldestChunkCacheEntry() {
    const oldestKey = chunkCacheRef.current.keys().next().value;
    if (oldestKey === undefined) return;
    const evicted = chunkCacheRef.current.get(oldestKey);
    if (evicted?.pdfUrl) URL.revokeObjectURL(evicted.pdfUrl);
    chunkCacheRef.current.delete(oldestKey);
  }

  async function handleSelectStructureChunk({ startPage, endPage, label }) {
    if (!activeJobId) return;
    const cacheKey = `${startPage}-${endPage}`;
    const cached = chunkCacheRef.current.get(cacheKey);
    if (cached) {
      // Re-insert so the Map's iteration order reflects recency - the
      // oldest (least-recently-used) entry is always the one evicted below.
      chunkCacheRef.current.delete(cacheKey);
      chunkCacheRef.current.set(cacheKey, cached);
      setChunkView({ startPage, endPage, label, ...cached });
      return;
    }

    const [pdfResp, dataResp] = await Promise.all([
      apiFetch(`/api/jobs/${activeJobId}/file/pages?start=${startPage}&end=${endPage}`),
      apiFetch(`/api/jobs/${activeJobId}?page_from=${startPage}&page_to=${endPage}`),
    ]);
    if (!pdfResp.ok || !dataResp.ok) return;
    const blob = await pdfResp.blob();
    const pdfUrl = URL.createObjectURL(blob);
    const data = await dataResp.json();
    const entry = { pdfUrl, pagesData: data.pages_data || [] };

    if (chunkCacheRef.current.size >= CHUNK_CACHE_MAX) evictOldestChunkCacheEntry();
    chunkCacheRef.current.set(cacheKey, entry);
    setChunkView({ startPage, endPage, label, ...entry });
  }

  function handleExitChunkView() {
    setChunkView(null);
  }

  useEffect(() => {
    let cancelled = false;

    if (!activeJobId) {
      setRemoteFileUrl((prev) => {
        if (prev) URL.revokeObjectURL(prev);
        return null;
      });
      return;
    }

    (async () => {
      try {
        const resp = await apiFetch(`/api/jobs/${activeJobId}/file`);
        if (cancelled) return;
        if (!resp.ok) {
          setRemoteFileUrl((prev) => {
            if (prev) URL.revokeObjectURL(prev);
            return null;
          });
          return;
        }
        const blob = await resp.blob();
        if (cancelled) return;
        const url = URL.createObjectURL(blob);
        setRemoteFileUrl((prev) => {
          if (prev) URL.revokeObjectURL(prev);
          return url;
        });
      } catch (e) {
        if (cancelled) return;
        setRemoteFileUrl((prev) => {
          if (prev) URL.revokeObjectURL(prev);
          return null;
        });
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [activeJobId]);

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
      params.set('processing_mode', form.processingMode || 'single');
      if (form.processingMode === 'batch') {
        params.set('chunk_size', form.batchSize || '5');
        params.set('enable_overlap', form.enableOverlap === false ? 'false' : 'true');
      }
    }
    params.set('escalate_before_flagging', form.escalateBeforeFlagging === false ? 'false' : 'true');

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
      // Same race as handleRetry: a brand-new job can briefly sit at
      // 'pending' (not recognized by hasActiveJobs) before the background
      // task flips it to 'processing', which would otherwise leave the
      // gated poller stuck off. Start it directly instead of waiting.
      if (!pollRef.current) startAutoPolling();
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
    // Select the resumed job (the history-list resume button never called
    // onSelect) and ensure polling is running right now rather than waiting
    // for a loadJobsList() snapshot to notice - the just-scheduled backend
    // task may not have flipped the job's status to 'processing' yet by the
    // time this resolves, which would otherwise leave the hasActiveJobs-gated
    // poller stuck off indefinitely even though the job is actively running.
    setActiveJobId(jobId);
    if (!pollRef.current) startAutoPolling();
    loadJobsList();
  }
  async function handleRetryPage(pageNum) {
    if (!activeJobId) return;
    setRetryingPage(pageNum);
    try {
      const resp = await apiFetch(`/api/jobs/${activeJobId}/pages/${pageNum}/retry`, { method: 'POST' });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        alert(`Retry failed: ${err.detail || resp.statusText}`);
        return;
      }
      const updated = await resp.json();
      setJobDetails((jd) => jd ? {
        ...jd,
        pages_data: jd.pages_data.map((p) => (p.page_num === updated.page_num ? { ...p, ...updated } : p)),
      } : jd);
    } finally {
      setRetryingPage(null);
    }
  }

  async function handleRetryPages(scope) {
    if (!activeJobId) return;
    const resp = await apiFetch(`/api/jobs/${activeJobId}/retry-pages?scope=${scope}`, { method: 'POST' });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      alert(`Retry failed: ${err.detail || resp.statusText}`);
      return;
    }
    if (!pollRef.current) startAutoPolling();
    loadJobsList();
  }

  async function handleReindexReferences(jobId) {
    if (!jobId) return;
    const startVersion = jobDetails?.reference_index_version || 0;
    const resp = await apiFetch(`/api/jobs/${jobId}/reindex-references`, { method: 'POST' });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      alert(`Reindex failed: ${err.detail || resp.statusText}`);
      return;
    }
    // Tracked locally rather than relying solely on the server's live
    // reference_index_reindexing flag - a rebuild can easily start and finish
    // between two 2.5s poll ticks, so that flag alone can silently never be
    // observed as true. Cleared once reference_index_version has moved past
    // this snapshot (see the effect watching reindexState above).
    setReindexState({ jobId, startVersion, startedAt: Date.now() });
    fetchJobDetails(jobId, currentPdfPageRef.current);
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
  async function handleSaveLongPrompts(enabled) {
    const resp = await apiFetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enable_unlimited_long_prompts: enabled }),
    });
    if (resp.ok) setSettings((s) => ({ ...s, enable_unlimited_long_prompts: enabled }));
  }
  async function handleSaveGrounding(enabled) {
    const resp = await apiFetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enable_unlimited_grounding: enabled }),
    });
    if (resp.ok) setSettings((s) => ({ ...s, enable_unlimited_grounding: enabled }));
  }

  const pdfSrc = file ? localPreviewUrl : remoteFileUrl;
  const pdfStatusLabel = file ? `${file.name} (local preview)` : (jobDetails ? `${jobDetails.filename} (${jobDetails.total_pages}p)` : 'No document selected');

  return (
    <div className="container">
      <Header idleState={idle} gpuStatus={gpuStatus} onExit={handleExit} />
      <IdleBanner visible={idleBanner.visible} expired={idleBanner.expired} message={idleBanner.message} onKeepAlive={handleKeepAlive} />
      <ControlBar visible={hasActiveJobs} onPauseCurrent={() => activeJobId && handlePause(activeJobId)} onStopAll={handleStopAll} />

      <div className={`dashboard${sidebarCollapsed ? ' sidebar-collapsed' : ''}`}>
        <div className="sidebar">
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
            onSaveLongPrompts={handleSaveLongPrompts}
            onSaveGrounding={handleSaveGrounding}
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
          sidebarCollapsed={sidebarCollapsed}
          onToggleSidebar={() => setSidebarCollapsed((v) => !v)}
          onRetryPage={handleRetryPage}
          retryingPage={retryingPage}
          onRetryPages={handleRetryPages}
          onReindexReferences={() => activeJobId && handleReindexReferences(activeJobId)}
          reindexing={!!(reindexState && reindexState.jobId === activeJobId) || !!jobDetails?.reference_index_reindexing}
          referenceStore={referenceStoreRef.current}
          scopeRanges={scopeRanges}
          onCurrentPageChange={setCurrentPdfPage}
          chunkView={chunkView}
          onSelectChunk={handleSelectStructureChunk}
          onExitChunk={handleExitChunkView}
        />
      </div>
    </div>
  );
}
