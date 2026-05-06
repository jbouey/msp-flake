/**
 * portalFetch — canonical client/partner portal mutation helpers.
 *
 * Round-table 32 (2026-05-05) closure of Maya P2 from the Session 217
 * final sweep. Pre-fix, 4 modals + 1 download handler each defined
 * their own postJson/getJson with identical credentials:'include' +
 * csrfHeaders + JSON parsing logic. The CLAUDE.md "Frontend mutation
 * CSRF rule" was satisfied each time but only because each surface
 * remembered to include both credentials AND the X-CSRF-Token header.
 * One forgotten import and the gate would reject the mutation 403.
 *
 * This module is the single source of truth. Anti-regression CI gate:
 * `tests/test_portal_fetch_canonical.py` fails if a portal modal
 * declares its own `postJson`/`getJson` instead of importing here.
 *
 * Exposed primitives:
 *   getJson<T>(url) -> T | null   (404 → null; other non-2xx → throw)
 *   postJson<T>(url, body) -> T
 *   patchJson<T>(url, body) -> T
 *   deleteJson<T>(url, body?) -> T
 *   fetchBlob(url) -> Response    (caller reads .blob() / handles status)
 *
 * Errors raised by these helpers carry .status + .detail so callers
 * can branch on auth failure (401/403), rate limit (429), validation
 * (400/422), and produce actionable copy without re-implementing
 * the parse-JSON-error-detail dance.
 */
import { csrfHeaders } from './csrf';

export interface PortalFetchError extends Error {
  status?: number;
  detail?: string;
}

function _err(status: number, text: string): PortalFetchError {
  let parsed: { detail?: string } | undefined;
  try {
    parsed = JSON.parse(text);
  } catch {
    // not JSON
  }
  const err = new Error(
    parsed?.detail || `${status} ${text || 'request failed'}`,
  ) as PortalFetchError;
  err.status = status;
  err.detail = parsed?.detail;
  return err;
}

async function _request<T>(
  url: string,
  options: { method?: string; body?: unknown } = {},
): Promise<T> {
  const method = options.method || 'GET';
  const headers: Record<string, string> = {};
  if (method !== 'GET' && method !== 'HEAD') {
    headers['Content-Type'] = 'application/json';
    Object.assign(headers, csrfHeaders());
  }
  const init: RequestInit = {
    method,
    credentials: 'include',
    headers,
  };
  if (options.body !== undefined && method !== 'GET' && method !== 'HEAD') {
    init.body = JSON.stringify(options.body);
  }
  const res = await fetch(url, init);
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw _err(res.status, text);
  }
  return res.json() as Promise<T>;
}

/** GET; returns null on 404 (use for "is there an active resource?" probes). */
export async function getJson<T>(url: string): Promise<T | null> {
  try {
    return await _request<T>(url, { method: 'GET' });
  } catch (e) {
    const err = e as PortalFetchError;
    if (err.status === 404) return null;
    throw e;
  }
}

export async function postJson<T>(url: string, body?: unknown): Promise<T> {
  return _request<T>(url, { method: 'POST', body });
}

export async function patchJson<T>(url: string, body?: unknown): Promise<T> {
  return _request<T>(url, { method: 'PATCH', body });
}

export async function deleteJson<T>(url: string, body?: unknown): Promise<T> {
  return _request<T>(url, { method: 'DELETE', body });
}

/**
 * Returns the raw Response on 2xx so the caller can `.blob()` (binary
 * downloads like the auditor kit). On non-2xx, throws PortalFetchError
 * with .status + .detail so callers branch on 401/429/etc with their
 * own actionable copy.
 */
export async function fetchBlob(url: string): Promise<Response> {
  const res = await fetch(url, { credentials: 'include' });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    const err = _err(res.status, text);
    // Capture Retry-After for 429 rate-limit copy
    const retry = res.headers.get('Retry-After');
    if (retry) {
      (err as PortalFetchError & { retryAfter?: string }).retryAfter = retry;
    }
    throw err;
  }
  return res;
}
