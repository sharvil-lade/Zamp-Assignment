/**
 * The single place this app talks to the server.
 *
 * Everything goes through `request`, so authentication, error shape and the
 * base URL are decided once. The credential is a signed session token: `login`
 * exchanges the shared password for one, it is kept here, and every later
 * request sends it as `Authorization: Bearer`.
 *
 * The token is mirrored into localStorage so a page reload does not sign the
 * user out. That does mean a script running on this origin could read it —
 * the accepted trade-off of a bearer token in a browser, recorded in
 * docs/10-assumptions-and-scope.md.
 */

const BASE = import.meta.env.VITE_API_BASE ?? "";
const STORAGE_KEY = "vo_access_token";

let accessToken = read();

function read() {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;                      // private mode, or storage disabled
  }
}

export function setToken(token) {
  accessToken = token || null;
  try {
    if (accessToken) localStorage.setItem(STORAGE_KEY, accessToken);
    else localStorage.removeItem(STORAGE_KEY);
  } catch {
    // In-memory only. The session lasts until the tab is reloaded.
  }
}

export const getToken = () => accessToken;

export class ApiError extends Error {
  constructor(status, detail) {
    super(detail);
    this.status = status;
    this.detail = detail;
  }
}

async function request(method, path, { json, form, signal } = {}) {
  const init = {
    method,
    signal,
    headers: { Accept: "application/json" },
  };
  if (accessToken) init.headers.Authorization = `Bearer ${accessToken}`;
  if (json !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(json);
  } else if (form !== undefined) {
    init.body = form; // multipart; the browser sets the boundary itself
  }

  let response;
  try {
    response = await fetch(`${BASE}/api${path}`, init);
  } catch {
    throw new ApiError(0, "Could not reach the server. Is the backend running?");
  }

  const body = await response.json().catch(() => null);
  if (!response.ok) {
    // An expired or tampered token is worth nothing; drop it rather than
    // retrying every request with a credential the server has rejected.
    if (response.status === 401) setToken(null);
    throw new ApiError(response.status, detailOf(body) || response.statusText);
  }
  return body;
}

function detailOf(body) {
  if (!body) return null;
  if (typeof body.detail === "string") return body.detail;
  // FastAPI validation errors arrive as a list of objects.
  if (Array.isArray(body.detail)) {
    return body.detail.map((d) => d.msg).filter(Boolean).join("; ");
  }
  return null;
}

const get = (path, opts) => request("GET", path, opts);
const post = (path, opts) => request("POST", path, opts);
const put = (path, opts) => request("PUT", path, opts);
const del = (path, opts) => request("DELETE", path, opts);

export const api = {
  // --- session -------------------------------------------------------------
  session: () => get("/session"),

  login: async (password) => {
    const token = await post("/login", { json: { password } });
    setToken(token.access_token);
    return token;
  },

  /** Stateless tokens cannot be revoked, so signing out is forgetting it. */
  logout: () => setToken(null),

  // --- dashboard -----------------------------------------------------------
  dashboard: (status) => get(`/dashboard${status ? `?status=${status}` : ""}`),

  // --- onboarding cases ----------------------------------------------------
  createCase: (body) => post("/onboardings", { json: body }),
  caseDetail: (caseId) => get(`/onboardings/${caseId}`),
  reopenCase: (caseId) => post(`/onboardings/${caseId}/reopen`),

  // --- onboarding forms ----------------------------------------------------
  templates: () => get("/forms/templates"),
  createTemplate: (body) => post("/forms/templates", { json: body }),
  template: (id) => get(`/forms/templates/${id}`),
  updateTemplate: (id, body) => put(`/forms/templates/${id}`, { json: body }),
  duplicateTemplate: (id, name) =>
    post(`/forms/templates/${id}/duplicate`, { json: { name } }),
  deleteTemplate: (id) => del(`/forms/templates/${id}`),

  // --- runs ----------------------------------------------------------------
  run: (runId, signal) => get(`/runs/${runId}`, { signal }),

  // --- direct submission ---------------------------------------------------

  // --- vendor portal (public; the token is the only authorisation) ---------
  vendorForm: (token) => get(`/vendor/onboard/${token}`),
  vendorSubmit: (token, form) => post(`/vendor/onboard/${token}`, { form }),
};

export default api;
