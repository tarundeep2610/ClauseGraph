import { describe, it, expect, vi, afterEach } from 'vitest';

describe('appInfo', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  it('falls back to "ClauseGraph" when VITE_APP_NAME is unset', async () => {
    vi.stubEnv('VITE_APP_NAME', '');
    vi.resetModules();
    const { APP_NAME } = await import('./appInfo');
    expect(APP_NAME).toBe('ClauseGraph');
  });

  it('uses VITE_APP_NAME when it is set, not the fallback', async () => {
    vi.stubEnv('VITE_APP_NAME', 'Some Custom Name');
    vi.resetModules();
    const { APP_NAME } = await import('./appInfo');
    expect(APP_NAME).toBe('Some Custom Name');
  });
});
