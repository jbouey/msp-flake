import React from 'react';
import { useNavigate } from 'react-router-dom';
import { GlassCard, InfoTip } from '../components/shared';
import { MetricCard } from '../components/composed';
import { HealthGauge } from '../components/fleet';
import { IncidentTrendChart, FleetHealthMatrix, AttentionPanel, ResolutionBreakdown, TopIncidentTypes } from '../components/command-center';
import { IncidentFeed } from '../components/incidents';
import { useGlobalStats, useStatsDeltas, useLearningStatus, useIncidents } from '../hooks';
import { METRIC_TOOLTIPS, getScoreStatus } from '../constants';

export const Dashboard: React.FC = () => {
  const navigate = useNavigate();

  const { data: stats, isLoading: statsLoading } = useGlobalStats();
  const { data: deltas } = useStatsDeltas();
  const { data: learning, isLoading: learningLoading } = useLearningStatus();
  const { data: incidents, isLoading: incidentsLoading, error: incidentsError } = useIncidents({ limit: 10 });
  const activeThreats = stats?.active_threats ?? 0;
  const complianceScore = stats?.avg_compliance_score ?? 0;
  const scoreStatus = getScoreStatus(statsLoading ? null : complianceScore);

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

      {/* Hero compliance score + 3 secondary KPIs */}
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
              <p className="text-xs text-label-tertiary mt-1">Fleet Compliance</p>
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
        </div>
      </div>

      {/* Attention + Incident trend (unified row, no worst-sites panel) */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <AttentionPanel className="lg:col-span-2" />
        <IncidentTrendChart className="self-start" />
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
