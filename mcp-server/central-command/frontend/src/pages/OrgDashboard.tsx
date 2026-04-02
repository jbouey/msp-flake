import React, { useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { GlassCard, Spinner, Badge } from '../components/shared';
import { organizationsApi } from '../utils/api';
import type { OrgSite, OrgCredential, OrgHealth } from '../utils/api';
import { formatTimeAgo, getStatusConfig } from '../constants';

const formatRelativeTime = formatTimeAgo;

const StatusDot: React.FC<{ status: string }> = ({ status }) => {
  const { dotColor } = getStatusConfig(status);
  return <span className={`inline-block w-2 h-2 rounded-full ${dotColor}`} />;
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

const AddSitePanel: React.FC<{ orgId: string }> = ({ orgId }) => {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);

  const { data: availData, isLoading } = useQuery({
    queryKey: ['available-sites', orgId],
    queryFn: () => organizationsApi.getAvailableSites(orgId),
    enabled: open,
  });

  const assignMutation = useMutation({
    mutationFn: (siteId: string) => organizationsApi.assignSite(orgId, siteId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['organization', orgId] });
      queryClient.invalidateQueries({ queryKey: ['available-sites', orgId] });
    },
  });

  const availableSites = availData?.sites || [];

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="px-3 py-1.5 rounded-ios bg-accent-primary text-white text-sm hover:bg-accent-primary/90 transition-colors flex items-center gap-1"
      >
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
        </svg>
        Add Site
      </button>
    );
  }

  return (
    <div className="p-4 bg-fill-quaternary rounded-ios space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-label-primary">Add Existing Site</h3>
        <button onClick={() => setOpen(false)} className="text-xs text-label-tertiary hover:text-label-primary">
          Close
        </button>
      </div>
      {isLoading ? (
        <Spinner size="sm" />
      ) : availableSites.length === 0 ? (
        <p className="text-sm text-label-tertiary">All sites are already assigned to organizations.</p>
      ) : (
        <div className="space-y-2 max-h-48 overflow-y-auto">
          {availableSites.map((site) => (
            <div key={site.site_id} className="flex items-center justify-between p-2 rounded-ios bg-fill-secondary">
              <div>
                <p className="text-sm font-medium text-label-primary">{site.clinic_name}</p>
                <p className="text-xs text-label-tertiary">{site.site_id}</p>
              </div>
              <button
                onClick={() => assignMutation.mutate(site.site_id)}
                disabled={assignMutation.isPending}
                className="px-3 py-1 rounded-ios bg-accent-primary text-white text-xs hover:bg-accent-primary/90 disabled:opacity-50"
              >
                {assignMutation.isPending ? '...' : 'Assign'}
              </button>
            </div>
          ))}
        </div>
      )}
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

  const { data: health } = useQuery<OrgHealth>({
    queryKey: ['org-health', orgId],
    queryFn: () => organizationsApi.getOrgHealth(orgId!),
    enabled: !!orgId,
    refetchInterval: 30000,
  });

  const { data: orgDevices } = useQuery({
    queryKey: ['org-devices', orgId],
    queryFn: () => organizationsApi.getOrgDevices(orgId!),
    enabled: !!orgId,
    refetchInterval: 60000,
  });

  const { data: orgAgents } = useQuery({
    queryKey: ['org-agents', orgId],
    queryFn: () => organizationsApi.getOrgAgents(orgId!),
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
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <GlassCard padding="md" className="text-center">
          <p className={`text-2xl font-bold ${(health?.compliance.score ?? avgCompliance) >= 80 ? 'text-health-healthy' : (health?.compliance.score ?? avgCompliance) >= 50 ? 'text-health-warning' : 'text-label-primary'}`}>
            {(health?.compliance.score ?? avgCompliance) > 0 ? `${Math.round(health?.compliance.score ?? avgCompliance)}%` : 'N/A'}
          </p>
          <p className="text-xs text-label-tertiary">Compliance Score</p>
        </GlassCard>
        <GlassCard padding="md" className="text-center">
          <p className={`text-2xl font-bold ${(health?.incidents.total_24h ?? totalIncidents24h) > 0 ? 'text-health-warning' : 'text-health-healthy'}`}>
            {health?.incidents.total_24h ?? totalIncidents24h}
          </p>
          <p className="text-xs text-label-tertiary">Incidents 24h</p>
        </GlassCard>
        <GlassCard padding="md" className="text-center">
          <p className={`text-2xl font-bold ${(health?.healing.success_rate ?? avgHealing) >= 80 ? 'text-health-healthy' : (health?.healing.success_rate ?? avgHealing) >= 50 ? 'text-health-warning' : 'text-label-primary'}`}>
            {(health?.healing.success_rate ?? avgHealing) > 0 ? `${Math.round(health?.healing.success_rate ?? avgHealing)}%` : 'N/A'}
          </p>
          <p className="text-xs text-label-tertiary">Healing Rate</p>
        </GlassCard>
        <GlassCard padding="md" className="text-center">
          <p className="text-2xl font-bold text-label-primary">
            {health ? `${health.fleet.online}/${health.fleet.total}` : `${totalOnlineAppliances}/${totalAppliances}`}
          </p>
          <p className="text-xs text-label-tertiary">Appliances Online</p>
        </GlassCard>
      </div>

      {/* Inventory Row — org-level aggregated */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
        <GlassCard padding="md" className="text-center">
          <p className="text-2xl font-bold text-accent-primary">{sites.length}</p>
          <p className="text-xs text-label-tertiary">Sites</p>
        </GlassCard>
        <GlassCard padding="md" className="text-center">
          <p className="text-2xl font-bold text-label-primary">{orgDevices?.summary?.total ?? '—'}</p>
          <p className="text-xs text-label-tertiary">
            Devices {orgDevices?.summary?.compliance_rate != null ? `(${orgDevices.summary.compliance_rate}% compliant)` : ''}
          </p>
        </GlassCard>
        <GlassCard padding="md" className="text-center">
          <p className="text-2xl font-bold text-label-primary">
            {orgAgents?.summary?.active ?? 0}/{orgAgents?.summary?.total ?? 0}
          </p>
          <p className="text-xs text-label-tertiary">Agents Active</p>
        </GlassCard>
        <GlassCard padding="md" className="text-center">
          <p className="text-2xl font-bold text-label-primary">{orgDevices?.summary?.site_count ?? '—'}</p>
          <p className="text-xs text-label-tertiary">Subnets Covered</p>
        </GlassCard>
        <GlassCard padding="md" className="text-center">
          <p className={`text-2xl font-bold ${(health?.evidence_witnesses?.coverage_pct ?? 0) >= 50 ? 'text-health-healthy' : 'text-label-primary'}`}>
            {health?.evidence_witnesses?.coverage_pct != null ? `${health.evidence_witnesses.coverage_pct}%` : '—'}
          </p>
          <p className="text-xs text-label-tertiary">
            Evidence Witnessed {health?.evidence_witnesses?.attestations_24h ? `(${health.evidence_witnesses.attestations_24h})` : ''}
          </p>
        </GlassCard>
      </div>

      {/* Incident Severity + Fleet Status (from health endpoint) */}
      {health && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <GlassCard>
            <h2 className="text-lg font-semibold text-label-primary mb-3">Incident Summary (7d)</h2>
            <div className="grid grid-cols-4 gap-3 text-center">
              {(['critical', 'high', 'medium', 'low'] as const).map((sev) => (
                <div key={sev}>
                  <p className={`text-xl font-bold ${sev === 'critical' ? 'text-health-critical' : sev === 'high' ? 'text-health-warning' : 'text-label-secondary'}`}>
                    {health.incidents.by_severity[sev] ?? 0}
                  </p>
                  <p className="text-xs text-label-tertiary capitalize">{sev}</p>
                </div>
              ))}
            </div>
          </GlassCard>
          <GlassCard>
            <h2 className="text-lg font-semibold text-label-primary mb-3">Fleet Status</h2>
            <div className="grid grid-cols-3 gap-3 text-center">
              <div>
                <p className="text-xl font-bold text-health-healthy">{health.fleet.online}</p>
                <p className="text-xs text-label-tertiary">Online</p>
              </div>
              <div>
                <p className="text-xl font-bold text-health-warning">{health.fleet.stale}</p>
                <p className="text-xs text-label-tertiary">Stale</p>
              </div>
              <div>
                <p className="text-xl font-bold text-health-critical">{health.fleet.offline}</p>
                <p className="text-xs text-label-tertiary">Offline</p>
              </div>
            </div>
          </GlassCard>
        </div>
      )}

      {/* Category Compliance Breakdown (from health endpoint) */}
      {health && Object.keys(health.categories).length > 0 && (
        <GlassCard>
          <h2 className="text-lg font-semibold text-label-primary mb-3">Compliance by Category</h2>
          <div className="space-y-2">
            {Object.entries(health.categories).map(([checkType, cat]) => (
              <div key={checkType} className="flex items-center gap-3">
                <span className="text-sm text-label-primary w-40 truncate">{checkType.replace(/_/g, ' ')}</span>
                <div className="flex-1 h-5 bg-fill-quaternary rounded-ios-sm overflow-hidden">
                  <div
                    className={`h-full rounded-ios-sm transition-all duration-500 ${cat.score >= 80 ? 'bg-health-healthy' : cat.score >= 50 ? 'bg-health-warning' : 'bg-health-critical'}`}
                    style={{ width: `${Math.max(cat.score, 2)}%` }}
                  />
                </div>
                <span className="text-sm font-medium text-label-secondary w-16 text-right">
                  {cat.passes}/{cat.total} ({cat.score}%)
                </span>
              </div>
            ))}
          </div>
        </GlassCard>
      )}

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
        <div className="px-4 py-3 border-b border-separator-light flex items-center justify-between">
          <h2 className="text-lg font-semibold text-label-primary">Sites</h2>
          <AddSitePanel orgId={orgId!} />
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

      {/* Shared Credentials */}
      <OrgCredentialsSection orgId={orgId!} />

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

const CREDENTIAL_TYPES = [
  { value: 'domain_admin', label: 'Domain Admin' },
  { value: 'local_admin', label: 'Local Admin' },
  { value: 'winrm', label: 'WinRM' },
  { value: 'ssh_password', label: 'SSH Password' },
  { value: 'ssh_key', label: 'SSH Key' },
];

const OrgCredentialsSection: React.FC<{ orgId: string }> = ({ orgId }) => {
  const queryClient = useQueryClient();
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({
    credential_name: '',
    credential_type: 'domain_admin',
    host: '',
    username: '',
    password: '',
    domain: '',
  });

  const { data: credData, isLoading } = useQuery({
    queryKey: ['org-credentials', orgId],
    queryFn: () => organizationsApi.getCredentials(orgId),
  });

  const createMutation = useMutation({
    mutationFn: () => organizationsApi.createCredential(orgId, form),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['org-credentials', orgId] });
      setShowForm(false);
      setForm({ credential_name: '', credential_type: 'domain_admin', host: '', username: '', password: '', domain: '' });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (credId: string) => organizationsApi.deleteCredential(orgId, credId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['org-credentials', orgId] });
    },
  });

  const credentials = credData?.credentials || [];

  return (
    <GlassCard>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold text-label-primary">Shared Credentials</h2>
        <button
          onClick={() => setShowForm(!showForm)}
          className="px-3 py-1.5 rounded-ios bg-accent-primary text-white text-sm hover:bg-accent-primary/90 transition-colors"
        >
          {showForm ? 'Cancel' : 'Add Credential'}
        </button>
      </div>

      <p className="text-xs text-label-tertiary mb-4">
        Org-level credentials are inherited by all sites. Site-level credentials take precedence.
      </p>

      {showForm && (
        <div className="mb-4 p-4 bg-fill-quaternary rounded-ios space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-label-secondary mb-1">Name</label>
              <input
                type="text"
                value={form.credential_name}
                onChange={(e) => setForm({ ...form, credential_name: e.target.value })}
                placeholder="e.g. Domain Admin - NORTHVALLEY"
                className="w-full px-3 py-2 rounded-ios bg-fill-secondary text-label-primary text-sm border border-separator-light focus:border-accent-primary focus:outline-none"
              />
            </div>
            <div>
              <label className="block text-xs text-label-secondary mb-1">Type</label>
              <select
                value={form.credential_type}
                onChange={(e) => setForm({ ...form, credential_type: e.target.value })}
                className="w-full px-3 py-2 rounded-ios bg-fill-secondary text-label-primary text-sm border border-separator-light focus:border-accent-primary focus:outline-none"
              >
                {CREDENTIAL_TYPES.map((t) => (
                  <option key={t.value} value={t.value}>{t.label}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs text-label-secondary mb-1">Host</label>
              <input
                type="text"
                value={form.host}
                onChange={(e) => setForm({ ...form, host: e.target.value })}
                placeholder="192.168.88.250"
                className="w-full px-3 py-2 rounded-ios bg-fill-secondary text-label-primary text-sm border border-separator-light focus:border-accent-primary focus:outline-none"
              />
            </div>
            <div>
              <label className="block text-xs text-label-secondary mb-1">Domain</label>
              <input
                type="text"
                value={form.domain}
                onChange={(e) => setForm({ ...form, domain: e.target.value })}
                placeholder="NORTHVALLEY"
                className="w-full px-3 py-2 rounded-ios bg-fill-secondary text-label-primary text-sm border border-separator-light focus:border-accent-primary focus:outline-none"
              />
            </div>
            <div>
              <label className="block text-xs text-label-secondary mb-1">Username</label>
              <input
                type="text"
                value={form.username}
                onChange={(e) => setForm({ ...form, username: e.target.value })}
                placeholder="DOMAIN\\Administrator"
                className="w-full px-3 py-2 rounded-ios bg-fill-secondary text-label-primary text-sm border border-separator-light focus:border-accent-primary focus:outline-none"
              />
            </div>
            <div>
              <label className="block text-xs text-label-secondary mb-1">Password</label>
              <input
                type="password"
                value={form.password}
                onChange={(e) => setForm({ ...form, password: e.target.value })}
                className="w-full px-3 py-2 rounded-ios bg-fill-secondary text-label-primary text-sm border border-separator-light focus:border-accent-primary focus:outline-none"
              />
            </div>
          </div>
          <div className="flex justify-end">
            <button
              onClick={() => createMutation.mutate()}
              disabled={!form.credential_name || !form.username || createMutation.isPending}
              className="px-4 py-2 rounded-ios bg-accent-primary text-white text-sm hover:bg-accent-primary/90 transition-colors disabled:opacity-50"
            >
              {createMutation.isPending ? 'Saving...' : 'Save Credential'}
            </button>
          </div>
        </div>
      )}

      {isLoading ? (
        <div className="flex justify-center py-4"><Spinner /></div>
      ) : credentials.length === 0 ? (
        <p className="text-sm text-label-tertiary">No shared credentials configured.</p>
      ) : (
        <table className="w-full">
          <thead>
            <tr className="border-b border-separator-light">
              <th className="px-2 py-2 text-left text-xs font-semibold text-label-secondary uppercase">Name</th>
              <th className="px-2 py-2 text-left text-xs font-semibold text-label-secondary uppercase">Type</th>
              <th className="px-2 py-2 text-left text-xs font-semibold text-label-secondary uppercase">Host</th>
              <th className="px-2 py-2 text-left text-xs font-semibold text-label-secondary uppercase">Username</th>
              <th className="px-2 py-2 text-left text-xs font-semibold text-label-secondary uppercase">Added</th>
              <th className="px-2 py-2 text-right text-xs font-semibold text-label-secondary uppercase"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-separator-light">
            {credentials.map((cred: OrgCredential) => (
              <tr key={cred.id}>
                <td className="px-2 py-2 text-sm text-label-primary">{cred.credential_name}</td>
                <td className="px-2 py-2">
                  <Badge variant="default">
                    {CREDENTIAL_TYPES.find((t) => t.value === cred.credential_type)?.label || cred.credential_type}
                  </Badge>
                </td>
                <td className="px-2 py-2 text-sm text-label-secondary font-mono">{cred.host || '-'}</td>
                <td className="px-2 py-2 text-sm text-label-secondary font-mono">
                  {cred.domain ? `${cred.domain}\\${cred.username}` : cred.username || '-'}
                </td>
                <td className="px-2 py-2 text-sm text-label-tertiary">
                  {cred.created_at ? new Date(cred.created_at).toLocaleDateString() : '-'}
                </td>
                <td className="px-2 py-2 text-right">
                  <button
                    onClick={() => deleteMutation.mutate(cred.id)}
                    className="text-xs text-health-critical hover:underline"
                    disabled={deleteMutation.isPending}
                  >
                    Remove
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </GlassCard>
  );
};

export default OrgDashboard;
