/**
 * OrgHealthPanel — organization-level health and quota overview.
 *
 * Renders on admin dashboard showing a selected org's health, quota usage,
 * BAA status, recent audit events, and drill-through to site/partner details.
 */
import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { orgManagementApi } from '../../utils/api';

interface OrgHealth {
  org: {
    id: string;
    name: string;
    status: string;
    compliance_framework: string;
    baa_effective_date: string | null;
    baa_expiration_date: string | null;
    max_sites: number;
    max_users: number;
    deprovisioned_at: string | null;
  };
  metrics: {
    sites: number;
    users: number;
    open_incidents: number;
    bundles_24h: number;
    executions_24h: number;
    healing_rate_24h_pct: number;
  };
  quota: {
    site_usage_pct: number;
    user_usage_pct: number;
  };
  baa_status: 'not_configured' | 'active' | 'expiring_soon' | 'expired';
  recent_audit: Array<{
    event_type: string;
    actor: string;
    target: string | null;
    at: string;
  }>;
}

interface Props {
  orgId: string;
}

const baaColor: Record<string, string> = {
  active: 'bg-health-healthy text-white',
  expiring_soon: 'bg-amber-500 text-white',
  expired: 'bg-health-critical text-white',
  not_configured: 'bg-slate-500 text-white',
};

const baaLabel: Record<string, string> = {
  active: 'BAA Active',
  expiring_soon: 'BAA Expiring Soon',
  expired: 'BAA Expired',
  not_configured: 'No BAA',
};

export const OrgHealthPanel: React.FC<Props> = ({ orgId }) => {
  const { data, isLoading, error } = useQuery<OrgHealth>({
    queryKey: ['org-health', orgId],
    queryFn: () => orgManagementApi.getHealth(orgId) as unknown as Promise<OrgHealth>,
    refetchInterval: 60_000,
    enabled: !!orgId,
  });

  if (isLoading) {
    return (
      <div className="rounded-xl border border-glass-border bg-background-secondary p-6">
        <div className="text-label-tertiary">Loading organization health…</div>
      </div>
    );
  }

  if (error || !data) {
    return null;
  }

  const { org, metrics, quota, baa_status, recent_audit } = data;

  return (
    <div className="rounded-xl border border-glass-border bg-background-secondary p-6 space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-lg font-semibold text-label-primary">{org.name}</h3>
          <p className="text-xs text-label-tertiary mt-0.5">
            {org.compliance_framework} • {org.status}
          </p>
        </div>
        <span className={`px-3 py-1 rounded-full text-xs font-medium ${baaColor[baa_status]}`}>
          {baaLabel[baa_status]}
        </span>
      </div>

      {/* Metrics grid */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div>
          <div className="text-xs text-label-tertiary uppercase">Sites</div>
          <div className="text-xl font-semibold text-label-primary">
            {metrics.sites}/{org.max_sites}
          </div>
          <div className="text-xs text-label-tertiary">
            {quota.site_usage_pct.toFixed(0)}% used
          </div>
        </div>
        <div>
          <div className="text-xs text-label-tertiary uppercase">Client Users</div>
          <div className="text-xl font-semibold text-label-primary">
            {metrics.users}/{org.max_users}
          </div>
          <div className="text-xs text-label-tertiary">
            {quota.user_usage_pct.toFixed(0)}% used
          </div>
        </div>
        <div>
          <div className="text-xs text-label-tertiary uppercase">Open Incidents</div>
          <div className={`text-xl font-semibold ${metrics.open_incidents > 0 ? 'text-amber-500' : 'text-health-healthy'}`}>
            {metrics.open_incidents}
          </div>
          <div className="text-xs text-label-tertiary">across all sites</div>
        </div>
        <div>
          <div className="text-xs text-label-tertiary uppercase">Healing 24h</div>
          <div className={`text-xl font-semibold ${metrics.healing_rate_24h_pct >= 80 ? 'text-health-healthy' : 'text-amber-500'}`}>
            {metrics.healing_rate_24h_pct.toFixed(0)}%
          </div>
          <div className="text-xs text-label-tertiary">
            {metrics.executions_24h} runs
          </div>
        </div>
      </div>

      {/* Activity 24h */}
      <div className="p-3 rounded-lg bg-glass-bg border border-glass-border">
        <div className="text-xs text-label-tertiary uppercase mb-1">Activity 24h</div>
        <div className="grid grid-cols-2 text-sm">
          <div>
            <span className="text-label-primary font-medium">{metrics.bundles_24h}</span>
            <span className="text-label-tertiary ml-1">compliance bundles</span>
          </div>
          <div>
            <span className="text-label-primary font-medium">{metrics.executions_24h}</span>
            <span className="text-label-tertiary ml-1">healing runs</span>
          </div>
        </div>
      </div>

      {/* BAA details */}
      {org.baa_expiration_date && (
        <div className="p-3 rounded-lg bg-glass-bg border border-glass-border">
          <div className="text-xs text-label-tertiary uppercase mb-1">BAA</div>
          <div className="text-sm text-label-primary">
            {org.baa_effective_date && (
              <span>Effective {new Date(org.baa_effective_date).toLocaleDateString()}</span>
            )}
            {' → '}
            <span>Expires {new Date(org.baa_expiration_date).toLocaleDateString()}</span>
          </div>
        </div>
      )}

      {/* Recent audit */}
      {recent_audit.length > 0 && (
        <div>
          <div className="text-xs text-label-tertiary uppercase mb-2">Recent Activity</div>
          <div className="space-y-1 text-xs max-h-40 overflow-y-auto">
            {recent_audit.slice(0, 10).map((a, i) => (
              <div key={i} className="flex justify-between text-label-secondary">
                <span>
                  <span className="font-mono text-label-primary">{a.event_type}</span>
                  {a.target && <span className="ml-2 text-label-tertiary">{a.target}</span>}
                </span>
                <span className="text-label-tertiary">
                  {a.actor} · {new Date(a.at).toLocaleString()}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Deprovisioned banner */}
      {org.deprovisioned_at && (
        <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/30">
          <div className="text-sm text-red-400 font-medium">Deprovisioned</div>
          <div className="text-xs text-label-tertiary mt-1">
            {new Date(org.deprovisioned_at).toLocaleString()} — data retained until retention period ends
          </div>
        </div>
      )}
    </div>
  );
};
