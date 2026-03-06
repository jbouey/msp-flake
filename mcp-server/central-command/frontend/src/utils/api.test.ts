import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// We need to test the internal _fetchWithBase logic via the exported API objects.
// The module uses module-level state (etagCache, responseCache) and calls global fetch.

// Mock window.location for 401 redirect tests
const locationMock = { href: '' };
Object.defineProperty(window, 'location', {
  value: locationMock,
  writable: true,
});

// Import after mocks are set up
import { fleetApi } from './api';

describe('fetchApi (via fleetApi)', () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    locationMock.href = '';
    document.cookie = '';
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it('successful GET returns parsed JSON', async () => {
    const mockData = [{ site_id: 'site-1', name: 'Test Site' }];

    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Headers(),
      json: () => Promise.resolve(mockData),
    });

    const result = await fleetApi.getFleet();

    expect(result).toEqual(mockData);
    expect(globalThis.fetch).toHaveBeenCalledOnce();

    const callArgs = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(callArgs[0]).toBe('/api/dashboard/fleet');
    expect(callArgs[1]).toMatchObject({
      credentials: 'same-origin',
    });
  });

  it('401 response redirects to /login and throws ApiError', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 401,
      headers: new Headers(),
      json: () => Promise.resolve({ detail: 'Unauthorized' }),
    });

    await expect(fleetApi.getFleet()).rejects.toThrow('Session expired');
    expect(locationMock.href).toBe('/login');
  });

  it('non-401 error response throws with detail message', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      headers: new Headers(),
      json: () => Promise.resolve({ detail: 'Internal server error' }),
    });

    await expect(fleetApi.getFleet()).rejects.toThrow('Internal server error');
  });

  it('network error (fetch rejects) propagates', async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'));

    await expect(fleetApi.getFleet()).rejects.toThrow('Failed to fetch');
  });

  it('sends If-None-Match header when ETag is cached', async () => {
    // First call: server returns ETag
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Headers({ etag: '"abc123"' }),
      json: () => Promise.resolve([{ site_id: 'site-1' }]),
    });

    await fleetApi.getFleet();

    // Second call: should include If-None-Match
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Headers({ etag: '"abc123"' }),
      json: () => Promise.resolve([{ site_id: 'site-1' }]),
    });

    await fleetApi.getFleet();

    const secondCallArgs = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    const headers = secondCallArgs[1].headers;
    expect(headers['If-None-Match']).toBe('"abc123"');
  });

  it('304 Not Modified returns cached response', async () => {
    const mockData = [{ site_id: 'site-1', name: 'Cached' }];

    // First call: populate cache
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Headers({ etag: '"etag304"' }),
      json: () => Promise.resolve(mockData),
    });

    const first = await fleetApi.getFleet();
    expect(first).toEqual(mockData);

    // Second call: 304 should return cached data
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: false, // 304 is not "ok" in terms of !response.ok, but status check comes first
      status: 304,
      headers: new Headers(),
      json: () => Promise.reject(new Error('Should not parse JSON for 304')),
    });

    const second = await fleetApi.getFleet();
    expect(second).toEqual(mockData);
  });

  it('abort error throws ApiError with isAborted flag', async () => {
    // Create a proper AbortError that matches the check in api.ts:
    // `error instanceof Error && error.name === 'AbortError'`
    const abortError = new Error('The operation was aborted');
    abortError.name = 'AbortError';

    globalThis.fetch = vi.fn().mockRejectedValue(abortError);

    try {
      await fleetApi.getFleet();
      expect.fail('Should have thrown');
    } catch (err: unknown) {
      const error = err as { message: string; isAborted?: boolean };
      expect(error.message).toBe('Request was cancelled or timed out');
      expect(error.isAborted).toBe(true);
    }
  });
});
