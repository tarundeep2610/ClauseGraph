import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { forwardRef } from 'react';
import { screen, waitFor, fireEvent, act, within } from '@testing-library/react';
import { apiFetch } from '../utils/api';
import { renderApp } from './testRouteHelpers';

vi.mock('../utils/api', () => ({
  apiFetch: vi.fn(),
}));

let mockAuth = { isAdmin: false };
vi.mock('../utils/AuthContext', () => ({
  useAuth: () => mockAuth,
}));

beforeEach(() => {
  mockAuth = { isAdmin: false };
});

vi.mock('../utils/ThemeContext', () => ({
  useTheme: () => ({ theme: 'dark', toggleTheme: vi.fn() }),
}));

// PdfCanvasViewer is tested in its own file — here it's stubbed to expose
// whatever `pdfSrc` Dashboard fed it (enough to tell a remote-job PDF apart
// from a locally-selected one and to tell "loaded" apart from "nothing
// yet"), plus a button that fires onPageChange (the same callback the real
// component calls on prev/next) so the windowed pages_data fetch
// (keyed off the page pdf.js is actually showing, reported up through
// ViewerPanel's onCurrentPageChange) can be exercised without touching
// real pdf.js/canvas rendering at all.
vi.mock('../components/PdfCanvasViewer', () => ({
  default: forwardRef(function MockPdfCanvasViewer({ pdfSrc, onPageChange }, ref) {
    return (
      <div data-testid="mock-pdf-canvas-viewer" data-pdf-src={pdfSrc || ''}>
        <button data-testid="mock-go-to-page-40" onClick={() => onPageChange && onPageChange(40)}>
          go to page 40
        </button>
      </div>
    );
  }),
  clampZoom: (z) => Math.min(3, Math.max(0.5, z)),
  ZOOM_STEP: 0.15,
}));

describe('AppShell/StudioView — PDF Remote Blob Preview & Regressions', () => {
  let originalCreateObjectURL;
  let originalRevokeObjectURL;
  let createObjectURLMock;
  let revokeObjectURLMock;

  beforeEach(() => {
    vi.clearAllMocks();

    originalCreateObjectURL = global.URL.createObjectURL;
    originalRevokeObjectURL = global.URL.revokeObjectURL;

    let blobIdCounter = 0;
    createObjectURLMock = vi.fn().mockImplementation((blob) => {
      blobIdCounter += 1;
      return `blob:fake-url-${blobIdCounter}`;
    });
    revokeObjectURLMock = vi.fn();

    global.URL.createObjectURL = createObjectURLMock;
    global.URL.revokeObjectURL = revokeObjectURLMock;

    // Default mock behavior for mount endpoints
    apiFetch.mockImplementation(async (url) => {
      if (url === '/api/prompts') {
        return {
          ok: true,
          json: async () => ({ prompts: {}, defaults: {} }),
        };
      }
      if (url === '/api/jobs') {
        return {
          ok: true,
          json: async () => [],
        };
      }
      return {
        ok: true,
        json: async () => ({}),
        blob: async () => new Blob(['test pdf content'], { type: 'application/pdf' }),
      };
    });
  });

  afterEach(() => {
    global.URL.createObjectURL = originalCreateObjectURL;
    global.URL.revokeObjectURL = originalRevokeObjectURL;
  });

  it('1. Happy path: fetches remote PDF via apiFetch and feeds it to PdfCanvasViewer', async () => {
    const jobs = [
      {
        job_id: 'job-101',
        filename: 'report.pdf',
        status: 'completed',
        total_pages: 3,
        processed_pages: 3,
        engine: 'unlimited',
        created_at: new Date().toISOString(),
      },
    ];

    apiFetch.mockImplementation(async (url) => {
      if (url === '/api/prompts') {
        return { ok: true, json: async () => ({ prompts: {}, defaults: {} }) };
      }
      if (url === '/api/jobs') {
        return { ok: true, json: async () => jobs };
      }
      if (url.split('?')[0] === '/api/jobs/job-101') {
        return {
          ok: true,
          json: async () => ({
            job_id: 'job-101',
            filename: 'report.pdf',
            status: 'completed',
            total_pages: 3,
            processed_pages: 3,
            pages: [],
          }),
        };
      }
      if (url === '/api/jobs/job-101/file') {
        return {
          ok: true,
          blob: async () => new Blob(['fake pdf binary'], { type: 'application/pdf' }),
        };
      }
      return { ok: false };
    });

    // Navigating straight to /studio/job-101 is what a real user does now
    // (via a history-list click or a direct link) — Library and Studio are
    // separate routes, so there's no "history-card" to wait for on this
    // screen; waiting on the file fetch below is the real readiness signal.
    renderApp('/studio/job-101');

    await waitFor(() => {
      expect(apiFetch).toHaveBeenCalledWith('/api/jobs/job-101/file');
      expect(createObjectURLMock).toHaveBeenCalledTimes(1);
    });

    await waitFor(() => {
      const viewer = screen.getByTestId('mock-pdf-canvas-viewer');
      expect(viewer.dataset.pdfSrc).toBe('blob:fake-url-1');
    });
  });

  it('2. Revokes previous blob URL when switching active jobs', async () => {
    const jobs = [
      {
        job_id: 'job-1',
        filename: 'first.pdf',
        status: 'completed',
        total_pages: 2,
        processed_pages: 2,
        engine: 'unlimited',
        created_at: new Date().toISOString(),
      },
      {
        job_id: 'job-2',
        filename: 'second.pdf',
        status: 'completed',
        total_pages: 4,
        processed_pages: 4,
        engine: 'unlimited',
        created_at: new Date().toISOString(),
      },
    ];

    apiFetch.mockImplementation(async (url) => {
      if (url === '/api/prompts') {
        return { ok: true, json: async () => ({ prompts: {}, defaults: {} }) };
      }
      if (url === '/api/jobs') {
        return { ok: true, json: async () => jobs };
      }
      if (url.startsWith('/api/jobs/job-')) {
        if (url.endsWith('/file')) {
          return {
            ok: true,
            blob: async () => new Blob(['pdf binary'], { type: 'application/pdf' }),
          };
        }
        const id = url.split('/')[3];
        return {
          ok: true,
          json: async () => ({
            job_id: id,
            filename: `${id}.pdf`,
            status: 'completed',
            total_pages: 2,
            processed_pages: 2,
            pages: [],
          }),
        };
      }
      return { ok: false };
    });

    // Start already viewing job-1 in Studio (as a real user would, having
    // navigated there from a history-list click).
    renderApp('/studio/job-1');

    await waitFor(() => {
      expect(createObjectURLMock).toHaveBeenCalledTimes(1);
    });

    // Switching documents is now a two-step trip through Library (Studio no
    // longer renders the history list next to the viewer) rather than a
    // click on a card that was already on screen.
    fireEvent.click(screen.getByTestId('back-to-library-btn'));
    const cards = await screen.findAllByTestId('history-card');
    expect(cards).toHaveLength(2);
    fireEvent.click(cards[1]);

    await waitFor(() => {
      expect(createObjectURLMock).toHaveBeenCalledTimes(2);
    });

    expect(revokeObjectURLMock).toHaveBeenCalledWith('blob:fake-url-1');
  });

  it('3. Gracefully handles failed file fetch by falling back to about:blank', async () => {
    const jobs = [
      {
        job_id: 'job-fail',
        filename: 'error.pdf',
        status: 'completed',
        total_pages: 1,
        processed_pages: 1,
        engine: 'unlimited',
        created_at: new Date().toISOString(),
      },
    ];

    apiFetch.mockImplementation(async (url) => {
      if (url === '/api/prompts') {
        return { ok: true, json: async () => ({ prompts: {}, defaults: {} }) };
      }
      if (url === '/api/jobs') {
        return { ok: true, json: async () => jobs };
      }
      if (url.split('?')[0] === '/api/jobs/job-fail') {
        return {
          ok: true,
          json: async () => ({
            job_id: 'job-fail',
            filename: 'error.pdf',
            status: 'completed',
            total_pages: 1,
            processed_pages: 1,
            pages: [],
          }),
        };
      }
      if (url === '/api/jobs/job-fail/file') {
        return {
          ok: false,
          status: 401,
          statusText: 'Unauthorized',
        };
      }
      return { ok: false };
    });

    renderApp('/studio/job-fail');

    await waitFor(() => {
      expect(apiFetch).toHaveBeenCalledWith('/api/jobs/job-fail/file');
    });
    expect(createObjectURLMock).not.toHaveBeenCalled();
    expect(screen.queryByTestId('mock-pdf-canvas-viewer')).not.toBeInTheDocument();
  });

  it('4. Local file selection takes precedence over remote job preview', async () => {
    const jobs = [
      {
        job_id: 'job-remote',
        filename: 'remote.pdf',
        status: 'completed',
        total_pages: 2,
        processed_pages: 2,
        engine: 'unlimited',
        created_at: new Date().toISOString(),
      },
    ];

    apiFetch.mockImplementation(async (url) => {
      if (url === '/api/prompts') {
        return { ok: true, json: async () => ({ prompts: {}, defaults: {} }) };
      }
      if (url === '/api/jobs') {
        return { ok: true, json: async () => jobs };
      }
      if (url.split('?')[0] === '/api/jobs/job-remote') {
        return {
          ok: true,
          json: async () => ({
            job_id: 'job-remote',
            filename: 'remote.pdf',
            status: 'completed',
            total_pages: 2,
            processed_pages: 2,
            pages: [],
          }),
        };
      }
      if (url === '/api/jobs/job-remote/file') {
        return {
          ok: true,
          blob: async () => new Blob(['remote pdf content'], { type: 'application/pdf' }),
        };
      }
      return { ok: false };
    });

    // Start on Library, navigate into Studio for job-remote (as a real user
    // would via the history list), confirm the remote PDF shows there.
    renderApp('/');
    const firstCards = await screen.findAllByTestId('history-card');
    fireEvent.click(firstCards[0]);

    await waitFor(() => {
      const viewer = screen.getByTestId('mock-pdf-canvas-viewer');
      expect(viewer.dataset.pdfSrc).toBe('blob:fake-url-1');
    });

    // Go back to Library and stage a local file WITHOUT submitting it — file
    // selection lives only on Library now (UploadPanel isn't rendered in
    // Studio), but the `file` state itself is owned by AppShell and shared
    // across both pages.
    fireEvent.click(screen.getByTestId('back-to-library-btn'));
    const fileInput = await screen.findByTestId('file-input');
    const localFile = new File(['local pdf content'], 'local_doc.pdf', { type: 'application/pdf' });
    fireEvent.change(fileInput, { target: { files: [localFile] } });

    // Re-entering Studio for the same job proves the precedence logic is
    // unchanged: the staged local file still wins over job-remote's own
    // remote file, even though upload and viewing are now different pages.
    const secondCards = await screen.findAllByTestId('history-card');
    fireEvent.click(secondCards[0]);

    await waitFor(() => {
      const viewer = screen.getByTestId('mock-pdf-canvas-viewer');
      expect(viewer.dataset.pdfSrc).toBe('blob:fake-url-2');
    });
  });

  // REWRITTEN (premise obsolete): the old monolithic Dashboard always showed
  // the viewer, so "no jobs and no file" meant an empty viewer on the same
  // screen as the sidebar. Now Library ("/") and Studio are separate routes,
  // and Studio is only ever reached via a document id — with no jobs and no
  // staged file there's nothing to navigate to, so the correct landing state
  // is simply the (empty) Library page, not a viewer showing "No document
  // selected".
  it('5. With no jobs and no local file, shows the Library page — there is no reachable empty-viewer state anymore', async () => {
    renderApp('/');
    await waitFor(() => {
      expect(screen.getByTestId('file-input')).toBeInTheDocument();
    });
    expect(screen.queryByTestId('mock-pdf-canvas-viewer')).not.toBeInTheDocument();
    expect(screen.queryByText('No document selected')).not.toBeInTheDocument();
  });

  it('6. Sidebar wrapper carries the "sidebar" class (layout/scroll fix depends on it)', async () => {
    renderApp('/');
    await waitFor(() => {
      expect(document.querySelector('.sidebar')).toBeInTheDocument();
    });
  });

  // REWRITTEN (premise obsolete): the sidebar-collapsed toggle button lives
  // in ViewerPanel's own toolbar (Studio-only), while the `.dashboard`
  // element it used to affect lives in LibraryHome — the two are never on
  // screen together anymore. What's actually worth testing post-split is the
  // reason AppShell exists at all: that sidebarCollapsed state survives
  // navigating away from and back to Library, instead of resetting.
  it('7. The sidebar-collapsed toggle (Studio) persists across navigation and is reflected on Library\'s .dashboard wrapper', async () => {
    const job = {
      job_id: 'job-x', filename: 'x.pdf', status: 'completed',
      total_pages: 1, processed_pages: 1, engine: 'unlimited', created_at: new Date().toISOString(),
    };
    apiFetch.mockImplementation(async (url) => {
      if (url === '/api/prompts') return { ok: true, json: async () => ({ prompts: {}, defaults: {} }) };
      if (url === '/api/jobs') return { ok: true, json: async () => [job] };
      if (url.split('?')[0] === '/api/jobs/job-x') return { ok: true, json: async () => ({ ...job, pages_data: [] }) };
      if (url === '/api/jobs/job-x/file') return { ok: true, blob: async () => new Blob(['x'], { type: 'application/pdf' }) };
      return { ok: false };
    });

    renderApp('/studio/job-x');
    await screen.findByTestId('sidebar-toggle-btn');

    fireEvent.click(screen.getByTestId('sidebar-toggle-btn'));
    fireEvent.click(screen.getByTestId('back-to-library-btn'));
    await waitFor(() => {
      expect(document.querySelector('.dashboard').className).toContain('sidebar-collapsed');
    });

    const card = await screen.findByTestId('history-card');
    fireEvent.click(card);
    await screen.findByTestId('sidebar-toggle-btn');
    fireEvent.click(screen.getByTestId('sidebar-toggle-btn'));
    fireEvent.click(screen.getByTestId('back-to-library-btn'));
    await waitFor(() => {
      expect(document.querySelector('.dashboard').className).not.toContain('sidebar-collapsed');
    });
  });
});

