import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { GlassCard, Spinner, Badge } from '../components/shared';
import { organizationsApi } from '../utils/api';
import type { Organization } from '../utils/api';
import { formatTimeAgo, getScoreStatus } from '../constants';
import type { BadgeVariant } from '../components/shared/Badge';

const formatRelativeTime = formatTimeAgo;

const ComplianceBadge: React.FC<{ score: number }> = ({ score }) => {
  const status = getScoreStatus(score > 0 ? score : null);
  const variantMap: Record<string, BadgeVariant> = { success: 'success', warning: 'warning', critical: 'error', neutral: 'default' };
  const variant: BadgeVariant = variantMap[status.type] || 'default';
  return <Badge variant={variant}>{score > 0 ? `${score}%` : 'N/A'}</Badge>;
};

const OrgRow: React.FC<{
  org: Organization;
  onClick: () => void;
  onEdit: () => void;
  onDelete: () => void;
  onDeprovision: () => void;
  onReprovision: () => void;
}> = ({ org, onClick, onEdit, onDelete, onDeprovision, onReprovision }) => {
  // #65 closure 2026-05-02: deprovision/reprovision admin button.
  // Toggles based on current org.status. Distinct icon (pause/resume)
  // from delete-trash icon to prevent click confusion.
  const isDeprovisioned = org.status === 'deprovisioned' || org.status === 'inactive';
  return (
    <tr
      onClick={onClick}
      className="group hover:bg-fill-quaternary cursor-pointer transition-colors"
    >
      <td className="px-4 py-3">
        <div className="flex items-center gap-2">
          <div className="flex-1">
            <p className="font-medium text-label-primary">{org.name}</p>
            <p className="text-xs text-label-tertiary">{org.primary_email}</p>
          </div>
          <div className="flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
            <button
              onClick={e => { e.stopPropagation(); onEdit(); }}
              className="p-1.5 rounded-ios-sm text-label-tertiary hover:text-accent-primary hover:bg-fill-secondary"
              title="Edit"
            >
              <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" /></svg>
            </button>
            <button
              onClick={e => { e.stopPropagation(); isDeprovisioned ? onReprovision() : onDeprovision(); }}
              className={`p-1.5 rounded-ios-sm text-label-tertiary ${isDeprovisioned ? 'hover:text-health-healthy hover:bg-health-healthy/10' : 'hover:text-amber-600 hover:bg-amber-100'}`}
              title={isDeprovisioned ? 'Reprovision (re-activate)' : 'Deprovision (pause; data retained)'}
            >
              {isDeprovisioned ? (
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z" /><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
              ) : (
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 9v6m4-6v6m7-3a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
              )}
            </button>
            <button
              onClick={e => { e.stopPropagation(); onDelete(); }}
              className="p-1.5 rounded-ios-sm text-label-tertiary hover:text-health-critical hover:bg-health-critical/10"
              title="Delete"
            >
              <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" /></svg>
            </button>
          </div>
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

const CreateOrgModal: React.FC<{ onClose: () => void; onCreated: () => void }> = ({ onClose, onCreated }) => {
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [phone, setPhone] = useState('');
  const [practiceType, setPracticeType] = useState('');
  const [providerCount, setProviderCount] = useState(1);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim() || !email.trim()) {
      setError('Name and email are required');
      return;
    }
    setSaving(true);
    setError('');
    try {
      await organizationsApi.createOrganization({
        name: name.trim(),
        primary_email: email.trim(),
        primary_phone: phone.trim() || undefined,
        practice_type: practiceType || undefined,
        provider_count: providerCount,
      });
      onCreated();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create organization');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="bg-fill-primary rounded-ios-lg shadow-xl w-full max-w-md p-6">
        <h2 className="text-lg font-semibold text-label-primary mb-4">New Organization</h2>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm text-label-secondary mb-1">Organization Name *</label>
            <input
              type="text"
              value={name}
              onChange={e => setName(e.target.value)}
              className="w-full px-3 py-2 rounded-ios bg-fill-secondary border border-separator-light text-label-primary"
              placeholder="North Valley Medical"
              autoFocus
            />
          </div>
          <div>
            <label className="block text-sm text-label-secondary mb-1">Primary Email *</label>
            <input
              type="email"
              value={email}
              onChange={e => setEmail(e.target.value)}
              className="w-full px-3 py-2 rounded-ios bg-fill-secondary border border-separator-light text-label-primary"
              placeholder="admin@clinic.com"
            />
          </div>
          <div>
            <label className="block text-sm text-label-secondary mb-1">Phone</label>
            <input
              type="tel"
              value={phone}
              onChange={e => setPhone(e.target.value)}
              className="w-full px-3 py-2 rounded-ios bg-fill-secondary border border-separator-light text-label-primary"
              placeholder="(570) 555-1234"
            />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm text-label-secondary mb-1">Practice Type</label>
              <select
                value={practiceType}
                onChange={e => setPracticeType(e.target.value)}
                className="w-full px-3 py-2 rounded-ios bg-fill-secondary border border-separator-light text-label-primary"
              >
                <option value="">Select...</option>
                <option value="medical">Medical</option>
                <option value="dental">Dental</option>
                <option value="mental_health">Mental Health</option>
                <option value="pharmacy">Pharmacy</option>
                <option value="veterinary">Veterinary</option>
                <option value="other">Other</option>
              </select>
            </div>
            <div>
              <label className="block text-sm text-label-secondary mb-1">Providers</label>
              <input
                type="number"
                min={1}
                value={providerCount}
                onChange={e => setProviderCount(Number(e.target.value) || 1)}
                className="w-full px-3 py-2 rounded-ios bg-fill-secondary border border-separator-light text-label-primary"
              />
            </div>
          </div>
          {error && <p className="text-sm text-health-critical">{error}</p>}
          <div className="flex justify-end gap-3 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 text-sm rounded-ios bg-fill-tertiary text-label-secondary hover:bg-fill-secondary"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={saving}
              className="px-4 py-2 text-sm font-medium rounded-ios bg-accent-primary text-white hover:bg-accent-primary/90 disabled:opacity-50"
            >
              {saving ? 'Creating...' : 'Create Organization'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};

export const Organizations: React.FC = () => {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [editOrg, setEditOrg] = useState<Organization | null>(null);
  const [deleteOrg, setDeleteOrg] = useState<Organization | null>(null);
  // #65 closure 2026-05-02: deprovision/reprovision modal state
  const [deprovOrg, setDeprovOrg] = useState<{org: Organization; mode: 'deprovision' | 'reprovision'} | null>(null);
  const [deprovReason, setDeprovReason] = useState('');
  const [actionLoading, setActionLoading] = useState(false);
  const [actionError, setActionError] = useState('');

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
      {/* Create Modal */}
      {showCreate && (
        <CreateOrgModal
          onClose={() => setShowCreate(false)}
          onCreated={() => {
            setShowCreate(false);
            queryClient.invalidateQueries({ queryKey: ['organizations'] });
          }}
        />
      )}

      {/* Delete Confirmation */}
      {deleteOrg && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="bg-fill-primary rounded-ios-lg shadow-xl w-full max-w-sm p-6">
            <h2 className="text-lg font-semibold text-label-primary mb-2">Delete Organization</h2>
            <p className="text-sm text-label-secondary mb-4">
              Are you sure you want to delete <strong>{deleteOrg.name}</strong>? This cannot be undone.
            </p>
            {actionError && (
              <div className="mb-3 p-2 rounded-ios bg-health-critical/10 text-health-critical text-sm">{actionError}</div>
            )}
            <div className="flex gap-3">
              <button
                onClick={() => { setDeleteOrg(null); setActionError(''); }}
                className="flex-1 px-4 py-2 text-sm rounded-ios bg-fill-secondary text-label-primary hover:bg-fill-tertiary"
              >Cancel</button>
              <button
                onClick={async () => {
                  setActionLoading(true);
                  setActionError('');
                  try {
                    await organizationsApi.deleteOrganization(deleteOrg.id);
                    setDeleteOrg(null);
                    queryClient.invalidateQueries({ queryKey: ['organizations'] });
                  } catch (err) {
                    setActionError(err instanceof Error ? err.message : 'Failed to delete');
                  } finally {
                    setActionLoading(false);
                  }
                }}
                disabled={actionLoading}
                className="flex-1 px-4 py-2 text-sm rounded-ios bg-health-critical text-white hover:bg-health-critical/90"
              >{actionLoading ? 'Deleting...' : 'Delete'}</button>
            </div>
          </div>
        </div>
      )}

      {/* Edit Modal */}
      {editOrg && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="bg-fill-primary rounded-ios-lg shadow-xl w-full max-w-md p-6">
            <h2 className="text-lg font-semibold text-label-primary mb-4">Edit Organization</h2>
            {actionError && (
              <div className="mb-3 p-2 rounded-ios bg-health-critical/10 text-health-critical text-sm">{actionError}</div>
            )}
            {/* eslint-disable-next-line no-undef */}
            <form onSubmit={async (e: React.FormEvent<HTMLFormElement>) => {
              e.preventDefault();
              const formData = new FormData(e.currentTarget);
              setActionLoading(true);
              setActionError('');
              try {
                // primary_email intentionally NOT sent (Task #95, 2026-05-15):
                // the backend rejects/discards primary_email mutations on PUT
                // /organizations/{id} since #91 — re-pointing primary_email
                // silently orphans baa_signatures (joined by LOWER(email)).
                // Until #93/#94 ship a BAA-aware rename helper, the email
                // field below is read-only and we MUST NOT send it.
                await organizationsApi.updateOrganization(editOrg.id, {
                  name: formData.get('name') as string,
                  primary_phone: formData.get('phone') as string,
                  practice_type: formData.get('practice_type') as string,
                });
                setEditOrg(null);
                queryClient.invalidateQueries({ queryKey: ['organizations'] });
              } catch (err) {
                setActionError(err instanceof Error ? err.message : 'Failed to update');
              } finally {
                setActionLoading(false);
              }
            }} className="space-y-3">
              <div>
                <label className="block text-sm text-label-secondary mb-1">Name</label>
                <input name="name" defaultValue={editOrg.name} className="w-full px-3 py-2 rounded-ios bg-fill-secondary border border-separator-light text-label-primary" />
              </div>
              <div>
                <label className="block text-sm text-label-secondary mb-1">Email</label>
                <input
                  name="email"
                  defaultValue={editOrg.primary_email}
                  disabled
                  readOnly
                  className="w-full px-3 py-2 rounded-ios bg-fill-secondary border border-separator-light text-label-secondary opacity-60 cursor-not-allowed"
                />
                <p className="mt-1 text-xs text-label-tertiary">
                  Read-only. Renaming the organization's email orphans
                  its BAA signatures; the BAA-aware rename helper is
                  pending (Task #94).
                </p>
              </div>
              <div>
                <label className="block text-sm text-label-secondary mb-1">Phone</label>
                <input name="phone" defaultValue={'primary_phone' in editOrg ? (editOrg as Organization & { primary_phone?: string }).primary_phone || '' : ''} className="w-full px-3 py-2 rounded-ios bg-fill-secondary border border-separator-light text-label-primary" />
              </div>
              <div>
                <label className="block text-sm text-label-secondary mb-1">Practice Type</label>
                <select name="practice_type" defaultValue={editOrg.practice_type || ''} className="w-full px-3 py-2 rounded-ios bg-fill-secondary border border-separator-light text-label-primary">
                  <option value="">Select...</option>
                  <option value="medical">Medical</option>
                  <option value="dental">Dental</option>
                  <option value="mental_health">Mental Health</option>
                  <option value="pharmacy">Pharmacy</option>
                  <option value="veterinary">Veterinary</option>
                  <option value="other">Other</option>
                </select>
              </div>
              <div className="flex gap-3 pt-2">
                <button type="button" onClick={() => { setEditOrg(null); setActionError(''); }} className="flex-1 px-4 py-2 text-sm rounded-ios bg-fill-secondary text-label-primary hover:bg-fill-tertiary">Cancel</button>
                <button type="submit" disabled={actionLoading} className="flex-1 px-4 py-2 text-sm rounded-ios bg-accent-primary text-white hover:bg-accent-primary/90">{actionLoading ? 'Saving...' : 'Save'}</button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-label-primary tracking-tight">Organizations</h1>
          <p className="text-label-tertiary text-sm mt-1">
            {orgs.length} organization{orgs.length !== 1 ? 's' : ''} managing {totalSites} sites
          </p>
        </div>
        <button
          onClick={() => setShowCreate(true)}
          className="px-4 py-2 text-sm font-medium rounded-ios bg-accent-primary text-white hover:bg-accent-primary/90 transition flex items-center gap-2"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
          </svg>
          New Organization
        </button>
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
          <p className={`text-2xl font-bold ${avgCompliance > 0 ? getScoreStatus(avgCompliance).color : 'text-label-primary'}`}>
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
                  onEdit={() => setEditOrg(org)}
                  onDelete={() => setDeleteOrg(org)}
                  onDeprovision={() => { setDeprovOrg({org, mode: 'deprovision'}); setDeprovReason(''); setActionError(''); }}
                  onReprovision={() => { setDeprovOrg({org, mode: 'reprovision'}); setDeprovReason(''); setActionError(''); }}
                />
              ))}
            </tbody>
          </table>
        )}
      </GlassCard>

      {/* #65 deprovision/reprovision modal */}
      {deprovOrg && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="bg-fill-primary rounded-ios-lg shadow-xl w-full max-w-md p-6">
            <h2 className="text-lg font-semibold text-label-primary">
              {deprovOrg.mode === 'deprovision' ? 'Deprovision' : 'Reprovision'} {deprovOrg.org.name}
            </h2>
            <p className="text-sm text-label-secondary mt-2">
              {deprovOrg.mode === 'deprovision'
                ? 'Mark organization as inactive. Portal access blocked. Billing paused. ALL DATA RETAINED for HIPAA §164.316(b)(2)(i) 6-year retention. Reversible via Reprovision.'
                : 'Re-activate this organization. Portal access restored, billing resumes. Data was retained throughout deprovisioned state.'}
            </p>
            <label className="block mt-4 text-xs font-medium text-label-secondary">
              Reason (audit-logged, ≥10 chars):
            </label>
            <textarea
              value={deprovReason}
              onChange={(e) => setDeprovReason(e.target.value)}
              className="w-full mt-1 px-3 py-2 text-sm bg-fill-secondary border border-separator-light rounded-ios-md focus:outline-none focus:ring-2 focus:ring-accent-primary"
              rows={3}
              placeholder="e.g. Customer canceled per email 2026-05-02; retention required..."
            />
            {actionError && (
              <div className="mt-3 p-2 rounded-ios bg-health-critical/10 text-health-critical text-sm">{actionError}</div>
            )}
            <div className="flex gap-3 mt-4">
              <button
                onClick={() => { setDeprovOrg(null); setActionError(''); }}
                disabled={actionLoading}
                className="flex-1 px-4 py-2 text-sm rounded-ios bg-fill-secondary text-label-primary hover:bg-fill-tertiary"
              >Cancel</button>
              <button
                onClick={async () => {
                  if (deprovReason.trim().length < 10) {
                    setActionError('Reason must be at least 10 characters (audit-log requirement)');
                    return;
                  }
                  setActionLoading(true);
                  setActionError('');
                  try {
                    if (deprovOrg.mode === 'deprovision') {
                      await organizationsApi.deprovisionOrganization(deprovOrg.org.id, deprovReason);
                    } else {
                      await organizationsApi.reprovisionOrganization(deprovOrg.org.id, deprovReason);
                    }
                    setDeprovOrg(null);
                    setDeprovReason('');
                    queryClient.invalidateQueries({ queryKey: ['organizations'] });
                  } catch (err) {
                    setActionError(err instanceof Error ? err.message : 'Action failed');
                  } finally {
                    setActionLoading(false);
                  }
                }}
                disabled={actionLoading || deprovReason.trim().length < 10}
                className={`flex-1 px-4 py-2 text-sm rounded-ios text-white disabled:opacity-50 ${
                  deprovOrg.mode === 'deprovision'
                    ? 'bg-amber-600 hover:bg-amber-700'
                    : 'bg-health-healthy hover:bg-health-healthy/90'
                }`}
              >
                {actionLoading
                  ? 'Working...'
                  : deprovOrg.mode === 'deprovision' ? 'Confirm Deprovision' : 'Confirm Reprovision'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default Organizations;
