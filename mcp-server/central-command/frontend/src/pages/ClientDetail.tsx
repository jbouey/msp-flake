import React, { useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { GlassCard, Spinner } from '../components/shared';
import { HealthGauge } from '../components/fleet';
import { IncidentRow } from '../components/incidents/IncidentRow';
import { useClient, useIncidents } from '../hooks';
import { formatTimeAgo, getScoreStatus } from '../constants';
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

  const [expandedAppliance, setExpandedAppliance] = useState<string | null>(null);
  const [incidentFilter, setIncidentFilter] = useState<string>('all');

  const formatCheckName = (check: string): string => {
    return check.charAt(0).toUpperCase() + check.slice(1);
  };

  const getCheckStatus = (score: number): { text: string; color: string } => {
    const status = getScoreStatus(score);
    return { text: status.label, color: status.color };
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
          <div className="space-y-2">
            {client.appliances.map((appliance: Appliance) => {
              const appId = String(appliance.id);
              const isExpanded = expandedAppliance === appId;
              const complianceScore = appliance.health?.compliance?.score ?? 0;
              const scoreStatus = getScoreStatus(complianceScore);

              return (
                <div key={appliance.id} className="border border-border-primary rounded-ios-md overflow-hidden">
                  {/* Collapsed card row — always visible */}
                  <div
                    onClick={() => setExpandedAppliance(isExpanded ? null : appId)}
                    className="flex items-center gap-3 p-3 cursor-pointer hover:bg-background-secondary/50 transition-colors"
                  >
                    {/* Status dot */}
                    <div className={`w-2.5 h-2.5 flex-shrink-0 rounded-full ${appliance.is_online ? 'bg-health-healthy' : 'bg-health-critical'}`} />

                    {/* Name */}
                    <p className="font-medium text-label-primary text-sm flex-shrink-0 w-36 truncate">
                      {appliance.hostname}
                    </p>

                    {/* Version + last checkin */}
                    <p className="text-xs text-label-tertiary flex-shrink-0 hidden sm:block">
                      v{appliance.agent_version || '?'} &middot; {formatTimeAgo(appliance.last_checkin)}
                    </p>

                    {/* Mini compliance bar */}
                    <div className="flex-1 flex items-center gap-2 min-w-0">
                      <div className="flex-1 h-1.5 bg-separator-light rounded-full overflow-hidden min-w-[40px]">
                        <div
                          className={`h-full rounded-full ${scoreStatus.bgColor}`}
                          style={{ width: `${complianceScore}%` }}
                        />
                      </div>
                      <span className="text-xs font-medium text-label-secondary flex-shrink-0">
                        {Math.round(complianceScore)}%
                      </span>
                    </div>

                    {/* Target count badge */}
                    {(appliance.assigned_target_count ?? 0) > 0 && (
                      <span className="flex-shrink-0 text-xs bg-background-secondary text-label-secondary px-2 py-0.5 rounded-full border border-border-primary">
                        {appliance.assigned_target_count} targets
                      </span>
                    )}

                    {/* Chevron */}
                    <svg
                      className={`w-4 h-4 flex-shrink-0 text-label-tertiary transition-transform ${isExpanded ? 'rotate-180' : ''}`}
                      fill="none" viewBox="0 0 24 24" stroke="currentColor"
                    >
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                    </svg>
                  </div>

                  {/* Expanded detail panel */}
                  {isExpanded && (
                    <div className="border-t border-border-primary bg-background-secondary/30 p-4 space-y-4">
                      {/* Compliance grid */}
                      {appliance.health?.compliance && (
                        <div>
                          <p className="text-xs font-semibold text-label-secondary uppercase tracking-wide mb-2">Compliance Checks</p>
                          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                            {Object.entries(appliance.health.compliance)
                              .filter(([k]) => k !== 'score')
                              .map(([check, score]) => {
                                const s = typeof score === 'number' ? score : 0;
                                const st = getScoreStatus(s);
                                return (
                                  <div key={check} className="bg-background-secondary/50 rounded-ios-sm p-2">
                                    <p className="text-xs text-label-tertiary capitalize mb-1">{formatCheckName(check)}</p>
                                    <div className="flex items-center gap-2">
                                      <div className="flex-1 h-1.5 bg-separator-light rounded-full overflow-hidden">
                                        <div className={`h-full rounded-full ${st.bgColor}`} style={{ width: `${s}%` }} />
                                      </div>
                                      <span className={`text-xs font-medium ${st.color}`}>{Math.round(s)}%</span>
                                    </div>
                                  </div>
                                );
                              })}
                          </div>
                        </div>
                      )}

                      {/* Connectivity stats */}
                      {appliance.health?.connectivity && (
                        <div>
                          <p className="text-xs font-semibold text-label-secondary uppercase tracking-wide mb-2">Connectivity</p>
                          <div className="grid grid-cols-3 gap-3">
                            {[
                              { label: 'Check-in', value: appliance.health.connectivity.checkin_freshness },
                              { label: 'Healing', value: appliance.health.connectivity.healing_success_rate },
                              { label: 'Orders', value: appliance.health.connectivity.order_execution_rate },
                            ].map(({ label, value }) => (
                              <div key={label} className="bg-background-secondary/50 rounded-ios-sm p-2 text-center">
                                <p className={`text-lg font-bold ${getScoreStatus(value).color}`}>{Math.round(value)}%</p>
                                <p className="text-xs text-label-tertiary">{label}</p>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}

                      {/* IP address */}
                      {appliance.ip_address && (
                        <div>
                          <p className="text-xs font-semibold text-label-secondary uppercase tracking-wide mb-1">IP Address</p>
                          <p className="text-sm font-mono text-label-primary">{appliance.ip_address}</p>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </GlassCard>

      {/* Recent Incidents */}
      <GlassCard>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold">Recent Incidents</h2>
          <span className="text-sm text-label-tertiary">Last 10</span>
        </div>

        {/* Appliance filter chips */}
        {client.appliances.length > 1 && (
          <div className="flex gap-2 mb-3 flex-wrap">
            <button
              onClick={() => setIncidentFilter('all')}
              className={`px-3 py-1 text-xs rounded-full border transition-colors ${
                incidentFilter === 'all'
                  ? 'bg-blue-600 text-white border-blue-600'
                  : 'border-border-primary text-label-secondary hover:border-accent-primary'
              }`}
            >
              All
            </button>
            {client.appliances.map((a: Appliance) => (
              <button
                key={a.id}
                onClick={() => setIncidentFilter(a.hostname)}
                className={`px-3 py-1 text-xs rounded-full border transition-colors ${
                  incidentFilter === a.hostname
                    ? 'bg-blue-600 text-white border-blue-600'
                    : 'border-border-primary text-label-secondary hover:border-accent-primary'
                }`}
              >
                {a.hostname}
              </button>
            ))}
          </div>
        )}

        {(() => {
          const filteredIncidents = incidentFilter === 'all'
            ? incidents
            : incidents.filter((inc) => inc.hostname === incidentFilter);

          return filteredIncidents.length === 0 ? (
            <div className="text-center py-8">
              <div className="w-12 h-12 mx-auto mb-3 rounded-full bg-health-healthy/10 flex items-center justify-center">
                <svg className="w-6 h-6 text-health-healthy" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                </svg>
              </div>
              <p className="text-label-secondary font-medium">No recent incidents</p>
              <p className="text-label-tertiary text-sm">
                {incidentFilter === 'all'
                  ? 'This client has been running smoothly.'
                  : `No incidents found for ${incidentFilter}.`}
              </p>
            </div>
          ) : (
            <div className="space-y-2">
              {filteredIncidents.map((incident) => (
                <IncidentRow key={incident.id} incident={incident} />
              ))}
            </div>
          );
        })()}
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