describe('AppShell/StudioView — GPU status polling', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // GPU status now lives in the admin-only section of SettingsModal (see
    // Header.jsx round-4 rewrite) rather than always-visible in the header.
    mockAuth = { isAdmin: true };
  });

  it('polls GET /health on mount and passes the result down to the Settings modal\'s GPU pill', async () => {
    apiFetch.mockImplementation(async (url) => {
      if (url === '/api/prompts') return { ok: true, json: async () => ({ prompts: {}, defaults: {} }) };
      if (url === '/api/jobs') return { ok: true, json: async () => [] };
      if (url === '/api/settings') return { ok: true, json: async () => ({ output_path: '', idle_timeout_minutes: 15 }) };
      if (url === '/api/ai-index/status') return { ok: true, json: async () => ({}) };
      if (url === '/health') {
        return {
          ok: true,
          json: async () => ({
            status: 'healthy', ollama_connection: 'online', gpu_ocr_service_connection: 'online',
            gpu_model_loaded: true, gpu_active_jobs: 1,
          }),
        };
      }
      return { ok: false };
    });

    renderApp('/');

    await waitFor(() => {
      expect(apiFetch).toHaveBeenCalledWith('/health');
    });
    fireEvent.click(screen.getByTestId('open-settings-btn'));
    fireEvent.click(await screen.findByTestId('settings-nav-session'));
    await waitFor(() => {
      expect(screen.getByTestId('gpu-status-pill')).toHaveTextContent('GPU: Ready');
    });
  });

  it('shows no GPU pill in Settings when the health check fails, without crashing the page', async () => {
    apiFetch.mockImplementation(async (url) => {
      if (url === '/api/prompts') return { ok: true, json: async () => ({ prompts: {}, defaults: {} }) };
      if (url === '/api/jobs') return { ok: true, json: async () => [] };
      if (url === '/api/settings') return { ok: true, json: async () => ({ output_path: '', idle_timeout_minutes: 15 }) };
      if (url === '/api/ai-index/status') return { ok: true, json: async () => ({}) };
      if (url === '/health') return { ok: false };
      return { ok: false };
    });

    expect(() => renderApp('/')).not.toThrow();

    await waitFor(() => {
      expect(apiFetch).toHaveBeenCalledWith('/health');
    });
    fireEvent.click(screen.getByTestId('open-settings-btn'));
    await screen.findByTestId('settings-modal');
    fireEvent.click(screen.getByTestId('settings-nav-session'));
    expect(screen.queryByTestId('gpu-status-pill')).not.toBeInTheDocument();
  });
});

