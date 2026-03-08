import React from 'react';
import { useNavigate } from 'react-router-dom';
import { GlassCard } from '../components/shared';
import { HealthGauge } from '../components/fleet';
import { IncidentTrendChart, FleetHealthMatrix, AttentionPanel, ResolutionBreakdown, TopIncidentTypes } from '../components/command-center';
import { IncidentFeed } from '../components/incidents';
import { useGlobalStats, useLearningStatus, useIncidents } from '../hooks';

export const Dashboard: React.FC = () => {
  const navigate = useNavigate();

  const { data: stats, isLoading: statsLoading } = useGlobalStats();
  const { data: learning, isLoading: learningLoading } = useLearningStatus();
  const { data: incidents, isLoading: incidentsLoading, error: incidentsError } = useIncidents({ limit: 10 });

  return (
    <div className="space-y-5 page-enter">
      {/* KPI row */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3 stagger-list">
        <GlassCard padding="md">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-label-tertiary text-[10px] font-semibold uppercase tracking-wider">Clients</p>
              <p className="text-2xl font-bold mt-1 tabular-nums animate-count-up">
                {statsLoading ? <span className="skeleton inline-block w-8 h-7" /> : stats?.total_clients ?? 0}
              </p>
            </div>
            <div
              className="w-9 h-9 rounded-ios-md flex items-center justify-center bg-ios-blue/15 text-ios-blue"
            >
              <svg className="w-4.5 h-4.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
              </svg>
            </div>
          </div>
        </GlassCard>

        <GlassCard padding="md">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-label-tertiary text-[10px] font-semibold uppercase tracking-wider">Compliance</p>
              <p className="text-2xl font-bold text-health-healthy mt-1 tabular-nums animate-count-up">
                {statsLoading ? <span className="skeleton inline-block w-12 h-7" /> : `${stats?.avg_compliance_score ?? 0}%`}
              </p>
            </div>
            <HealthGauge score={stats?.avg_compliance_score ?? 0} size="sm" showLabel={false} />
          </div>
        </GlassCard>

        <GlassCard padding="md">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-label-tertiary text-[10px] font-semibold uppercase tracking-wider">Incidents 24h</p>
              <p className="text-2xl font-bold mt-1 tabular-nums animate-count-up">
                {statsLoading ? <span className="skeleton inline-block w-8 h-7" /> : stats?.incidents_24h ?? 0}
              </p>
            </div>
            <div
              className="w-9 h-9 rounded-ios-md flex items-center justify-center bg-ios-orange/15 text-ios-orange"
            >
              <svg className="w-4.5 h-4.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
              </svg>
            </div>
          </div>
        </GlassCard>

        <GlassCard padding="md">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-label-tertiary text-[10px] font-semibold uppercase tracking-wider">L1 Auto-Heal</p>
              <p className="text-2xl font-bold text-ios-blue mt-1 tabular-nums animate-count-up">
                {statsLoading ? <span className="skeleton inline-block w-12 h-7" /> : `${stats?.l1_resolution_rate ?? 0}%`}
              </p>
            </div>
            <div
              className="w-9 h-9 rounded-ios-md flex items-center justify-center bg-ios-blue/15 text-ios-blue"
            >
              <svg className="w-4.5 h-4.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            </div>
          </div>
        </GlassCard>

        {/* Drift Detection — 6 HIPAA checks active */}
        <GlassCard padding="md">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-label-tertiary text-[10px] font-semibold uppercase tracking-wider">Drift Checks</p>
              <p className="text-2xl font-bold text-health-healthy mt-1 tabular-nums animate-count-up">
                {statsLoading ? <span className="skeleton inline-block w-8 h-7" /> : '6 Active'}
              </p>
            </div>
            <div
              className="w-9 h-9 rounded-ios-md flex items-center justify-center bg-health-healthy/15 text-health-healthy"
            >
              <svg className="w-4.5 h-4.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.99 11.99 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285z" />
              </svg>
            </div>
          </div>
        </GlassCard>
      </div>

      {/* Attention + Incident trend */}
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        <AttentionPanel className="lg:col-span-2" />
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
              { label: 'Control Coverage', value: statsLoading ? null : `${stats?.avg_compliance_score ?? 0}%`, color: 'text-health-healthy' },
              { label: 'Connectivity', value: statsLoading ? null : `${stats?.avg_connectivity_score ?? 0}%` },
              { label: 'Appliances Online', value: statsLoading ? null : `${stats?.online_appliances ?? 0}/${stats?.total_appliances ?? 0}` },
              { label: 'Incidents (7d)', value: statsLoading ? null : `${stats?.incidents_7d ?? 0}` },
              { label: 'Incidents (30d)', value: statsLoading ? null : `${stats?.incidents_30d ?? 0}` },
            ].map((row) => (
              <div key={row.label} className="flex items-center justify-between py-0.5">
                <span className="text-sm text-label-secondary">{row.label}</span>
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
            <h2 className="text-base font-semibold text-label-primary">Learning Loop</h2>
            <button
              onClick={() => navigate('/learning')}
              className="text-xs text-accent-primary font-medium hover:underline"
            >
              View All
            </button>
          </div>
          <div className="space-y-3">
            {[
              { label: 'L1 Rules', value: learningLoading ? null : `${learning?.total_l1_rules ?? 0}`, color: 'text-ios-blue' },
              { label: 'L2 Decisions (30d)', value: learningLoading ? null : `${learning?.total_l2_decisions_30d ?? 0}`, color: 'text-ios-purple' },
              { label: 'Awaiting Promotion', value: learningLoading ? null : `${learning?.patterns_awaiting_promotion ?? 0}`, color: 'text-health-warning' },
              { label: 'Success Rate', value: learningLoading ? null : `${learning?.promotion_success_rate ?? 0}%`, color: 'text-health-healthy' },
            ].map((row) => (
              <div key={row.label} className="flex items-center justify-between py-0.5">
                <span className="text-sm text-label-secondary">{row.label}</span>
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
