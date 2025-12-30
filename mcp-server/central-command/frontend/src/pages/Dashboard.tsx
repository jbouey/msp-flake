import React from 'react';
import { useNavigate } from 'react-router-dom';
import { GlassCard } from '../components/shared';
import { FleetOverview, HealthGauge } from '../components/fleet';
import { IncidentFeed } from '../components/incidents';
import { useFleet, useIncidents, useGlobalStats, useLearningStatus } from '../hooks';

export const Dashboard: React.FC = () => {
  const navigate = useNavigate();

  // Fetch data with 30-second polling
  const { data: clients = [], isLoading: fleetLoading, error: fleetError } = useFleet();
  const { data: incidents = [], isLoading: incidentsLoading, error: incidentsError } = useIncidents({ limit: 10 });
  const { data: stats, isLoading: statsLoading } = useGlobalStats();
  const { data: learning, isLoading: learningLoading } = useLearningStatus();

  return (
    <div className="space-y-6">
      {/* Header stats row */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <GlassCard padding="md">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-label-tertiary text-sm">Total Clients</p>
              <p className="text-2xl font-semibold">
                {statsLoading ? '...' : stats?.total_clients ?? clients.length}
              </p>
            </div>
            <div className="w-10 h-10 bg-accent-tint rounded-ios-md flex items-center justify-center">
              <svg className="w-5 h-5 text-accent-primary" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
              </svg>
            </div>
          </div>
        </GlassCard>

        <GlassCard padding="md">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-label-tertiary text-sm">Avg Compliance</p>
              <p className="text-2xl font-semibold text-health-healthy">
                {statsLoading ? '...' : `${stats?.avg_compliance_score ?? 0}%`}
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
              <p className="text-label-tertiary text-sm">Incidents (24h)</p>
              <p className="text-2xl font-semibold">
                {statsLoading ? '...' : stats?.incidents_24h ?? 0}
              </p>
            </div>
            <div className="w-10 h-10 bg-orange-50 rounded-ios-md flex items-center justify-center">
              <svg className="w-5 h-5 text-health-warning" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
              </svg>
            </div>
          </div>
        </GlassCard>

        <GlassCard padding="md">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-label-tertiary text-sm">L1 Resolution</p>
              <p className="text-2xl font-semibold text-ios-blue">
                {statsLoading ? '...' : `${stats?.l1_resolution_rate ?? 0}%`}
              </p>
            </div>
            <div className="w-10 h-10 bg-blue-50 rounded-ios-md flex items-center justify-center">
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

      {/* Incidents */}
      <IncidentFeed
        incidents={incidents}
        isLoading={incidentsLoading}
        error={incidentsError as Error | null}
        title="Recent Incidents"
        showViewAll={true}
        limit={5}
        compact={true}
      />

      {/* Bottom row */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <GlassCard>
          <h2 className="text-lg font-semibold mb-4">System Health</h2>
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-label-secondary">Avg Compliance</span>
              <span className="font-medium">
                {statsLoading ? '...' : `${stats?.avg_compliance_score ?? 0}%`}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-label-secondary">Avg Connectivity</span>
              <span className="font-medium">
                {statsLoading ? '...' : `${stats?.avg_connectivity_score ?? 0}%`}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-label-secondary">Online Appliances</span>
              <span className="font-medium">
                {statsLoading ? '...' : `${stats?.online_appliances ?? 0}/${stats?.total_appliances ?? 0}`}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-label-secondary">Incidents (7d)</span>
              <span className="font-medium">
                {statsLoading ? '...' : stats?.incidents_7d ?? 0}
              </span>
            </div>
          </div>
        </GlassCard>

        <GlassCard>
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold">Learning Loop</h2>
            <button
              onClick={() => navigate('/learning')}
              className="text-sm text-accent-primary hover:underline"
            >
              View All
            </button>
          </div>
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-label-secondary">L1 Rules</span>
              <span className="font-medium">
                {learningLoading ? '...' : learning?.total_l1_rules ?? 0}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-label-secondary">L2 Decisions (30d)</span>
              <span className="font-medium">
                {learningLoading ? '...' : learning?.total_l2_decisions_30d ?? 0}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-label-secondary">Awaiting Promotion</span>
              <span className="font-medium text-health-warning">
                {learningLoading ? '...' : learning?.patterns_awaiting_promotion ?? 0}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-label-secondary">Success Rate</span>
              <span className="font-medium text-health-healthy">
                {learningLoading ? '...' : `${learning?.promotion_success_rate ?? 0}%`}
              </span>
            </div>
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
