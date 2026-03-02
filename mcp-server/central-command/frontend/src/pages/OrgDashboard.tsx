import React from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { GlassCard, Spinner, Badge } from '../components/shared';
import { organizationsApi } from '../utils/api';
import type { OrgSite } from '../utils/api';

function formatRelativeTime(dateString: string | null): string {
  if (!dateString) return 'Never';
  const date = new Date(dateString);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  if (diffMins < 1) return 'Just now';
  if (diffMins < 60) return `${diffMins} min ago`;
  const diffHours = Math.floor(diffMins / 60);
  if (diffHours < 24) return `${diffHours}h ago`;
  const diffDays = Math.floor(diffHours / 24);
  return `${diffDays}d ago`;
}

const StatusDot: React.FC<{ status: string }> = ({ status }) => {
  const cls = status === 'online' ? 'status-dot-healthy'
    : status === 'stale' ? 'status-dot-warning'
    : status === 'offline' ? 'status-dot-critical'
    : 'status-dot-neutral';
  return <span className={`status-dot ${cls}`} />;
};

const ComplianceBar: React.FC<{ site: OrgSite }> = ({ site }) => {
  const pct = site.compliance_score;
  const color = pct >= 80 ? 'bg-health-healthy' : pct >= 50 ? 'bg-health-warning' : pct > 0 ? 'bg-health-critical' : 'bg-fill-quaternary';

  return (
    <div className="flex items-center gap-3">
      <span className="text-sm text-label-primary w-24 truncate">{site.clinic_name}</span>
      <div className="flex-1 h-5 bg-fill-quaternary rounded-ios-sm overflow-hidden">
        <div
          className={`h-full ${color} rounded-ios-sm transition-all duration-500`}
          style={{ width: `${Math.max(pct, 2)}%` }}
        />
      </div>
      <span className="text-sm font-medium text-label-secondary w-12 text-right">
        {pct > 0 ? `${pct}%` : 'N/A'}
      </span>
    </div>
  );
};

