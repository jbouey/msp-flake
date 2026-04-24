/**
 * apiFieldGuard tests — Session 210 Layer 3.
 *
 * These tests enforce:
 *   - Fields that ARE defined return without telemetry (zero overhead path)
 *   - Undefined fields trigger a POST to the telemetry endpoint
 *   - Repeat undefined reads of the same (endpoint, field) within 60s are
 *     deduped (one event, not N)
 *   - Telemetry failure never throws into the caller
 *   - null objects pass through (different failure class)
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { requireField, _resetFieldGuardForTests } from './apiFieldGuard';

interface SampleResponse {
  tier: string;
  name?: string;
  config?: { primary_color: string };
}

describe('apiFieldGuard.requireField', () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    _resetFieldGuardForTests();
    fetchMock = vi.fn().mockResolvedValue({ ok: true });
    vi.stubGlobal('fetch', fetchMock);
    // jsdom provides document; ensure csrf cookie read works.
    document.cookie = 'csrf_token=test-token';
  });

  it('returns the field value when present, no telemetry', () => {
    const obj: SampleResponse = { tier: 'pro', name: 'ACME' };
    const result = requireField(obj, 'tier', { endpoint: '/api/site' });
    expect(result).toBe('pro');
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('returns undefined AND emits telemetry when field is missing', () => {
    const obj = { name: 'ACME' } as unknown as SampleResponse;
    const result = requireField(obj, 'tier', { endpoint: '/api/site' });
    expect(result).toBeUndefined();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/admin/telemetry/client-field-undefined');
    expect(init.method).toBe('POST');
    const body = JSON.parse(init.body);
    expect(body.kind).toBe('FIELD_UNDEFINED');
    expect(body.endpoint).toBe('/api/site');
    expect(body.field).toBe('tier');
    expect(body.observed_type).toBe('object');
    // CSRF header present
    expect(init.headers['X-CSRF-Token']).toBe('test-token');
  });

  it('dedupes repeat calls for the same (endpoint, field) within the window', () => {
    const obj = { name: 'ACME' } as unknown as SampleResponse;
    requireField(obj, 'tier', { endpoint: '/api/site' });
    requireField(obj, 'tier', { endpoint: '/api/site' });
    requireField(obj, 'tier', { endpoint: '/api/site' });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('does NOT dedupe across different endpoints', () => {
    const obj = { name: 'ACME' } as unknown as SampleResponse;
    requireField(obj, 'tier', { endpoint: '/api/site/A' });
    requireField(obj, 'tier', { endpoint: '/api/site/B' });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('does NOT dedupe across different fields on the same endpoint', () => {
    const obj = { name: 'ACME' } as unknown as SampleResponse;
    requireField(obj, 'tier', { endpoint: '/api/site' });
    requireField(obj, 'config', { endpoint: '/api/site' });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('passes through when the whole response is null (different failure class)', () => {
    const result = requireField(null, 'tier', { endpoint: '/api/site' });
    expect(result).toBeUndefined();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('passes through when the whole response is undefined', () => {
    const result = requireField(undefined, 'tier', { endpoint: '/api/site' });
    expect(result).toBeUndefined();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('does not throw when fetch rejects', async () => {
    fetchMock.mockRejectedValue(new Error('network down'));
    const obj = { name: 'ACME' } as unknown as SampleResponse;
    // Should not throw even though fetch rejects.
    expect(() => requireField(obj, 'tier', { endpoint: '/api/site' })).not.toThrow();
    // Wait a tick for the rejection to settle.
    await new Promise((r) => setTimeout(r, 0));
  });

  it('includes optional component name in payload', () => {
    const obj = { name: 'ACME' } as unknown as SampleResponse;
    requireField(obj, 'tier', { endpoint: '/api/site', component: 'PortalScorecard' });
    const body = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(body.component).toBe('PortalScorecard');
  });
});
