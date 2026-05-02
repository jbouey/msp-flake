/**
 * Centralized kill-switch state hook.
 *
 * #76 closure 2026-05-02 (Steve adversarial-round followup of #64).
 *
 * Pre-fix: AdminSubstrateHealth's KillSwitchPanel polled every 30s
 * AND KillSwitchBanner (in app shell) polled every 60s — two
 * separate fetches for the same data. Wasteful + creates state-
 * divergence windows where the banner and panel disagree briefly.
 *
 * Now: single useKillSwitchState() hook, both consumers subscribe
 * via the same React Query queryKey. One fetch every 60s; cache
 * hits across components. Banner appears + disappears in lockstep
 * with the panel button state.
 */

import { useQuery } from '@tanstack/react-query';

export interface KillSwitchState {
  disabled: boolean;
  actor?: string;
  reason?: string;
  set_at?: string;
}

const POLL_INTERVAL_MS = 60_000;  // 60s — emergency-stop UX is OK with up-to-1-min lag
const STALE_TIME_MS = 30_000;     // 30s — cache fresh for half a poll cycle

async function fetchKillSwitchState(): Promise<KillSwitchState> {
  const res = await fetch('/api/admin/healing/global-state', {
    credentials: 'include',
  });
  if (!res.ok) {
    // Silent on 401/403 — the banner is non-critical UX. Returning
    // a "disabled: false" sentinel makes consumers fail-safe (they
    // render nothing rather than wrong-state).
    return { disabled: false };
  }
  return res.json();
}

export function useKillSwitchState() {
  return useQuery<KillSwitchState>({
    queryKey: ['admin', 'healing', 'global-state'],
    queryFn: fetchKillSwitchState,
    refetchInterval: POLL_INTERVAL_MS,
    staleTime: STALE_TIME_MS,
    // Don't retry on auth errors (banner is best-effort)
    retry: false,
  });
}
