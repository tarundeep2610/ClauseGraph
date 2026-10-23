import { useCallback, useEffect, useRef, useState } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import { apiFetch } from '../utils/api';
import { useAuth } from '../utils/AuthContext';
import { loadPersistedSettings, savePersistedSettings } from '../utils/persistedSettings';
import Header from '../components/Header';
import SettingsModal from '../components/SettingsModal';
import SaveFileDialog from '../components/SaveFileDialog';
import IdleBanner from '../components/IdleBanner';
import ControlBar from '../components/ControlBar';
import { createReferenceStore, mergeDelta } from '../utils/referenceStore';
import { createActionItemStore, mergeActionItemDelta } from '../utils/actionItemStore';

// AppShell is the parent layout route (see App.jsx): it owns every piece of
// state the old monolithic Dashboard.jsx used to own directly (job list +
// polling, the idle timer, settings, the active document's viewer state,
// structure-tree overrides, the reference-index store, etc.) and stays
// mounted across navigation between LibraryHome ("/") and StudioView
// ("/studio/:docId"), which is the whole point of this split: none of that
// state (job-list polling, the idle timer, ...) is lost when the user
// navigates between the two pages. It renders the chrome that's always
// visible (Header/IdleBanner/ControlBar) plus an <Outlet> that hands every
// piece of state and handler function below to whichever page is active via
// React Router's useOutletContext() pattern.

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

