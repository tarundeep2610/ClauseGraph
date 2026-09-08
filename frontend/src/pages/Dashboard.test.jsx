import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react';
import { apiFetch } from '../utils/api';
import Dashboard from './Dashboard';

vi.mock('../utils/api', () => ({
  apiFetch: vi.fn(),
}));

vi.mock('../utils/AuthContext', () => ({
  useAuth: () => ({ isAdmin: false }),
}));

describe('Dashboard — PDF Remote Blob Preview & Regressions', () => {
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

  it('1. Happy path: fetches remote PDF via apiFetch and assigns object URL to iframe pdfSrc', async () => {
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
      if (url === '/api/jobs/job-101') {
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

    render(<Dashboard />);

    await waitFor(() => {
      expect(screen.getByTestId('history-card')).toBeInTheDocument();
    });

    const iframe = document.querySelector('iframe.pdf-frame');
    expect(iframe).toBeInTheDocument();

    await waitFor(() => {
      expect(apiFetch).toHaveBeenCalledWith('/api/jobs/job-101/file');
      expect(iframe.getAttribute('src')).toBe('blob:fake-url-1');
    });

    // Ensure raw endpoint is NOT used as iframe src directly
    expect(iframe.getAttribute('src')).not.toBe('/api/jobs/job-101/file');
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

    render(<Dashboard />);

    await waitFor(() => {
      expect(screen.getAllByTestId('history-card')).toHaveLength(2);
    });

    const iframe = document.querySelector('iframe.pdf-frame');
    await waitFor(() => {
      expect(iframe.getAttribute('src')).toBe('blob:fake-url-1');
    });

    const cards = screen.getAllByTestId('history-card');
    fireEvent.click(cards[1]);

    await waitFor(() => {
      expect(iframe.getAttribute('src')).toBe('blob:fake-url-2');
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
      if (url === '/api/jobs/job-fail') {
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

    render(<Dashboard />);

    await waitFor(() => {
      expect(screen.getByTestId('history-card')).toBeInTheDocument();
    });

    const iframe = document.querySelector('iframe.pdf-frame');
    await waitFor(() => {
      expect(apiFetch).toHaveBeenCalledWith('/api/jobs/job-fail/file');
      expect(iframe.getAttribute('src')).toBe('about:blank');
    });
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
      if (url === '/api/jobs/job-remote') {
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

    render(<Dashboard />);

    await waitFor(() => {
      expect(screen.getByTestId('history-card')).toBeInTheDocument();
    });

    const iframe = document.querySelector('iframe.pdf-frame');
    await waitFor(() => {
      expect(iframe.getAttribute('src')).toBe('blob:fake-url-1');
    });

    const fileInput = screen.getByTestId('file-input');
    const localFile = new File(['local pdf content'], 'local_doc.pdf', { type: 'application/pdf' });

    fireEvent.change(fileInput, { target: { files: [localFile] } });

    await waitFor(() => {
      expect(iframe.getAttribute('src')).toBe('blob:fake-url-2');
    });
  });

  it('5. Renders about:blank when no jobs and no local file are present', async () => {
    render(<Dashboard />);

    const iframe = document.querySelector('iframe.pdf-frame');
    expect(iframe).toBeInTheDocument();

    await waitFor(() => {
      expect(iframe.getAttribute('src')).toBe('about:blank');
    });
  });

  it('6. Sidebar wrapper carries the "sidebar" class (layout/scroll fix depends on it)', async () => {
    render(<Dashboard />);
    await waitFor(() => {
      expect(document.querySelector('.sidebar')).toBeInTheDocument();
    });
  });

  it('7. Clicking the sidebar toggle adds/removes the "sidebar-collapsed" class on .dashboard', async () => {
    render(<Dashboard />);
    await waitFor(() => {
      expect(document.querySelector('.dashboard')).toBeInTheDocument();
    });
    expect(document.querySelector('.dashboard').className).not.toContain('sidebar-collapsed');

    fireEvent.click(screen.getByTestId('sidebar-toggle-btn'));
    await waitFor(() => {
      expect(document.querySelector('.dashboard').className).toContain('sidebar-collapsed');
    });

    fireEvent.click(screen.getByTestId('sidebar-toggle-btn'));
    await waitFor(() => {
      expect(document.querySelector('.dashboard').className).not.toContain('sidebar-collapsed');
    });
  });
});

describe('Dashboard — GPU status polling', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('polls GET /health on mount and passes the result down to the Header pill', async () => {
    apiFetch.mockImplementation(async (url) => {
      if (url === '/api/prompts') return { ok: true, json: async () => ({ prompts: {}, defaults: {} }) };
      if (url === '/api/jobs') return { ok: true, json: async () => [] };
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

    render(<Dashboard />);

    await waitFor(() => {
      expect(apiFetch).toHaveBeenCalledWith('/health');
    });
    await waitFor(() => {
      expect(screen.getByTestId('gpu-status-pill')).toHaveTextContent('GPU: Ready');
    });
  });

  it('shows no GPU pill when the health check fails, without crashing the page', async () => {
    apiFetch.mockImplementation(async (url) => {
      if (url === '/api/prompts') return { ok: true, json: async () => ({ prompts: {}, defaults: {} }) };
      if (url === '/api/jobs') return { ok: true, json: async () => [] };
      if (url === '/health') return { ok: false };
      return { ok: false };
    });

    expect(() => render(<Dashboard />)).not.toThrow();

    await waitFor(() => {
      expect(apiFetch).toHaveBeenCalledWith('/health');
    });
    expect(screen.queryByTestId('gpu-status-pill')).not.toBeInTheDocument();
  });
});

describe('Dashboard — settings persistence (localStorage)', () => {
  beforeEach(() => {
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

    render(<Dashboard />);

    await waitFor(() => {
      expect(screen.getByTestId('engine-select').value).toBe('glm');
    });
    expect(screen.getByRole('button', { name: /PDF Only/i })).toHaveClass('active');
  });

  it('persists a form change to localStorage', async () => {
    render(<Dashboard />);

    const engineSelect = await screen.findByTestId('engine-select');
    fireEvent.change(engineSelect, { target: { value: 'glm' } });

    await waitFor(() => {
      const stored = JSON.parse(localStorage.getItem('ocr-ui-settings'));
      expect(stored.form.engine).toBe('glm');
    });
  });

  it('never persists customPrompt or pageRange (per-document/session state, not a user preference)', async () => {
    render(<Dashboard />);

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

    expect(() => render(<Dashboard />)).not.toThrow();
    await waitFor(() => {
      expect(screen.getByTestId('engine-select')).toBeInTheDocument();
    });
  });

  it('restores a persisted collapsed sidebar on mount', async () => {
    localStorage.setItem('ocr-ui-settings', JSON.stringify({ sidebarCollapsed: true }));

    render(<Dashboard />);

    await waitFor(() => {
      expect(document.querySelector('.dashboard').className).toContain('sidebar-collapsed');
    });
  });

  it('persists sidebarCollapsed to localStorage when the toggle is clicked', async () => {
    render(<Dashboard />);
    await screen.findByTestId('sidebar-toggle-btn');

    fireEvent.click(screen.getByTestId('sidebar-toggle-btn'));

    await waitFor(() => {
      const stored = JSON.parse(localStorage.getItem('ocr-ui-settings'));
      expect(stored.sidebarCollapsed).toBe(true);
    });
  });
});

describe('Dashboard — jobDetails is cleared immediately on any job switch (regression)', () => {
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
      if (url === '/api/jobs/job-a') return { ok: true, json: async () => ({ ...jobA, pages_data: [{ page: 1, text: 'Job A text' }] }) };
      if (url === '/api/jobs/job-b') {
        await bDetailPromise;
        return { ok: true, json: async () => ({ ...jobB, pages_data: [{ page: 1, text: 'Job B text' }] }) };
      }
      return { ok: false };
    });

    render(<Dashboard />);
    await screen.findByText('Job A text');

    const cards = screen.getAllByTestId('history-card');
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
      if (url === '/api/jobs/job-old') return { ok: true, json: async () => ({ ...oldJob, pages_data: [{ page: 1, text: 'Old job text' }] }) };
      if (url.startsWith('/api/jobs?') && opts?.method === 'POST') {
        return { ok: true, json: async () => ({ job_id: 'job-new' }) };
      }
      if (url === '/api/jobs/job-new') {
        await newDetailPromise;
        return { ok: true, json: async () => ({ job_id: 'job-new', filename: 'new.pdf', status: 'processing', total_pages: 1, processed_pages: 0, pages_data: [] }) };
      }
      return { ok: false };
    });

    render(<Dashboard />);
    await screen.findByText('Old job text');

    const fileInput = screen.getByTestId('file-input');
    const localFile = new File(['pdf content'], 'new.pdf', { type: 'application/pdf' });
    fireEvent.change(fileInput, { target: { files: [localFile] } });
    fireEvent.click(screen.getByTestId('submit-btn'));

    await waitFor(() => {
      expect(screen.queryByText('Old job text')).not.toBeInTheDocument();
    });

    await act(async () => {
      resolveNew();
      await newDetailPromise;
    });
  });
});

