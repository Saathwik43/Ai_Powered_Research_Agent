/**
 * One API client (1.13).
 *
 * `import.meta.env.VITE_API_URL || 'http://localhost:8000'` was written out at
 * roughly thirty call sites across six pages. That is not only repetition: it
 * meant the origin, the error shape and the "did this actually succeed" check
 * were each decided independently, so some callers surfaced `detail` from the
 * body, some surfaced a generic string, and some treated a 429 as a network
 * error. Everything that talks to the backend goes through here now.
 *
 * `authFetch` (AuthContext) still owns the Authorization header, because only
 * it can see the token. This module owns the URL and the response contract.
 */

export const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000';

/** An HTTP response the caller cannot use. Carries the status so a page can
 *  distinguish "rate limited" from "wrong input" without re-reading the body. */
export class ApiError extends Error {
  constructor(message, { status = 0, body = null } = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.body = body;
  }

  get isRateLimited() {
    return this.status === 429;
  }

  get isUnavailable() {
    return this.status === 503;
  }
}

/**
 * Absolute URL for an API path, with query parameters.
 *
 * Null/undefined/'' parameters are dropped rather than sent as the string
 * "undefined", which is what hand-built template literals did whenever an
 * optional filter was unset.
 */
export function apiUrl(path, params) {
  const base = path.startsWith('http')
    ? path
    : `${API_BASE}${path.startsWith('/') ? '' : '/'}${path}`;
  if (!params) return base;

  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue;
    search.append(key, String(value));
  }
  const query = search.toString();
  return query ? `${base}${base.includes('?') ? '&' : '?'}${query}` : base;
}

/** The most useful message the server gave us, falling back to the status. */
export async function readError(response) {
  let body = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }
  const detail = body?.detail;
  const message =
    (typeof detail === 'string' && detail) ||
    detail?.message ||
    body?.message ||
    `Request failed (${response.status})`;
  return new ApiError(message, { status: response.status, body });
}

/**
 * Bind the client to a signed-in session.
 *
 * Returns methods that resolve to parsed JSON and throw {@link ApiError} on a
 * non-2xx, so a page never has to remember to check `res.ok`. `raw` is the
 * escape hatch for the responses that are not JSON — the LaTeX zip — and for
 * SSE, which needs the body stream rather than a parsed object.
 */
export function createApi(authFetch) {
  const request = async (path, { params, signal, ...init } = {}) => {
    const response = await authFetch(apiUrl(path, params), { signal, ...init });
    if (!response.ok) throw await readError(response);
    if (response.status === 204) return null;
    return response.json();
  };

  return {
    get: (path, options) => request(path, { method: 'GET', ...options }),
    post: (path, body, options) =>
      request(path, { method: 'POST', body: JSON.stringify(body ?? {}), ...options }),
    patch: (path, body, options) =>
      request(path, { method: 'PATCH', body: JSON.stringify(body ?? {}), ...options }),
    del: (path, options) => request(path, { method: 'DELETE', ...options }),
    /** Unparsed Response — for streams, downloads, and per-status handling. */
    raw: (path, { params, ...init } = {}) => authFetch(apiUrl(path, params), init),
  };
}
