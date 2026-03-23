import React from 'react';
import { useNavigate } from 'react-router-dom';
import { GlassCard, InfoTip } from '../components/shared';
import { MetricCard } from '../components/composed';
import { HealthGauge } from '../components/fleet';
import { IncidentTrendChart, FleetHealthMatrix, AttentionPanel, ResolutionBreakdown, TopIncidentTypes } from '../components/command-center';
import { IncidentFeed } from '../components/incidents';
import { useGlobalStats, useStatsDeltas, useLearningStatus, useIncidents, useFleetPosture } from '../hooks';
import { METRIC_TOOLTIPS, getScoreStatus } from '../constants';

export const Dashboard: React.FC = () => {
  const navigate = useNavigate();

  const { data: stats, isLoading: statsLoading } = useGlobalStats();
  const { data: deltas } = useStatsDeltas();
  const { data: learning, isLoading: learningLoading } = useLearningStatus();
  const { data: incidents, isLoading: incidentsLoading, error: incidentsError } = useIncidents({ limit: 10 });
  const { data: fleetPosture, isLoading: fleetLoading } = useFleetPosture();

  // Worst 3 sites by compliance score (fleet-posture is already sorted by needs-attention)
  const worstSites = React.useMemo(() => {
    if (!fleetPosture || fleetPosture.length === 0) return [];
    return [...fleetPosture]
      .sort((a, b) => a.compliance_score - b.compliance_score)
      .slice(0, 3);
  }, [fleetPosture]);

  const activeThreats = stats?.active_threats ?? 0;

  return (
    <div className="space-y-5 page-enter">
      {/* Active Threat Banner */}
      {activeThreats > 0 && (
        <div className="bg-health-critical text-white px-4 py-3 rounded-ios-md flex items-center gap-3 mb-4">
          <svg className="w-5 h-5 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
          </svg>
          <span className="font-semibold">{activeThreats} active threat{activeThreats > 1 ? 's' : ''} detected</span>
          <button
            onClick={() => navigate('/incidents?severity=critical')}
            className="ml-auto text-sm underline hover:no-underline"
          >
            View Details
          </button>
        </div>
      )}

      {/* KPI row -- uses MetricCard with auto-resolved tooltips from METRIC_TOOLTIPS */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3 stagger-list">
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
        />

        <MetricCard
          metric="compliance_score"
          label="Compliance"
          value={stats?.avg_compliance_score ?? 0}
          suffix="%"
          loading={statsLoading}
          delta={deltas?.compliance_delta}
          deltaSuffix="%"
          valueColor="text-health-healthy"
        >
          <HealthGauge score={stats?.avg_compliance_score ?? 0} size="sm" showLabel={false} />
        </MetricCard>

        <MetricCard
          metric="incidents_24h"
          label="Incidents 24h"
          value={stats?.incidents_24h ?? 0}
          loading={statsLoading}
          delta={deltas?.incidents_24h_delta}
          deltaInvert
          icon={
            <svg className="w-[18px] h-[18px]" width="18" height="18" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
            </svg>
          }
          iconColor="text-ios-orange"
        />

        <MetricCard
          metric="l1_rate"
          label="L1 Auto-Heal"
          value={stats?.l1_resolution_rate ?? 0}
          suffix="%"
          loading={statsLoading}
          delta={deltas?.l1_rate_delta}
          deltaSuffix="%"
          valueColor="text-ios-blue"
          icon={
            <svg className="w-[18px] h-[18px]" width="18" height="18" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
          }
          iconColor="text-ios-blue"
        />

        <MetricCard
          metric="drift_checks"
          label="Drift Checks"
          value={statsLoading ? null : `${stats?.active_drift_checks ?? 47} Active`}
          loading={statsLoading}
          icon={
            <svg className="w-[18px] h-[18px]" width="18" height="18" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.99 11.99 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285z" />
            </svg>
          }
          iconColor="text-health-healthy"
          valueColor="text-health-healthy"
        />
      </div>

      {/* Attention + Worst Sites + Incident trend */}
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        <div className="lg:col-span-2 space-y-4">
          <AttentionPanel />

          {/* Worst-performing sites panel */}
          <GlassCard>
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-base font-semibold text-label-primary">Needs Attention</h3>
              <button
                onClick={() => navigate('/fleet')}
                className="text-xs text-accent-primary font-medium hover:underline"
              >
                View Fleet
              </button>
            </div>
            {fleetLoading ? (
              <div className="space-y-3">
                {[1, 2, 3].map((i) => (
                  <div key={i} className="skeleton h-14 rounded-ios-md" />
                ))}
              </div>
            ) : worstSites.length === 0 ? (
              <p className="text-sm text-label-tertiary py-4 text-center">All sites healthy -- nothing needs attention</p>
            ) : (
              <div className="space-y-1">
                {worstSites.map((site) => {
                  const siteStatus = getScoreStatus(site.compliance_score);
                  const trendIcon =
                    site.trend === 'improving' ? (
                      <svg className="w-3 h-3 text-health-healthy" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M5 15l7-7 7 7" />
                      </svg>
                    ) : site.trend === 'declining' ? (
                      <svg className="w-3 h-3 text-health-critical" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
                      </svg>
                    ) : null;

                  return (
                    <button
                      key={site.site_id}
                      onClick={() => navigate(`/sites/${site.site_id}`)}
                      className="w-full flex items-center gap-3 p-3 rounded-ios-md text-left transition-all hover:bg-fill-secondary"
                    >
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <p className="text-sm font-medium text-label-primary truncate">
                            {site.clinic_name}
                          </p>
                          {trendIcon}
                        </div>
                        <div className="flex items-center gap-2 mt-1.5">
                          <div className="flex-1 h-1.5 bg-fill-secondary rounded-full overflow-hidden">
                            <div
                              className={`h-full rounded-full transition-all ${siteStatus.dotColor}`}
                              style={{ width: `${Math.min(site.compliance_score, 100)}%` }}
                            />
                          </div>
                          <span className={`text-xs font-semibold tabular-nums ${siteStatus.color}`}>
                            {site.compliance_score}%
                          </span>
                        </div>
                        {(site.unresolved > 0 || site.incidents_24h > 0) && (
                          <p className="text-[10px] text-label-tertiary mt-1">
                            {site.unresolved > 0 && <span>{site.unresolved} unresolved</span>}
                            {site.unresolved > 0 && site.incidents_24h > 0 && <span> / </span>}
                            {site.incidents_24h > 0 && <span>{site.incidents_24h} in 24h</span>}
                          </p>
                        )}
                      </div>
                    </button>
                  );
                })}
              </div>
            )}
          </GlassCard>
        </div>
        <IncidentTrendChart className="lg:col-span-3" />
      </div>

      {/* Recent incidents feed */}
      <IncidentFeed
        incidents={incidents ?? []}
        isLoading={incidentsLoading}
        error={incidentsError}
        title="Recent Incidents"
        showViewAll={true}
        compact={true}
        limit={8}
      />

      {/* Incident analytics row */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <ResolutionBreakdown />
        <TopIncidentTypes />
      </div>

      {/* Fleet health matrix */}
      <FleetHealthMatrix />

      {/* Bottom row: System Health + Learning Loop */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <GlassCard>
          <h2 className="text-base font-semibold mb-4 text-label-primary">System Health</h2>
          <div className="space-y-3">
            {[
              { label: 'Control Coverage', value: statsLoading ? null : `${stats?.avg_compliance_score ?? 0}%`, color: 'text-health-healthy', tip: METRIC_TOOLTIPS.control_coverage },
              { label: 'Connectivity', value: statsLoading ? null : `${stats?.avg_connectivity_score ?? 0}%`, tip: METRIC_TOOLTIPS.connectivity },
              { label: 'Appliances Online', value: statsLoading ? null : `${stats?.online_appliances ?? 0}/${stats?.total_appliances ?? 0}`, tip: METRIC_TOOLTIPS.appliances_online },
              { label: 'Incidents (7d)', value: statsLoading ? null : `${stats?.incidents_7d ?? 0}`, tip: METRIC_TOOLTIPS.incidents_7d },
              { label: 'Incidents (30d)', value: statsLoading ? null : `${stats?.incidents_30d ?? 0}`, tip: METRIC_TOOLTIPS.incidents_30d },
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
          </div>
        </GlassCard>
      </div>
    </div>
  );
};

export default Dashboard;