describe('AppShell/StudioView — settings persistence (localStorage)', () => {
  beforeEach(() => {
    mockAuth = { isAdmin: true };
    vi.clearAllMocks();
    localStorage.clear();
    apiFetch.mockImplementation(async (url) => {
      if (url === '/api/prompts') return { ok: true, json: async () => ({ prompts: {}, defaults: {} }) };
      if (url === '/api/jobs') return { ok: true, json: async () => [] };
      return { ok: false };
    });
  });

  afterEach(() => {
    localStorage.clear();
  });

  it('restores engine and view mode from a previously persisted settings blob', async () => {
    localStorage.setItem(
      'ocr-ui-settings',
      JSON.stringify({ form: { engine: 'glm', unlimitedBaseSize: '2048' }, viewMode: 'pdf' })
    );
    // engine-select is Library-side; the view-mode toggle it also checks is
    // Studio-side (ViewerPanel's own toolbar) — one job so there's a history
    // card to navigate in with and confirm both pieces of restored state.
    const job = {
      job_id: 'job-1', filename: 'a.pdf', status: 'completed',
      total_pages: 1, processed_pages: 1, engine: 'unlimited', created_at: new Date().toISOString(),
    };
    apiFetch.mockImplementation(async (url) => {
      if (url === '/api/prompts') return { ok: true, json: async () => ({ prompts: {}, defaults: {} }) };
      if (url === '/api/jobs') return { ok: true, json: async () => [job] };
      if (url.split('?')[0] === '/api/jobs/job-1') return { ok: true, json: async () => ({ ...job, pages_data: [] }) };
      if (url === '/api/jobs/job-1/file') return { ok: true, blob: async () => new Blob(['x'], { type: 'application/pdf' }) };
      return { ok: false };
    });

    renderApp('/');

    fireEvent.click(await screen.findByTestId('open-settings-btn'));
    fireEvent.click(await screen.findByTestId('settings-nav-engine'));
    await waitFor(() => {
      expect(screen.getByTestId('engine-select').value).toBe('glm');
    });
    fireEvent.click(screen.getByTestId('settings-modal-close-btn'));

    fireEvent.click(await screen.findByTestId('history-card'));
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /PDF Only/i })).toHaveClass('active');
    });
  });

  it('persists a form change to localStorage', async () => {
    renderApp('/');

    fireEvent.click(await screen.findByTestId('open-settings-btn'));
    fireEvent.click(await screen.findByTestId('settings-nav-engine'));
    const engineSelect = await screen.findByTestId('engine-select');
    fireEvent.change(engineSelect, { target: { value: 'glm' } });

    await waitFor(() => {
      const stored = JSON.parse(localStorage.getItem('ocr-ui-settings'));
      expect(stored.form.engine).toBe('glm');
    });
  });

  it('never persists customPrompt or pageRange (per-document/session state, not a user preference)', async () => {
    renderApp('/');

    fireEvent.click(await screen.findByTestId('open-settings-btn'));
    fireEvent.click(await screen.findByTestId('settings-nav-engine'));
    const engineSelect = await screen.findByTestId('engine-select');
    fireEvent.change(engineSelect, { target: { value: 'glm' } });

    await waitFor(() => {
      const stored = JSON.parse(localStorage.getItem('ocr-ui-settings'));
      expect(stored.form).not.toHaveProperty('customPrompt');
      expect(stored.form).not.toHaveProperty('pageRange');
    });
  });

  it('ignores unrecognized/legacy stored keys instead of crashing', async () => {
    localStorage.setItem(
      'ocr-ui-settings',
      JSON.stringify({ form: { engine: 'not-a-real-engine', bogusKey: 'x' } })
    );

    expect(() => renderApp('/')).not.toThrow();
    fireEvent.click(await screen.findByTestId('open-settings-btn'));
    fireEvent.click(await screen.findByTestId('settings-nav-engine'));
    await waitFor(() => {
      expect(screen.getByTestId('engine-select')).toBeInTheDocument();
    });
  });

  it('restores a persisted collapsed sidebar on mount', async () => {
    localStorage.setItem('ocr-ui-settings', JSON.stringify({ sidebarCollapsed: true }));

    renderApp('/');

    await waitFor(() => {
      expect(document.querySelector('.dashboard').className).toContain('sidebar-collapsed');
    });
  });

  it('persists sidebarCollapsed to localStorage when the toggle is clicked', async () => {
    // The toggle button lives in ViewerPanel's own toolbar (Studio-only) —
    // the job itself doesn't need to exist for the toolbar to render.
    renderApp('/studio/any-doc');
    await screen.findByTestId('sidebar-toggle-btn');

    fireEvent.click(screen.getByTestId('sidebar-toggle-btn'));

    await waitFor(() => {
      const stored = JSON.parse(localStorage.getItem('ocr-ui-settings'));
      expect(stored.sidebarCollapsed).toBe(true);
    });
  });
});

describe('AppShell/StudioView — jobDetails is cleared immediately on any job switch (regression)', () => {
  // Regression coverage for: the OCR results panel kept showing the
  // previously-selected document's text for a moment after uploading a new
  // PDF or switching to a different job in history, because jobDetails was
  // only ever cleared when deselecting entirely, not when switching between
  // two different truthy activeJobIds.
  let originalCreateObjectURL;
  let originalRevokeObjectURL;

  beforeEach(() => {
    vi.clearAllMocks();
    originalCreateObjectURL = global.URL.createObjectURL;
    originalRevokeObjectURL = global.URL.revokeObjectURL;
    global.URL.createObjectURL = vi.fn(() => 'blob:fake-url');
    global.URL.revokeObjectURL = vi.fn();
  });

  afterEach(() => {
    global.URL.createObjectURL = originalCreateObjectURL;
    global.URL.revokeObjectURL = originalRevokeObjectURL;
  });

  it('shows a loading state instead of the previous job\'s stale text while switching', async () => {
    const jobA = {
      job_id: 'job-a', filename: 'a.pdf', status: 'completed',
      total_pages: 1, processed_pages: 1, engine: 'unlimited', created_at: new Date().toISOString(),
    };
    const jobB = {
      job_id: 'job-b', filename: 'b.pdf', status: 'completed',
      total_pages: 1, processed_pages: 1, engine: 'unlimited', created_at: new Date().toISOString(),
    };
    let resolveB;
    const bDetailPromise = new Promise((resolve) => { resolveB = resolve; });

    apiFetch.mockImplementation(async (url) => {
      if (url === '/api/prompts') return { ok: true, json: async () => ({ prompts: {}, defaults: {} }) };
      if (url === '/api/jobs') return { ok: true, json: async () => [jobA, jobB] };
      if (url.split('?')[0] === '/api/jobs/job-a') return { ok: true, json: async () => ({ ...jobA, pages_data: [{ page: 1, text: 'Job A text' }] }) };
      if (url.split('?')[0] === '/api/jobs/job-b') {
        await bDetailPromise;
        return { ok: true, json: async () => ({ ...jobB, pages_data: [{ page: 1, text: 'Job B text' }] }) };
      }
      return { ok: false };
    });

    renderApp('/studio/job-a');
    await screen.findByText('Job A text');

    // Switching documents now goes through Library (Studio no longer shows
    // the history list alongside the viewer).
    fireEvent.click(screen.getByTestId('back-to-library-btn'));
    const cards = await screen.findAllByTestId('history-card');
    fireEvent.click(cards[1]); // switch to job-b, whose detail fetch is still pending

    await waitFor(() => {
      expect(screen.queryByText('Job A text')).not.toBeInTheDocument();
    });
    expect(screen.getByText('Loading document...')).toBeInTheDocument();

    resolveB();
    await screen.findByText('Job B text');
  });

  it('shows a loading state (not the pre-upload job\'s stale text) right after submitting a new job', async () => {
    const oldJob = {
      job_id: 'job-old', filename: 'old.pdf', status: 'completed',
      total_pages: 1, processed_pages: 1, engine: 'unlimited', created_at: new Date().toISOString(),
    };
    let resolveNew;
    const newDetailPromise = new Promise((resolve) => { resolveNew = resolve; });
    let jobsListCalls = 0;

    apiFetch.mockImplementation(async (url, opts) => {
      if (url === '/api/prompts') return { ok: true, json: async () => ({ prompts: {}, defaults: {} }) };
      if (url === '/api/jobs' && (!opts || opts.method === undefined)) {
        jobsListCalls += 1;
        return { ok: true, json: async () => (jobsListCalls === 1 ? [oldJob] : [oldJob, { ...oldJob, job_id: 'job-new', filename: 'new.pdf' }]) };
      }
      if (url.split('?')[0] === '/api/jobs/job-old') return { ok: true, json: async () => ({ ...oldJob, pages_data: [{ page: 1, text: 'Old job text' }] }) };
      if (url.startsWith('/api/jobs?') && opts?.method === 'POST') {
        return { ok: true, json: async () => ({ job_id: 'job-new' }) };
      }
      if (url.split('?')[0] === '/api/jobs/job-new') {
        await newDetailPromise;
        return { ok: true, json: async () => ({ job_id: 'job-new', filename: 'new.pdf', status: 'processing', total_pages: 1, processed_pages: 0, pages_data: [] }) };
      }
      return { ok: false };
    });

    // REWRITTEN (premise changed, intent preserved): the old test watched
    // "Old job text" disappear on the SAME screen right after submitting,
    // because upload and viewer lived together. Now upload is Library-only
    // and the viewer is Studio-only, so the regression has to be exercised
    // across that boundary: view job-old in Studio, go back to Library and
    // submit a new file (handleSubmit sets AppShell's activeJobId to the new
    // job immediately on success, even while still on Library — this is the
    // actual regression path), then navigate to the new job's history entry
    // and confirm job-old's stale text never leaks into it.
    renderApp('/studio/job-old');
    await screen.findByText('Old job text');

    fireEvent.click(screen.getByTestId('back-to-library-btn'));
    const fileInput = await screen.findByTestId('file-input');
    const localFile = new File(['pdf content'], 'new.pdf', { type: 'application/pdf' });
    fireEvent.change(fileInput, { target: { files: [localFile] } });
    fireEvent.click(screen.getByTestId('submit-btn'));

    const historyList = screen.getByTestId('history-list');
    const newCard = await within(historyList).findByText('new.pdf');
    fireEvent.click(newCard.closest('[data-testid="history-card"]'));

    await waitFor(() => {
      expect(screen.queryByText('Old job text')).not.toBeInTheDocument();
    });

    await act(async () => {
      resolveNew();
      await newDetailPromise;
    });
  });
});

