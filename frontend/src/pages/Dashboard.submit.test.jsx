import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { apiFetch } from '../utils/api';
import Dashboard from './Dashboard';

vi.mock('../utils/api', () => ({
  apiFetch: vi.fn(),
}));

vi.mock('../utils/AuthContext', () => ({
  useAuth: () => ({ isAdmin: false }),
}));

describe('Dashboard — job submission includes the new batch/escalation params', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    global.URL.createObjectURL = vi.fn(() => 'blob:fake');
    global.URL.revokeObjectURL = vi.fn();

    apiFetch.mockImplementation(async (url, opts) => {
      if (url === '/api/prompts') return { ok: true, json: async () => ({ prompts: {}, defaults: {} }) };
      if (url === '/api/jobs' && (!opts || opts.method === undefined)) return { ok: true, json: async () => [] };
      if (url.startsWith('/api/jobs?')) {
        return { ok: true, json: async () => ({ job_id: 'new-job', total_pages: 1, status: 'pending' }) };
      }
      return { ok: true, json: async () => ({}) };
    });
  });

  it('sends processing_mode/escalate_before_flagging for the default unlimited engine', async () => {
    render(<Dashboard />);
    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith('/api/prompts'));

    const file = new File(['dummy'], 'test.pdf', { type: 'application/pdf' });
    const input = screen.getByTestId('file-input');
    fireEvent.change(input, { target: { files: [file] } });

    const submitBtn = await screen.findByTestId('submit-btn');
    fireEvent.click(submitBtn);

    await waitFor(() => {
      const submitCall = apiFetch.mock.calls.find(([url]) => url.startsWith('/api/jobs?'));
      expect(submitCall).toBeTruthy();
      const params = new URLSearchParams(submitCall[0].split('?')[1]);
      expect(params.get('processing_mode')).toBe('single');
      expect(params.get('escalate_before_flagging')).toBe('true');
      expect(params.has('chunk_size')).toBe(false); // only set when processing_mode=batch
      expect(params.has('enable_overlap')).toBe(false); // batch-only, same as chunk_size
    });
  });

  it('sends enable_overlap when processing_mode=batch, defaulting to true', async () => {
    render(<Dashboard />);
    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith('/api/prompts'));

    const file = new File(['dummy'], 'test.pdf', { type: 'application/pdf' });
    const input = screen.getByTestId('file-input');
    fireEvent.change(input, { target: { files: [file] } });

    const batchParams = await screen.findByTestId('batch-mode-params');
    const processingModeSelect = batchParams.querySelector('select');
    fireEvent.change(processingModeSelect, { target: { value: 'batch' } });

    const overlapCheckbox = await screen.findByTestId('enable-overlap-checkbox');
    expect(overlapCheckbox.checked).toBe(true);

    const submitBtn = await screen.findByTestId('submit-btn');
    fireEvent.click(submitBtn);

    await waitFor(() => {
      const submitCall = apiFetch.mock.calls.find(([url]) => url.startsWith('/api/jobs?'));
      expect(submitCall).toBeTruthy();
      const params = new URLSearchParams(submitCall[0].split('?')[1]);
      expect(params.get('processing_mode')).toBe('batch');
      expect(params.get('enable_overlap')).toBe('true');
    });
  });

  it('sends enable_overlap=false when the overlap checkbox is unchecked in batch mode', async () => {
    render(<Dashboard />);
    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith('/api/prompts'));

    const file = new File(['dummy'], 'test.pdf', { type: 'application/pdf' });
    const input = screen.getByTestId('file-input');
    fireEvent.change(input, { target: { files: [file] } });

    const batchParams = await screen.findByTestId('batch-mode-params');
    const processingModeSelect = batchParams.querySelector('select');
    fireEvent.change(processingModeSelect, { target: { value: 'batch' } });

    const overlapCheckbox = await screen.findByTestId('enable-overlap-checkbox');
    fireEvent.click(overlapCheckbox);

    const submitBtn = await screen.findByTestId('submit-btn');
    fireEvent.click(submitBtn);

    await waitFor(() => {
      const submitCall = apiFetch.mock.calls.find(([url]) => url.startsWith('/api/jobs?'));
      const params = new URLSearchParams(submitCall[0].split('?')[1]);
      expect(params.get('enable_overlap')).toBe('false');
    });
  });
});