describe('Dashboard — per-page and bulk Retry actions', () => {
  beforeEach(() => {
    vi.clearAllMocks();
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
      if (url === '/api/jobs/job-r') return { ok: true, json: async () => job };
      if (url === '/api/jobs/job-r/pages/1/retry' && opts?.method === 'POST') {
        return { ok: true, json: async () => ({ page_num: 1, status: 'success', text: 'new retried text', confidence_pct: 95 }) };
      }
      return { ok: false };
    });

    render(<Dashboard />);
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
      if (url === '/api/jobs/job-r') return { ok: true, json: async () => job };
      if (url.startsWith('/api/jobs/job-r/retry-pages') && opts?.method === 'POST') {
        seenUrls.push(url);
        return { ok: true, json: async () => ({ status: 'escalating' }) };
      }
      return { ok: false };
    });

    render(<Dashboard />);
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
      if (url === '/api/jobs/job-r') return { ok: true, json: async () => job };
      if (url.startsWith('/api/jobs/job-r/retry-pages') && opts?.method === 'POST') {
        seenUrls.push(url);
        return { ok: true, json: async () => ({ status: 'escalating' }) };
      }
      return { ok: false };
    });

    render(<Dashboard />);
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
      if (url === '/api/jobs/job-r') return { ok: true, json: async () => job };
      if (url.startsWith('/api/jobs/job-r/retry-pages') && opts?.method === 'POST') {
        seenUrls.push(url);
        return { ok: true, json: async () => ({ status: 'escalating' }) };
      }
      return { ok: false };
    });

    render(<Dashboard />);
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
      if (url === '/api/jobs/job-r') return { ok: true, json: async () => job };
      if (url.startsWith('/api/jobs/job-r/retry-pages')) seenUrls.push(url);
      return { ok: false };
    });

    render(<Dashboard />);
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
      if (url === '/api/jobs/job-r') return { ok: true, json: async () => job };
      if (url.startsWith('/api/jobs/job-r/retry-pages') && opts?.method === 'POST') {
        return { ok: false, statusText: 'Conflict', json: async () => ({ detail: 'A bulk retry is already running for this job.' }) };
      }
      return { ok: false };
    });
    const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {});

    render(<Dashboard />);
    await screen.findByTestId('retry-pages-btn');
    fireEvent.click(screen.getByTestId('retry-pages-btn'));
    fireEvent.click(screen.getByTestId('retry-option-failed'));

    await waitFor(() => {
      expect(alertSpy).toHaveBeenCalledWith(expect.stringContaining('A bulk retry is already running for this job.'));
    });
  });
});

