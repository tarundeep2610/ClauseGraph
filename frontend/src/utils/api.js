import { getAccessToken, login, DEV_MODE } from './authService';

export async function apiFetch(url, opts = {}) {
  const token = await getAccessToken();
  const headers = new Headers(opts.headers || {});
  if (token) headers.set('Authorization', `Bearer ${token}`);

  const resp = await fetch(url, { ...opts, headers, credentials: 'same-origin' });

  if (resp.status === 401) {
    if (DEV_MODE) {
      window.location.href = '/login';
    } else {
      await login();
    }
  }
  return resp;
}
