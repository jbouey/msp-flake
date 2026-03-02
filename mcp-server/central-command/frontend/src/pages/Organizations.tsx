import React from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { GlassCard, Spinner, Badge } from '../components/shared';
import { organizationsApi } from '../utils/api';
import type { Organization } from '../utils/api';

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

const ComplianceBadge: React.FC<{ score: number }> = ({ score }) => {
  const variant = score >= 80 ? 'success' : score >= 50 ? 'warning' : score > 0 ? 'error' : 'default';
  return <Badge variant={variant}>{score > 0 ? `${score}%` : 'N/A'}</Badge>;
};

const OrgRow: React.FC<{ org: Organization; onClick: () => void }> = ({ org, onClick }) => {
  return (
    <tr
      onClick={onClick}
      className="hover:bg-fill-quaternary cursor-pointer transition-colors"
    >
      <td className="px-4 py-3">
        <div>
          <p className="font-medium text-label-primary">{org.name}</p>
          <p className="text-xs text-label-tertiary">{org.primary_email}</p>
        </div>
      </td>
      <td className="px-4 py-3 text-sm text-label-secondary">
        {org.practice_type || '-'}
      </td>
      <td className="px-4 py-3 text-center">
        <span className="text-sm font-medium text-accent-primary">{org.site_count}</span>
      </td>
      <td className="px-4 py-3 text-center">
        <span className="text-sm text-label-secondary">{org.appliance_count}</span>
      </td>
      <td className="px-4 py-3">
        <ComplianceBadge score={org.avg_compliance} />
      </td>
      <td className="px-4 py-3 text-sm text-label-secondary">
        {formatRelativeTime(org.last_checkin)}
      </td>
      <td className="px-4 py-3">
        <Badge variant={org.status === 'active' ? 'success' : 'default'}>
          {org.status}
        </Badge>
      </td>
    </tr>
  );
};

export const Organizations: React.FC = () => {
  const navigate = useNavigate();

  const { data, isLoading } = useQuery({
    queryKey: ['organizations'],
    queryFn: () => organizationsApi.getOrganizations(),
    refetchInterval: 30000,
  });

  const orgs = data?.organizations || [];
  const totalSites = orgs.reduce((sum, o) => sum + o.site_count, 0);
  const totalAppliances = orgs.reduce((sum, o) => sum + o.appliance_count, 0);
  const avgCompliance = orgs.length > 0
    ? Math.round(orgs.reduce((sum, o) => sum + o.avg_compliance, 0) / orgs.length)
    : 0;

  return (
    <div className="space-y-6 page-enter">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-label-primary tracking-tight">Organizations</h1>
          <p className="text-label-tertiary text-sm mt-1">
            {orgs.length} organization{orgs.length !== 1 ? 's' : ''} managing {totalSites} sites
          </p>
        </div>
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <GlassCard padding="md" className="text-center">
          <p className="text-2xl font-bold text-accent-primary">{orgs.length}</p>
          <p className="text-xs text-label-tertiary">Organizations</p>
        </GlassCard>
        <GlassCard padding="md" className="text-center">
          <p className="text-2xl font-bold text-label-primary">{totalSites}</p>
          <p className="text-xs text-label-tertiary">Total Sites</p>
        </GlassCard>
        <GlassCard padding="md" className="text-center">
          <p className="text-2xl font-bold text-label-primary">{totalAppliances}</p>
          <p className="text-xs text-label-tertiary">Total Appliances</p>
        </GlassCard>
        <GlassCard padding="md" className="text-center">
          <p className={`text-2xl font-bold ${avgCompliance >= 80 ? 'text-health-healthy' : avgCompliance >= 50 ? 'text-health-warning' : 'text-label-primary'}`}>
            {avgCompliance > 0 ? `${avgCompliance}%` : 'N/A'}
          </p>
          <p className="text-xs text-label-tertiary">Avg Compliance</p>
        </GlassCard>
      </div>

      {/* Orgs table */}
      <GlassCard padding="none">
        {isLoading ? (
          <div className="flex items-center justify-center py-12">
            <Spinner size="lg" />
          </div>
        ) : orgs.length === 0 ? (
          <div className="text-center py-12">
            <h3 className="font-semibold text-label-primary mb-2">No organizations yet</h3>
            <p className="text-label-tertiary text-sm">
              Organizations are created automatically when sites are added.
            </p>
          </div>
        ) : (
          <table className="w-full">
            <thead className="bg-fill-quaternary border-b border-separator-light">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-semibold text-label-secondary uppercase tracking-wider">
                  Organization
                </th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-label-secondary uppercase tracking-wider">
                  Practice Type
                </th>
                <th className="px-4 py-3 text-center text-xs font-semibold text-label-secondary uppercase tracking-wider">
                  Sites
                </th>
                <th className="px-4 py-3 text-center text-xs font-semibold text-label-secondary uppercase tracking-wider">
                  Appliances
                </th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-label-secondary uppercase tracking-wider">
                  Compliance
                </th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-label-secondary uppercase tracking-wider">
                  Last Checkin
                </th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-label-secondary uppercase tracking-wider">
                  Status
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-separator-light">
              {orgs.map((org) => (
                <OrgRow
                  key={org.id}
                  org={org}
                  onClick={() => navigate(`/organizations/${org.id}`)}
                />
              ))}
            </tbody>
          </table>
        )}
      </GlassCard>
    </div>
  );
};

export default Organizations;
