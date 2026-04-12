/**
 * Provisions queue — admin view of all pre-registered appliance MACs.
 *
 * Session 205: previously admins created provisions via the site detail modal
 * but had no way to see the queue, edit typos, or delete stale entries.
 * This page fills that gap: list/filter/edit/delete across all sites.
 */
import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { GlassCard, Spinner } from '../components/shared';
import { PageShell } from '../components/composed';
import { provisionsApi, type ProvisionRow } from '../utils/api';

type StatusFilter = 'all' | 'pending' | 'claimed' | 'stale';

function statusColor(status: ProvisionRow['status']): string {
  if (status === 'claimed') return 'text-health-healthy';
  if (status === 'pending') return 'text-ios-blue';
  return 'text-health-warning'; // stale
}

function statusLabel(status: ProvisionRow['status']): string {
  if (status === 'claimed') return 'Claimed';
  if (status === 'pending') return 'Pending';
  return 'Stale (>30d)';
}

function formatTime(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  const ageMin = Math.round((Date.now() - d.getTime()) / 60_000);
  if (ageMin < 60) return `${ageMin}m ago`;
  if (ageMin < 1440) return `${Math.round(ageMin / 60)}h ago`;
  return `${Math.round(ageMin / 1440)}d ago`;
}

interface EditModalProps {
  provision: ProvisionRow;
  onClose: () => void;
  onSaved: () => void;
}

const EditProvisionModal: React.FC<EditModalProps> = ({ provision, onClose, onSaved }) => {
  const [notes, setNotes] = useState(provision.notes || '');
  const [siteId, setSiteId] = useState(provision.site_id || '');
  const [email, setEmail] = useState(provision.client_contact_email || '');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      await provisionsApi.update(provision.mac_address, {
        notes,
        site_id: siteId !== provision.site_id ? siteId : undefined,
        client_email: email !== (provision.client_contact_email || '') ? email : undefined,
      });
      onSaved();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="bg-white rounded-2xl p-6 w-full max-w-md shadow-xl text-slate-900">
        <h3 className="text-lg font-semibold mb-4">Edit Provision</h3>
        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">MAC Address</label>
            <p className="text-sm text-slate-500 font-mono">{provision.mac_address}</p>
          </div>
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">Site</label>
            <input
              type="text"
              value={siteId}
              onChange={(e) => setSiteId(e.target.value)}
              className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm text-slate-900 focus:outline-none focus:ring-2 focus:ring-teal-500"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">Client Email</label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm text-slate-900 focus:outline-none focus:ring-2 focus:ring-teal-500"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">Notes</label>
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              rows={3}
              className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm text-slate-900 focus:outline-none focus:ring-2 focus:ring-teal-500"
            />
          </div>
          {error && <p className="text-sm text-health-critical">{error}</p>}
        </div>
        <div className="flex justify-end gap-3 mt-6">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm text-slate-600 hover:text-slate-900"
            disabled={saving}
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            className="px-4 py-2 text-sm font-medium rounded-lg text-white disabled:opacity-50"
            style={{ background: 'linear-gradient(135deg, #14A89E 0%, #0d9488 100%)' }}
          >
            {saving ? 'Saving...' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  );
};

