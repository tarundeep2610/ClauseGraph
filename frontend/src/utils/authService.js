import { UserManager, WebStorageStateStore, User, Log } from 'oidc-client-ts';
import { APP_NAME } from './appInfo';

Log.setLogger(console);
Log.setLevel(Log.WARN);

const clientOrigin = window.location.origin;

const authority = import.meta.env.VITE_AUTH_AUTHORITY?.trim().replace(/\/$/, '');
const projectId = import.meta.env.VITE_PROJECT_ID?.trim();
const orgId = import.meta.env.VITE_ORG_ID?.trim();
const clientId = import.meta.env.VITE_CLIENT_ID?.trim();

export const DEV_MODE = import.meta.env.VITE_DEV_MODE === 'true';

export function decodeJwtPayload(token) {
  if (!token || !token.includes('.')) return null;
  try {
    const b64 = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
    const padded = b64 + '==='.slice((b64.length + 3) % 4 || 0);
    return JSON.parse(atob(padded));
  } catch (e) {
    console.error('[AUTH] Failed to decode JWT payload:', e);
    return null;
  }
}

// Relative paths — same-origin only. This service deliberately never bakes an
// absolute host:port into the build (that's what broke itender's portability).
const settings = {
  authority: authority || '',
  client_id: clientId || '',
  redirect_uri: `${clientOrigin}/callback`,
  post_logout_redirect_uri: `${clientOrigin}/login`,
  response_type: 'code',
  scope: `openid profile email role offline_access urn:zitadel:iam:org:project:id:${projectId}:aud`,

  loadUserInfo: true,
  automaticSilentRenew: true,
  monitorSession: false,
  userStore: new WebStorageStateStore({ store: window.localStorage }),
  stateStore: new WebStorageStateStore({ store: window.localStorage }),

  metadata: {
    issuer: authority || '',
    authorization_endpoint: `${authority}/api/v1/auth/authorize`,
    token_endpoint: `${clientOrigin}/api/auth/proxy/token`,
    userinfo_endpoint: `${clientOrigin}/api/auth/proxy/userinfo`,
    jwks_uri: `${authority}/api/v1/auth/jwks`,
    end_session_endpoint: `${authority}/oidc/v1/end_session`,
  },

  extraQueryParams: {
    org_id: orgId || '',
    project_id: projectId || '',
    // Lets the auth provider's own login/consent screen display this app's
    // name — same param name tender_ai/frontend's authService.js sends
    // (there sourced from a separate VITE_PROJECT_NAME; here it's the same
    // APP_NAME already driving the header/tab title, one source of truth).
    project_name: APP_NAME,
  },
};

export const userManager = new UserManager(settings);

export async function getUser() {
  if (DEV_MODE) {
    const raw = localStorage.getItem('dev_token');
    if (!raw) return null;
    const profile = decodeJwtPayload(raw) ?? {};
    if (profile.exp && profile.exp * 1000 < Date.now()) {
      localStorage.removeItem('dev_token');
      return null;
    }
    return { access_token: raw, profile };
  }
  return userManager.getUser();
}

export async function getAccessToken() {
  const user = await getUser();
  return user?.access_token ?? null;
}

export function getRole(user) {
  const roles = user?.profile?.roles ?? [];
  return roles.includes('admin') ? 'admin' : 'user';
}

export async function devLogin(userId) {
  const resp = await fetch('/api/auth/dev/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id: userId }),
  });
  if (!resp.ok) throw new Error('Dev login failed');
  const data = await resp.json();
  localStorage.setItem('dev_token', data.access_token);
  return data;
}

export async function getDevPersonas() {
  const resp = await fetch('/api/auth/dev/users');
  if (!resp.ok) return [];
  return resp.json();
}

export async function logout() {
  if (DEV_MODE) {
    localStorage.removeItem('dev_token');
    window.location.href = '/login';
    return;
  }
  await userManager.removeUser();
  await userManager.signoutRedirect();
}

export async function login() {
  if (DEV_MODE) return; // handled by the persona picker UI
  await userManager.signinRedirect();
}
