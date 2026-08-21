import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('./authService', () => ({
  getAccessToken: vi.fn(),
  login: vi.fn(),
  DEV_MODE: false,
}));

import { getAccessToken, login } from './authService';
import { apiFetch } from './api';

describe('apiFetch', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    global.fetch = vi.fn();
  });

  it('attaches Authorization: Bearer <token> when a token is available', async () => {
    getAccessToken.mockResolvedValue('my-token');
    global.fetch.mockResolvedValue(new Response('{}', { status: 200 }));

    await apiFetch('/api/jobs');

    const [, opts] = global.fetch.mock.calls[0];
    expect(opts.headers.get('Authorization')).toBe('Bearer my-token');
  });

  it('does not set an Authorization header when there is no token', async () => {
    getAccessToken.mockResolvedValue(null);
    global.fetch.mockResolvedValue(new Response('{}', { status: 200 }));

    await apiFetch('/api/jobs');

    const [, opts] = global.fetch.mock.calls[0];
    expect(opts.headers.has('Authorization')).toBe(false);
  });

  it('triggers login() on a 401 response instead of silently failing', async () => {
    getAccessToken.mockResolvedValue('stale-token');
    global.fetch.mockResolvedValue(new Response('{}', { status: 401 }));

    await apiFetch('/api/jobs');

    expect(login).toHaveBeenCalledOnce();
  });

  it('does not call login() on a successful response', async () => {
    getAccessToken.mockResolvedValue('good-token');
    global.fetch.mockResolvedValue(new Response('{}', { status: 200 }));

    await apiFetch('/api/jobs');

    expect(login).not.toHaveBeenCalled();
  });
});