const Provisions: React.FC = () => {
  const qc = useQueryClient();
  const [filter, setFilter] = useState<StatusFilter>('all');
  const [editing, setEditing] = useState<ProvisionRow | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<ProvisionRow | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ['provisions', filter],
    queryFn: () =>
      provisionsApi.list({ status: filter === 'all' ? undefined : filter, limit: 200 }),
    refetchInterval: 30_000,
  });

  const deleteMutation = useMutation({
    mutationFn: (mac: string) => provisionsApi.remove(mac),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['provisions'] }),
  });

  const handleDelete = async (mac: string) => {
    await deleteMutation.mutateAsync(mac);
    setConfirmDelete(null);
  };

  return (
    <PageShell
      title="Appliance Provisions"
      subtitle="Pre-registered MAC addresses waiting for appliances to boot + claim. Edit or delete stale entries here."
    >
      {/* Summary pills */}
      <div className="flex gap-3 mb-4">
        {(['all', 'pending', 'claimed', 'stale'] as StatusFilter[]).map((f) => {
          const count =
            f === 'all' ? data?.summary?.total ?? 0 :
            f === 'pending' ? data?.summary?.pending ?? 0 :
            f === 'claimed' ? data?.summary?.claimed ?? 0 :
            data?.summary?.stale ?? 0;
          const active = filter === f;
          return (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`px-4 py-2 rounded-ios text-sm font-medium transition ${
                active
                  ? 'bg-ios-blue text-white'
                  : 'bg-fill-secondary text-label-secondary hover:bg-fill-tertiary'
              }`}
            >
              {f.charAt(0).toUpperCase() + f.slice(1)}
              <span className="ml-2 opacity-80 tabular-nums">{count}</span>
            </button>
          );
        })}
      </div>

      <GlassCard>
        {isLoading && (
          <div className="flex items-center justify-center py-8">
            <Spinner />
          </div>
        )}
        {error && (
          <p className="text-health-critical text-sm py-4">
            Failed to load provisions: {error instanceof Error ? error.message : 'unknown'}
          </p>
        )}
        {!isLoading && !error && data?.provisions && data.provisions.length === 0 && (
          <p className="text-label-tertiary text-sm py-8 text-center">
            No provisions match this filter.
          </p>
        )}
        {!isLoading && !error && data?.provisions && data.provisions.length > 0 && (
          <div className="overflow-x-auto">
            <table className="min-w-full">
              <thead>
                <tr className="text-left text-xs uppercase text-label-tertiary border-b border-separator-light">
                  <th className="py-2 pr-4">MAC</th>
                  <th className="py-2 pr-4">Site</th>
                  <th className="py-2 pr-4">Status</th>
                  <th className="py-2 pr-4">Registered</th>
                  <th className="py-2 pr-4">Claimed</th>
                  <th className="py-2 pr-4">Notes</th>
                  <th className="py-2 pr-4 text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {data.provisions.map((p) => (
                  <tr
                    key={p.mac_address}
                    className="border-b border-separator-light hover:bg-fill-primary/50"
                  >
                    <td className="py-2 pr-4 font-mono text-sm">{p.mac_address}</td>
                    <td className="py-2 pr-4 text-sm">
                      {p.clinic_name || p.site_id || (
                        <span className="text-label-tertiary italic">unassigned</span>
                      )}
                    </td>
                    <td className={`py-2 pr-4 text-sm font-medium ${statusColor(p.status)}`}>
                      {statusLabel(p.status)}
                    </td>
                    <td className="py-2 pr-4 text-sm text-label-secondary tabular-nums">
                      {formatTime(p.registered_at)}
                    </td>
                    <td className="py-2 pr-4 text-sm text-label-secondary tabular-nums">
                      {formatTime(p.provisioned_at)}
                    </td>
                    <td className="py-2 pr-4 text-xs text-label-tertiary truncate max-w-[240px]">
                      {p.notes || '—'}
                    </td>
                    <td className="py-2 pr-4 text-right">
                      <button
                        onClick={() => setEditing(p)}
                        className="text-xs text-ios-blue hover:underline mr-3"
                      >
                        Edit
                      </button>
                      <button
                        onClick={() => setConfirmDelete(p)}
                        className="text-xs text-health-critical hover:underline"
                      >
                        Delete
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </GlassCard>

      {editing && (
        <EditProvisionModal
          provision={editing}
          onClose={() => setEditing(null)}
          onSaved={() => qc.invalidateQueries({ queryKey: ['provisions'] })}
        />
      )}

      {confirmDelete && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="bg-white rounded-2xl p-6 w-full max-w-md shadow-xl text-slate-900">
            <h3 className="text-lg font-semibold mb-2">Delete Provision?</h3>
            <p className="text-sm text-slate-600 mb-4">
              Remove pre-registration for MAC <span className="font-mono font-semibold">{confirmDelete.mac_address}</span>?
            </p>
            {confirmDelete.status === 'claimed' && (
              <p className="text-sm text-amber-700 bg-amber-50 p-2 rounded mb-4">
                This MAC has already claimed a slot. The running appliance will continue to check in
                normally — only the pre-registration record is removed.
              </p>
            )}
            <div className="flex justify-end gap-3">
              <button
                onClick={() => setConfirmDelete(null)}
                className="px-4 py-2 text-sm text-slate-600 hover:text-slate-900"
              >
                Cancel
              </button>
              <button
                onClick={() => handleDelete(confirmDelete.mac_address)}
                disabled={deleteMutation.isPending}
                className="px-4 py-2 text-sm font-medium rounded-lg text-white bg-red-600 hover:bg-red-700 disabled:opacity-50"
              >
                {deleteMutation.isPending ? 'Deleting...' : 'Delete'}
              </button>
            </div>
          </div>
        </div>
      )}
    </PageShell>
  );
};

export default Provisions;