describe('AppShell/StudioView — per-page and bulk Retry actions', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Retry/Rerun controls are admin-only (Studio redesign Sprint 0) — these
    // tests exercise the retry wiring itself, not role-gating, so run as admin.
    mockAuth = { isAdmin: true };
  });

  function _jobWithOnePage(status) {
    return {
      job_id: 'job-r', filename: 'r.pdf', status: 'completed',
      total_pages: 1, processed_pages: 1, engine: 'unlimited', created_at: new Date().toISOString(),
      failed_pages: status === 'failed' ? 1 : 0,
      review_pages: status === 'review' ? 1 : 0,
      pages_data: [{ page: 1, page_num: 1, text: 'old text', status, confidence_pct: 0, confidence_tier: 'failed' }],
    };
  }

  it('handleRetryPage merges the returned page (matched by page_num) into pages_data', async () => {
    const job = _jobWithOnePage('failed');
    apiFetch.mockImplementation(async (url, opts) => {
      if (url === '/api/prompts') return { ok: true, json: async () => ({ prompts: {}, defaults: {} }) };
      if (url === '/api/jobs') return { ok: true, json: async () => [job] };
      if (url.split('?')[0] === '/api/jobs/job-r') return { ok: true, json: async () => job };
      if (url === '/api/jobs/job-r/pages/1/retry' && opts?.method === 'POST') {
        const updated = { page_num: 1, status: 'success', text: 'new retried text', confidence_pct: 95 };
        // A real backend persists the retry before responding, so any
        // later GET of the job (e.g. AppShell's own real 2500ms per-job
        // poll, fetchJobDetails) would see this same updated text too —
        // mutate the shared `job` fixture here so this mock doesn't race
        // that poll in real test time by handing back a frozen snapshot.
        job.pages_data = job.pages_data.map((p) => (p.page_num === updated.page_num ? { ...p, ...updated } : p));
        return { ok: true, json: async () => updated };
      }
      return { ok: false };
    });

    renderApp('/studio/job-r');
    await screen.findByTestId('retry-page-1');

    fireEvent.click(screen.getByTestId('retry-page-1'));

    await waitFor(() => {
      expect(screen.getByText('new retried text')).toBeInTheDocument();
    });
    expect(screen.queryByText('old text')).not.toBeInTheDocument();
  });

  it('handleRetryPages: choosing "All pages" in the modal sends scope=all', async () => {
    const job = _jobWithOnePage('success');
    const seenUrls = [];
    apiFetch.mockImplementation(async (url, opts) => {
      if (url === '/api/prompts') return { ok: true, json: async () => ({ prompts: {}, defaults: {} }) };
      if (url === '/api/jobs') return { ok: true, json: async () => [job] };
      if (url.split('?')[0] === '/api/jobs/job-r') return { ok: true, json: async () => job };
      if (url.startsWith('/api/jobs/job-r/retry-pages') && opts?.method === 'POST') {
        seenUrls.push(url);
        return { ok: true, json: async () => ({ status: 'escalating' }) };
      }
      return { ok: false };
    });

    renderApp('/studio/job-r');
    await screen.findByTestId('retry-pages-btn');
    fireEvent.click(screen.getByTestId('retry-pages-btn'));
    fireEvent.click(screen.getByTestId('retry-option-all'));

    await waitFor(() => {
      expect(seenUrls).toEqual(['/api/jobs/job-r/retry-pages?scope=all']);
    });
  });

  it('handleRetryPages: choosing "Failed only" in the modal sends scope=failed', async () => {
    const job = _jobWithOnePage('failed');
    const seenUrls = [];
    apiFetch.mockImplementation(async (url, opts) => {
      if (url === '/api/prompts') return { ok: true, json: async () => ({ prompts: {}, defaults: {} }) };
      if (url === '/api/jobs') return { ok: true, json: async () => [job] };
      if (url.split('?')[0] === '/api/jobs/job-r') return { ok: true, json: async () => job };
      if (url.startsWith('/api/jobs/job-r/retry-pages') && opts?.method === 'POST') {
        seenUrls.push(url);
        return { ok: true, json: async () => ({ status: 'escalating' }) };
      }
      return { ok: false };
    });

    renderApp('/studio/job-r');
    await screen.findByTestId('retry-pages-btn');
    fireEvent.click(screen.getByTestId('retry-pages-btn'));
    fireEvent.click(screen.getByTestId('retry-option-failed'));

    await waitFor(() => {
      expect(seenUrls).toEqual(['/api/jobs/job-r/retry-pages?scope=failed']);
    });
  });

  it('handleRetryPages: choosing "Low Confidence only" in the modal sends scope=review', async () => {
    const job = _jobWithOnePage('review');
    const seenUrls = [];
    apiFetch.mockImplementation(async (url, opts) => {
      if (url === '/api/prompts') return { ok: true, json: async () => ({ prompts: {}, defaults: {} }) };
      if (url === '/api/jobs') return { ok: true, json: async () => [job] };
      if (url.split('?')[0] === '/api/jobs/job-r') return { ok: true, json: async () => job };
      if (url.startsWith('/api/jobs/job-r/retry-pages') && opts?.method === 'POST') {
        seenUrls.push(url);
        return { ok: true, json: async () => ({ status: 'escalating' }) };
      }
      return { ok: false };
    });

    renderApp('/studio/job-r');
    await screen.findByTestId('retry-pages-btn');
    fireEvent.click(screen.getByTestId('retry-pages-btn'));
    fireEvent.click(screen.getByTestId('retry-option-review'));

    await waitFor(() => {
      expect(seenUrls).toEqual(['/api/jobs/job-r/retry-pages?scope=review']);
    });
  });

  it('handleRetryPages: cancelling the modal makes no network call at all', async () => {
    const job = _jobWithOnePage('failed');
    const seenUrls = [];
    apiFetch.mockImplementation(async (url, opts) => {
      if (url === '/api/prompts') return { ok: true, json: async () => ({ prompts: {}, defaults: {} }) };
      if (url === '/api/jobs') return { ok: true, json: async () => [job] };
      if (url.split('?')[0] === '/api/jobs/job-r') return { ok: true, json: async () => job };
      if (url.startsWith('/api/jobs/job-r/retry-pages')) seenUrls.push(url);
      return { ok: false };
    });

    renderApp('/studio/job-r');
    await screen.findByTestId('retry-pages-btn');
    fireEvent.click(screen.getByTestId('retry-pages-btn'));
    fireEvent.click(screen.getByTestId('retry-modal-cancel'));

    await new Promise((r) => setTimeout(r, 10));
    expect(seenUrls).toEqual([]);
  });

  it('handleRetryPages surfaces a non-OK response\'s error detail via alert()', async () => {
    const job = _jobWithOnePage('failed');
    apiFetch.mockImplementation(async (url, opts) => {
      if (url === '/api/prompts') return { ok: true, json: async () => ({ prompts: {}, defaults: {} }) };
      if (url === '/api/jobs') return { ok: true, json: async () => [job] };
      if (url.split('?')[0] === '/api/jobs/job-r') return { ok: true, json: async () => job };
      if (url.startsWith('/api/jobs/job-r/retry-pages') && opts?.method === 'POST') {
        return { ok: false, statusText: 'Conflict', json: async () => ({ detail: 'A bulk retry is already running for this job.' }) };
      }
      return { ok: false };
    });
    const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {});

    renderApp('/studio/job-r');
    await screen.findByTestId('retry-pages-btn');
    fireEvent.click(screen.getByTestId('retry-pages-btn'));
    fireEvent.click(screen.getByTestId('retry-option-failed'));

    await waitFor(() => {
      expect(alertSpy).toHaveBeenCalledWith(expect.stringContaining('A bulk retry is already running for this job.'));
    });
  });
});

