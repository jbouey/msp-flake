import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';

// Types
interface ComplianceException {
  id: string;
  site_id: string;
  scope_type: 'runbook' | 'check' | 'control';
  item_id: string;
  device_filter: string | null;
  requested_by: string;
  approved_by: string;
  approval_date: string;
  approval_tier: string;
  approval_notes: string | null;
  start_date: string;
  expiration_date: string;
  requires_renewal: boolean;
  reason: string;
  compensating_control: string | null;
  risk_accepted_by: string;
  action: 'suppress_alert' | 'skip_remediation' | 'both';
  created_at: string;
  updated_at: string;
  is_active: boolean;
  is_valid: boolean;
  days_until_expiration: number;
}

interface ExceptionSummary {
  total: number;
  active: number;
  expired: number;
  revoked: number;
  expiring_soon: number;
  by_scope: { runbook: number; check: number; control: number };
  by_tier: { client_admin: number; partner: number; l3_escalation: number };
}

interface Site {
  id: string;
  name: string;
}

interface ExceptionCreateRequest {
  site_id: string;
  scope_type: 'runbook' | 'check' | 'control';
  item_id: string;
  reason: string;
  compensating_control?: string;
  device_filter?: string;
  duration_days: number;
  action: 'suppress_alert' | 'skip_remediation' | 'both';
}

interface ExceptionRenewRequest {
  duration_days: number;
  reason: string;
}

interface ExceptionAuditEntry {
  id: string;
  action: string;
  performed_by: string;
  performed_at: string;
  details: Record<string, unknown>;
  notes?: string;
}

// API functions - uses cookie-based auth (OAuth session) or partner_api_key
const getAuthHeaders = (): Record<string, string> => {
  const apiKey = localStorage.getItem('partner_api_key');
  return apiKey ? { 'X-API-Key': apiKey } : {};
};

const api = {
  async getExceptions(siteId: string, activeOnly = true): Promise<ComplianceException[]> {
    const res = await fetch(`/api/exceptions?site_id=${siteId}&active_only=${activeOnly}`, {
      credentials: 'include',  // Include session cookies for OAuth
      headers: getAuthHeaders()
    });
    if (!res.ok) throw new Error('Failed to fetch exceptions');
    return res.json();
  },

  async getSummary(siteId: string): Promise<ExceptionSummary> {
    const res = await fetch(`/api/exceptions/summary?site_id=${siteId}`, {
      credentials: 'include',
      headers: getAuthHeaders()
    });
    if (!res.ok) throw new Error('Failed to fetch summary');
    return res.json();
  },

  async createException(data: ExceptionCreateRequest): Promise<ComplianceException> {
    const res = await fetch('/api/exceptions', {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        ...getAuthHeaders()
      },
      body: JSON.stringify(data)
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Failed to create exception');
    }
    return res.json();
  },

  async renewException(id: string, data: ExceptionRenewRequest): Promise<ComplianceException> {
    const res = await fetch(`/api/exceptions/${id}/renew`, {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        ...getAuthHeaders()
      },
      body: JSON.stringify(data)
    });
    if (!res.ok) throw new Error('Failed to renew exception');
    return res.json();
  },

  async revokeException(id: string, reason: string): Promise<ComplianceException> {
    const res = await fetch(`/api/exceptions/${id}/revoke`, {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        ...getAuthHeaders()
      },
      body: JSON.stringify({ reason })
    });
    if (!res.ok) throw new Error('Failed to revoke exception');
    return res.json();
  },

  async getAuditLog(id: string): Promise<ExceptionAuditEntry[]> {
    const res = await fetch(`/api/exceptions/${id}/audit`, {
      credentials: 'include',
      headers: getAuthHeaders()
    });
    if (!res.ok) throw new Error('Failed to fetch audit log');
    return res.json();
  }
};

