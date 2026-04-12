import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQueryClient, useQuery } from '@tanstack/react-query';
import { GlassCard, InfoTip, Spinner, DashboardErrorBoundary } from '../components/shared';
import { MetricCard, FloatingActionButton, Sparkline, type FloatingAction } from '../components/composed';
import { HealthGauge } from '../components/fleet';
import {
  IncidentTrendChart,
  FleetHealthMatrix,
  AttentionPanel,
  ResolutionBreakdown,
  TopIncidentTypes,
  DashboardSLAStrip,
} from '../components/command-center';
import { IncidentFeed } from '../components/incidents';
import { useGlobalStats, useStatsDeltas, useLearningStatus, useIncidents, useFlywheelIntelligence, useInstallReports } from '../hooks';
import { METRIC_TOOLTIPS, getScoreStatus, formatTimeAgo } from '../constants';

// Shape of the /api/dashboard/sla-strip response — shared between
// DashboardSLAStrip (which renders the pills) and this page (which
// surfaces OTS/MFA in System Health).
interface DashboardSLAData {
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

interface KPITrendsData {
  days: number;
  series: {
    incidents_24h: number[];
    l1_rate: number[];
    clients: number[];
  };
  computed_at?: string | null;
}

const INCIDENT_FEED_LIMIT = 8;
const L1_AUTOHEAL_TARGET = 85;
// +20% week-over-week on a 24h incident count is the red-alert threshold —
// a +122 change from the screenshot is well past this line and should tint
// the card critically instead of sitting in neutral gray.
const INCIDENT_SPIKE_THRESHOLD = 20;

export const Dashboard: React.FC = () => {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  // "Active Threats" banner can be dismissed for the current session —
  // operator should not have to stare at it while they work the alert.
  const [threatBannerDismissed, setThreatBannerDismissed] = useState(false);

  const { data: stats, isLoading: statsLoading, refetch: refetchStats } = useGlobalStats();
  const { data: deltas, refetch: refetchDeltas } = useStatsDeltas();
  const { data: learning, isLoading: learningLoading } = useLearningStatus();
  const { data: flywheel, isLoading: flywheelLoading } = useFlywheelIntelligence();
  const { data: installs, isLoading: installsLoading } = useInstallReports({ limit: 10 });
  const {
    data: incidents,
    isLoading: incidentsLoading,
    error: incidentsError,
  } = useIncidents({ limit: INCIDENT_FEED_LIMIT });

  // Dedicated SLA strip query — also used to surface OTS delay + MFA coverage
  // in the System Health card without adding more fields to the generic
  // /stats endpoint (which is shared by many pages).
  const { data: slaData } = useQuery<DashboardSLAData>({
    queryKey: ['dashboard-sla-strip'],
    queryFn: async () => {
      const res = await fetch('/api/dashboard/sla-strip', { credentials: 'same-origin' });
      if (!res.ok) throw new Error(`SLA strip failed: ${res.status}`);
      return res.json();
    },
    refetchInterval: 5 * 60_000,
    staleTime: 60_000,
    retry: false,
  });

  // 14-day KPI trends for the three secondary KPI card sparklines.
  // Single query keeps the dashboard chatty-query count down to 5 total.
  const { data: trendsData } = useQuery<KPITrendsData>({
    queryKey: ['dashboard-kpi-trends', 14],
    queryFn: async () => {
      const res = await fetch('/api/dashboard/kpi-trends?days=14', { credentials: 'same-origin' });
      if (!res.ok) throw new Error(`KPI trends failed: ${res.status}`);
      return res.json();
    },
    refetchInterval: 10 * 60_000,
    staleTime: 5 * 60_000,
    retry: false,
  });

  const activeThreats = stats?.active_threats ?? 0;
  const complianceScore = stats?.avg_compliance_score ?? 0;
  const scoreStatus = getScoreStatus(statsLoading ? null : complianceScore);

  // Red-tint the incidents-24h card when the WoW delta is a meaningful spike.
  // This turns a calm-looking MetricCard into an alarm without requiring the
  // user to read the subtle arrow character.
  const incidentsDelta = deltas?.incidents_24h_delta ?? 0;
  const incidentsSpike = incidentsDelta >= INCIDENT_SPIKE_THRESHOLD;

  // L1 auto-heal below target is a silent failure — the arrow may say "+18%"
  // and still be below the contractual 85%. Tint the card when below target.
  const l1Rate = stats?.l1_resolution_rate ?? 0;
  const l1BelowTarget = !statsLoading && l1Rate < L1_AUTOHEAL_TARGET;

  // Manual refresh button: user can force-refetch the two hottest queries
  // without waiting for the 30/60s interval. Not every query — we don't want
  // to re-pull the 30-day incident history on every click.
  const handleRefresh = () => {
    refetchStats();
    refetchDeltas();
    queryClient.invalidateQueries({ queryKey: ['incidents-list'] });
    queryClient.invalidateQueries({ queryKey: ['attention-required'] });
  };

  const showEmptyState = !statsLoading && (stats?.total_clients ?? 0) === 0;

  // Quick-action fan-out — the most common ops tasks an admin reaches for
  // from the dashboard without having to nav into a specific site first.
  const fabActions: FloatingAction[] = [
    {
      label: 'Refresh Dashboard',
      tone: 'primary',
      icon: (
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
        </svg>
      ),
      onClick: handleRefresh,
    },
    {
      label: 'Open Incidents',
      icon: (
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
        </svg>
      ),
      onClick: () => navigate('/incidents'),
    },
    {
      label: 'Open Learning',
      icon: (
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
        </svg>
      ),
      onClick: () => navigate('/learning'),
    },
    {
      label: 'Open Sites',
      icon: (
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1" />
        </svg>
      ),
      onClick: () => navigate('/sites'),
    },
  ];

  return (
    <DashboardErrorBoundary section="Dashboard">
    <div className="space-y-5 page-enter">
      {/* Active Threat Banner — dismissable */}
      {activeThreats > 0 && !threatBannerDismissed && (
        <div className="bg-health-critical text-white px-4 py-3 rounded-ios-md flex items-center gap-3">
          <svg
            className="w-5 h-5 flex-shrink-0"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
            aria-hidden
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
            />
          </svg>
          <span className="font-semibold">
            {activeThreats} active threat{activeThreats > 1 ? 's' : ''} detected
          </span>
          <button
            onClick={() => navigate('/incidents?severity=critical')}
            className="ml-auto text-sm underline hover:no-underline"
          >
            View Details
          </button>
          <button
            onClick={() => setThreatBannerDismissed(true)}
            aria-label="Dismiss threat banner"
            className="p-1 rounded hover:bg-white/20"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
      )}

      {/* Empty state — no sites provisioned yet */}
      {showEmptyState && (
        <GlassCard className="text-center py-12">
          <div className="w-16 h-16 rounded-full bg-accent-primary/10 flex items-center justify-center mx-auto mb-4">
            <svg className="w-8 h-8 text-accent-primary" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1" />
            </svg>
          </div>
          <h2 className="text-xl font-semibold text-label-primary mb-2">Welcome to OsirisCare</h2>
          <p className="text-sm text-label-secondary max-w-md mx-auto mb-5">
            You have no sites provisioned yet. Provision your first site to start seeing
            compliance data, incidents, and healing metrics here.
          </p>
          <button
            onClick={() => navigate('/sites?action=create')}
            className="px-5 py-2.5 rounded-ios bg-accent-primary text-white text-sm font-medium hover:bg-accent-primary/90"
          >
            Provision first site
          </button>
        </GlassCard>
      )}

      {/* Data freshness strip — shows last-computed timestamp + manual refresh + export */}
      {!showEmptyState && (
        <div className="flex items-center justify-end gap-3 text-xs text-label-tertiary dashboard-toolbar-hide-on-print">
          <span>
            Stats as of{' '}
            <span className="text-label-secondary font-medium">
              {formatTimeAgo(stats?.computed_at)}
            </span>
          </span>
          <button
            onClick={handleRefresh}
            className="flex items-center gap-1.5 px-2.5 py-1 rounded-ios bg-fill-secondary hover:bg-fill-tertiary transition-colors"
            aria-label="Refresh dashboard data"
          >
            {statsLoading ? (
              <Spinner size="sm" />
            ) : (
              <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
                />
              </svg>
            )}
            Refresh
          </button>
          <button
            onClick={() => window.print()}
            className="flex items-center gap-1.5 px-2.5 py-1 rounded-ios bg-fill-secondary hover:bg-fill-tertiary transition-colors"
            aria-label="Export dashboard snapshot as PDF"
            title="Print or save as PDF"
          >
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} aria-hidden>
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M17 17h2a2 2 0 002-2v-4a2 2 0 00-2-2H5a2 2 0 00-2 2v4a2 2 0 002 2h2m2 4h6a2 2 0 002-2v-4a2 2 0 00-2-2H9a2 2 0 00-2 2v4a2 2 0 002 2zm8-12V5a2 2 0 00-2-2H9a2 2 0 00-2 2v4h10z"
              />
            </svg>
            Export PDF
          </button>
        </div>
      )}

      {/* Hero compliance score + 3 secondary KPIs */}
      {!showEmptyState && (
        <div className="grid grid-cols-1 lg:grid-cols-4 gap-4 stagger-list">
          {/* Hero compliance gauge */}
          <GlassCard className="lg:col-span-1 flex flex-col items-center justify-center py-3">
            {statsLoading ? (
              <div className="skeleton w-[128px] h-[128px] rounded-full" />
            ) : (
              <>
                <HealthGauge score={complianceScore} size="xl" showLabel={false} showPercentage={false} />
                <p className={`text-3xl font-bold mt-3 tabular-nums ${scoreStatus.color}`}>
                  {complianceScore}%
                </p>
                <p className="text-xs text-label-tertiary mt-1">Fleet Health</p>
                {stats && (stats.total_appliances || 0) > 0 && (
                  <p className="text-[10px] text-label-tertiary mt-0.5">
                    across {stats.online_appliances}/{stats.total_appliances} appliances
                  </p>
                )}
                {deltas?.compliance_delta !== undefined && deltas?.compliance_delta !== null && deltas.compliance_delta !== 0 && (
                  <div className={`flex items-center gap-1 mt-2 text-xs font-medium ${
                    deltas.compliance_delta > 0 ? 'text-health-healthy' : 'text-health-critical'
                  }`}>
                    <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                      <path strokeLinecap="round" strokeLinejoin="round" d={
                        deltas.compliance_delta > 0 ? 'M5 15l7-7 7 7' : 'M19 9l-7 7-7-7'
                      } />
                    </svg>
                    <span className="tabular-nums">{deltas.compliance_delta > 0 ? '+' : ''}{deltas.compliance_delta}%</span>
                    <span className="text-label-tertiary font-normal">vs 24h ago</span>
                  </div>
                )}
              </>
            )}
          </GlassCard>

          {/* 3 secondary KPIs */}
          <div className="lg:col-span-3 grid grid-cols-1 sm:grid-cols-3 gap-3">
            <MetricCard
              metric="incidents_24h"
              label="Incidents 24h"
              value={stats?.incidents_24h ?? 0}
              loading={statsLoading}
              delta={deltas?.incidents_24h_delta}
              deltaInvert
              // Red-tint the card when the WoW delta crosses the spike threshold.
              // MetricCard's `valueColor` prop controls the main value color.
              valueColor={incidentsSpike ? 'text-health-critical' : undefined}
              icon={
                <svg className="w-[18px] h-[18px]" width="18" height="18" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                </svg>
              }
              iconColor={incidentsSpike ? 'text-health-critical' : 'text-ios-orange'}
              footer={
                trendsData?.series.incidents_24h ? (
                  <Sparkline
                    points={trendsData.series.incidents_24h}
                    width={140}
                    height={28}
                    color={incidentsSpike ? 'text-health-critical' : 'text-ios-orange'}
                    label="14-day incident trend"
                  />
                ) : undefined
              }
            />

            <MetricCard
              metric="l1_rate"
              label="L1 Auto-Heal"
              value={stats?.l1_resolution_rate ?? 0}
              suffix="%"
              loading={statsLoading}
              delta={deltas?.l1_rate_delta}
              deltaSuffix="%"
              // L1 auto-heal below 85% target → amber regardless of delta.
              valueColor={l1BelowTarget ? 'text-health-warning' : 'text-ios-blue'}
              icon={
                <svg className="w-[18px] h-[18px]" width="18" height="18" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
              }
              iconColor={l1BelowTarget ? 'text-health-warning' : 'text-ios-blue'}
              footer={
                !statsLoading ? (
                  <div className="flex items-center gap-2">
                    {trendsData?.series.l1_rate && (
                      <Sparkline
                        points={trendsData.series.l1_rate}
                        width={110}
                        height={24}
                        color={l1BelowTarget ? 'text-health-warning' : 'text-ios-blue'}
                        referenceY={L1_AUTOHEAL_TARGET}
                        label="14-day L1 auto-heal trend vs target"
                      />
                    )}
                    <span className="text-[10px] text-label-tertiary">
                      target {L1_AUTOHEAL_TARGET}%
                    </span>
                  </div>
                ) : undefined
              }
            />

            <MetricCard
              metric="clients"
              label="Clients"
              value={stats?.total_clients ?? 0}
              loading={statsLoading}
              delta={deltas?.clients_delta}
              icon={
                <svg className="w-[18px] h-[18px]" width="18" height="18" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
                </svg>
              }
              iconColor="text-ios-blue"
              footer={
                trendsData?.series.clients ? (
                  <Sparkline
                    points={trendsData.series.clients}
                    width={140}
                    height={28}
                    color="text-ios-blue"
                    label="14-day client count trend"
                  />
                ) : undefined
              }
            />
          </div>
        </div>
      )}

      {/* Platform SLA strip — Healing, Evidence, Fleet */}
      {!showEmptyState && <DashboardSLAStrip />}

      {/* Attention + Incident trend */}
      {!showEmptyState && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          <AttentionPanel className="lg:col-span-2" />
          <IncidentTrendChart className="self-start" />
        </div>
      )}