describe('AppShell/StudioView — resuming a job no longer needs a lucky poll to notice it (regression)', () => {
  // Regression coverage for: redeploy pauses a job -> user clicks resume ->
  // the backend confirms success in its own logs, but the frontend never
  // shows new pages because polling was gated on hasActiveJobs, which only
  // ever got set from a loadJobsList() snapshot - and the very snapshot taken
  // right after the resume click can easily still read the pre-resume status
  // (POST /retry just schedules a background task and returns immediately),
  // permanently leaving the poller stuck off.
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('1. clicking resume starts polling even when every /api/jobs snapshot since keeps reading a non-active status', async () => {
    const job = {
      job_id: 'job-resume', filename: 'doc.pdf', status: 'paused',
      total_pages: 5, processed_pages: 0, engine: 'unlimited',
      created_at: new Date().toISOString(),
    };
    let jobsListCalls = 0;

    apiFetch.mockImplementation(async (url, opts) => {
      if (url === '/api/prompts') return { ok: true, json: async () => ({ prompts: {}, defaults: {} }) };
      if (url === '/api/jobs/job-resume/retry' && opts?.method === 'POST') {
        return { ok: true, json: async () => ({}) };
      }
      if (url === '/api/jobs') {
        jobsListCalls += 1;
        // Deliberately NEVER flips to 'processing' - simulates the worst case
        // where the background task hasn't updated the DB status yet by the
        // time any of these snapshots are taken.
        return { ok: true, json: async () => [job] };
      }
      if (url.split('?')[0] === '/api/jobs/job-resume') {
        return { ok: true, json: async () => ({ ...job, pages_data: [] }) };
      }
      return { ok: false };
    });

    // Fake timers must be installed BEFORE render - any setInterval created
    // during mount (before this point) would use the real timer implementation
    // and be invisible to advanceTimersByTimeAsync below. shouldAdvanceTime
    // keeps the fake clock moving in lockstep with real time too, so
    // findByTestId/waitFor's own internal polling still works normally.
    vi.useFakeTimers({ shouldAdvanceTime: true });

    renderApp('/');
    await screen.findByTestId('history-card');
    const callsBeforeResume = jobsListCalls;

    const card = screen.getByTestId('history-card');
    const resumeBtn = card.querySelector('.fa-rotate-right').closest('button');

    fireEvent.click(resumeBtn);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); }); // let the retry POST + its own loadJobsList() resolve
    await act(async () => { await vi.advanceTimersByTimeAsync(2500); }); // one poll tick

    expect(jobsListCalls).toBeGreaterThan(callsBeforeResume + 1); // the resume's own call, PLUS at least one poll tick
  });

  it('2. per-job page polling keeps working after resume, independent of the job-list status ever flipping', async () => {
    const job = {
      job_id: 'job-resume', filename: 'doc.pdf', status: 'paused',
      total_pages: 5, processed_pages: 0, engine: 'unlimited',
      created_at: new Date().toISOString(),
    };
    let detailCalls = 0;

    apiFetch.mockImplementation(async (url, opts) => {
      if (url === '/api/prompts') return { ok: true, json: async () => ({ prompts: {}, defaults: {} }) };
      if (url === '/api/jobs/job-resume/retry' && opts?.method === 'POST') {
        return { ok: true, json: async () => ({}) };
      }
      if (url === '/api/jobs') return { ok: true, json: async () => [job] };
      if (url.split('?')[0] === '/api/jobs/job-resume') {
        detailCalls += 1;
        // The reader stays on page 1 throughout (no navigation in this
        // test) — the Result pane only ever shows the current page now, so
        // "polling keeps working" is observed as that one page's own
        // content refreshing over time, not a later page's block appearing.
        const page1Text = detailCalls < 3 ? 'first page text (partial)' : 'first page text (final)';
        return { ok: true, json: async () => ({ ...job, pages_data: [{ page: 1, text: page1Text }] }) };
      }
      // The WysiwygEditor Result pane re-syncs its content whenever a poll
      // brings in different page text (see WysiwygEditor.jsx's resync
      // effect) — ProseMirror's setContent() emits its own update event on
      // that resync, which schedules the editor's normal ~800ms debounced
      // autosave (onSavePageText) exactly as if the reader had typed it.
      // This test advances fake timers far enough for that to fire, so it
      // must be handled here too, not just the detail/jobs endpoints.
      if (url === '/api/jobs/job-resume/pages/1/text' && opts?.method === 'PATCH') {
        return { ok: true, json: async () => ({ page_num: 1, text: JSON.parse(opts.body).text }) };
      }
      return { ok: false };
    });

    // See test 1 for why fake timers must be installed before render.
    vi.useFakeTimers({ shouldAdvanceTime: true });

    // Resuming is exercised via ViewerPanel's own "Resume" button (Studio's
    // toolbar) instead of the history-list icon (Library-side) — both call
    // the same handleRetry, and this keeps the whole test on one page since
    // it's really about per-job polling, not about navigation.
    renderApp('/studio/job-resume');
    await screen.findByText('first page text (partial)');

    fireEvent.click(screen.getByRole('button', { name: /Resume/i }));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    await act(async () => { await vi.advanceTimersByTimeAsync(2500); });
    await act(async () => { await vi.advanceTimersByTimeAsync(2500); });

    expect(screen.getByText('first page text (final)')).toBeInTheDocument();
  });

  // REWRITTEN (premise changed): the old assertion was "the active card's
  // highlight moves to the resumed job" because resuming just flipped local
  // state on the same screen. Now resuming from Library must actually
  // navigate into Studio for that job (per the redesign — HistoryPanel's
  // resume/select actions navigate rather than calling setActiveJobId
  // directly), so this checks for that navigation instead. It also no
  // longer asserts an auto-selected first job on mount, since that
  // auto-select was intentionally removed (see AppShell.jsx's loadJobsList).
  it('3. resuming from the history list navigates into Studio and shows that job', async () => {
    const jobs = [
      {
        job_id: 'job-active', filename: 'active.pdf', status: 'completed',
        total_pages: 2, processed_pages: 2, engine: 'unlimited', created_at: new Date().toISOString(),
      },
      {
        job_id: 'job-paused', filename: 'paused.pdf', status: 'paused',
        total_pages: 3, processed_pages: 1, engine: 'unlimited', created_at: new Date().toISOString(),
      },
    ];
    let retriedPaused = false;

    apiFetch.mockImplementation(async (url, opts) => {
      if (url === '/api/prompts') return { ok: true, json: async () => ({ prompts: {}, defaults: {} }) };
      if (url === '/api/jobs/job-paused/retry' && opts?.method === 'POST') {
        retriedPaused = true;
        return { ok: true, json: async () => ({}) };
      }
      if (url === '/api/jobs') return { ok: true, json: async () => jobs };
      if (url.split('?')[0] === '/api/jobs/job-active') {
        return { ok: true, json: async () => ({ ...jobs[0], pages_data: [] }) };
      }
      if (url.split('?')[0] === '/api/jobs/job-paused') {
        return { ok: true, json: async () => ({ ...jobs[1], pages_data: [] }) };
      }
      return { ok: false };
    });

    renderApp('/');
    await waitFor(() => {
      expect(screen.getAllByTestId('history-card')).toHaveLength(2);
    });

    // No auto-selected/active job on initial mount anymore — activeJobId
    // only ever changes via an explicit action (see AppShell.jsx).
    const cards = screen.getAllByTestId('history-card');
    expect(cards[0].className).not.toContain('active');
    expect(cards[1].className).not.toContain('active');

    const resumeBtn = cards[1].querySelector('.fa-rotate-right').closest('button');
    fireEvent.click(resumeBtn);

    await waitFor(() => {
      expect(retriedPaused).toBe(true);
    });
    // Resuming navigated into Studio for job-paused.
    await waitFor(() => {
      expect(screen.getByTestId('back-to-library-btn')).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(screen.getByText(/paused\.pdf/)).toBeInTheDocument();
    });
  });
});