describe('Dashboard — resuming a job no longer needs a lucky poll to notice it (regression)', () => {
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
      if (url === '/api/jobs/job-resume') {
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

    render(<Dashboard />);
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
      if (url === '/api/jobs/job-resume') {
        detailCalls += 1;
        const pages = detailCalls < 3
          ? [{ page: 1, text: 'first page text' }, { page: 2, text: 'second page text' }]
          : [
              { page: 1, text: 'first page text' }, { page: 2, text: 'second page text' },
              { page: 3, text: 'third page text' }, { page: 4, text: 'fourth page text' },
              { page: 5, text: 'fifth page text' },
            ];
        return { ok: true, json: async () => ({ ...job, pages_data: pages }) };
      }
      return { ok: false };
    });

    // See test 1 for why fake timers must be installed before render.
    vi.useFakeTimers({ shouldAdvanceTime: true });

    render(<Dashboard />);
    await screen.findByTestId('history-card');
    await screen.findByTestId('page-block-1');
    expect(screen.queryByTestId('page-block-5')).not.toBeInTheDocument();

    const card = screen.getByTestId('history-card');
    const resumeBtn = card.querySelector('.fa-rotate-right').closest('button');

    fireEvent.click(resumeBtn);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    await act(async () => { await vi.advanceTimersByTimeAsync(2500); });
    await act(async () => { await vi.advanceTimersByTimeAsync(2500); });

    expect(screen.getByTestId('page-block-5')).toBeInTheDocument();
  });

  it('3. resuming from the history list actually switches the viewer to that job', async () => {
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
      if (url === '/api/jobs/job-active') {
        return { ok: true, json: async () => ({ ...jobs[0], pages_data: [] }) };
      }
      if (url === '/api/jobs/job-paused') {
        return { ok: true, json: async () => ({ ...jobs[1], pages_data: [] }) };
      }
      return { ok: false };
    });

    render(<Dashboard />);
    await waitFor(() => {
      expect(screen.getAllByTestId('history-card')).toHaveLength(2);
    });

    // Default activeJobId falls back to jobs[0] ('job-active') on mount.
    const cards = screen.getAllByTestId('history-card');
    expect(cards[0].className).toContain('active');
    expect(cards[1].className).not.toContain('active');

    const resumeBtn = cards[1].querySelector('.fa-rotate-right').closest('button');
    fireEvent.click(resumeBtn);

    await waitFor(() => {
      expect(retriedPaused).toBe(true);
    });
    await waitFor(() => {
      const refreshedCards = screen.getAllByTestId('history-card');
      expect(refreshedCards[1].className).toContain('active');
      expect(refreshedCards[0].className).not.toContain('active');
    });
  });
});
