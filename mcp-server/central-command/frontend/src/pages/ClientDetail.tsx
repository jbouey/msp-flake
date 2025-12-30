import React from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { GlassCard, Spinner } from '../components/shared';
import { HealthGauge } from '../components/fleet';
import { IncidentRow } from '../components/incidents/IncidentRow';
import { useClient, useIncidents } from '../hooks';
import type { CheckType, Appliance } from '../types';

/**
 * ClientDetail page - Deep dive into a single client
 */
export const ClientDetail: React.FC = () => {
  const { siteId } = useParams<{ siteId: string }>();
  const navigate = useNavigate();

  // Fetch client data
  const { data: client, isLoading, error } = useClient(siteId || null);
  const { data: incidents = [] } = useIncidents({ site_id: siteId, limit: 10 });

  const formatCheckName = (check: string): string => {
    return check.charAt(0).toUpperCase() + check.slice(1);
  };

  const getCheckStatus = (score: number): { text: string; color: string } => {
    if (score >= 100) return { text: 'PASS', color: 'text-health-healthy' };
    if (score >= 50) return { text: 'WARN', color: 'text-health-warning' };
    return { text: 'FAIL', color: 'text-health-critical' };
  };

  const formatLastCheckin = (dateStr?: string): string => {
    if (!dateStr) return 'Never';
    const date = new Date(dateStr);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffMins = Math.floor(diffMs / 60000);

    if (diffMins < 1) return 'Just now';
    if (diffMins < 60) return `${diffMins}m ago`;
    if (diffMins < 1440) return `${Math.floor(diffMins / 60)}h ago`;
    return date.toLocaleDateString();
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <Spinner size="lg" />
      </div>
    );
  }

  if (error || !client) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[400px] text-center">
        <div className="w-16 h-16 mb-4 rounded-full bg-health-critical/10 flex items-center justify-center">
          <svg className="w-8 h-8 text-health-critical" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
          </svg>
        </div>
        <h2 className="text-xl font-semibold text-label-primary mb-2">Client Not Found</h2>
        <p className="text-label-tertiary mb-4">Could not load data for "{siteId}"</p>
        <button onClick={() => navigate('/')} className="btn-primary">
          Return to Dashboard
        </button>
      </div>
    );
  }

  const complianceChecks: CheckType[] = ['patching', 'antivirus', 'backup', 'logging', 'firewall', 'encryption'];

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center gap-4">
        <button
          onClick={() => navigate('/')}
          className="p-2 hover:bg-separator-light rounded-ios-sm transition-colors"
          aria-label="Back to dashboard"
        >
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
          </svg>
        </button>
        <div>
          <h1 className="text-2xl font-semibold text-label-primary">{client.name}</h1>
          <p className="text-label-tertiary">{client.tier} Tier</p>
        </div>
      </div>

      {/* Health overview */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {/* Overall Health */}
        <GlassCard className="flex flex-col items-center justify-center py-8">
          <HealthGauge score={client.health.overall} size="xl" />
          <p className="mt-4 text-sm text-label-tertiary">Overall Health</p>
        </GlassCard>

        {/* Connectivity Metrics */}
        <GlassCard>
          <div className="flex items-center justify-between mb-4">
            <h3 className="font-semibold">Connectivity</h3>
            <span className="text-lg font-bold text-accent-primary">
              {client.health.connectivity.score.toFixed(0)}%
            </span>
          </div>
          <div className="space-y-3">
            <div>
              <div className="flex justify-between text-sm mb-1">
                <span className="text-label-secondary">Check-in Freshness</span>
                <span className={`font-medium ${getCheckStatus(client.health.connectivity.checkin_freshness).color}`}>
                  {client.health.connectivity.checkin_freshness}%
                </span>
              </div>
              <div className="h-1.5 bg-separator-light rounded-full overflow-hidden">
                <div
                  className="h-full bg-accent-primary rounded-full transition-all"
                  style={{ width: `${client.health.connectivity.checkin_freshness}%` }}
                />
              </div>
            </div>
            <div>
              <div className="flex justify-between text-sm mb-1">
                <span className="text-label-secondary">Healing Success</span>
                <span className={`font-medium ${getCheckStatus(client.health.connectivity.healing_success_rate).color}`}>
                  {client.health.connectivity.healing_success_rate}%
                </span>
              </div>
              <div className="h-1.5 bg-separator-light rounded-full overflow-hidden">
                <div
                  className="h-full bg-accent-primary rounded-full transition-all"
                  style={{ width: `${client.health.connectivity.healing_success_rate}%` }}
                />
              </div>
            </div>
            <div>
              <div className="flex justify-between text-sm mb-1">
                <span className="text-label-secondary">Order Execution</span>
                <span className={`font-medium ${getCheckStatus(client.health.connectivity.order_execution_rate).color}`}>
                  {client.health.connectivity.order_execution_rate}%
                </span>
              </div>
              <div className="h-1.5 bg-separator-light rounded-full overflow-hidden">
                <div
                  className="h-full bg-accent-primary rounded-full transition-all"
                  style={{ width: `${client.health.connectivity.order_execution_rate}%` }}
                />
              </div>
            </div>
          </div>
        </GlassCard>

        {/* Compliance Checks */}
        <GlassCard>
          <div className="flex items-center justify-between mb-4">
            <h3 className="font-semibold">Compliance</h3>
            <span className="text-lg font-bold text-accent-primary">
              {client.health.compliance.score.toFixed(0)}%
            </span>
          </div>
          <div className="space-y-2">
            {complianceChecks.map((check) => {
              const score = client.compliance_breakdown[check as keyof typeof client.compliance_breakdown] ?? 0;
              const status = getCheckStatus(score * 100);
              return (
                <div key={check} className="flex justify-between text-sm">
                  <span className="text-label-secondary">{formatCheckName(check)}</span>
                  <span className={`font-medium ${status.color}`}>{status.text}</span>
                </div>
              );
            })}
          </div>
        </GlassCard>
      </div>

      {/* Appliances */}
      <GlassCard>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold">
            Appliances ({client.appliances.length})
          </h2>
          <div className="flex items-center gap-2 text-sm text-label-tertiary">
            <span className="w-2 h-2 rounded-full bg-health-healthy" />
            {client.appliances.filter((a: Appliance) => a.is_online).length} online
          </div>
        </div>

        {client.appliances.length === 0 ? (
          <p className="text-label-tertiary text-center py-8">
            No appliances registered for this client.
          </p>
        ) : (
          <div className="space-y-3">
            {client.appliances.map((appliance: Appliance) => (
              <div
                key={appliance.id}
                className="flex items-center justify-between p-4 bg-separator-light/50 rounded-ios-md"
              >
                <div className="flex items-center gap-4">
                  <div className={`w-3 h-3 rounded-full ${appliance.is_online ? 'bg-health-healthy' : 'bg-health-critical'}`} />
                  <div>
                    <p className="font-medium text-label-primary">{appliance.hostname}</p>
                    <p className="text-xs text-label-tertiary">
                      {appliance.ip_address || 'No IP'} | v{appliance.agent_version || '?'}
                    </p>
                  </div>
                </div>
                <div className="flex items-center gap-4">
                  <div className="text-right text-xs text-label-tertiary">
                    <p>Last check-in</p>
                    <p className="font-medium text-label-secondary">
                      {formatLastCheckin(appliance.last_checkin)}
                    </p>
                  </div>
                  {appliance.health && (
                    <HealthGauge score={appliance.health.overall} size="sm" />
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </GlassCard>

      {/* Recent Incidents */}
      <GlassCard>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold">Recent Incidents</h2>
          <span className="text-sm text-label-tertiary">Last 10</span>
        </div>

        {incidents.length === 0 ? (
          <div className="text-center py-8">
            <div className="w-12 h-12 mx-auto mb-3 rounded-full bg-health-healthy/10 flex items-center justify-center">
              <svg className="w-6 h-6 text-health-healthy" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
              </svg>
            </div>
            <p className="text-label-secondary font-medium">No recent incidents</p>
            <p className="text-label-tertiary text-sm">This client has been running smoothly.</p>
          </div>
        ) : (
          <div className="space-y-2">
            {incidents.map((incident) => (
              <IncidentRow key={incident.id} incident={incident} />
            ))}
          </div>
        )}
      </GlassCard>

      {/* Quick Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <GlassCard padding="md" className="text-center">
          <p className="text-3xl font-bold text-label-primary">
            {client.appliances.length}
          </p>
          <p className="text-sm text-label-tertiary">Total Appliances</p>
        </GlassCard>
        <GlassCard padding="md" className="text-center">
          <p className="text-3xl font-bold text-health-healthy">
            {client.appliances.filter((a: Appliance) => a.is_online).length}
          </p>
          <p className="text-sm text-label-tertiary">Online</p>
        </GlassCard>
        <GlassCard padding="md" className="text-center">
          <p className="text-3xl font-bold text-label-primary">
            {incidents.filter((i) => !i.resolved).length}
          </p>
          <p className="text-sm text-label-tertiary">Open Incidents</p>
        </GlassCard>
        <GlassCard padding="md" className="text-center">
          <p className="text-3xl font-bold text-accent-primary">
            {incidents.filter((i) => i.resolution_level === 'L1').length}
          </p>
          <p className="text-sm text-label-tertiary">L1 Resolved</p>
        </GlassCard>
      </div>
    </div>
  );
};

export default ClientDetail;