describe('AppShell/StudioView — Rebuild Cross-References button', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('shows "Rebuilding…" right after click and clears once a later poll reports a higher reference_index_version, even though the live reindexing flag is never observed true', async () => {
    // Reproduces the reported bug: reindex_job() re-parses already-stored
    // text, so for a small job it can start AND finish between two poll
    // ticks - the server's reference_index_reindexing flag can go true-then
    // -false without ever being caught by a poll. The button must still show
    // real feedback, driven by reference_index_version instead.
    const baseJob = {
      job_id: 'job-r', filename: 'r.pdf', status: 'completed',
      total_pages: 1, processed_pages: 1, engine: 'unlimited', created_at: new Date().toISOString(),
      pages_data: [{ page: 1, page_num: 1, text: 'Clause 1 text', status: 'success' }],
    };
    let version = 3;
    let fetchesSinceReindex = null; // null until the reindex POST fires

    apiFetch.mockImplementation(async (url, opts) => {
      if (url === '/api/prompts') return { ok: true, json: async () => ({ prompts: {}, defaults: {} }) };
      if (url === '/api/jobs') return { ok: true, json: async () => [baseJob] };
      if (url.startsWith('/api/jobs/job-r/reindex-references') && opts?.method === 'POST') {
        fetchesSinceReindex = 0;
        return { ok: true, json: async () => ({ status: 'reindexing' }) };
      }
      if (url.split('?')[0] === '/api/jobs/job-r') {
        if (fetchesSinceReindex !== null) {
          fetchesSinceReindex += 1;
          // The rebuild "completes" (version bumps) only from the second
          // fetch after the click onward - simulating it finishing sometime
          // before the next 2.5s poll tick, but not on the very same tick
          // the click's own immediate fetch runs on.
          if (fetchesSinceReindex >= 2) version = 4;
        }
        return {
          ok: true,
          json: async () => ({
            ...baseJob,
            reference_index_version: version,
            reference_index_reindexing: false, // never observed live as true
            // The real backend stamps index_built_at immediately on the
            // POST request (not on completion) — see database.mark_index_built.
            index_built_at: fetchesSinceReindex !== null ? new Date().toISOString() : null,
          }),
        };
      }
      if (url.startsWith('/api/jobs/job-r/references')) {
        return { ok: true, json: async () => ({ reference_index_version: version, changed_pages: {} }) };
      }
      return { ok: false };
    });

    vi.useFakeTimers({ shouldAdvanceTime: true });

    renderApp("/studio/job-r");
    await screen.findByTestId('reindex-references-btn');

    // This job already has an index (version starts at 3), so the click
    // opens the rebuild-choice modal rather than rebuilding directly —
    // confirm with the default "Index" checked, equivalent to the old
    // single-button full rebuild this test was written against.
    fireEvent.click(screen.getByTestId('reindex-references-btn'));
    fireEvent.click(screen.getByTestId('rebuild-modal-confirm'));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); }); // let the POST + its immediate fetchJobDetails resolve

    expect(screen.getByTestId('reindex-references-btn')).toHaveTextContent('Rebuilding…');
    expect(screen.getByTestId('reindex-references-btn')).toBeDisabled();

    await act(async () => { await vi.advanceTimersByTimeAsync(2500); }); // next poll tick observes the version bump

    await waitFor(() => {
      expect(screen.getByTestId('reindex-references-btn')).toHaveTextContent('Rebuild Index & Reference Links');
    });
    expect(screen.getByTestId('reindex-references-btn')).not.toBeDisabled();
  });
});

describe('AppShell/StudioView — windowed pages_data fetch centered on the current page', () => {
  let originalCreateObjectURL;
  let originalRevokeObjectURL;

  beforeEach(() => {
    originalCreateObjectURL = global.URL.createObjectURL;
    originalRevokeObjectURL = global.URL.revokeObjectURL;
    global.URL.createObjectURL = vi.fn(() => 'blob:fake-url');
    global.URL.revokeObjectURL = vi.fn();
  });

  afterEach(() => {
    global.URL.createObjectURL = originalCreateObjectURL;
    global.URL.revokeObjectURL = originalRevokeObjectURL;
  });

  it('fetches a small window around page 1 on initial load, and re-centers within ~300ms of navigation — not waiting for the next 2.5s poll tick', async () => {
    const job = {
      job_id: 'job-window', filename: 'doc.pdf', status: 'completed',
      total_pages: 900, processed_pages: 900, engine: 'unlimited',
      created_at: new Date().toISOString(),
    };
    const seenPageParams = [];

    apiFetch.mockImplementation(async (url) => {
      if (url === '/api/prompts') return { ok: true, json: async () => ({ prompts: {}, defaults: {} }) };
      if (url === '/api/jobs') return { ok: true, json: async () => [job] };
      if (url === '/api/jobs/job-window/file') {
        return { ok: true, blob: async () => new Blob(['fake pdf binary'], { type: 'application/pdf' }) };
      }
      if (url.split('?')[0] === '/api/jobs/job-window') {
        const params = new URLSearchParams(url.split('?')[1] || '');
        seenPageParams.push({ from: params.get('page_from'), to: params.get('page_to') });
        return { ok: true, json: async () => ({ ...job, pages_data: [] }) };
      }
      return { ok: false };
    });

    vi.useFakeTimers({ shouldAdvanceTime: true });

    renderApp("/studio/job-window");
    await screen.findByTestId('mock-pdf-canvas-viewer');
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });

    // WINDOW = 2, centered on page 1 (the default before any navigation) —
    // the Result pane only ever shows one page now, so the window no
    // longer needs to be wide enough to scroll through.
    expect(seenPageParams[0]).toEqual({ from: '1', to: '3' });

    fireEvent.click(screen.getByTestId('mock-go-to-page-40'));
    // The refetch fires on the debounced navigate-effect (~200ms), the
    // actual regression test for the fix — not the 2.5s poll tick.
    await act(async () => { await vi.advanceTimersByTimeAsync(300); });

    const last = seenPageParams[seenPageParams.length - 1];
    expect(last).toEqual({ from: '38', to: '42' });
  });
});

describe('AppShell/StudioView — Structure-tab reading modal + content cache', () => {
  let originalCreateObjectURL;
  let originalRevokeObjectURL;
  let createObjectURLMock;
  let revokeObjectURLMock;

  beforeEach(() => {
    let blobIdCounter = 0;
    originalCreateObjectURL = global.URL.createObjectURL;
    originalRevokeObjectURL = global.URL.revokeObjectURL;
    createObjectURLMock = vi.fn(() => `blob:chunk-${++blobIdCounter}`);
    revokeObjectURLMock = vi.fn();
    global.URL.createObjectURL = createObjectURLMock;
    global.URL.revokeObjectURL = revokeObjectURLMock;
  });

  afterEach(() => {
    global.URL.createObjectURL = originalCreateObjectURL;
    global.URL.revokeObjectURL = originalRevokeObjectURL;
  });

  function annexureCandidate(n, page) {
    return {
      anchor_id: `anchor-a-${n}`, collapsed_key: `annexure:a-${n}`, key: `annexure:a-${n}`,
      page, scope_chain: [], fingerprint: '', snippet: '', truncated: false,
      continues_on_page: null, scope_provisional: false,
    };
  }

  // Magnifying-glass/jump button is temporarily disabled in StructureTree.jsx (currently the only way
  // the Structure tab opens the reading modal) — re-enable this test alongside it.
  it.skip('opens the reading modal, fetches the PDF + text in parallel on first click, serves the cache on a repeat click without re-fetching, and leaves the main viewer untouched throughout', async () => {
    const job = {
      job_id: 'job-chunk', filename: 'doc.pdf', status: 'completed',
      total_pages: 900, processed_pages: 900, engine: 'unlimited',
      created_at: new Date().toISOString(),
    };
    const filePagesCalls = [];

    apiFetch.mockImplementation(async (url) => {
      if (url === '/api/prompts') return { ok: true, json: async () => ({ prompts: {}, defaults: {} }) };
      if (url === '/api/jobs') return { ok: true, json: async () => [job] };
      if (url === '/api/jobs/job-chunk/file') {
        return { ok: true, blob: async () => new Blob(['main pdf'], { type: 'application/pdf' }) };
      }
      if (url === '/api/jobs/job-chunk/file/pages?start=427&end=446') {
        filePagesCalls.push(url);
        return { ok: true, blob: async () => new Blob(['chunk pdf'], { type: 'application/pdf' }) };
      }
      if (url === '/api/jobs/job-chunk?page_from=427&page_to=446') {
        return { ok: true, json: async () => ({ ...job, pages_data: [{ page: 427, text: 'Chunk page text' }] }) };
      }
      if (url.split('?')[0] === '/api/jobs/job-chunk') {
        return { ok: true, json: async () => ({ ...job, reference_index_version: 1, pages_data: [] }) };
      }
      if (url.startsWith('/api/jobs/job-chunk/references')) {
        return {
          ok: true,
          json: async () => ({ reference_index_version: 1, changed_pages: { 427: { 'annexure:a-2': [annexureCandidate(2, 427)] } } }),
        };
      }
      if (url === '/api/jobs/job-chunk/scope-ranges') {
        return { ok: true, json: async () => ({ reference_index_version: 1, ranges: [{ key: 'annexure:a-2', scope_key: '', depth: 0, start_page: 427, end_page: 446 }] }) };
      }
      return { ok: false };
    });

    renderApp("/studio/job-chunk");
    await screen.findByTestId('mock-pdf-canvas-viewer');
    // Captured before the modal ever opens, while there's still exactly one
    // PdfCanvasViewer instance — the main pane's own element, reused by
    // reference below rather than re-queried (which would fail once the
    // modal's own instance is also on screen).
    const mainPdfEl = screen.getByTestId('mock-pdf-canvas-viewer');
    const mainPdfSrcBefore = mainPdfEl.dataset.pdfSrc;

    fireEvent.click(screen.getByTestId('structure-sidebar-toggle-btn'));
    await screen.findByText('Annexure A-2');

    fireEvent.click(screen.getByTestId('structure-jump-root/annexure:a-2'));

    const modal = await screen.findByTestId('structure-reading-modal');
    expect(modal).toHaveTextContent('Annexure A-2');
    const modalPdf = await within(modal).findByTestId('mock-pdf-canvas-viewer');
    // blob:chunk-1 is the main doc's own blob (created first, on mount);
    // the chunk fetch is the second blob created.
    expect(modalPdf.dataset.pdfSrc).toBe('blob:chunk-2');
    expect(within(modal).getByText('Chunk page text')).toBeInTheDocument();
    expect(filePagesCalls).toHaveLength(1);

    // The main viewer is completely untouched throughout.
    expect(mainPdfEl.dataset.pdfSrc).toBe(mainPdfSrcBefore);

    // Close, then reopen the same reference — must be served from cache,
    // not re-fetched.
    fireEvent.click(within(modal).getByTestId('structure-reading-modal-close'));
    await waitFor(() => expect(screen.queryByTestId('structure-reading-modal')).not.toBeInTheDocument());

    fireEvent.click(screen.getByTestId('structure-jump-root/annexure:a-2'));
    const modal2 = await screen.findByTestId('structure-reading-modal');
    await within(modal2).findByText('Chunk page text');
    expect(filePagesCalls).toHaveLength(1);
  });

  // Depends on the temporarily-disabled magnifying-glass/jump button (see above) to open the modal.
  it.skip('evicts and revokes the oldest cached chunk once more than the cache bound have been viewed', async () => {
    const N = 11; // one more than CHUNK_CACHE_MAX (10)
    const job = {
      job_id: 'job-cache', filename: 'doc.pdf', status: 'completed',
      total_pages: 900, processed_pages: 900, engine: 'unlimited',
      created_at: new Date().toISOString(),
    };
    const changed_pages = {};
    const ranges = [];
    for (let n = 1; n <= N; n++) {
      changed_pages[n] = { [`annexure:a-${n}`]: [annexureCandidate(n, n)] };
      ranges.push({ key: `annexure:a-${n}`, scope_key: '', depth: 0, start_page: n, end_page: n });
    }

    apiFetch.mockImplementation(async (url) => {
      if (url === '/api/prompts') return { ok: true, json: async () => ({ prompts: {}, defaults: {} }) };
      if (url === '/api/jobs') return { ok: true, json: async () => [job] };
      if (url === '/api/jobs/job-cache/file') {
        return { ok: true, blob: async () => new Blob(['main pdf'], { type: 'application/pdf' }) };
      }
      const filePagesMatch = /^\/api\/jobs\/job-cache\/file\/pages\?start=(\d+)&end=(\d+)$/.exec(url);
      if (filePagesMatch) {
        return { ok: true, blob: async () => new Blob([`chunk pdf ${filePagesMatch[1]}`], { type: 'application/pdf' }) };
      }
      if (url.split('?')[0] === '/api/jobs/job-cache') {
        return { ok: true, json: async () => ({ ...job, reference_index_version: 1, pages_data: [] }) };
      }
      if (url.startsWith('/api/jobs/job-cache/references')) {
        return { ok: true, json: async () => ({ reference_index_version: 1, changed_pages }) };
      }
      if (url === '/api/jobs/job-cache/scope-ranges') {
        return { ok: true, json: async () => ({ reference_index_version: 1, ranges }) };
      }
      return { ok: false };
    });

    renderApp("/studio/job-cache");
    await screen.findByTestId('mock-pdf-canvas-viewer');
    fireEvent.click(screen.getByTestId('structure-sidebar-toggle-btn'));
    await screen.findByText('Annexure A-1');

    for (let n = 1; n <= N; n++) {
      fireEvent.click(screen.getByTestId(`structure-jump-root/annexure:a-${n}`));
      await waitFor(() => expect(screen.getByTestId('structure-reading-modal')).toHaveTextContent(`Annexure A-${n}`));
    }

    // The first chunk viewed (annexure:a-1, blob "blob:chunk-2" — index 1 is
    // the main doc's own blob, created first) must have been evicted once
    // the 11th distinct chunk pushed the cache past its 10-entry bound.
    expect(revokeObjectURLMock).toHaveBeenCalledWith('blob:chunk-2');
  });
});