export default function AppShell() {
  const { isAdmin } = useAuth();
  const [settingsOpen, setSettingsOpen] = useState(false);
  // { blob, suggestedName } while the fallback "Save As" filename prompt is
  // open (browsers with no window.showSaveFilePicker — Firefox/Safari), null
  // otherwise. See _downloadViaAuthedFetch below.
  const [saveDialog, setSaveDialog] = useState(null);

  const [form, setForm] = useState(() => {
    const persisted = loadPersistedSettings().form || {};
    const { customPrompt, pageRange, ...persistedRest } = persisted;
    return { ...DEFAULT_FORM, ...persistedRest };
  });
  const updateForm = useCallback((patch) => setForm((f) => ({ ...f, ...patch })), []);

  const [file, setFile] = useState(null);
  const [localPreviewUrl, setLocalPreviewUrl] = useState(null);
  const [remoteFileUrl, setRemoteFileUrl] = useState(null);
  // True while the /file blob fetch below is in flight for the current
  // activeJobId — lets the PDF pane show an honest "loading" state instead
  // of either the *previous* document (remoteFileUrl isn't cleared until the
  // new blob replaces it, to avoid a flash to "No document selected") or a
  // static empty-state message, which is what made clicking a document in
  // the Library feel like it needed 2-3 clicks to "register."
  const [fileLoading, setFileLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const [prompts, setPrompts] = useState({});
  const [defaults, setDefaults] = useState({});
  const [settings, setSettings] = useState({ output_path: '', idle_timeout_minutes: 15 });
  const [aiIndexStatus, setAiIndexStatus] = useState(null);

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

  // Actionable items: same ref-held, delta-merged accumulator pattern as
  // referenceStoreRef above, much simpler store shape (see actionItemStore.js).
  const actionItemStoreRef = useRef(createActionItemStore());
  const [actionItemStoreTick, setActionItemStoreTick] = useState(0);

  // Part B: container page-spans, fetched once per job load and re-fetched
  // whenever reference_index_version moves (the endpoint itself is cached
  // server-side by that same version, so this is cheap to re-issue).
  const [scopeRanges, setScopeRanges] = useState([]);

  // Guided Review Queue (Sprint 6): the job's own flagged pages, re-fetched
  // whenever the job's review_pages count changes (bumped by
  // refresh_job_page_counts on every status transition and by
  // confirm-correct/retry) rather than on every 2.5s poll tick regardless.
  const [reviewPages, setReviewPages] = useState([]);

  // Add/edit/delete/reorder overrides on top of the auto-detected Structure
  // tree — { parents: {item_key: new_parent_key}, order: {scope_key:
  // [item_key,...]} }, plain passthrough of GET .../structure-hierarchy-
  // overrides. Re-fetched on the same reference_index_version trigger as
  // scopeRanges/references above (every override mutation bumps that same
  // version, per the backend's design — see structure_overrides.py).
  const [structureHierarchy, setStructureHierarchy] = useState({ parents: [], order: [] });

  // Structure-tab reading modal content — the range currently open (or
  // being fetched) in StructureReadingModal, null when the modal is closed.
  // { startPage, endPage, label, status: 'loading'|'error'|'ready', pdfUrl?,
  // pagesData?, message? }. structureContentCacheRef is a bounded,
  // in-memory cache keyed by "start-end" so bouncing between a couple of
  // already-seen references doesn't re-fetch/re-generate the same PDF every
  // time; capped and LRU-evicted (oldest Map entry, since re-gets move an
  // entry to the end) with URL.revokeObjectURL on eviction so a long
  // session doesn't accumulate unbounded blob URLs.
  const [structureContent, setStructureContent] = useState(null);
  const structureContentCacheRef = useRef(new Map());
  const STRUCTURE_CONTENT_CACHE_MAX = 10;

  // The page currently showing in PdfCanvasViewer/Result pane, reported up
  // via ViewerPanel's onCurrentPageChange — drives the windowed pages_data
  // fetch below so a 900+ page job doesn't re-transfer every page's OCR
  // text on every 2.5s poll tick. Always known exactly (no scroll-drift
  // risk): pdf.js renders one page at a time, and the Result pane now shows
  // only this same page, so this only ever changes on an explicit
  // page-change event (prev/next, jump-to-page, or a parent-driven jump),
  // never silently.
  const [currentPdfPage, setCurrentPdfPage] = useState(1);
  const currentPdfPageRef = useRef(1);
  useEffect(() => { currentPdfPageRef.current = currentPdfPage; }, [currentPdfPage]);
  // Both panes now show exactly one page at a time (no continuous Result
  // scroll left to fill), so this only needs to be a small prefetch buffer
  // for prev/next to feel instant — not the wide pre-fetched window the old
  // continuously-scrolling Result pane needed.
  const PAGE_WINDOW = 2;
  const fetchSeqRef = useRef(0);

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
  // Same "track locally, don't rely solely on the live in-flight flag"
  // pattern as reindexState above, for the separate actionable-items pass.
  const [actionItemReindexState, setActionItemReindexState] = useState(null); // { jobId, startVersion, startedAt }

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
      loadAiIndexStatus();
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
    // Deliberately does NOT auto-select the first job (the old monolithic
    // Dashboard did, via setActiveJobId((prev) => prev ?? data[0]?.job_id),
    // so the single-screen layout always showed a document). Now that
    // Library ("/") and Studio ("/studio/:docId") are separate routes, the
    // user should land on the list, not get auto-redirected into a
    // document — activeJobId only ever changes via an explicit action
    // (submitting a job, resuming one, or StudioView syncing it from the
    // :docId URL param).
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
    actionItemStoreRef.current = createActionItemStore();
    setActionItemStoreTick(0);
    setActionItemReindexState(null);
    setScopeRanges([]);
    setStructureHierarchy({ parents: [], order: [] });
    setReindexState(null);
    setCurrentPdfPage(1);
    currentPdfPageRef.current = 1;
    // A different job means its cached chunk PDFs/text are meaningless (and
    // its page numbers may not even exist in the new document) - close the
    // reading modal and drop the whole cache, revoking every cached blob
    // URL so switching documents repeatedly doesn't leak memory.
    setStructureContent(null);
    for (const entry of structureContentCacheRef.current.values()) {
      if (entry.pdfUrl) URL.revokeObjectURL(entry.pdfUrl);
    }
    structureContentCacheRef.current.clear();
    fetchJobDetails(activeJobId, 1);
    const t = setInterval(() => fetchJobDetails(activeJobId, currentPdfPageRef.current), 2500);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeJobId]);

  // Fires immediately (debounced) on every page-change event, instead of
  // waiting for the next 2.5s poll tick — this is the actual fix for the
  // Result pane "running dry": before, the windowed fetch only re-centered
  // when the slow poll happened to read a moved currentPdfPageRef, so a
  // reader navigating faster than 2.5s per page would outrun the loaded
  // window. Skipped while there's no active job.
  useEffect(() => {
    if (!activeJobId) return;
    const t = setTimeout(() => fetchJobDetails(activeJobId, currentPdfPage), 200);
    return () => clearTimeout(t);
  }, [activeJobId, currentPdfPage]);

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
    // above, against the separate action_item_index_version counter.
    if (!activeJobId) return;
    const serverVersion = jobDetails?.action_item_index_version || 0;
    if (serverVersion <= actionItemStoreRef.current.version) return;
    let cancelled = false;
    (async () => {
      const sinceVersion = actionItemStoreRef.current.version;
      const resp = await apiFetch(`/api/jobs/${activeJobId}/action-items?since_version=${sinceVersion}`);
      if (!resp.ok || cancelled) return;
      const delta = await resp.json();
      mergeActionItemDelta(actionItemStoreRef.current, delta);
      if (!cancelled) setActionItemStoreTick((v) => v + 1);
    })();
    return () => { cancelled = true; };
  }, [activeJobId, jobDetails?.action_item_index_version]);

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
    // Same cheap-re-fetch-on-version-change pattern as references/scope-
    // ranges above — every add/edit/delete/reorder mutation bumps
    // reference_index_version too, so this stays in sync with them for free.
    if (!activeJobId) { setStructureHierarchy({ parents: [], order: [] }); return; }
    let cancelled = false;
    (async () => {
      const resp = await apiFetch(`/api/jobs/${activeJobId}/structure-hierarchy-overrides`);
      if (!resp.ok || cancelled) return;
      const body = await resp.json();
      if (!cancelled) setStructureHierarchy({ parents: body.parents || [], order: body.order || [] });
    })();
    return () => { cancelled = true; };
  }, [activeJobId, jobDetails?.reference_index_version]);

  useEffect(() => {
    if (!activeJobId) { setReviewPages([]); return; }
    let cancelled = false;
    (async () => {
      const resp = await apiFetch(`/api/jobs/${activeJobId}/review-pages?limit=200`);
      if (!resp.ok || cancelled) return;
      const body = await resp.json();
      if (!cancelled) setReviewPages(body.pages || []);
    })();
    return () => { cancelled = true; };
  }, [activeJobId, jobDetails?.review_pages]);

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

  useEffect(() => {
    if (!actionItemReindexState || actionItemReindexState.jobId !== activeJobId) return;
    const currentVersion = jobDetails?.action_item_index_version || 0;
    if (currentVersion > actionItemReindexState.startVersion) {
      setActionItemReindexState(null);
    } else if (Date.now() - actionItemReindexState.startedAt > REINDEX_MAX_WAIT_MS) {
      setActionItemReindexState(null);
    }
  }, [actionItemReindexState, activeJobId, jobDetails?.action_item_index_version]);

  async function fetchJobDetails(jobId, aroundPage) {
    const seq = ++fetchSeqRef.current;
    const params = new URLSearchParams();
    if (aroundPage) {
      params.set('page_from', String(Math.max(1, aroundPage - PAGE_WINDOW)));
      params.set('page_to', String(aroundPage + PAGE_WINDOW));
    }
    const qs = params.toString();
    const resp = await apiFetch(`/api/jobs/${jobId}${qs ? `?${qs}` : ''}`);
    if (!resp.ok) return;
    const data = await resp.json();
    // An older in-flight request resolving after a newer one must not
    // clobber the newer page's already-applied response — possible now
    // that fetches fire on every navigation instead of a slow serialized
    // poll, so responses can arrive out of order.
    if (seq !== fetchSeqRef.current) return;
    setJobDetails(data);
  }

  // Fetches (or serves from the cache) both the PDF and OCR text for the
  // page or page-range a Structure-tab click opened the reading modal for.
  // Reusing the existing cache means a leaf click and a later container
  // click covering that same page both hit the same entry.
  function evictOldestStructureContentCacheEntry() {
    const oldestKey = structureContentCacheRef.current.keys().next().value;
    if (oldestKey === undefined) return;
    const evicted = structureContentCacheRef.current.get(oldestKey);
    if (evicted?.pdfUrl) URL.revokeObjectURL(evicted.pdfUrl);
    structureContentCacheRef.current.delete(oldestKey);
  }

  async function handleRequestStructureContent({ startPage, endPage, label }) {
    if (!activeJobId) return;
    const cacheKey = `${startPage}-${endPage}`;
    const cached = structureContentCacheRef.current.get(cacheKey);
    if (cached) {
      // Re-insert so the Map's iteration order reflects recency - the
      // oldest (least-recently-used) entry is always the one evicted below.
      structureContentCacheRef.current.delete(cacheKey);
      structureContentCacheRef.current.set(cacheKey, cached);
      setStructureContent({ startPage, endPage, label, status: 'ready', ...cached });
      return;
    }

    // Set before awaiting the fetch so the modal shows a spinner
    // immediately instead of appearing to do nothing.
    setStructureContent({ startPage, endPage, label, status: 'loading' });

    const [pdfResp, dataResp] = await Promise.all([
      apiFetch(`/api/jobs/${activeJobId}/file/pages?start=${startPage}&end=${endPage}`),
      apiFetch(`/api/jobs/${activeJobId}?page_from=${startPage}&page_to=${endPage}`),
    ]);
    if (!pdfResp.ok || !dataResp.ok) {
      setStructureContent({ startPage, endPage, label, status: 'error', message: 'Failed to load this section.' });
      return;
    }
    const blob = await pdfResp.blob();
    const pdfUrl = URL.createObjectURL(blob);
    const data = await dataResp.json();
    const entry = { pdfUrl, pagesData: data.pages_data || [] };

    if (structureContentCacheRef.current.size >= STRUCTURE_CONTENT_CACHE_MAX) evictOldestStructureContentCacheEntry();
    structureContentCacheRef.current.set(cacheKey, entry);
    setStructureContent({ startPage, endPage, label, status: 'ready', ...entry });
  }

  useEffect(() => {
    let cancelled = false;

    if (!activeJobId) {
      setFileLoading(false);
      setRemoteFileUrl((prev) => {
        if (prev) URL.revokeObjectURL(prev);
        return null;
      });
      return;
    }

    setFileLoading(true);

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
      } finally {
        if (!cancelled) setFileLoading(false);
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

  async function handleSavePageText(pageNum, text) {
    if (!activeJobId) return;
    const resp = await apiFetch(`/api/jobs/${activeJobId}/pages/${pageNum}/text`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      alert(`Save failed: ${err.detail || resp.statusText}`);
      return;
    }
    const updated = await resp.json();
    setJobDetails((jd) => jd ? {
      ...jd,
      pages_data: jd.pages_data.map((p) => (p.page_num === updated.page_num ? { ...p, ...updated } : p)),
    } : jd);
  }

  // Guided Review Queue's "[✓ Mark Verified & Next]" — closes out a flagged
  // page with no reason required (confirm-correct), then removes it from the
  // locally-held queue immediately rather than waiting for the next
  // review_pages-keyed refetch, so the queue list and the "next" jump
  // ViewerPanel computes from it both update in the same tick.
  async function handleConfirmCorrectPage(pageNum) {
    if (!activeJobId) return;
    const resp = await apiFetch(`/api/jobs/${activeJobId}/pages/${pageNum}/confirm-correct`, { method: 'POST' });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      alert(`Could not mark verified: ${err.detail || resp.statusText}`);
      return;
    }
    const updated = await resp.json();
    setJobDetails((jd) => jd ? {
      ...jd,
      pages_data: jd.pages_data.map((p) => (p.page_num === updated.page_num ? { ...p, ...updated } : p)),
    } : jd);
    setReviewPages((pages) => pages.filter((p) => p.page_num !== updated.page_num));
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

  // Triggers the separate actionable-items extraction pass — same locally-
  // tracked in-flight pattern as handleReindexReferences above.
  async function handleReindexActionItems(jobId) {
    if (!jobId) return;
    const startVersion = jobDetails?.action_item_index_version || 0;
    const resp = await apiFetch(`/api/jobs/${jobId}/reindex-action-items`, { method: 'POST' });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      alert(`Action-item extraction failed: ${err.detail || resp.statusText}`);
      return;
    }
    setActionItemReindexState({ jobId, startVersion, startedAt: Date.now() });
    fetchJobDetails(jobId, currentPdfPageRef.current);
  }

  // Rebuilds reference links only (mentions of e.g. "see Clause 8" matched
  // against already-known targets), leaving the outline/structure untouched
  // — the cheaper half of a full rebuild, offered via RebuildIndexModal once
  // an index already exists. Same "track locally, don't rely solely on the
  // live reindexing flag" pattern as handleReindexReferences above.
  async function handleBuildReferenceLinksOnly(jobId) {
    if (!jobId) return;
    const startVersion = jobDetails?.reference_index_version || 0;
    const resp = await apiFetch(`/api/jobs/${jobId}/references/build-mentions`, { method: 'POST' });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      alert(`Rebuild failed: ${err.detail || resp.statusText}`);
      return;
    }
    setReindexState({ jobId, startVersion, startedAt: Date.now() });
    fetchJobDetails(jobId, currentPdfPageRef.current);
  }

  // ── Structure-tree add/edit/delete/reorder ──────────────────────────────
  // Not optimistic: each call throws on failure (surfaced by the modal) and
  // otherwise just re-triggers fetchJobDetails, which picks up the bumped
  // reference_index_version — the existing version-watching effects above
  // (references, scope-ranges, structure-hierarchy) then refetch on their
  // own, same pattern handleReindexReferences already uses.
  // `targetContext` ({targetDescriptor, existingChildren}), when given (the
  // per-node add button, see StructureTree.jsx's onAddItem / ViewerPanel's
  // openAddChildModal), means the new entry must land as a child of
  // targetDescriptor rather than at the tree root — chains this create with
  // one handleMoveStructureItem call, reusing it exactly as drag-and-drop
  // already does (including its own failure handling: a move that fails
  // alerts rather than throwing, same as a rejected drag-and-drop today —
  // the create has already succeeded by that point, so the new item is
  // left at the root rather than lost, and can still be dragged manually).
  async function handleAddStructureItem({ heading, startPage, endPage }, targetContext) {
    if (!activeJobId) return;
    const resp = await apiFetch(`/api/jobs/${activeJobId}/structure-items`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ heading, start_page: startPage, end_page: endPage }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to add this entry.');
    }
    if (targetContext?.targetDescriptor) {
      const created = await resp.json();
      const { targetDescriptor, existingChildren } = targetContext;
      await handleMoveStructureItem({
        itemKey: created.manual_key, itemPage: created.start_page, itemOccurrenceIndex: 0,
        newParentKey: targetDescriptor.itemKey, newParentPage: targetDescriptor.page,
        newParentOccurrenceIndex: targetDescriptor.occurrenceIndex,
        orderedSiblings: [
          ...existingChildren,
          { item_key: created.manual_key, item_page: created.start_page, item_occurrence_index: 0 },
        ],
      });
      return; // handleMoveStructureItem already refetched job details on success
    }
    fetchJobDetails(activeJobId, currentPdfPageRef.current);
  }

  async function handleEditStructureItem(descriptor, { heading, startPage, endPage }) {
    if (!activeJobId) return;
    // Only send fields that actually changed from the descriptor's current
    // values. The modal always submits all three, but the backend treats
    // the mere PRESENCE of start_page/end_page as "the range is changing"
    // (triggering its container-range/multi-occurrence validation) — always
    // sending them, even unchanged, would spuriously 409 a pure heading
    // rename on a container with disjoint ranges.
    const body = {};
    if (heading !== descriptor.heading) body.heading = heading;
    if (startPage !== descriptor.startPage) body.start_page = startPage;
    if (endPage !== descriptor.endPage) body.end_page = endPage;
    if (Object.keys(body).length === 0) return; // nothing changed — treat as a no-op save

    const params = new URLSearchParams();
    if (!descriptor.isManual) {
      params.set('page', String(descriptor.page));
      params.set('occurrence_index', String(descriptor.occurrenceIndex ?? 0));
    }
    const qs = params.toString();
    const resp = await apiFetch(
      `/api/jobs/${activeJobId}/structure-items/${encodeURIComponent(descriptor.itemKey)}${qs ? `?${qs}` : ''}`,
      { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) },
    );
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to save changes.');
    }
    fetchJobDetails(activeJobId, currentPdfPageRef.current);
  }

  async function handleDeleteStructureItem(descriptor) {
    if (!activeJobId || !descriptor) return;
    const params = new URLSearchParams();
    if (!descriptor.isManual) {
      params.set('page', String(descriptor.page));
      params.set('occurrence_index', String(descriptor.occurrenceIndex ?? 0));
    }
    const qs = params.toString();
    const resp = await apiFetch(
      `/api/jobs/${activeJobId}/structure-items/${encodeURIComponent(descriptor.itemKey)}${qs ? `?${qs}` : ''}`,
      { method: 'DELETE' },
    );
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to delete this entry.');
    }
    fetchJobDetails(activeJobId, currentPdfPageRef.current);
  }

  async function handleMoveStructureItem({
    itemKey, itemPage, itemOccurrenceIndex, newParentKey, newParentPage, newParentOccurrenceIndex, orderedSiblings,
  }) {
    if (!activeJobId) return;
    const resp = await apiFetch(`/api/jobs/${activeJobId}/structure-items/move`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        item_key: itemKey, item_page: itemPage, item_occurrence_index: itemOccurrenceIndex,
        new_parent_key: newParentKey, new_parent_page: newParentPage, new_parent_occurrence_index: newParentOccurrenceIndex,
        ordered_siblings: orderedSiblings,
      }),
    });
    // A rejected move (e.g. a cycle the server caught that the client-side
    // guard missed) surfaces the same way every other structure-tree
    // mutation in this file does — a silent failure here previously meant
    // a drag-and-drop gesture could appear to do nothing with no
    // indication of why, which is exactly what made an ambiguous-key move
    // landing on the wrong occurrence hard to diagnose.
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      alert(`Move failed: ${err.detail || resp.statusText}`);
      return;
    }
    fetchJobDetails(activeJobId, currentPdfPageRef.current);
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

  const _MIME_BY_EXT = {
    docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    pdf: 'application/pdf',
    md: 'text/markdown',
  };
  function _extensionOf(filename) {
    const m = /\.([a-zA-Z0-9]+)$/.exec(filename || '');
    return m ? m[1].toLowerCase() : '';
  }

  function _anchorDownloadBlob(blob, filename) {
    const blobUrl = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = blobUrl;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(blobUrl);
  }

  function _pickerTypesFor(suggestedName) {
    const ext = _extensionOf(suggestedName);
    const mime = _MIME_BY_EXT[ext] || 'application/octet-stream';
    return [{ description: ext ? `${ext.toUpperCase()} file` : 'File', accept: { [mime]: [`.${ext || 'bin'}`] } }];
  }

  // Resolves to the fetched Response, or null (after already alerting) on a
  // non-OK status — callers just check truthiness and return.
  async function _fetchDownload(url) {
    // window.open()/plain navigation never carries the Authorization header
    // apiFetch attaches, so authenticated downloads have to go through a
    // real fetch and be saved from the resulting blob instead.
    const resp = await apiFetch(url);
    if (!resp.ok) {
      alert(`Download failed: ${resp.status} ${resp.statusText}`);
      return null;
    }
    return resp;
  }

  async function _downloadViaAuthedFetch(url, fallbackFilename) {
    if (typeof window.showSaveFilePicker === 'function') {
      // The File System Access API's save picker is only callable while the
      // click that triggered it still has an active user gesture — calling
      // it after even one `await apiFetch(...)` (confirmed against real
      // Chromium, not assumed) loses that gesture and throws SecurityError,
      // silently falling back to a plain auto-download every time instead of
      // ever showing the picker. So the picker has to come FIRST, using the
      // generic fallback name (the server's nicer Content-Disposition name
      // isn't known yet) — only fetching afterward, once a location is
      // actually chosen.
      let handle = null;
      try {
        handle = await window.showSaveFilePicker({
          suggestedName: fallbackFilename,
          types: _pickerTypesFor(fallbackFilename),
        });
      } catch (err) {
        if (err?.name === 'AbortError') return; // user cancelled — nothing to fetch
        handle = null; // an unexpected picker failure — fall through to the universal path below
      }
      if (handle) {
        const resp = await _fetchDownload(url);
        if (!resp) return;
        const blob = await resp.blob();
        const writable = await handle.createWritable();
        await writable.write(blob);
        await writable.close();
        return;
      }
    }

    // Firefox/Safari (no native picker) or an unexpected native-picker
    // failure: fetch first — this path has no user-gesture timing
    // constraint, so it can afford to wait for the real response and let
    // the fallback dialog suggest the server's own nicer Content-
    // Disposition filename instead of the generic one.
    const resp = await _fetchDownload(url);
    if (!resp) return;
    const blob = await resp.blob();
    const filename = _filenameFromContentDisposition(resp.headers.get('Content-Disposition'), fallbackFilename);
    setSaveDialog({ blob, suggestedName: filename });
  }

  function handleSaveDialogConfirm(chosenFilename) {
    if (saveDialog) _anchorDownloadBlob(saveDialog.blob, chosenFilename);
    setSaveDialog(null);
  }
  function handleSaveDialogCancel() {
    setSaveDialog(null);
  }

  function handleDownloadFile(jobId) {
    _downloadViaAuthedFetch(`/api/jobs/${jobId}/file?download=true`, `${jobId}`);
  }
  function handleDownloadResult(jobId) {
    _downloadViaAuthedFetch(`/api/jobs/${jobId}/download-result`, `${jobId}_ocr_result.md`);
  }
  function handleExportDocument(jobId, { format, scope, includeMetadata, pageNum, pageRange }) {
    if (!jobId) return;
    const params = new URLSearchParams({ format, scope });
    if (scope === 'page' && pageNum) params.set('page_num', String(pageNum));
    if (scope === 'range' && pageRange) params.set('page_range', pageRange);
    params.set('include_metadata', includeMetadata ? 'true' : 'false');
    _downloadViaAuthedFetch(`/api/jobs/${jobId}/export?${params.toString()}`, `${jobId}_export.${format}`);
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
  async function handleSaveAiIndexing(enabled) {
    const resp = await apiFetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enable_ai_indexing: enabled }),
    });
    if (resp.ok) setSettings((s) => ({ ...s, enable_ai_indexing: enabled }));
  }
  async function handleSaveAiChunkPages(pages) {
    const resp = await apiFetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ai_index_chunk_pages: pages }),
    });
    if (resp.ok) setSettings((s) => ({ ...s, ai_index_chunk_pages: pages }));
  }
  async function handleSaveAiReviewRulePrior(enabled) {
    const resp = await apiFetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ai_index_review_rule_prior: enabled }),
    });
    if (resp.ok) setSettings((s) => ({ ...s, ai_index_review_rule_prior: enabled }));
  }
  async function handleSaveAiChunkOverlapPages(pages) {
    const resp = await apiFetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ai_index_chunk_overlap_pages: pages }),
    });
    if (resp.ok) setSettings((s) => ({ ...s, ai_index_chunk_overlap_pages: pages }));
  }
  async function loadAiIndexStatus() {
    const resp = await apiFetch('/api/ai-index/status');
    if (resp.ok) setAiIndexStatus(await resp.json());
  }
  async function handleTestAiConnection() {
    const resp = await apiFetch('/api/ai-index/test', { method: 'POST' });
    const data = await resp.json().catch(() => ({ ok: false, detail: 'Request failed' }));
    return { ok: resp.ok && data.ok, ...data };
  }

  const pdfSrc = file ? localPreviewUrl : remoteFileUrl;
  const pdfStatusLabel = file ? `${file.name} (local preview)` : (jobDetails ? `${jobDetails.filename} (${jobDetails.total_pages}p)` : 'No document selected');
  const hasSelection = !!(file || activeJobId);
  const reindexing = !!(reindexState && reindexState.jobId === activeJobId) || !!jobDetails?.reference_index_reindexing;
  const actionItemsReindexing = !!(actionItemReindexState && actionItemReindexState.jobId === activeJobId) || !!jobDetails?.action_item_reindexing;

  const location = useLocation();
  const isStudio = location.pathname.startsWith('/studio');

  return (
    <div className={`container app-shell-container${isStudio ? ' is-studio' : ''}`}>
      <Header onOpenSettings={() => setSettingsOpen(true)} />
      <SettingsModal
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        idleState={idle}
        gpuStatus={gpuStatus}
        onExit={handleExit}
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
        onSaveAiIndexing={handleSaveAiIndexing}
        onSaveAiChunkPages={handleSaveAiChunkPages}
        onSaveAiReviewRulePrior={handleSaveAiReviewRulePrior}
        onSaveAiChunkOverlapPages={handleSaveAiChunkOverlapPages}
        aiIndexStatus={aiIndexStatus}
        onTestAiConnection={handleTestAiConnection}
      />
      <SaveFileDialog
        open={!!saveDialog}
        suggestedName={saveDialog?.suggestedName}
        onCancel={handleSaveDialogCancel}
        onConfirm={handleSaveDialogConfirm}
      />
      {!isStudio && <IdleBanner visible={idleBanner.visible} expired={idleBanner.expired} message={idleBanner.message} onKeepAlive={handleKeepAlive} />}
      {!isStudio && <ControlBar visible={hasActiveJobs} onPauseCurrent={() => activeJobId && handlePause(activeJobId)} onStopAll={handleStopAll} />}

      <Outlet
        context={{
          onOpenSettings: () => setSettingsOpen(true),
          // Upload / submit (LibraryHome)
          file, onFileSelected: handleFileSelected, onClearFile: handleClearFile,
          form, updateForm, onSubmit: handleSubmit, submitting,
          // Advanced settings (LibraryHome)
          prompts, defaults, onSavePrompt: handleSavePrompt, onResetPrompt: handleResetPrompt,
          settings, onSaveOutputPath: handleSaveOutputPath, onSaveIdleTimeout: handleSaveIdleTimeout,
          onSaveLongPrompts: handleSaveLongPrompts, onSaveGrounding: handleSaveGrounding,
          onSaveAiIndexing: handleSaveAiIndexing, onSaveAiChunkPages: handleSaveAiChunkPages,
          onSaveAiReviewRulePrior: handleSaveAiReviewRulePrior, onSaveAiChunkOverlapPages: handleSaveAiChunkOverlapPages,
          aiIndexStatus, onTestAiConnection: handleTestAiConnection,
          // Job list / history (LibraryHome)
          jobs, activeJobId, onPause: handlePause, onRetry: handleRetry,
          onDownloadFile: handleDownloadFile, onDownloadResult: handleDownloadResult,
          onDelete: handleDelete, onRefresh: loadJobsList,
          // Shared layout state (LibraryHome's .dashboard grid class + StudioView's viewer toggle)
          sidebarCollapsed, setSidebarCollapsed,
          // Active-document sync (StudioView sets this from its :docId URL param)
          setActiveJobId,
          // Viewer (StudioView → ViewerPanel)
          viewMode, setViewMode, pdfSrc, pdfStatusLabel, jobDetails, hasSelection, fileLoading,
          handlePause, handleRetry, handleDownloadFile, handleDownloadResult,
          handleSavePageText, handleExportDocument,
          handleRetryPage, retryingPage, handleRetryPages,
          handleReindexReferences, handleBuildReferenceLinksOnly, reindexing,
          handleReindexActionItems, actionItemsReindexing,
          actionItemStore: actionItemStoreRef.current,
          reviewPages, handleConfirmCorrectPage,
          referenceStore: referenceStoreRef.current,
          scopeRanges,
          setCurrentPdfPage,
          structureContent, handleRequestStructureContent,
          parentOverrides: structureHierarchy.parents, sortOverrides: structureHierarchy.order,
          handleAddStructureItem, handleEditStructureItem, handleDeleteStructureItem, handleMoveStructureItem,
        }}
      />
    </div>
  );
}
