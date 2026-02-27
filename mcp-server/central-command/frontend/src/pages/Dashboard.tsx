import React from 'react';
import { useNavigate } from 'react-router-dom';
import { GlassCard } from '../components/shared';
import { HealthGauge } from '../components/fleet';
import { IncidentTrendChart, FleetHealthMatrix, AttentionPanel, ResolutionBreakdown, TopIncidentTypes } from '../components/command-center';
import { useGlobalStats, useLearningStatus, useAttentionRequired } from '../hooks';

export const Dashboard: React.FC = () => {
  const navigate = useNavigate();

  const { data: stats, isLoading: statsLoading } = useGlobalStats();
  const { data: learning, isLoading: learningLoading } = useLearningStatus();
  const { data: attention } = useAttentionRequired();

  const attentionCount = attention?.count ?? 0;

  return (
    <div className="space-y-5 page-enter">
      {/* KPI row */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <GlassCard padding="md">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-label-tertiary text-[10px] font-semibold uppercase tracking-wider">Clients</p>
              <p className="text-2xl font-bold mt-1 tabular-nums">
                {statsLoading ? <span className="skeleton inline-block w-8 h-7" /> : stats?.total_clients ?? 0}
              </p>
            </div>
            <div
              className="w-9 h-9 rounded-ios-md flex items-center justify-center"
              style={{ background: 'linear-gradient(135deg, rgba(0, 122, 255, 0.12) 0%, rgba(88, 86, 214, 0.08) 100%)' }}
            >
              <svg className="w-4.5 h-4.5 text-accent-primary" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
              </svg>
            </div>
          </div>
        </GlassCard>

        <GlassCard padding="md">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-label-tertiary text-[10px] font-semibold uppercase tracking-wider">Compliance</p>
              <p className="text-2xl font-bold text-health-healthy mt-1 tabular-nums">
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
              <p className="text-2xl font-bold mt-1 tabular-nums">
                {statsLoading ? <span className="skeleton inline-block w-8 h-7" /> : stats?.incidents_24h ?? 0}
              </p>
            </div>
            <div
              className="w-9 h-9 rounded-ios-md flex items-center justify-center"
              style={{ background: 'linear-gradient(135deg, rgba(255, 149, 0, 0.12) 0%, rgba(255, 59, 48, 0.06) 100%)' }}
            >
              <svg className="w-4.5 h-4.5 text-health-warning" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
              </svg>
            </div>
          </div>
        </GlassCard>

        <GlassCard padding="md">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-label-tertiary text-[10px] font-semibold uppercase tracking-wider">L1 Auto-Heal</p>
              <p className="text-2xl font-bold text-ios-blue mt-1 tabular-nums">
                {statsLoading ? <span className="skeleton inline-block w-12 h-7" /> : `${stats?.l1_resolution_rate ?? 0}%`}
              </p>
            </div>
            <div
              className="w-9 h-9 rounded-ios-md flex items-center justify-center"
              style={{ background: 'linear-gradient(135deg, rgba(0, 122, 255, 0.12) 0%, rgba(0, 199, 190, 0.06) 100%)' }}
            >
              <svg className="w-4.5 h-4.5 text-ios-blue" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            </div>
          </div>
        </GlassCard>

        {/* Attention count — the new KPI */}
        <GlassCard padding="md">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-label-tertiary text-[10px] font-semibold uppercase tracking-wider">Needs You</p>
              <p className={`text-2xl font-bold mt-1 tabular-nums ${attentionCount > 0 ? 'text-health-critical' : 'text-health-healthy'}`}>
                {attentionCount}
              </p>
            </div>
            <div
              className="w-9 h-9 rounded-ios-md flex items-center justify-center"
              style={{
                background: attentionCount > 0
                  ? 'linear-gradient(135deg, rgba(255, 59, 48, 0.15) 0%, rgba(255, 149, 0, 0.08) 100%)'
                  : 'linear-gradient(135deg, rgba(52, 199, 89, 0.12) 0%, rgba(0, 199, 190, 0.06) 100%)',
              }}
            >
              {attentionCount > 0 ? (
                <svg className="w-4.5 h-4.5 text-health-critical" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
                </svg>
              ) : (
                <svg className="w-4.5 h-4.5 text-health-healthy" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
              )}
            </div>
          </div>
        </GlassCard>
      </div>

      {/* Attention + Incident trend */}
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        <AttentionPanel className="lg:col-span-2" />
        <IncidentTrendChart className="lg:col-span-3" />
      </div>

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