describe('AppShell/StudioView — Structure item edit only sends fields that actually changed', () => {
  let originalCreateObjectURL;
  let originalRevokeObjectURL;

  beforeEach(() => {
    originalCreateObjectURL = global.URL.createObjectURL;
    originalRevokeObjectURL = global.URL.revokeObjectURL;
    global.URL.createObjectURL = vi.fn(() => 'blob:fake-url');
    global.URL.revokeObjectURL = vi.fn();
  });

  afterEach(() => {
    global.URL.createObjectURL = originalCreateObjectURL;
    global.URL.revokeObjectURL = originalRevokeObjectURL;
  });

  it('a heading-only rename omits start_page/end_page from the PATCH body (would otherwise spuriously trigger the backend\'s range-change validation)', async () => {
    const job = {
      job_id: 'job-edit', filename: 'doc.pdf', status: 'completed',
      total_pages: 900, processed_pages: 900, engine: 'unlimited',
      created_at: new Date().toISOString(),
    };
    const patchCalls = [];

    apiFetch.mockImplementation(async (url, opts) => {
      if (url === '/api/prompts') return { ok: true, json: async () => ({ prompts: {}, defaults: {} }) };
      if (url === '/api/jobs') return { ok: true, json: async () => [job] };
      if (url === '/api/jobs/job-edit/file') {
        return { ok: true, blob: async () => new Blob(['main pdf'], { type: 'application/pdf' }) };
      }
      if (url.startsWith('/api/jobs/job-edit/structure-items/') && opts?.method === 'PATCH') {
        patchCalls.push(JSON.parse(opts.body));
        return { ok: true, json: async () => ({}) };
      }
      if (url.split('?')[0] === '/api/jobs/job-edit') {
        return { ok: true, json: async () => ({ ...job, reference_index_version: 1, index_built_at: '2026-01-01T00:00:00Z', pages_data: [] }) };
      }
      if (url.startsWith('/api/jobs/job-edit/references')) {
        return {
          ok: true,
          json: async () => ({
            reference_index_version: 1,
            changed_pages: { 427: { 'annexure:a-2': [{ anchor_id: 'a-leaf', collapsed_key: 'annexure:a-2', key: 'annexure:a-2', page: 427, occurrence_index: 0, scope_chain: [] }] } },
          }),
        };
      }
      if (url === '/api/jobs/job-edit/scope-ranges') {
        return { ok: true, json: async () => ({ reference_index_version: 1, ranges: [{ key: 'annexure:a-2', scope_key: '', depth: 0, start_page: 427, end_page: 446 }] }) };
      }
      if (url === '/api/jobs/job-edit/structure-hierarchy-overrides') {
        return { ok: true, json: async () => ({ reference_index_version: 1, parents: [], order: [] }) };
      }
      return { ok: false };
    });

    renderApp("/studio/job-edit");
    await screen.findByTestId('mock-pdf-canvas-viewer');
    fireEvent.click(screen.getByTestId('structure-sidebar-toggle-btn'));
    await screen.findByText('Annexure A-2');

    fireEvent.click(screen.getByTestId('structure-edit-root/annexure:a-2'));
    fireEvent.change(screen.getByTestId('structure-item-heading-input'), { target: { value: 'Renamed Annexure' } });
    fireEvent.click(screen.getByTestId('structure-item-modal-submit'));

    await waitFor(() => expect(patchCalls).toHaveLength(1));
    expect(patchCalls[0]).toEqual({ heading: 'Renamed Annexure' });
  });
});

