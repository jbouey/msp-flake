/**
 * CSRF token utilities for cookie-authenticated portals.
 *
 * The CSRF middleware uses a double-submit cookie pattern:
 * - Server sets a `csrf_token` cookie on responses
 * - Client must echo it back in an `X-CSRF-Token` header on mutations
 *
 * All session-authenticated portals (client, partner, companion) must
 * include this header on POST/PUT/DELETE/PATCH requests.
 */

/** Read the CSRF token from the csrf_token cookie. */
export function getCsrfToken(): string | null {
  const match = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : null;
}

/**
 * Read the CSRF token from the csrf_token cookie, returning an empty string
 * when the cookie is absent. Used by call sites that embed the token directly
 * into a header map (where `string | null` would be an invalid value).
 */
export function getCsrfTokenOrEmpty(): string {
  const match = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : '';
}

/** Return headers object containing X-CSRF-Token if available. */
export function csrfHeaders(): Record<string, string> {
  const token = getCsrfToken();
  return token ? { 'X-CSRF-Token': token } : {};
}

/**
 * Merge CSRF headers into an existing headers object.
 * Use for fetch() calls: `headers: { 'Content-Type': 'application/json', ...csrfHeaders() }`
 */
export function withCsrf(headers: Record<string, string> = {}): Record<string, string> {
  return { ...headers, ...csrfHeaders() };
}

/**
 * Canonical authed-headers builder for partner mutations (Session 212
 * round-table P2 #8, 2026-04-28). The 7-file CSRF-additive sweep on
 * 2026-04-28 (commits 0c81fef6, efe413cf, 7c3e6551) replaced the
 * apiKey-precedence-ternary pattern with cookie+CSRF unconditional
 * + X-API-Key additive. This helper centralizes that shape so future
 * callers can't reintroduce the bug class:
 *   - cookie credentials: always (caller passes credentials:'include')
 *   - X-CSRF-Token:       always when cookie present
 *   - X-API-Key:          additive when apiKey is non-empty
 *   - Content-Type:       optional, additive when body is JSON
 *
 * Usage:
 *   const opts: RequestInit = {
 *     method: 'POST',
 *     credentials: 'include',
 *     headers: buildAuthedHeaders({ apiKey, json: true }),
 *     body: JSON.stringify(payload),
 *   };
 *
 * Or for GETs:
 *   fetch(url, {
 *     credentials: 'include',
 *     headers: buildAuthedHeaders({ apiKey }),
 *   });
 */
export interface AuthedHeadersOptions {
  /** Partner X-API-Key. Empty/undefined → header omitted. */
  apiKey?: string | null;
  /** Set Content-Type: application/json when true. */
  json?: boolean;
  /** Additional headers merged in last (caller-priority). */
  extra?: Record<string, string>;
}

export function buildAuthedHeaders(opts: AuthedHeadersOptions = {}): Record<string, string> {
  const { apiKey, json, extra } = opts;
  const out: Record<string, string> = {
    ...(json ? { 'Content-Type': 'application/json' } : {}),
    ...csrfHeaders(),
    ...(apiKey ? { 'X-API-Key': apiKey } : {}),
    ...(extra ?? {}),
  };
  return out;
}