export const OrgDashboard: React.FC = () => {
  const { orgId } = useParams<{ orgId: string }>();
  const navigate = useNavigate();

  const { data: org, isLoading } = useQuery({
    queryKey: ['organization', orgId],
    queryFn: () => organizationsApi.getOrganization(orgId!),
    enabled: !!orgId,
    refetchInterval: 30000,
  });

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-24">
        <Spinner size="lg" />
      </div>
    );
  }

  if (!org) {
    return (
      <div className="text-center py-24">
        <h2 className="text-xl font-semibold text-label-primary">Organization not found</h2>
        <button onClick={() => navigate('/organizations')} className="btn-primary mt-4">
          Back to Organizations
        </button>
      </div>
    );
  }

  const sites = org.sites || [];
  const totalAppliances = sites.reduce((sum, s) => sum + s.appliance_count, 0);
  const totalOnlineAppliances = sites.reduce((sum, s) => sum + s.online_count, 0);
  const avgCompliance = sites.length > 0
    ? Math.round(sites.reduce((sum, s) => sum + s.compliance_score, 0) / sites.length)
    : 0;
  const avgHealing = sites.length > 0
    ? Math.round(sites.reduce((sum, s) => sum + s.healing_success_rate, 0) / sites.length)
    : 0;
  const totalIncidents24h = sites.reduce((sum, s) => sum + s.incidents_24h, 0);

  return (
    <div className="space-y-6 page-enter">
      {/* Breadcrumb + Header */}
      <div>
        <button
          onClick={() => navigate('/organizations')}
          className="text-sm text-accent-primary hover:underline mb-2 inline-block"
        >
          Organizations
        </button>
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-semibold text-label-primary tracking-tight">{org.name}</h1>
            <p className="text-label-tertiary text-sm mt-1">
              {org.practice_type || 'Healthcare Practice'} {org.npi_number ? `| NPI: ${org.npi_number}` : ''}
              {org.address ? ` | ${org.address}` : ''}
            </p>
          </div>
          <div className="flex items-center gap-3">
            <a
              href={`/api/evidence/organizations/${orgId}/bundle`}
              className="px-4 py-2 rounded-ios bg-fill-quaternary text-label-primary hover:bg-fill-secondary transition-colors text-sm"
              download
            >
              Download Evidence Bundle
            </a>
            <Badge variant={org.status === 'active' ? 'success' : 'default'}>
              {org.status}
            </Badge>
          </div>
        </div>
      </div>

      {/* KPI Row */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
        <GlassCard padding="md" className="text-center">
          <p className={`text-2xl font-bold ${avgCompliance >= 80 ? 'text-health-healthy' : avgCompliance >= 50 ? 'text-health-warning' : 'text-label-primary'}`}>
            {avgCompliance > 0 ? `${avgCompliance}%` : 'N/A'}
          </p>
          <p className="text-xs text-label-tertiary">Compliance Score</p>
        </GlassCard>
        <GlassCard padding="md" className="text-center">
          <p className={`text-2xl font-bold ${totalIncidents24h > 0 ? 'text-health-warning' : 'text-health-healthy'}`}>
            {totalIncidents24h}
          </p>
          <p className="text-xs text-label-tertiary">Incidents 24h</p>
        </GlassCard>
        <GlassCard padding="md" className="text-center">
          <p className={`text-2xl font-bold ${avgHealing >= 80 ? 'text-health-healthy' : avgHealing >= 50 ? 'text-health-warning' : 'text-label-primary'}`}>
            {avgHealing > 0 ? `${avgHealing}%` : 'N/A'}
          </p>
          <p className="text-xs text-label-tertiary">Healing Rate</p>
        </GlassCard>
        <GlassCard padding="md" className="text-center">
          <p className="text-2xl font-bold text-accent-primary">{sites.length}</p>
          <p className="text-xs text-label-tertiary">Sites</p>
        </GlassCard>
        <GlassCard padding="md" className="text-center">
          <p className="text-2xl font-bold text-label-primary">
            {totalOnlineAppliances}/{totalAppliances}
          </p>
          <p className="text-xs text-label-tertiary">Appliances Online</p>
        </GlassCard>
      </div>

      {/* Per-site compliance chart */}
      <GlassCard>
        <h2 className="text-lg font-semibold text-label-primary mb-4">Per-Site Compliance</h2>
        <div className="space-y-3">
          {sites.map((site) => (
            <ComplianceBar key={site.site_id} site={site} />
          ))}
          {sites.length === 0 && (
            <p className="text-sm text-label-tertiary">No sites in this organization.</p>
          )}
        </div>
      </GlassCard>

      {/* Sites detail table */}
      <GlassCard padding="none">
        <div className="px-4 py-3 border-b border-separator-light">
          <h2 className="text-lg font-semibold text-label-primary">Sites</h2>
        </div>
        <table className="w-full">
          <thead className="bg-fill-quaternary border-b border-separator-light">
            <tr>
              <th className="px-4 py-3 text-left text-xs font-semibold text-label-secondary uppercase tracking-wider">
                Site
              </th>
              <th className="px-4 py-3 text-left text-xs font-semibold text-label-secondary uppercase tracking-wider">
                Status
              </th>
              <th className="px-4 py-3 text-center text-xs font-semibold text-label-secondary uppercase tracking-wider">
                Appliances
              </th>
              <th className="px-4 py-3 text-left text-xs font-semibold text-label-secondary uppercase tracking-wider">
                Compliance
              </th>
              <th className="px-4 py-3 text-left text-xs font-semibold text-label-secondary uppercase tracking-wider">
                Healing
              </th>
              <th className="px-4 py-3 text-center text-xs font-semibold text-label-secondary uppercase tracking-wider">
                Incidents 24h
              </th>
              <th className="px-4 py-3 text-left text-xs font-semibold text-label-secondary uppercase tracking-wider">
                Last Checkin
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-separator-light">
            {sites.map((site) => (
              <tr
                key={site.site_id}
                onClick={() => navigate(`/sites/${site.site_id}`)}
                className="hover:bg-fill-quaternary cursor-pointer transition-colors"
              >
                <td className="px-4 py-3">
                  <div>
                    <p className="font-medium text-label-primary">{site.clinic_name}</p>
                    <p className="text-xs text-label-tertiary">{site.site_id}</p>
                  </div>
                </td>
                <td className="px-4 py-3">
                  <span className="inline-flex items-center gap-2 text-sm">
                    <StatusDot status={site.live_status} />
                    <span className="capitalize">{site.live_status}</span>
                  </span>
                </td>
                <td className="px-4 py-3 text-center text-sm">
                  <span className="text-health-healthy">{site.online_count}</span>
                  <span className="text-label-tertiary">/{site.appliance_count}</span>
                </td>
                <td className="px-4 py-3">
                  <Badge variant={
                    site.compliance_score >= 80 ? 'success' :
                    site.compliance_score >= 50 ? 'warning' :
                    site.compliance_score > 0 ? 'error' : 'default'
                  }>
                    {site.compliance_score > 0 ? `${site.compliance_score}%` : 'N/A'}
                  </Badge>
                </td>
                <td className="px-4 py-3">
                  <Badge variant={
                    site.healing_success_rate >= 80 ? 'success' :
                    site.healing_success_rate >= 50 ? 'warning' :
                    site.healing_success_rate > 0 ? 'error' : 'default'
                  }>
                    {site.healing_success_rate > 0 ? `${site.healing_success_rate}%` : 'N/A'}
                  </Badge>
                </td>
                <td className="px-4 py-3 text-center">
                  <span className={`text-sm font-medium ${site.incidents_24h > 0 ? 'text-health-warning' : 'text-health-healthy'}`}>
                    {site.incidents_24h}
                  </span>
                </td>
                <td className="px-4 py-3 text-sm text-label-secondary">
                  {formatRelativeTime(site.last_checkin)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </GlassCard>

      {/* Contact Info */}
      <GlassCard>
        <h2 className="text-lg font-semibold text-label-primary mb-3">Organization Details</h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
          <div>
            <p className="text-label-tertiary">Email</p>
            <p className="text-label-primary">{org.primary_email}</p>
          </div>
          {org.primary_phone && (
            <div>
              <p className="text-label-tertiary">Phone</p>
              <p className="text-label-primary">{org.primary_phone}</p>
            </div>
          )}
          {org.provider_count && (
            <div>
              <p className="text-label-tertiary">Providers</p>
              <p className="text-label-primary">{org.provider_count}</p>
            </div>
          )}
          <div>
            <p className="text-label-tertiary">Since</p>
            <p className="text-label-primary">
              {org.created_at ? new Date(org.created_at).toLocaleDateString() : 'N/A'}
            </p>
          </div>
        </div>
      </GlassCard>
    </div>
  );
};

export default OrgDashboard;