describe('AppShell/StudioView — add-as-child chains create with one move', () => {
  let originalCreateObjectURL;
  let originalRevokeObjectURL;

  beforeEach(() => {
    originalCreateObjectURL = global.URL.createObjectURL;
    originalRevokeObjectURL = global.URL.revokeObjectURL;
    global.URL.createObjectURL = vi.fn(() => 'blob:fake-url');
    global.URL.revokeObjectURL = vi.fn();
  });

  afterEach(() => {
    global.URL.createObjectURL = originalCreateObjectURL;
    global.URL.revokeObjectURL = originalRevokeObjectURL;
  });

  it('clicking a node\'s add button creates the item then moves it under that node, in one user action', async () => {
    const job = {
      job_id: 'job-add-child', filename: 'doc.pdf', status: 'completed',
      total_pages: 900, processed_pages: 900, engine: 'unlimited',
      created_at: new Date().toISOString(),
    };
    const postCalls = [];
    const moveCalls = [];

    apiFetch.mockImplementation(async (url, opts) => {
      if (url === '/api/prompts') return { ok: true, json: async () => ({ prompts: {}, defaults: {} }) };
      if (url === '/api/jobs') return { ok: true, json: async () => [job] };
      if (url === '/api/jobs/job-add-child/file') {
        return { ok: true, blob: async () => new Blob(['main pdf'], { type: 'application/pdf' }) };
      }
      if (url === '/api/jobs/job-add-child/structure-items' && opts?.method === 'POST') {
        postCalls.push(JSON.parse(opts.body));
        return { ok: true, json: async () => ({ manual_key: 'manual:new1', job_id: job.job_id, heading: 'New Clause', start_page: 427, end_page: 427 }) };
      }
      if (url === '/api/jobs/job-add-child/structure-items/move') {
        moveCalls.push(JSON.parse(opts.body));
        return { ok: true, json: async () => ({}) };
      }
      if (url.split('?')[0] === '/api/jobs/job-add-child') {
        return { ok: true, json: async () => ({ ...job, reference_index_version: 1, index_built_at: '2026-01-01T00:00:00Z', pages_data: [] }) };
      }
      if (url.startsWith('/api/jobs/job-add-child/references')) {
        return {
          ok: true,
          json: async () => ({
            reference_index_version: 1,
            changed_pages: { 427: { 'annexure:a-2': [{ anchor_id: 'a-leaf', collapsed_key: 'annexure:a-2', key: 'annexure:a-2', page: 427, occurrence_index: 0, scope_chain: [] }] } },
          }),
        };
      }
      if (url === '/api/jobs/job-add-child/scope-ranges') {
        return { ok: true, json: async () => ({ reference_index_version: 1, ranges: [{ key: 'annexure:a-2', scope_key: '', depth: 0, start_page: 427, end_page: 446 }] }) };
      }
      if (url === '/api/jobs/job-add-child/structure-hierarchy-overrides') {
        return { ok: true, json: async () => ({ reference_index_version: 1, parents: [], order: [] }) };
      }
      return { ok: false };
    });

    renderApp("/studio/job-add-child");
    await screen.findByTestId('mock-pdf-canvas-viewer');
    fireEvent.click(screen.getByTestId('structure-sidebar-toggle-btn'));
    await screen.findByText('Annexure A-2');

    fireEvent.click(screen.getByTestId('structure-add-child-root/annexure:a-2'));
    fireEvent.change(screen.getByTestId('structure-item-heading-input'), { target: { value: 'New Clause' } });
    fireEvent.click(screen.getByTestId('structure-item-modal-submit'));

    await waitFor(() => expect(moveCalls).toHaveLength(1));
    expect(postCalls).toEqual([{ heading: 'New Clause', start_page: 427, end_page: 427 }]);
    expect(moveCalls[0]).toEqual({
      item_key: 'manual:new1', item_page: 427, item_occurrence_index: 0,
      new_parent_key: 'annexure:a-2', new_parent_page: 427, new_parent_occurrence_index: 0,
      ordered_siblings: [{ item_key: 'manual:new1', item_page: 427, item_occurrence_index: 0 }],
    });
  });
});

describe('AppShell — export asks for filename + location before downloading', () => {
  let createObjectURLMock;
  let revokeObjectURLMock;
  let originalShowSaveFilePicker;

  function mockJobApi() {
    const job = {
      job_id: 'job-1', filename: 'first.pdf', status: 'completed',
      total_pages: 2, processed_pages: 2, engine: 'unlimited', created_at: new Date().toISOString(),
    };
    apiFetch.mockImplementation(async (url) => {
      if (url === '/api/prompts') return { ok: true, json: async () => ({ prompts: {}, defaults: {} }) };
      if (url === '/api/jobs') return { ok: true, json: async () => [job] };
      if (url.startsWith('/api/jobs/job-1/export')) {
        return {
          ok: true,
          headers: { get: (name) => (name === 'Content-Disposition' ? 'attachment; filename="job-1_ocr_result.docx"' : null) },
          blob: async () => new Blob(['fake docx bytes'], { type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' }),
        };
      }
      if (url.startsWith('/api/jobs/job-')) {
        if (url.endsWith('/file')) return { ok: true, blob: async () => new Blob(['pdf binary'], { type: 'application/pdf' }) };
        return { ok: true, json: async () => ({ ...job, pages: [] }) };
      }
      return { ok: false };
    });
  }

  // Loading the Studio PDF preview itself calls URL.createObjectURL once
  // (the remote-file blob preview — unrelated to export), so assertions
  // about the export flow's own createObjectURL usage compare against this
  // baseline count rather than an absolute zero.
  async function openExportAndConfirm() {
    renderApp('/studio/job-1');
    await screen.findByTestId('mock-pdf-canvas-viewer');
    await waitFor(() => expect(createObjectURLMock).toHaveBeenCalledTimes(1));
    const baselineObjectUrlCalls = createObjectURLMock.mock.calls.length;
    fireEvent.click(screen.getByTestId('export-dropdown-toggle'));
    fireEvent.click(await screen.findByTestId('export-confirm-btn'));
    return { baselineObjectUrlCalls };
  }

  beforeEach(() => {
    vi.clearAllMocks();
    mockAuth = { isAdmin: true };
    mockJobApi();

    createObjectURLMock = vi.fn(() => 'blob:fake-export-url');
    revokeObjectURLMock = vi.fn();
    global.URL.createObjectURL = createObjectURLMock;
    global.URL.revokeObjectURL = revokeObjectURLMock;

    originalShowSaveFilePicker = window.showSaveFilePicker;
  });

  afterEach(() => {
    if (originalShowSaveFilePicker === undefined) delete window.showSaveFilePicker;
    else window.showSaveFilePicker = originalShowSaveFilePicker;
  });

  it('uses the native Save-As picker when the browser supports it, writing the fetched blob to the chosen handle', async () => {
    const write = vi.fn().mockResolvedValue(undefined);
    const close = vi.fn().mockResolvedValue(undefined);
    const createWritable = vi.fn().mockResolvedValue({ write, close });
    window.showSaveFilePicker = vi.fn().mockResolvedValue({ createWritable });

    const { baselineObjectUrlCalls } = await openExportAndConfirm();

    // The picker has to be called with the generic client-side fallback name
    // (not the server's nicer Content-Disposition one) — it must fire before
    // the export fetch even starts, to stay inside the click's user-gesture
    // window (confirmed against real Chromium: calling it after an awaited
    // fetch throws SecurityError), so the real filename isn't known yet.
    await waitFor(() => {
      expect(window.showSaveFilePicker).toHaveBeenCalledWith(
        expect.objectContaining({ suggestedName: 'job-1_export.md' })
      );
    });
    await waitFor(() => expect(write).toHaveBeenCalledOnce());
    expect(close).toHaveBeenCalledOnce();
    expect(screen.queryByTestId('save-file-dialog')).not.toBeInTheDocument();
    // Native picker handled it end-to-end — the universal anchor-download
    // fallback path must not also have fired.
    expect(createObjectURLMock).toHaveBeenCalledTimes(baselineObjectUrlCalls);
  });

  it('calls the native picker BEFORE fetching the export, not after — calling it post-fetch loses the click\'s user-activation and throws SecurityError in real Chromium (caught via a live run, not assumed)', async () => {
    const order = [];
    window.showSaveFilePicker = vi.fn(async (...args) => {
      order.push('picker');
      return { createWritable: vi.fn().mockResolvedValue({ write: vi.fn(), close: vi.fn() }) };
    });
    const originalImpl = apiFetch.getMockImplementation();
    apiFetch.mockImplementation(async (url, ...rest) => {
      if (url.startsWith('/api/jobs/job-1/export')) order.push('export-fetch');
      return originalImpl(url, ...rest);
    });

    await openExportAndConfirm();

    await waitFor(() => expect(order).toContain('export-fetch'));
    expect(order.indexOf('picker')).toBeLessThan(order.indexOf('export-fetch'));
  });

  it('falls back to the in-app filename prompt when the native picker is unavailable, then downloads with the chosen name', async () => {
    delete window.showSaveFilePicker;

    const { baselineObjectUrlCalls } = await openExportAndConfirm();

    const dialog = await screen.findByTestId('save-file-dialog');
    expect(within(dialog).getByTestId('save-file-dialog-input')).toHaveValue('job-1_ocr_result.docx');

    fireEvent.change(within(dialog).getByTestId('save-file-dialog-input'), { target: { value: 'my-renamed-export.docx' } });
    fireEvent.click(within(dialog).getByTestId('save-file-dialog-save'));

    await waitFor(() => expect(createObjectURLMock).toHaveBeenCalledTimes(baselineObjectUrlCalls + 1));
    expect(revokeObjectURLMock).toHaveBeenCalledOnce();
    expect(screen.queryByTestId('save-file-dialog')).not.toBeInTheDocument();
  });

  it('treats a cancelled native picker (AbortError) as a silent no-op — no error alert, no fallback download, and no export fetch at all', async () => {
    const abortError = Object.assign(new Error('The user aborted a request.'), { name: 'AbortError' });
    window.showSaveFilePicker = vi.fn().mockRejectedValue(abortError);
    const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {});

    const { baselineObjectUrlCalls } = await openExportAndConfirm();

    await waitFor(() => expect(window.showSaveFilePicker).toHaveBeenCalledOnce());
    expect(alertSpy).not.toHaveBeenCalled();
    expect(createObjectURLMock).toHaveBeenCalledTimes(baselineObjectUrlCalls);
    expect(screen.queryByTestId('save-file-dialog')).not.toBeInTheDocument();
    // Cancelling the picker (before any fetch was ever made, by design)
    // must not still trigger the export request behind the user's back.
    expect(apiFetch.mock.calls.some(([url]) => url.startsWith('/api/jobs/job-1/export'))).toBe(false);
  });
});
