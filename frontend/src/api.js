const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000').replace(/\/$/, '');
const REFRESH_KEY = 'chably.refresh-token';

let accessToken = null;
let refreshPromise = null;

export const session = {
  get accessToken() { return accessToken; },
  set(tokens) {
    accessToken = tokens.access_token || null;
    if (tokens.refresh_token) sessionStorage.setItem(REFRESH_KEY, tokens.refresh_token);
  },
  clear() { accessToken = null; sessionStorage.removeItem(REFRESH_KEY); },
  get refreshToken() { return sessionStorage.getItem(REFRESH_KEY); },
};

export class ApiError extends Error {
  constructor(message, status, errors = []) { super(message); this.status = status; this.errors = errors; }
}

async function refreshAccessToken() {
  if (refreshPromise) return refreshPromise;
  const refreshToken = session.refreshToken;
  if (!refreshToken) throw new ApiError('Your session has expired. Please sign in again.', 401);
  refreshPromise = fetch(`${API_BASE_URL}/api/v1/auth/refresh`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ refresh_token: refreshToken }),
  }).then(async (response) => {
    const envelope = await response.json().catch(() => ({}));
    if (!response.ok || !envelope.success) { session.clear(); throw new ApiError(envelope.message || 'Session refresh failed.', response.status, envelope.errors); }
    session.set(envelope.data);
    return envelope.data.access_token;
  }).finally(() => { refreshPromise = null; });
  return refreshPromise;
}

export async function api(path, options = {}, retry = true) {
  const headers = new Headers(options.headers || {});
  if (session.accessToken) headers.set('Authorization', `Bearer ${session.accessToken}`);
  if (options.body && !(options.body instanceof FormData) && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  const response = await fetch(`${API_BASE_URL}${path}`, { ...options, headers });
  const envelope = await response.json().catch(() => ({}));
  if (response.status === 401 && retry && !path.endsWith('/auth/refresh')) {
    await refreshAccessToken();
    return api(path, options, false);
  }
  if (!response.ok || envelope.success === false) throw new ApiError(envelope.message || `Request failed (${response.status})`, response.status, envelope.errors || []);
  return envelope.data;
}

export const endpoints = {
  register: (body) => api('/api/v1/auth/register', { method: 'POST', body: JSON.stringify(body) }),
  login: (body) => api('/api/v1/auth/login', { method: 'POST', body: JSON.stringify(body) }),
  googleLogin: () => api('/api/v1/auth/google/login'),
  googleExchange: (code) => api('/api/v1/auth/google/exchange', { method: 'POST', body: JSON.stringify({ code }) }),
  logout: () => api('/api/v1/auth/logout', { method: 'POST', body: JSON.stringify({ refresh_token: session.refreshToken }) }),
  me: () => api('/api/v1/auth/me'),
  dashboard: () => api('/api/v1/dashboard'),
  profile: () => api('/api/v1/profile/me'),
  profilePatch: (data) => api('/api/v1/profile/me', { method: 'PATCH', body: JSON.stringify({ data }) }),
  completeness: () => api('/api/v1/profile/me/completeness'),
  uploadResume: (file) => { const data = new FormData(); data.append('file', file); return api('/api/v1/resumes/upload', { method: 'POST', body: data }); },
  interviewStart: (userId) => api(`/api/v1/interview/${encodeURIComponent(userId)}/start`, { method: 'POST' }),
  interviewAnswer: (userId, body) => api(`/api/v1/interview/${encodeURIComponent(userId)}/answer`, { method: 'POST', body: JSON.stringify(body) }),
  interviewHistory: (userId) => api(`/api/v1/interview/${encodeURIComponent(userId)}/history`),
  recommendations: (userId) => api(`/api/v1/roles/${encodeURIComponent(userId)}/recommendations`),
  resumeAnalysis: (userId) => api(`/api/v1/resumes/${encodeURIComponent(userId)}/analysis`, { method: 'POST', body: JSON.stringify({}) }),
  resumeRewrite: (userId, target_role) => api(`/api/v1/resumes/${encodeURIComponent(userId)}/rewrite`, { method: 'POST', body: JSON.stringify({ target_role }) }),
  search: (body) => api('/api/v1/job-search', { method: 'POST', body: JSON.stringify(body) }),
  searchProgress: (id) => api(`/api/v1/job-search/${encodeURIComponent(id)}/progress`),
  searchResults: (id) => api(`/api/v1/job-search/${encodeURIComponent(id)}`),
  opportunities: () => api('/api/v1/opportunities?limit=50&offset=0'),
  savedJobs: () => api('/api/v1/saved-jobs?limit=50&offset=0'),
  applications: () => api('/api/v1/applications?limit=50&offset=0'),
  updateApplication: (id, userId, body) => api(`/api/v1/applications/${encodeURIComponent(id)}?user_id=${encodeURIComponent(userId)}`, { method: 'PATCH', body: JSON.stringify(body) }),
  googleConnect: () => api('/api/v1/integrations/google/connect', { method: 'POST' }),
  googleStatus: () => api('/api/v1/integrations/google/status'),
  googleSync: (userId) => api(`/api/v1/integrations/google/${encodeURIComponent(userId)}/sync`, { method: 'POST' }),
  outreach: () => api('/api/v1/outreach?limit=50&offset=0'),
  approveOutreach: (id, userId) => api(`/api/v1/outreach/${encodeURIComponent(id)}/approve?user_id=${encodeURIComponent(userId)}`, { method: 'POST' }),
  sendOutreach: (id, userId) => api(`/api/v1/outreach/${encodeURIComponent(id)}/send?user_id=${encodeURIComponent(userId)}`, { method: 'POST' }),
  accountExport: () => api('/api/v1/account/export'),
  deleteAccount: () => api('/api/v1/account', { method: 'DELETE' }),
  settings: () => api('/api/v1/settings'),
  patchSettings: (body) => api('/api/v1/settings', { method: 'PATCH', body: JSON.stringify(body) }),
};
