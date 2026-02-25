/**
 * React Query hooks for the Companion Portal API.
 */

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';

const BASE = '/api/companion';

function getCsrfToken(): string | null {
  const match = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : null;
}

function csrfHeaders(): Record<string, string> {
  const token = getCsrfToken();
  return token ? { 'X-CSRF-Token': token } : {};
}

async function fetchJson(url: string, opts?: RequestInit) {
  const res = await fetch(url, { credentials: 'same-origin', ...opts });
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    throw new Error(`${res.status}: ${body}`);
  }
  return res.json();
}

async function postJson(url: string, body: unknown) {
  return fetchJson(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...csrfHeaders() },
    body: JSON.stringify(body),
  });
}

async function putJson(url: string, body: unknown) {
  return fetchJson(url, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...csrfHeaders() },
    body: JSON.stringify(body),
  });
}

async function deleteReq(url: string) {
  return fetchJson(url, { method: 'DELETE', headers: { ...csrfHeaders() } });
}

// =============================================================================
// Client listing
// =============================================================================

export function useCompanionClients() {
  return useQuery({
    queryKey: ['companion', 'clients'],
    queryFn: () => fetchJson(`${BASE}/clients`),
    staleTime: 60_000,
  });
}

export function useCompanionClientOverview(orgId: string | undefined) {
  return useQuery({
    queryKey: ['companion', 'client', orgId, 'overview'],
    queryFn: () => fetchJson(`${BASE}/clients/${orgId}/overview`),
    enabled: !!orgId,
    staleTime: 30_000,
  });
}

// =============================================================================
// Stats
// =============================================================================

export function useCompanionStats() {
  return useQuery({
    queryKey: ['companion', 'stats'],
    queryFn: () => fetchJson(`${BASE}/stats`),
    staleTime: 60_000,
  });
}

// =============================================================================
// Notes
// =============================================================================

export function useCompanionNotes(orgId: string | undefined, moduleKey?: string) {
  const path = moduleKey
    ? `${BASE}/clients/${orgId}/notes/${moduleKey}`
    : `${BASE}/clients/${orgId}/notes`;
  return useQuery({
    queryKey: ['companion', 'notes', orgId, moduleKey],
    queryFn: () => fetchJson(path),
    enabled: !!orgId,
    staleTime: 15_000,
  });
}

export function useCreateNote(orgId: string, moduleKey: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (note: string) => postJson(`${BASE}/clients/${orgId}/notes/${moduleKey}`, { note }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['companion', 'notes', orgId] });
    },
  });
}

export function useUpdateNote() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ noteId, note }: { noteId: string; note: string }) =>
      putJson(`${BASE}/notes/${noteId}`, { note }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['companion', 'notes'] });
    },
  });
}

export function useDeleteNote() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (noteId: string) => deleteReq(`${BASE}/notes/${noteId}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['companion', 'notes'] });
    },
  });
}

// =============================================================================
// Activity
// =============================================================================

export function useCompanionActivity(orgId?: string, limit = 100) {
  const params = new URLSearchParams({ limit: String(limit) });
  if (orgId) params.set('org_id', orgId);
  return useQuery({
    queryKey: ['companion', 'activity', orgId, limit],
    queryFn: () => fetchJson(`${BASE}/activity?${params}`),
    staleTime: 30_000,
  });
}

export function useClientActivity(orgId: string | undefined, limit = 50) {
  return useQuery({
    queryKey: ['companion', 'client-activity', orgId, limit],
    queryFn: () => fetchJson(`${BASE}/clients/${orgId}/activity?limit=${limit}`),
    enabled: !!orgId,
    staleTime: 30_000,
  });
}
