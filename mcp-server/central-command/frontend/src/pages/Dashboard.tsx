import React from 'react';
import { useNavigate } from 'react-router-dom';
import { GlassCard } from '../components/shared';
import { FleetOverview, HealthGauge } from '../components/fleet';
import { IncidentFeed } from '../components/incidents';
import { EventFeed } from '../components/events';
import { useFleet, useIncidents, useEvents, useGlobalStats, useLearningStatus } from '../hooks';

export const Dashboard: React.FC = () => {
  const navigate = useNavigate();

  // Fetch data with 60-second polling
  const { data: clients = [], isLoading: fleetLoading, error: fleetError } = useFleet();
  const { data: incidents = [], isLoading: incidentsLoading, error: incidentsError } = useIncidents({ limit: 10 });
  const { data: events = [], isLoading: eventsLoading, error: eventsError } = useEvents({ limit: 10 });
  const { data: stats, isLoading: statsLoading } = useGlobalStats();
  const { data: learning, isLoading: learningLoading } = useLearningStatus();

  return (
    <div className="space-y-6 page-enter">
      {/* Header stats row */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <GlassCard padding="md">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-label-tertiary text-xs font-medium uppercase tracking-wide">Total Clients</p>
              <p className="text-2xl font-bold mt-1 tabular-nums">
                {statsLoading ? <span className="skeleton inline-block w-10 h-7" /> : stats?.total_clients ?? clients.length}
              </p>
            </div>
            <div
              className="w-10 h-10 rounded-ios-md flex items-center justify-center"
              style={{ background: 'linear-gradient(135deg, rgba(0, 122, 255, 0.12) 0%, rgba(88, 86, 214, 0.08) 100%)' }}
            >
              <svg className="w-5 h-5 text-accent-primary" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
              </svg>
            </div>
          </div>
        </GlassCard>

        <GlassCard padding="md">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-label-tertiary text-xs font-medium uppercase tracking-wide">Control Coverage</p>
              <p className="text-2xl font-bold text-health-healthy mt-1 tabular-nums">
                {statsLoading ? <span className="skeleton inline-block w-14 h-7" /> : `${stats?.avg_compliance_score ?? 0}%`}
              </p>
            </div>
            <HealthGauge
              score={stats?.avg_compliance_score ?? 0}
              size="sm"
              showLabel={false}
            />
          </div>
        </GlassCard>

        <GlassCard padding="md">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-label-tertiary text-xs font-medium uppercase tracking-wide">Incidents (24h)</p>
              <p className="text-2xl font-bold mt-1 tabular-nums">
                {statsLoading ? <span className="skeleton inline-block w-8 h-7" /> : stats?.incidents_24h ?? 0}
              </p>
            </div>
            <div
              className="w-10 h-10 rounded-ios-md flex items-center justify-center"
              style={{ background: 'linear-gradient(135deg, rgba(255, 149, 0, 0.12) 0%, rgba(255, 59, 48, 0.06) 100%)' }}
            >
              <svg className="w-5 h-5 text-health-warning" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
              </svg>
            </div>
          </div>
        </GlassCard>

        <GlassCard padding="md">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-label-tertiary text-xs font-medium uppercase tracking-wide">L1 Resolution</p>
              <p className="text-2xl font-bold text-ios-blue mt-1 tabular-nums">
                {statsLoading ? <span className="skeleton inline-block w-14 h-7" /> : `${stats?.l1_resolution_rate ?? 0}%`}
              </p>
            </div>
            <div
              className="w-10 h-10 rounded-ios-md flex items-center justify-center"
              style={{ background: 'linear-gradient(135deg, rgba(0, 122, 255, 0.12) 0%, rgba(0, 199, 190, 0.06) 100%)' }}
            >
              <svg className="w-5 h-5 text-ios-blue" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            </div>
          </div>
        </GlassCard>
      </div>

      {/* Fleet overview */}
      <GlassCard>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold">Fleet Overview</h2>
          <span className="text-xs text-label-tertiary">
            {clients.length} client{clients.length !== 1 ? 's' : ''}
          </span>
        </div>
        <FleetOverview
          clients={clients}
          isLoading={fleetLoading}
          error={fleetError as Error | null}
        />
      </GlassCard>

      {/* Incidents and Events */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <IncidentFeed
          incidents={incidents}
          isLoading={incidentsLoading}
          error={incidentsError as Error | null}
          title="Incidents"
          showViewAll={true}
          limit={5}
          compact={true}
        />
        <EventFeed
          events={events}
          isLoading={eventsLoading}
          error={eventsError as Error | null}
          title="Recent Activity"
          limit={5}
          compact={true}
        />
      </div>

      {/* Bottom row */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <GlassCard>
          <h2 className="text-base font-semibold mb-4 text-label-primary">System Health</h2>
          <div className="space-y-3">
            {[
              { label: 'Control Coverage', value: statsLoading ? null : `${stats?.avg_compliance_score ?? 0}%`, color: 'text-health-healthy' },
              { label: 'Avg Connectivity', value: statsLoading ? null : `${stats?.avg_connectivity_score ?? 0}%` },
              { label: 'Online Appliances', value: statsLoading ? null : `${stats?.online_appliances ?? 0}/${stats?.total_appliances ?? 0}` },
              { label: 'Incidents (7d)', value: statsLoading ? null : `${stats?.incidents_7d ?? 0}` },
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

      {/* Onboarding summary */}
      <GlassCard>
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold">Onboarding Pipeline</h2>
            <p className="text-sm text-label-tertiary mt-1">
              Track prospects through acquisition and activation phases
            </p>
          </div>
          <button
            onClick={() => navigate('/onboarding')}
            className="btn-secondary text-sm"
          >
            View Pipeline
          </button>
        </div>
      </GlassCard>
    </div>
  );
};

export default Dashboard;
