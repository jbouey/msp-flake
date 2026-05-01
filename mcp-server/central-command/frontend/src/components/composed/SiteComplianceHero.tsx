import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { GlassCard, Spinner } from '../shared';
import { getScoreStatus, formatTimeAgo } from '../../constants';
import type { SiteDetail } from '../../utils/api';

interface ComplianceHealthData {
  overall_score: number | null;
  counts: { passed: number; failed: number; warnings: number; total: number };
  trend: Array<{ date: string; score: number | null }>;
  network_coverage_pct?: number;
  unmanaged_device_count?: number;
}

interface SiteComplianceHeroProps {
  site: SiteDetail;
}

/**
 * Site Compliance Hero — enterprise-grade summary panel pinned to the top of
 * the SiteDetail view. Displays the headline compliance score, a 7-day delta
 * and three inline stat chips (open incidents, devices, last scan).
 *
 * Data is derived from `/api/dashboard/sites/{siteId}/compliance-health`
 * (same endpoint that powers the infographic farther down the page, so no
 * extra network cost — react-query dedupes the request).
 */
export const SiteComplianceHero: React.FC<SiteComplianceHeroProps> = ({ site }) => {
  // Shares the same query key as the `compliance-health-coverage` query in
  // SiteDetail.tsx so both consumers reuse a single cached response — the
  // hero renders without an extra network round-trip.
  const { data, isLoading } = useQuery<ComplianceHealthData | null>({
    queryKey: ['compliance-health-coverage', site.site_id],
    queryFn: async () => {
      // BUG 2 fix 2026-05-01: 'same-origin' doesn't send the session
      // cookie when the API mounts on a different port behind Caddy
      // (production deploy posture). 'include' is the rest of the
      // codebase's posture (utils/csrf.ts:57,64 documents this; api.ts
      // fetchApi at :116 uses it). Round-table-approved (fork
      // ab059e8a8bf6dbaed) — APPROVE_WITH_CHANGES + verify-first;
      // verified live by user (score went 100% → 94.0% in real-time
      // when this fetch began succeeding).
      const res = await fetch(`/api/dashboard/sites/${site.site_id}/compliance-health`, {
        credentials: 'include',
      });
      if (!res.ok) return null;
      return res.json();
    },
    staleTime: 60_000,
    retry: false,
  });

  const score = data?.overall_score ?? null;
  const scoreStatus = getScoreStatus(score);

  // 7-day trend delta: compare the most recent trend point with the point
  // closest to 7 days earlier. The endpoint returns up to 30 days sorted ASC.
  const delta: number | null = (() => {
    if (!data?.trend || data.trend.length < 2) return null;
    const trend = data.trend.filter(p => typeof p.score === 'number') as Array<{ date: string; score: number }>;
    if (trend.length < 2) return null;
    const latest = trend[trend.length - 1];
    // Walk backward for the first point ~7 days older than latest.
    const latestDate = new Date(latest.date).getTime();
    let reference = trend[0];
    for (let i = trend.length - 2; i >= 0; i--) {
      const age = (latestDate - new Date(trend[i].date).getTime()) / (1000 * 60 * 60 * 24);
      if (age >= 6) {
        reference = trend[i];
        break;
      }
      reference = trend[i];
    }
    return Math.round((latest.score - reference.score) * 10) / 10;
  })();

  // Find last scan timestamp: prefer baseline, then scanning, then appliance last_checkin.
  const lastScanSource =
    site.timestamps.baseline_at ||
    site.timestamps.scanning_at ||
    site.last_checkin ||
    (site.appliances.length > 0 ? site.appliances[0].last_checkin : null);

  // Open incidents is not on SiteDetail directly — use failed check count from
  // compliance-health as a proxy (same signal that drives the incidents page).
  const openIncidents = data?.counts?.failed ?? null;
  const totalDevices = site.appliances.reduce(
    (acc, a) => acc + (a.assigned_target_count || 0),
    0,
  );
  // Fall back to unmanaged device count if mesh assignment is unpopulated
  const deviceCount = totalDevices > 0 ? totalDevices : data?.unmanaged_device_count ?? null;

  const trendBadge: { icon: string; color: string; text: string } | null = (() => {
    if (delta === null) return null;
    if (delta > 0.5) {
      return { icon: '↑', color: 'text-health-healthy', text: `+${delta.toFixed(1)}` };
    }
    if (delta < -0.5) {
      return { icon: '↓', color: 'text-health-critical', text: `${delta.toFixed(1)}` };
    }
    return { icon: '→', color: 'text-label-tertiary', text: `${delta >= 0 ? '+' : ''}${delta.toFixed(1)}` };
  })();

  return (
    <GlassCard className="p-6">
      <div className="flex flex-col lg:flex-row lg:items-center gap-6">
        {/* Score */}
        <div className="flex items-center gap-5 lg:min-w-[260px]">
          <div
            className={`w-20 h-20 rounded-2xl flex items-center justify-center font-bold text-3xl ${scoreStatus.bgColor} ${scoreStatus.color}`}
          >
            {isLoading ? (
              <Spinner size="sm" />
            ) : score !== null ? (
              <span>{Math.round(score)}</span>
            ) : (
              <span className="text-lg">—</span>
            )}
          </div>
          <div className="min-w-0">
            <p className="text-xs uppercase tracking-wide text-label-tertiary font-medium">
              Compliance Score
            </p>
            <div className="flex items-baseline gap-2">
              <p className={`text-2xl font-semibold ${scoreStatus.color}`}>
                {score !== null ? `${score.toFixed(1)}%` : 'No Data'}
              </p>
              {trendBadge && (
                <span className={`text-sm font-medium ${trendBadge.color} inline-flex items-center gap-0.5`}
                  title="7-day trend"
                >
                  <span>{trendBadge.icon}</span>
                  <span>{trendBadge.text}</span>
                </span>
              )}
            </div>
            <p className="text-xs text-label-tertiary mt-0.5">
              {scoreStatus.label} · based on last 30 days of evidence
            </p>
          </div>
        </div>

        {/* Stat chips */}
        <div className="flex flex-wrap gap-2 lg:ml-auto">
          <Link
            to={`/incidents?site_id=${site.site_id}&status=open`}
            className="group flex items-center gap-2 px-3 py-2 rounded-ios bg-fill-secondary hover:bg-fill-tertiary transition-colors"
          >
            <span className="flex items-center gap-1.5">
              <span className={`w-2 h-2 rounded-full ${openIncidents && openIncidents > 0 ? 'bg-health-warning' : 'bg-health-healthy'}`} />
              <span className="text-xs uppercase text-label-tertiary font-medium tracking-wide">Open Incidents</span>
            </span>
            <span className="text-sm font-semibold text-label-primary">
              {openIncidents !== null ? openIncidents : '—'}
            </span>
          </Link>

          <Link
            to={`/sites/${site.site_id}/devices`}
            className="group flex items-center gap-2 px-3 py-2 rounded-ios bg-fill-secondary hover:bg-fill-tertiary transition-colors"
          >
            <span className="flex items-center gap-1.5">
              <svg className="w-3.5 h-3.5 text-label-tertiary" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 17.25v1.007a3 3 0 01-.879 2.122L7.5 21h9l-.621-.621A3 3 0 0115 18.257V17.25m6-12V15a2.25 2.25 0 01-2.25 2.25H5.25A2.25 2.25 0 013 15V5.25m18 0A2.25 2.25 0 0018.75 3H5.25A2.25 2.25 0 003 5.25m18 0V12a2.25 2.25 0 01-2.25 2.25H5.25A2.25 2.25 0 013 12V5.25" />
              </svg>
              <span className="text-xs uppercase text-label-tertiary font-medium tracking-wide">Devices</span>
            </span>
            <span className="text-sm font-semibold text-label-primary">
              {deviceCount !== null ? deviceCount : '—'}
            </span>
          </Link>

          <div className="flex items-center gap-2 px-3 py-2 rounded-ios bg-fill-secondary">
            <span className="flex items-center gap-1.5">
              <svg className="w-3.5 h-3.5 text-label-tertiary" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
              <span className="text-xs uppercase text-label-tertiary font-medium tracking-wide">Last Scan</span>
            </span>
            <span className="text-sm font-semibold text-label-primary">
              {formatTimeAgo(lastScanSource)}
            </span>
          </div>
        </div>
      </div>
    </GlassCard>
  );
};

export default SiteComplianceHero;