// Main Component
export function PartnerExceptionManagement({ sites }: { sites: Site[] }) {
  const [selectedSite, setSelectedSite] = useState<string>(sites[0]?.id || '');
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [showDetailsModal, setShowDetailsModal] = useState<ComplianceException | null>(null);
  const [showAllExceptions, setShowAllExceptions] = useState(false);

  const queryClient = useQueryClient();

  // Queries
  const { data: exceptions = [], isLoading } = useQuery({
    queryKey: ['exceptions', selectedSite, showAllExceptions],
    queryFn: () => api.getExceptions(selectedSite, !showAllExceptions),
    enabled: !!selectedSite
  });

  const { data: summary } = useQuery({
    queryKey: ['exception-summary', selectedSite],
    queryFn: () => api.getSummary(selectedSite),
    enabled: !!selectedSite
  });

  // Mutations
  const revokeMutation = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) => api.revokeException(id, reason),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['exceptions'] });
      queryClient.invalidateQueries({ queryKey: ['exception-summary'] });
    }
  });

  const renewMutation = useMutation({
    mutationFn: ({ id, data }: { id: string; data: any }) => api.renewException(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['exceptions'] });
      queryClient.invalidateQueries({ queryKey: ['exception-summary'] });
    }
  });

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold text-slate-900 tracking-tight">Exception Management</h2>
          <p className="text-slate-500 text-sm">Manage compliance exceptions and risk acceptances</p>
        </div>
        <div className="flex items-center gap-4">
          <select
            value={selectedSite}
            onChange={(e) => setSelectedSite(e.target.value)}
            className="rounded-md border-slate-300 shadow-sm"
          >
            {sites.map((site) => (
              <option key={site.id} value={site.id}>{site.name}</option>
            ))}
          </select>
          <button
            onClick={() => setShowCreateModal(true)}
            className="px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700"
          >
            + New Exception
          </button>
        </div>
      </div>

      {/* Summary Cards */}
      {summary && (
        <div className="grid grid-cols-5 gap-4">
          <SummaryCard title="Active" value={summary.active} color="green" />
          <SummaryCard title="Expiring Soon" value={summary.expiring_soon} color="yellow" />
          <SummaryCard title="Expired" value={summary.expired} color="red" />
          <SummaryCard title="Revoked" value={summary.revoked} color="gray" />
          <SummaryCard title="Total" value={summary.total} color="blue" />
        </div>
      )}

      {/* Filters */}
      <div className="flex items-center gap-4">
        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={!showAllExceptions}
            onChange={(e) => setShowAllExceptions(!e.target.checked)}
            className="rounded"
          />
          <span className="text-sm text-slate-600">Active only</span>
        </label>
      </div>

      {/* Exceptions Table */}
      <div className="bg-white rounded-2xl shadow-sm border border-slate-100 overflow-hidden">
        <table className="min-w-full divide-y divide-slate-200">
          <thead className="bg-slate-50">
            <tr>
              <th className="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase">ID</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase">Scope</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase">Item</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase">Status</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase">Expires</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase">Approved By</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase">Actions</th>
            </tr>
          </thead>
          <tbody className="bg-white divide-y divide-slate-200">
            {isLoading ? (
              <tr>
                <td colSpan={7} className="px-6 py-4 text-center text-slate-500">Loading...</td>
              </tr>
            ) : exceptions.length === 0 ? (
              <tr>
                <td colSpan={7} className="px-6 py-4 text-center text-slate-500">
                  No exceptions found
                </td>
              </tr>
            ) : (
              exceptions.map((exc) => (
                <tr key={exc.id} className="hover:bg-indigo-50/50">
                  <td className="px-6 py-4 whitespace-nowrap text-sm font-medium text-blue-600">
                    <button onClick={() => setShowDetailsModal(exc)}>{exc.id}</button>
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm">
                    <span className={`px-2 py-1 rounded-full text-xs ${
                      exc.scope_type === 'runbook' ? 'bg-purple-100 text-purple-800' :
                      exc.scope_type === 'check' ? 'bg-blue-100 text-blue-800' :
                      'bg-orange-100 text-orange-800'
                    }`}>
                      {exc.scope_type}
                    </span>
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-slate-900">
                    {exc.item_id}
                    {exc.device_filter && (
                      <span className="ml-2 text-slate-400 text-xs">({exc.device_filter})</span>
                    )}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap">
                    <StatusBadge exception={exc} />
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-slate-500">
                    {exc.is_valid && (
                      <>
                        {exc.days_until_expiration} days
                        <br />
                        <span className="text-xs text-slate-400">
                          {new Date(exc.expiration_date).toLocaleDateString()}
                        </span>
                      </>
                    )}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-slate-500">
                    {exc.approved_by}
                    <br />
                    <span className="text-xs text-slate-400">{exc.approval_tier}</span>
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm">
                    {exc.is_active && exc.is_valid && (
                      <div className="flex gap-2">
                        <button
                          onClick={() => {
                            const reason = prompt('Renewal reason:');
                            if (reason) renewMutation.mutate({ id: exc.id, data: { reason } });
                          }}
                          className="text-blue-600 hover:text-blue-800"
                        >
                          Renew
                        </button>
                        <button
                          onClick={() => {
                            const reason = prompt('Revocation reason:');
                            if (reason) revokeMutation.mutate({ id: exc.id, reason });
                          }}
                          className="text-red-600 hover:text-red-800"
                        >
                          Revoke
                        </button>
                      </div>
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Create Modal */}
      {showCreateModal && (
        <CreateExceptionModal
          siteId={selectedSite}
          onClose={() => setShowCreateModal(false)}
          onSuccess={() => {
            setShowCreateModal(false);
            queryClient.invalidateQueries({ queryKey: ['exceptions'] });
            queryClient.invalidateQueries({ queryKey: ['exception-summary'] });
          }}
        />
      )}

      {/* Details Modal */}
      {showDetailsModal && (
        <ExceptionDetailsModal
          exception={showDetailsModal}
          onClose={() => setShowDetailsModal(null)}
        />
      )}
    </div>
  );
}

// Sub-components
function SummaryCard({ title, value, color }: { title: string; value: number; color: string }) {
  const colors: Record<string, string> = {
    green: 'bg-green-100 text-green-800 border-green-200',
    yellow: 'bg-yellow-100 text-yellow-800 border-yellow-200',
    red: 'bg-red-100 text-red-800 border-red-200',
    gray: 'bg-slate-100 text-slate-800 border-slate-200',
    blue: 'bg-blue-100 text-blue-800 border-blue-200',
  };

  return (
    <div className={`p-4 rounded-2xl border ${colors[color]}`}>
      <div className="text-2xl font-bold tabular-nums">{value}</div>
      <div className="text-sm">{title}</div>
    </div>
  );
}

function StatusBadge({ exception }: { exception: ComplianceException }) {
  if (!exception.is_active) {
    return <span className="px-2 py-1 rounded-full text-xs bg-slate-100 text-slate-600">Revoked</span>;
  }
  if (!exception.is_valid) {
    return <span className="px-2 py-1 rounded-full text-xs bg-red-100 text-red-600">Expired</span>;
  }
  if (exception.days_until_expiration <= 14) {
    return <span className="px-2 py-1 rounded-full text-xs bg-yellow-100 text-yellow-600">Expiring Soon</span>;
  }
  return <span className="px-2 py-1 rounded-full text-xs bg-green-100 text-green-600">Active</span>;
}

function CreateExceptionModal({
  siteId,
  onClose,
  onSuccess
}: {
  siteId: string;
  onClose: () => void;
  onSuccess: () => void;
}) {
  const [formData, setFormData] = useState<{
    site_id: string;
    scope_type: 'runbook' | 'check' | 'control';
    item_id: string;
    device_filter: string;
    reason: string;
    compensating_control: string;
    risk_accepted_by: string;
    duration_days: number;
    action: 'suppress_alert' | 'skip_remediation' | 'both';
  }>({
    site_id: siteId,
    scope_type: 'runbook',
    item_id: '',
    device_filter: '',
    reason: '',
    compensating_control: '',
    risk_accepted_by: '',
    duration_days: 30,
    action: 'both'
  });
  const [error, setError] = useState('');

  const createMutation = useMutation({
    mutationFn: api.createException,
    onSuccess: () => onSuccess(),
    onError: (err: Error) => setError(err.message)
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    createMutation.mutate({
      ...formData,
      device_filter: formData.device_filter || undefined,
      compensating_control: formData.compensating_control || undefined
    });
  };

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center p-4 z-50 modal-backdrop">
      <div className="bg-white rounded-2xl max-w-2xl w-full max-h-[90vh] overflow-y-auto shadow-xl">
        <div className="p-6">
          <h3 className="text-xl font-bold mb-4">Create Compliance Exception</h3>

          {error && (
            <div className="mb-4 p-3 bg-red-100 text-red-700 rounded">{error}</div>
          )}

          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">Scope Type</label>
                <select
                  value={formData.scope_type}
                  onChange={(e) => setFormData({ ...formData, scope_type: e.target.value as 'runbook' | 'check' | 'control' })}
                  className="w-full rounded-md border-slate-300 shadow-sm"
                  required
                >
                  <option value="runbook">Runbook</option>
                  <option value="check">Check</option>
                  <option value="control">Control</option>
                </select>
              </div>

              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">Item ID</label>
                <input
                  type="text"
                  value={formData.item_id}
                  onChange={(e) => setFormData({ ...formData, item_id: e.target.value })}
                  placeholder="e.g., RB-WIN-PATCH-001"
                  className="w-full rounded-md border-slate-300 shadow-sm"
                  required
                />
              </div>
            </div>

            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">
                Device Filter (optional)
              </label>
              <input
                type="text"
                value={formData.device_filter}
                onChange={(e) => setFormData({ ...formData, device_filter: e.target.value })}
                placeholder="e.g., hostname:LEGACY-* or leave empty for all devices"
                className="w-full rounded-md border-slate-300 shadow-sm"
              />
              <p className="text-xs text-slate-500 mt-1">Leave empty to apply to all devices at this site</p>
            </div>

            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">Reason *</label>
              <textarea
                value={formData.reason}
                onChange={(e) => setFormData({ ...formData, reason: e.target.value })}
                placeholder="Explain why this exception is needed..."
                className="w-full rounded-md border-slate-300 shadow-sm"
                rows={3}
                required
                minLength={10}
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">
                Compensating Control (recommended)
              </label>
              <textarea
                value={formData.compensating_control}
                onChange={(e) => setFormData({ ...formData, compensating_control: e.target.value })}
                placeholder="What alternative protection is in place? (e.g., Network segmentation isolates the system)"
                className="w-full rounded-md border-slate-300 shadow-sm"
                rows={2}
              />
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">
                  Risk Accepted By *
                </label>
                <input
                  type="text"
                  value={formData.risk_accepted_by}
                  onChange={(e) => setFormData({ ...formData, risk_accepted_by: e.target.value })}
                  placeholder="Name and role (e.g., Dr. Smith, Practice Owner)"
                  className="w-full rounded-md border-slate-300 shadow-sm"
                  required
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">Duration</label>
                <select
                  value={formData.duration_days}
                  onChange={(e) => setFormData({ ...formData, duration_days: parseInt(e.target.value) })}
                  className="w-full rounded-md border-slate-300 shadow-sm"
                >
                  <option value={7}>7 days</option>
                  <option value={14}>14 days</option>
                  <option value={30}>30 days</option>
                  <option value={60}>60 days</option>
                  <option value={90}>90 days (max for partners)</option>
                </select>
              </div>
            </div>

            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">Action</label>
              <select
                value={formData.action}
                onChange={(e) => setFormData({ ...formData, action: e.target.value as 'suppress_alert' | 'skip_remediation' | 'both' })}
                className="w-full rounded-md border-slate-300 shadow-sm"
              >
                <option value="both">Suppress Alert + Skip Remediation</option>
                <option value="suppress_alert">Suppress Alert Only</option>
                <option value="skip_remediation">Skip Remediation Only</option>
              </select>
              <p className="text-xs text-slate-500 mt-1">
                Choose what happens when this check fails
              </p>
            </div>

            <div className="flex justify-end gap-3 pt-4 border-t">
              <button
                type="button"
                onClick={onClose}
                className="px-4 py-2 text-slate-700 hover:bg-indigo-50 rounded-md"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={createMutation.isPending}
                className="px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:opacity-50"
              >
                {createMutation.isPending ? 'Creating...' : 'Create Exception'}
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>
  );
}

function ExceptionDetailsModal({
  exception,
  onClose
}: {
  exception: ComplianceException;
  onClose: () => void;
}) {
  const { data: auditLog = [] } = useQuery({
    queryKey: ['exception-audit', exception.id],
    queryFn: () => api.getAuditLog(exception.id)
  });

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center p-4 z-50 modal-backdrop">
      <div className="bg-white rounded-2xl max-w-2xl w-full max-h-[90vh] overflow-y-auto shadow-xl">
        <div className="p-6">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-xl font-bold">Exception Details</h3>
            <button onClick={onClose} className="text-slate-500 hover:text-indigo-600">
              <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>

          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="text-sm text-slate-500">Exception ID</label>
                <p className="font-medium">{exception.id}</p>
              </div>
              <div>
                <label className="text-sm text-slate-500">Status</label>
                <p><StatusBadge exception={exception} /></p>
              </div>
              <div>
                <label className="text-sm text-slate-500">Scope</label>
                <p className="font-medium">{exception.scope_type}: {exception.item_id}</p>
              </div>
              <div>
                <label className="text-sm text-slate-500">Device Filter</label>
                <p className="font-medium">{exception.device_filter || 'All devices'}</p>
              </div>
              <div>
                <label className="text-sm text-slate-500">Action</label>
                <p className="font-medium">{exception.action.replace('_', ' ')}</p>
              </div>
              <div>
                <label className="text-sm text-slate-500">Approval Tier</label>
                <p className="font-medium">{exception.approval_tier}</p>
              </div>
            </div>

            <div className="border-t pt-4">
              <label className="text-sm text-slate-500">Reason</label>
              <p className="mt-1">{exception.reason}</p>
            </div>

            {exception.compensating_control && (
              <div>
                <label className="text-sm text-slate-500">Compensating Control</label>
                <p className="mt-1">{exception.compensating_control}</p>
              </div>
            )}

            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="text-sm text-slate-500">Risk Accepted By</label>
                <p className="font-medium">{exception.risk_accepted_by}</p>
              </div>
              <div>
                <label className="text-sm text-slate-500">Approved By</label>
                <p className="font-medium">{exception.approved_by}</p>
              </div>
              <div>
                <label className="text-sm text-slate-500">Start Date</label>
                <p className="font-medium">{new Date(exception.start_date).toLocaleDateString()}</p>
              </div>
              <div>
                <label className="text-sm text-slate-500">Expiration Date</label>
                <p className="font-medium">{new Date(exception.expiration_date).toLocaleDateString()}</p>
              </div>
            </div>

            <div className="border-t pt-4">
              <h4 className="font-medium mb-2">Audit Log</h4>
              <div className="space-y-2">
                {auditLog.map((entry, i) => (
                  <div key={i} className="text-sm bg-slate-50 p-2 rounded">
                    <span className="font-medium">{entry.action}</span> by {entry.performed_by}
                    <span className="text-slate-400 ml-2">
                      {new Date(entry.performed_at).toLocaleString()}
                    </span>
                    {entry.notes && <p className="text-slate-600 mt-1">{entry.notes}</p>}
                  </div>
                ))}
              </div>
            </div>
          </div>

          <div className="flex justify-end pt-4 mt-4 border-t">
            <button
              onClick={onClose}
              className="px-4 py-2 bg-indigo-50 text-indigo-700 rounded-md hover:bg-indigo-100"
            >
              Close
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default PartnerExceptionManagement;
