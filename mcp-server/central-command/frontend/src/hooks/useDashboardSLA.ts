/**
 * Shared hook for the platform SLA strip data.
 *
 * Two consumers share this query under the same React Query cache key:
 *   - `DashboardSLAStrip` renders the healing / evidence / fleet pill row.
 *   - `Dashboard` surfaces OTS anchor age + MFA coverage in the System
 *     Health card.
 *
 * Before this hook, each component defined its own `useQuery` at the same
 * queryKey with a different queryFn. React Query dedupes by key and uses
 * whichever observer's queryFn was attached first; the loser's fetcher
 * became dead code, with visible consequences when the primary endpoint
 * transiently 5xx'd (one path had a `/stats` fallback, the other threw
 * and cached the error for every consumer).
 *
 * Single hook → single fetcher → deterministic behavior.
 */

import { useQuery, type UseQueryResult } from '@tanstack/react-query';

/**
 * Superset of the two previous local types. Includes `mfa_coverage_pct`
 * and `mfa_target` since Dashboard reads them; the fallback path sets
 * them to `null` / 100 because `/api/dashboard/stats` doesn't carry them.
 */
export interface DashboardSLAData {
  healing_rate_24h: number | null;
  healing_target: number;
  ots_anchor_age_minutes: number | null;
  ots_target_minutes: number;
  online_appliances_pct: number | null;
  fleet_target: number;
  mfa_coverage_pct: number | null;
  mfa_target: number;
  computed_at?: string | null;
}

export const DASHBOARD_SLA_QUERY_KEY = ['dashboard-sla-strip'] as const;

/**
 * Fetch the SLA strip data. Tries the dedicated endpoint first, falls
 * back to computing a partial payload from `/api/dashboard/stats`. The
 * fallback is permissive by design — fields that can't be derived from
 * `/stats` return `null` and the UI renders "—".
 */
export async function fetchDashboardSLA(): Promise<DashboardSLAData> {
  try {
    const res = await fetch('/api/dashboard/sla-strip', { credentials: 'same-origin' });
    if (res.ok) return (await res.json()) as DashboardSLAData;
  } catch {
    // fall through to stats fallback
  }

  const statsRes = await fetch('/api/dashboard/stats', { credentials: 'same-origin' });
  if (!statsRes.ok) {
    throw new Error(`stats fallback failed: ${statsRes.status}`);
  }
  const stats = await statsRes.json();
  const onlinePct =
    stats.total_appliances > 0
      ? (stats.online_appliances / stats.total_appliances) * 100
      : null;
  return {
    healing_rate_24h: stats.l1_resolution_rate ?? null,
    healing_target: 85,
    ots_anchor_age_minutes: null,
    ots_target_minutes: 120,
    online_appliances_pct: onlinePct,
    fleet_target: 95,
    mfa_coverage_pct: null,
    mfa_target: 100,
    computed_at: stats.computed_at ?? null,
  };
}

export function useDashboardSLA(): UseQueryResult<DashboardSLAData> {
  return useQuery<DashboardSLAData>({
    queryKey: DASHBOARD_SLA_QUERY_KEY,
    queryFn: fetchDashboardSLA,
    refetchInterval: 5 * 60_000,
    staleTime: 60_000,
    retry: false,
  });
}
