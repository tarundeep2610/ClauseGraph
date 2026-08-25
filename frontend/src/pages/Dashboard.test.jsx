import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
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
});
