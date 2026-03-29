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