      {/* Recent incidents feed */}
      {!showEmptyState && (
        <IncidentFeed
          incidents={incidents ?? []}
          isLoading={incidentsLoading}
          error={incidentsError}
          title="Recent Incidents"
          showViewAll={true}
          compact={true}
          limit={INCIDENT_FEED_LIMIT}
        />
      )}

      {/* Incident analytics row */}
      {!showEmptyState && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <ResolutionBreakdown />
          <TopIncidentTypes />
        </div>
      )}

      {/* Fleet health matrix */}
      {!showEmptyState && <FleetHealthMatrix />}

      {/* Bottom row: System Health + Learning Loop */}
      {!showEmptyState && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <GlassCard>
            <h2 className="text-base font-semibold mb-4 text-label-primary">System Health</h2>
            <div className="space-y-3">
              {/*
                Control Coverage row intentionally REMOVED — it was a duplicate
                of the Fleet Compliance hero gauge above. Same query, same
                number, confusing to see it twice on the same page.

                Evidence Delay + MFA Coverage come from /sla-strip (not /stats)
                so we don't bloat the generic stats endpoint with dashboard-only
                compliance metrics.
              */}
              {[
                { label: 'Connectivity', value: statsLoading ? null : `${stats?.avg_connectivity_score ?? 0}%`, tip: METRIC_TOOLTIPS.connectivity, color: '' },
                { label: 'Appliances Online', value: statsLoading ? null : `${stats?.online_appliances ?? 0}/${stats?.total_appliances ?? 0}`, tip: METRIC_TOOLTIPS.appliances_online, color: '' },
                { label: 'Incidents (7d)', value: statsLoading ? null : `${stats?.incidents_7d ?? 0}`, tip: METRIC_TOOLTIPS.incidents_7d, color: '' },
                { label: 'Incidents (30d)', value: statsLoading ? null : `${stats?.incidents_30d ?? 0}`, tip: METRIC_TOOLTIPS.incidents_30d, color: '' },
                {
                  label: 'Evidence Delay',
                  value:
                    slaData?.ots_anchor_age_minutes === undefined
                      ? null
                      : slaData.ots_anchor_age_minutes === null
                        ? '—'
                        : `${Math.round(slaData.ots_anchor_age_minutes)}m`,
                  tip: 'Age of the oldest pending OpenTimestamps anchor. Target: ≤120min for HIPAA evidence integrity.',
                  color:
                    slaData?.ots_anchor_age_minutes === null || slaData?.ots_anchor_age_minutes === undefined
                      ? 'text-label-tertiary'
                      : slaData.ots_anchor_age_minutes > (slaData.ots_target_minutes ?? 120)
                        ? 'text-health-critical'
                        : 'text-health-healthy',
                },
                {
                  label: 'MFA Coverage',
                  value:
                    slaData?.mfa_coverage_pct === undefined
                      ? null
                      : slaData.mfa_coverage_pct === null
                        ? '—'
                        : `${slaData.mfa_coverage_pct.toFixed(0)}%`,
                  tip: 'Percentage of active admin users with MFA enrolled. HIPAA §164.312(d) target: 100%.',
                  color:
                    slaData?.mfa_coverage_pct === null || slaData?.mfa_coverage_pct === undefined
                      ? 'text-label-tertiary'
                      : slaData.mfa_coverage_pct >= 100
                        ? 'text-health-healthy'
                        : slaData.mfa_coverage_pct >= 75
                          ? 'text-health-warning'
                          : 'text-health-critical',
                },
              ].map((row) => (
                <div key={row.label} className="flex items-center justify-between py-0.5">
                  <span className="text-sm text-label-secondary">{row.label}{row.tip && <InfoTip text={row.tip} />}</span>
                  {row.value !== null ? (
                    <span className={`text-sm font-semibold tabular-nums ${row.color || 'text-label-primary'}`}>{row.value}</span>
                  ) : (
                    <span className="skeleton inline-block w-10 h-4" />
                  )}
                </div>
              ))}
            </div>
          </GlassCard>

          <GlassCard>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-base font-semibold text-label-primary">Learning Loop<InfoTip text={METRIC_TOOLTIPS.learning_loop} /></h2>
              <button
                onClick={() => navigate('/learning')}
                className="text-xs text-accent-primary font-medium hover:underline"
              >
                View All
              </button>
            </div>
            <div className="space-y-3">
              {[
                { label: 'L1 Rules', value: learningLoading ? null : `${learning?.total_l1_rules ?? 0}`, color: 'text-ios-blue', tip: METRIC_TOOLTIPS.l1_rules },
                { label: 'L2 Decisions (30d)', value: learningLoading ? null : `${learning?.total_l2_decisions_30d ?? 0}`, color: 'text-ios-purple', tip: METRIC_TOOLTIPS.l2_decisions },
                { label: 'Awaiting Promotion', value: learningLoading ? null : `${learning?.patterns_awaiting_promotion ?? 0}`, color: 'text-health-warning', tip: METRIC_TOOLTIPS.awaiting_promotion },
                { label: 'Success Rate', value: learningLoading ? null : `${learning?.promotion_success_rate ?? 0}%`, color: 'text-health-healthy', tip: METRIC_TOOLTIPS.promotion_success },
              ].map((row) => (
                <div key={row.label} className="flex items-center justify-between py-0.5">
                  <span className="text-sm text-label-secondary">{row.label}{row.tip && <InfoTip text={row.tip} />}</span>
                  {row.value !== null ? (
                    <span className={`text-sm font-semibold tabular-nums ${row.color}`}>{row.value}</span>
                  ) : (
                    <span className="skeleton inline-block w-10 h-4" />
                  )}
                </div>
              ))}
              {/* Last promotion — "is the flywheel alive?" signal at a glance */}
              {!learningLoading && learning?.last_promotion_at && (
                <div className="pt-2 mt-2 border-t border-glass-border text-xs text-label-tertiary">
                  Last rule promoted{' '}
                  <span className="text-label-secondary font-medium">
                    {formatTimeAgo(learning.last_promotion_at)}
                  </span>
                </div>
              )}
              {!learningLoading && !learning?.last_promotion_at && (
                <div className="pt-2 mt-2 border-t border-glass-border text-xs text-label-tertiary">
                  No promotions yet — flywheel will promote the first eligible pattern
                  automatically.
                </div>
              )}
            </div>
          </GlassCard>

          {/* Flywheel Intelligence — recurrence awareness */}
          <GlassCard>
            <div className="space-y-2">
              <div className="flex items-center justify-between mb-1">
                <h3 className="text-sm font-semibold text-label-primary">Flywheel Intelligence</h3>
                {flywheelLoading && <Spinner size="sm" />}
              </div>
              {[
                {
                  label: 'Recurrence Rate',
                  value: flywheelLoading ? null : `${flywheel?.recurrence_rate_pct ?? 0}%`,
                  color: (flywheel?.recurrence_rate_pct ?? 0) > 20 ? 'text-health-warning' : 'text-health-healthy',
                  tip: 'Percentage of resolved incidents that recur within 4 hours. Lower is better — means L1 fixes are sticking.',
                },
                {
                  label: 'Chronic Patterns',
                  value: flywheelLoading ? null : `${flywheel?.chronic_count ?? 0}`,
                  color: (flywheel?.chronic_count ?? 0) > 0 ? 'text-health-warning' : 'text-health-healthy',
                  tip: 'Incident types recurring 3+ times in 4 hours. These bypass L1 and go to L2 for root-cause analysis.',
                },
                {
                  label: 'L2 Root-Cause Fixes',
                  value: flywheelLoading ? null : `${flywheel?.l2_recurrence_decisions?.actionable ?? 0}`,
                  color: 'text-ios-purple',
                  tip: 'L2 decisions triggered by recurrence that produced actionable root-cause fixes.',
                },
                {
                  label: 'Auto-Promotions',
                  value: flywheelLoading ? null : `${flywheel?.auto_promotions?.length ?? 0}`,
                  color: 'text-health-healthy',
                  tip: 'L2 root-cause fixes that stopped recurrence for 24h+ and were auto-promoted to L1 rules.',
                },
                {
                  label: 'Correlations Found',
                  value: flywheelLoading ? null : `${flywheel?.correlations?.length ?? 0}`,
                  color: 'text-ios-blue',
                  tip: 'Cross-incident patterns: when fixing incident A consistently triggers incident B within 10 minutes.',
                },
              ].map((row) => (
                <div key={row.label} className="flex items-center justify-between py-0.5">
                  <span className="text-sm text-label-secondary">{row.label}<InfoTip text={row.tip} /></span>
                  {row.value !== null ? (
                    <span className={`text-sm font-semibold tabular-nums ${row.color}`}>{row.value}</span>
                  ) : (
                    <span className="skeleton inline-block w-10 h-4" />
                  )}
                </div>
              ))}
              {!flywheelLoading && flywheel?.chronic_patterns && flywheel.chronic_patterns.length > 0 && (
                <div className="pt-2 mt-2 border-t border-glass-border">
                  <p className="text-[10px] text-label-tertiary mb-1">Active recurrence patterns:</p>
                  {flywheel.chronic_patterns.slice(0, 3).map((p) => (
                    <div key={`${p.site_id}-${p.incident_type}`} className="flex justify-between text-xs py-0.5">
                      <span className="text-label-secondary">{p.incident_type}</span>
                      <span className="text-health-warning tabular-nums">{p.velocity_per_hour.toFixed(1)}/hr</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </GlassCard>

          {/* Install Reports — pre-boot telemetry from installer ISO */}
          <GlassCard>
            <div className="space-y-2">
              <div className="flex items-center justify-between mb-1">
                <h3 className="text-sm font-semibold text-label-primary">Installer Telemetry</h3>
                {installsLoading && <Spinner size="sm" />}
              </div>
              {[
                {
                  label: 'Installs (7d)',
                  value: installsLoading ? null : `${installs?.summary?.total_7d ?? 0}`,
                  color: 'text-ios-blue',
                  tip: 'Total install attempts in the last 7 days (from installer ISO telemetry).',
                },
                {
                  label: 'Successful',
                  value: installsLoading ? null : `${installs?.summary?.success_count ?? 0}`,
                  color: 'text-health-healthy',
                  tip: 'Installs that completed successfully with verification passed.',
                },
                {
                  label: 'Failed',
                  value: installsLoading ? null : `${installs?.summary?.failure_count ?? 0}`,
                  color: (installs?.summary?.failure_count ?? 0) > 0 ? 'text-health-critical' : 'text-label-tertiary',
                  tip: 'Installs that failed during hardware probe, network check, image write, or verification.',
                },
                {
                  label: 'In Progress',
                  value: installsLoading ? null : `${installs?.summary?.in_progress_count ?? 0}`,
                  color: 'text-health-warning',
                  tip: 'Installers that started but never reported completion. May indicate halted/crashed installs.',
                },
              ].map((row) => (
                <div key={row.label} className="flex items-center justify-between py-0.5">
                  <span className="text-sm text-label-secondary">{row.label}<InfoTip text={row.tip} /></span>
                  {row.value !== null ? (
                    <span className={`text-sm font-semibold tabular-nums ${row.color}`}>{row.value}</span>
                  ) : (
                    <span className="skeleton inline-block w-10 h-4" />
                  )}
                </div>
              ))}
              {!installsLoading && installs?.reports && installs.reports.length > 0 && (
                <div className="pt-2 mt-2 border-t border-glass-border">
                  <p className="text-[10px] text-label-tertiary mb-1">Recent installs:</p>
                  {installs.reports.slice(0, 3).map((r) => {
                    const started = new Date(r.install_started_at);
                    const ago = Math.round((Date.now() - started.getTime()) / 60_000);
                    const status =
                      r.install_success === true ? '✓' :
                      r.install_success === false ? '✗' :
                      '…';
                    const statusColor =
                      r.install_success === true ? 'text-health-healthy' :
                      r.install_success === false ? 'text-health-critical' :
                      'text-health-warning';
                    return (
                      <div key={r.installer_id} className="flex justify-between text-xs py-0.5">
                        <span className="text-label-secondary truncate max-w-[70%]" title={r.serial_number || r.mac_address || r.installer_id}>
                          <span className={`${statusColor} mr-1`}>{status}</span>
                          {r.product_name || r.drive_model || r.installer_id.slice(0, 8)}
                          {r.error_step && <span className="text-health-critical ml-1">({r.error_step})</span>}
                        </span>
                        <span className="text-label-tertiary tabular-nums">
                          {ago < 60 ? `${ago}m` : ago < 1440 ? `${Math.round(ago / 60)}h` : `${Math.round(ago / 1440)}d`}
                        </span>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </GlassCard>
        </div>
      )}

      {/* Floating quick actions — reusable FAB component from composed/ */}
      {!showEmptyState && (
        <FloatingActionButton ariaLabel="Dashboard quick actions" actions={fabActions} />
      )}
    </div>
    </DashboardErrorBoundary>
  );
};

export default Dashboard;
