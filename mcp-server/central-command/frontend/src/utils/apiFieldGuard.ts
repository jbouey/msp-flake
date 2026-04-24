/**
 * apiFieldGuard — runtime detection of API contract drift.
 *
 * Session 210 (2026-04-24) Layer 3 of enterprise API reliability. Even with
 * Pydantic contract checks (Layer 6) + OpenAPI codegen (Layer 1), semantic
 * drift can still reach prod:
 *   - Enum value added (`status: 'recovering'`) that the generated type covers
 *     but the frontend switch doesn't handle
 *   - JSONB sub-field renamed silently
 *   - Field value format changed (ISO 8601 → epoch seconds)
 *
 * This module gives callers a single primitive:
 *
 *   requireField<T>(obj, path, ctx) → T | undefined
 *
 * When the field is undefined AND the caller is reading a field it expected,
 * we emit a `FIELD_UNDEFINED` telemetry event. Aggregated server-side, a
 * spike fires the `frontend_field_undefined_spike` substrate invariant
 * within 60 seconds.
 *
 * Design:
 *   - Zero overhead when field IS present — just a property access.
 *   - Sampling to avoid log flood: once per {endpoint,field} per 60s.
 *   - `sendBeacon` for reliability during page-unload; falls back to fetch.
 *   - Never throws — telemetry failure must never break the UI.
 *
 * Usage:
 *   import { requireField } from '@/utils/apiFieldGuard';
 *
 *   const tier = requireField(site, 'tier', { endpoint: '/api/portal/site/{id}' });
 *   // tier is typed and emits telemetry if the field vanishes in prod
 */

interface FieldContext {
  /** Endpoint that returned the object, e.g. "/api/portal/site/{id}". */
  endpoint: string;
  /**
   * Optional extra context for triage (e.g. component name). Keep small —
   * the telemetry payload is capped.
   */
  component?: string;
}

// Dedup window: fire at most once per (endpoint, field) per RATE_WINDOW_MS.
// Prevents N list-item renders from generating N telemetry events.
const RATE_WINDOW_MS = 60_000;
const recentlySeen = new Map<string, number>();

function shouldEmit(key: string): boolean {
  const now = Date.now();
  const prev = recentlySeen.get(key);
  if (prev !== undefined && now - prev < RATE_WINDOW_MS) {
    return false;
  }
  recentlySeen.set(key, now);
  // Soft cap on the dedup map — unbounded growth would be a memory leak
  // on long-lived dashboards.
  if (recentlySeen.size > 500) {
    const oldest = [...recentlySeen.entries()].sort((a, b) => a[1] - b[1])[0];
    if (oldest) recentlySeen.delete(oldest[0]);
  }
  return true;
}

/**
 * Read the CSRF token from the csrf_token cookie. Required for non-GET
 * requests to cookie-session endpoints. Returns "" if cookie absent —
 * the backend will reject the request, which is fine; telemetry is
 * best-effort.
 */
function getCsrfTokenCookie(): string {
  if (typeof document === 'undefined') return '';
  const match = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : '';
}

/**
 * Emit a FIELD_UNDEFINED event to the backend telemetry endpoint.
 * Never throws. Best-effort delivery via fetch+keepalive (survives most
 * page-unload scenarios; sendBeacon was considered but rejected because
 * it cannot set the X-CSRF-Token header this endpoint requires).
 */
function emitFieldUndefined(payload: {
  endpoint: string;
  field: string;
  component?: string;
  observed_type: string;
}): void {
  try {
    if (typeof fetch === 'undefined') return;
    const body = JSON.stringify({
      kind: 'FIELD_UNDEFINED',
      endpoint: payload.endpoint,
      field: payload.field,
      component: payload.component ?? 'unknown',
      observed_type: payload.observed_type,
      page: typeof window !== 'undefined' ? window.location.pathname : '',
      ts: new Date().toISOString(),
    });
    void fetch('/api/admin/telemetry/client-field-undefined', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': getCsrfTokenCookie(),
      },
      body,
      credentials: 'same-origin',
      keepalive: true,
    }).catch(() => undefined);
  } catch {
    // Never throw from telemetry.
  }
}

/**
 * Read a field on an API response object. If the field is undefined —
 * meaning the backend's contract for this endpoint has drifted relative
 * to what the frontend expected — emit a FIELD_UNDEFINED telemetry event.
 *
 * Returns the field value (possibly undefined) so callers control the
 * fallback behavior (render a placeholder, skip the component, etc.).
 *
 * @param obj The API response object (usually a Pydantic model JSON).
 * @param field The property name expected on that object.
 * @param ctx Context for triage (endpoint path, component name).
 */
export function requireField<T, K extends keyof T>(
  obj: T | null | undefined,
  field: K,
  ctx: FieldContext,
): T[K] | undefined {
  if (obj == null) {
    // The whole response is missing — a different failure class
    // (404, 500, aborted). Not this guard's concern.
    return undefined;
  }
  const value = obj[field];
  if (value !== undefined) {
    return value;
  }
  const key = `${ctx.endpoint}::${String(field)}`;
  if (!shouldEmit(key)) {
    return undefined;
  }
  emitFieldUndefined({
    endpoint: ctx.endpoint,
    field: String(field),
    component: ctx.component,
    observed_type: typeof obj,
  });
  return undefined;
}

/**
 * Test-only: reset the dedup window. Call from vitest setup between cases.
 */
export function _resetFieldGuardForTests(): void {
  recentlySeen.clear();
}
