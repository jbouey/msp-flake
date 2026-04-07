import React, { useState, useEffect } from 'react';
import { useClient } from './ClientContext';
import { formatTimeAgo } from '../constants';
import { DisclaimerFooter } from '../components/composed';
import { csrfHeaders } from '../utils/csrf';

interface EscalationTicket {
  id: string;
  site_id: string;
  site_name?: string;
  incident_type: string;
  severity: string;
  priority: string;
  title: string;
  summary: string;
  raw_data: Record<string, unknown> | null;
  hipaa_controls: string[];
  attempted_actions: Record<string, unknown>[] | null;
  recommended_action: string | null;
  status: string;
  acknowledged_at: string | null;
  resolved_at: string | null;
  resolved_by: string | null;
  resolution_notes: string | null;
  created_at: string;
  sla_breached: boolean;
  recurrence_count?: number;
  escalated_to_l4?: boolean;
}

interface EscalationPrefs {
  escalation_mode: 'partner' | 'direct' | 'both';
  email_enabled: boolean;
  email_recipients: string[];
  slack_enabled: boolean;
  slack_webhook_url: string | null;
  teams_enabled: boolean;
  teams_webhook_url: string | null;
  escalation_timeout_minutes: number;
  configured: boolean;
}

interface TicketCounts {
  open_count: number;
  acknowledged_count: number;
  resolved_count: number;
  sla_breached_count: number;
}

type StatusFilter = 'all' | 'open' | 'acknowledged' | 'resolved';

const fetchOpts: RequestInit = { credentials: 'include' };
const postJson = (body: Record<string, unknown>): RequestInit => ({
  method: 'POST', headers: { 'Content-Type': 'application/json', ...csrfHeaders() },
  credentials: 'include', body: JSON.stringify(body),
});
const putJson = (body: Record<string, unknown>): RequestInit => ({
  method: 'PUT', headers: { 'Content-Type': 'application/json', ...csrfHeaders() },
  credentials: 'include', body: JSON.stringify(body),
});

export const ClientEscalations: React.FC = () => {
  const { user, isAuthenticated } = useClient();

  const [tickets, setTickets] = useState<EscalationTicket[]>([]);
  const [counts, setCounts] = useState<TicketCounts | null>(null);
  const [prefs, setPrefs] = useState<EscalationPrefs | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const [selectedTicket, setSelectedTicket] = useState<EscalationTicket | null>(null);
  const [showResolveModal, setShowResolveModal] = useState(false);
  const [resolutionNotes, setResolutionNotes] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [showPrefs, setShowPrefs] = useState(false);

  // Editable prefs state
  const [editMode, setEditMode] = useState<'partner' | 'direct' | 'both'>('partner');
  const [editEmail, setEditEmail] = useState('');
  const [savingPrefs, setSavingPrefs] = useState(false);

  useEffect(() => { if (isAuthenticated) loadAll(); }, [isAuthenticated, statusFilter]);

  useEffect(() => {
    if (success) { const t = setTimeout(() => setSuccess(null), 3000); return () => clearTimeout(t); }
  }, [success]);

  const loadAll = async () => {
    setLoading(true);
    setError(null);
    const sp = statusFilter !== 'all' ? `?status=${statusFilter}` : '';
    try {
      const [ticketsRes, prefsRes] = await Promise.all([
        fetch(`/api/client/escalations${sp}`, fetchOpts),
        fetch('/api/client/escalation-preferences', fetchOpts),
      ]);
      if (ticketsRes.ok) {
        const d = await ticketsRes.json();
        setTickets(d.tickets || []);
        setCounts(d.counts || null);
      }
      if (prefsRes.ok) {
        const p = await prefsRes.json();
        setPrefs(p);
        setEditMode(p.escalation_mode || 'partner');
        setEditEmail((p.email_recipients || []).join(', '));
      }
    } catch { setError('Network error'); }
    finally { setLoading(false); }
  };

  const savePrefs = async () => {
    setSavingPrefs(true);
    try {
      const recipients = editEmail.split(',').map(e => e.trim()).filter(Boolean);
      const res = await fetch('/api/client/escalation-preferences', putJson({
        escalation_mode: editMode,
        email_enabled: recipients.length > 0,
        email_recipients: recipients,
        escalation_timeout_minutes: 60,
      }));
      if (res.ok) {
        setSuccess('Escalation preferences saved');
        setShowPrefs(false);
        loadAll();
      } else { const e = await res.json(); setError(e.detail || 'Save failed'); }
    } catch { setError('Network error'); }
    finally { setSavingPrefs(false); }
  };

  const handleAcknowledge = async (ticketId: string) => {
    try {
      const res = await fetch(`/api/client/escalations/${ticketId}/acknowledge`, postJson({}));
      if (res.ok) { setSuccess('Ticket acknowledged'); setSelectedTicket(null); loadAll(); }
      else { const e = await res.json(); setError(e.detail || 'Failed'); }
    } catch { setError('Network error'); }
  };

  const handleResolve = async () => {
    if (!selectedTicket || !resolutionNotes.trim()) return;
    setSubmitting(true);
    try {
      const res = await fetch(`/api/client/escalations/${selectedTicket.id}/resolve`, postJson({
        resolution_notes: resolutionNotes.trim(),
      }));
      if (res.ok) {
        setSuccess('Ticket resolved');
        setShowResolveModal(false);
        setResolutionNotes('');
        setSelectedTicket(null);
        loadAll();
      } else { const e = await res.json(); setError(e.detail || 'Failed'); }
    } catch { setError('Network error'); }
    finally { setSubmitting(false); }
  };

  const priorityColor = (p: string) => {
    switch (p) {
      case 'critical': return 'bg-health-critical/10 text-health-critical';
      case 'high': return 'bg-ios-orange/10 text-ios-orange';
      case 'medium': return 'bg-health-warning/10 text-health-warning';
      default: return 'bg-fill-secondary text-label-tertiary';
    }
  };
  const statusColor = (s: string) => {
    switch (s) {
      case 'open': return 'bg-health-critical/10 text-health-critical';
      case 'acknowledged': return 'bg-ios-blue/10 text-ios-blue';
      case 'resolved': return 'bg-health-healthy/10 text-health-healthy';
      default: return 'bg-fill-secondary text-label-tertiary';
    }
  };
  const severityColor = (s: string) => {
    switch (s) {
      case 'critical': return 'bg-health-critical text-white';
      case 'high': return 'bg-ios-orange text-white';
      case 'medium': return 'bg-health-warning text-white';
      default: return 'bg-label-tertiary text-white';
    }
  };

  const incidentLabel = (t: string) => t.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());

  const extractDetails = (raw: Record<string, unknown> | null) => {
    if (!raw) return null;
    const map: Record<string, string> = {
      hostname: 'Hostname', check_type: 'Check Type', message: 'Message',
      details: 'Details', service_name: 'Service', drift_type: 'Issue Type',
      expected: 'Expected', actual: 'Actual', os_type: 'OS Type',
    };
    const fields: { label: string; value: string }[] = [];
    for (const [k, label] of Object.entries(map)) {
      if (raw[k] !== undefined && raw[k] !== null) fields.push({ label, value: String(raw[k]) });
    }
    return fields.length ? fields : null;
  };

  const isAdmin = user?.role === 'owner' || user?.role === 'admin';
  const modeLabel = { partner: 'Partner handles L3s', direct: 'Direct to you', both: 'Both partner + you' };

  if (loading && !tickets.length) {
    return (
      <div className="text-center py-12">
        <div className="w-8 h-8 border-2 border-indigo-300 border-t-indigo-600 rounded-full animate-spin mx-auto mb-3" />
        <p className="text-sm text-slate-500">Loading escalations...</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {error && (
        <div className="bg-health-critical/10 border border-health-critical/20 rounded-xl px-4 py-3 text-sm text-health-critical flex justify-between">
          {error}
          <button onClick={() => setError(null)} className="text-health-critical/60 hover:text-health-critical">x</button>
        </div>
      )}
      {success && (
        <div className="bg-health-healthy/10 border border-health-healthy/20 rounded-xl px-4 py-3 text-sm text-health-healthy">{success}</div>
      )}

      {/* Header with prefs toggle */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold text-slate-900">Escalation Queue</h2>
          <p className="text-sm text-slate-500 mt-0.5">
            L3 incidents that require human attention &middot; Mode: <span className="font-medium">{modeLabel[prefs?.escalation_mode || 'partner']}</span>
          </p>
        </div>
        {isAdmin && (
          <button
            onClick={() => setShowPrefs(!showPrefs)}
            className="px-4 py-2 text-sm font-medium bg-white border border-slate-200 rounded-lg hover:bg-slate-50 transition"
          >
            Escalation Settings
          </button>
        )}
      </div>

      {/* Preferences panel */}
      {showPrefs && (
        <div className="bg-white rounded-2xl p-5 shadow-sm border border-slate-100 space-y-4">
          <h3 className="text-sm font-semibold text-slate-900">L3 Escalation Routing</h3>
          <div className="grid grid-cols-3 gap-3">
            {(['partner', 'direct', 'both'] as const).map(mode => (
              <button
                key={mode}
                onClick={() => setEditMode(mode)}
                className={`p-3 rounded-xl text-left border-2 transition ${
                  editMode === mode ? 'border-indigo-500 bg-indigo-50' : 'border-slate-200 hover:border-slate-300'
                }`}
              >
                <p className="text-sm font-semibold text-slate-900">{mode === 'partner' ? 'Partner Only' : mode === 'direct' ? 'Direct to Me' : 'Both'}</p>
                <p className="text-xs text-slate-500 mt-1">
                  {mode === 'partner' && 'MSP partner handles all L3 escalations'}
                  {mode === 'direct' && 'Get L3 alerts directly — skip the partner'}
                  {mode === 'both' && 'Both you and partner receive L3 alerts'}
                </p>
              </button>
            ))}
          </div>
          {editMode !== 'partner' && (
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">Notification Email(s)</label>
              <input
                type="text"
                value={editEmail}
                onChange={e => setEditEmail(e.target.value)}
                placeholder="admin@clinic.com, it@clinic.com"
                className="w-full px-4 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
              />
              <p className="text-xs text-slate-500 mt-1">Comma-separated. These addresses receive L3 escalation alerts.</p>
            </div>
          )}
          <div className="flex gap-3 justify-end">
            <button onClick={() => setShowPrefs(false)} className="px-4 py-2 text-sm text-slate-600 hover:text-slate-900">Cancel</button>
            <button
              onClick={savePrefs}
              disabled={savingPrefs}
              className="px-4 py-2 text-sm font-medium bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-50 transition"
            >
              {savingPrefs ? 'Saving...' : 'Save Preferences'}
            </button>
          </div>
        </div>
      )}

      {/* Summary cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="bg-white rounded-2xl p-5 shadow-sm border border-slate-100">
          <p className="text-xs font-medium text-slate-500 uppercase tracking-wide mb-1">Open</p>
          <p className="text-2xl font-bold text-health-critical tabular-nums">{counts?.open_count ?? 0}</p>
        </div>
        <div className="bg-white rounded-2xl p-5 shadow-sm border border-slate-100">
          <p className="text-xs font-medium text-slate-500 uppercase tracking-wide mb-1">Acknowledged</p>
          <p className="text-2xl font-bold text-ios-blue tabular-nums">{counts?.acknowledged_count ?? 0}</p>
        </div>
        <div className="bg-white rounded-2xl p-5 shadow-sm border border-slate-100">
          <p className="text-xs font-medium text-slate-500 uppercase tracking-wide mb-1">Resolved</p>
          <p className="text-2xl font-bold text-health-healthy tabular-nums">{counts?.resolved_count ?? 0}</p>
        </div>
        <div className="bg-white rounded-2xl p-5 shadow-sm border border-slate-100">
          <p className="text-xs font-medium text-slate-500 uppercase tracking-wide mb-1">SLA Breached</p>
          <p className={`text-2xl font-bold tabular-nums ${(counts?.sla_breached_count ?? 0) > 0 ? 'text-health-critical' : 'text-label-tertiary'}`}>
            {counts?.sla_breached_count ?? 0}
          </p>
        </div>
      </div>

      {/* Filter tabs */}
      <div className="flex gap-2">
        {(['all', 'open', 'acknowledged', 'resolved'] as StatusFilter[]).map(f => (
          <button key={f} onClick={() => setStatusFilter(f)}
            className={`px-4 py-2 text-sm font-medium rounded-lg transition ${
              statusFilter === f ? 'bg-indigo-600 text-white' : 'bg-white text-slate-600 hover:bg-indigo-50 border border-slate-200'
            }`}
          >{f.charAt(0).toUpperCase() + f.slice(1)}</button>
        ))}
      </div>

      {/* Ticket list */}
      <div className="bg-white rounded-2xl shadow-sm border border-slate-100 overflow-hidden">
        {tickets.length === 0 ? (
          <div className="p-12 text-center">
            <div className="w-16 h-16 bg-health-healthy/10 rounded-full flex items-center justify-center mx-auto mb-4">
              <svg className="w-8 h-8 text-health-healthy" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            </div>
            <h3 className="text-lg font-medium text-slate-900 mb-1">No escalations</h3>
            <p className="text-slate-500 text-sm">
              {statusFilter === 'all' ? 'All clear — no L3 escalation tickets.' : `No ${statusFilter} tickets.`}
            </p>
          </div>
        ) : (
          <table className="w-full">
            <thead className="bg-slate-50 border-b">
              <tr>
                <th className="px-5 py-3 text-left text-xs font-medium text-slate-500 uppercase">Priority</th>
                <th className="px-5 py-3 text-left text-xs font-medium text-slate-500 uppercase">Title</th>
                <th className="px-5 py-3 text-left text-xs font-medium text-slate-500 uppercase">Site</th>
                <th className="px-5 py-3 text-left text-xs font-medium text-slate-500 uppercase">Status</th>
                <th className="px-5 py-3 text-left text-xs font-medium text-slate-500 uppercase">Age</th>
                <th className="px-5 py-3 text-right text-xs font-medium text-slate-500 uppercase">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {tickets.map(ticket => (
                <tr key={ticket.id} className="hover:bg-indigo-50/30 transition">
                  <td className="px-5 py-4">
                    <span className={`px-2 py-1 text-xs font-semibold rounded-full ${priorityColor(ticket.priority)}`}>{ticket.priority}</span>
                  </td>
                  <td className="px-5 py-4">
                    <button onClick={() => setSelectedTicket(ticket)} className="text-left hover:text-indigo-600 transition">
                      <div className="flex items-center gap-1.5">
                        <p className="font-medium text-slate-900 text-sm">{ticket.title}</p>
                        {(ticket.recurrence_count ?? 0) > 0 && (
                          <span className="px-1.5 py-0.5 text-[10px] font-bold rounded-full bg-amber-100 text-amber-700">x{ticket.recurrence_count}</span>
                        )}
                      </div>
                      <p className="text-xs text-slate-500 mt-0.5 line-clamp-1">{ticket.summary}</p>
                    </button>
                  </td>
                  <td className="px-5 py-4 text-sm text-slate-600">{ticket.site_name || ticket.site_id}</td>
                  <td className="px-5 py-4">
                    <span className={`px-2 py-1 text-xs font-medium rounded-full ${statusColor(ticket.status)}`}>{ticket.status}</span>
                  </td>
                  <td className="px-5 py-4 text-sm text-slate-500 tabular-nums">{formatTimeAgo(ticket.created_at)}</td>
                  <td className="px-5 py-4 text-right">
                    <div className="flex items-center justify-end gap-2">
                      {ticket.status === 'open' && (
                        <button onClick={() => handleAcknowledge(ticket.id)}
                          className="px-3 py-1.5 text-xs font-medium bg-ios-blue/10 text-ios-blue rounded-lg hover:bg-ios-blue/20 transition"
                        >Acknowledge</button>
                      )}
                      {ticket.status !== 'resolved' && (
                        <button onClick={() => { setSelectedTicket(ticket); setShowResolveModal(true); }}
                          className="px-3 py-1.5 text-xs font-medium bg-health-healthy/10 text-health-healthy rounded-lg hover:bg-health-healthy/20 transition"
                        >Resolve</button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Ticket Detail Modal */}
      {selectedTicket && !showResolveModal && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
          <div className="bg-white rounded-2xl p-6 w-full max-w-2xl shadow-xl max-h-[80vh] overflow-y-auto">
            <div className="flex items-start justify-between mb-4">
              <div>
                <div className="flex items-center gap-2 mb-1">
                  <span className={`px-2.5 py-1 text-xs font-bold rounded-lg ${severityColor(selectedTicket.severity)}`}>
                    {selectedTicket.severity.toUpperCase()}
                  </span>
                  <span className={`px-2 py-0.5 text-xs font-medium rounded-full ${priorityColor(selectedTicket.priority)}`}>{selectedTicket.priority}</span>
                  <span className={`px-2 py-0.5 text-xs font-medium rounded-full ${statusColor(selectedTicket.status)}`}>{selectedTicket.status}</span>
                </div>
                <h3 className="text-lg font-semibold text-slate-900">{selectedTicket.title}</h3>
                <p className="text-sm text-slate-500">{selectedTicket.site_name || selectedTicket.site_id} &middot; {incidentLabel(selectedTicket.incident_type)}</p>
              </div>
              <button onClick={() => setSelectedTicket(null)} className="text-slate-400 hover:text-slate-600 text-xl">x</button>
            </div>

            <div className="space-y-4">
              <div>
                <p className="text-xs font-medium text-slate-500 uppercase mb-1">Summary</p>
                <p className="text-sm text-slate-700">{selectedTicket.summary}</p>
              </div>

              {(() => {
                const fields = extractDetails(selectedTicket.raw_data);
                if (!fields) return null;
                return (
                  <div>
                    <p className="text-xs font-medium text-slate-500 uppercase mb-2">Incident Details</p>
                    <div className="bg-slate-50 rounded-lg p-3 grid grid-cols-2 gap-x-4 gap-y-2">
                      {fields.map(f => (
                        <div key={f.label}>
                          <p className="text-[10px] font-medium text-slate-400 uppercase">{f.label}</p>
                          <p className="text-sm text-slate-700 break-words">{f.value}</p>
                        </div>
                      ))}
                    </div>
                  </div>
                );
              })()}

              {selectedTicket.recommended_action && (
                <div className="bg-indigo-50 rounded-lg p-3">
                  <p className="text-xs font-medium text-indigo-600 uppercase mb-1">Recommended Action</p>
                  <p className="text-sm text-indigo-800">{selectedTicket.recommended_action}</p>
                </div>
              )}

              {selectedTicket.attempted_actions && Array.isArray(selectedTicket.attempted_actions) && selectedTicket.attempted_actions.length > 0 && (
                <div>
                  <p className="text-xs font-medium text-slate-500 uppercase mb-2">Attempted Auto-Healing</p>
                  <div className="bg-amber-50 border border-amber-100 rounded-lg p-3 space-y-1.5">
                    {selectedTicket.attempted_actions.map((action, i) => (
                      <div key={i} className="flex items-start gap-2 text-sm">
                        <span className="text-amber-500 mt-0.5 shrink-0">
                          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                          </svg>
                        </span>
                        <span className="text-amber-800">{typeof action === 'string' ? action : JSON.stringify(action)}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {selectedTicket.hipaa_controls.length > 0 && (
                <div>
                  <p className="text-xs font-medium text-slate-500 uppercase mb-1">HIPAA Controls</p>
                  <div className="flex flex-wrap gap-1">
                    {selectedTicket.hipaa_controls.map(c => (
                      <span key={c} className="px-2 py-0.5 bg-purple-50 text-purple-700 text-xs rounded-full">{c}</span>
                    ))}
                  </div>
                </div>
              )}

              {selectedTicket.resolution_notes && (
                <div className="bg-health-healthy/10 rounded-lg p-3">
                  <p className="text-xs font-medium text-health-healthy uppercase mb-1">Resolution</p>
                  <p className="text-sm text-health-healthy">{selectedTicket.resolution_notes}</p>
                  {selectedTicket.resolved_by && (
                    <p className="text-xs text-health-healthy mt-1">Resolved by {selectedTicket.resolved_by} on {new Date(selectedTicket.resolved_at!).toLocaleString()}</p>
                  )}
                </div>
              )}

              <div className="grid grid-cols-2 gap-4 text-sm">
                <div>
                  <p className="text-xs text-slate-500">Created</p>
                  <p className="text-slate-700">{new Date(selectedTicket.created_at).toLocaleString()}</p>
                </div>
                {selectedTicket.acknowledged_at && (
                  <div>
                    <p className="text-xs text-slate-500">Acknowledged</p>
                    <p className="text-slate-700">{new Date(selectedTicket.acknowledged_at).toLocaleString()}</p>
                  </div>
                )}
              </div>
            </div>

            {(selectedTicket.recurrence_count ?? 0) > 0 && (
              <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 mt-4">
                <p className="text-xs font-semibold text-amber-800 uppercase mb-1">Recurring Issue</p>
                <p className="text-sm text-amber-700">
                  This issue has recurred {selectedTicket.recurrence_count} time{(selectedTicket.recurrence_count ?? 0) > 1 ? 's' : ''}.
                  Contact your MSP partner or Central Command if it cannot be permanently resolved.
                </p>
              </div>
            )}

            <div className="flex gap-2 justify-end mt-6 pt-4 border-t">
              {selectedTicket.status === 'open' && (
                <button onClick={() => handleAcknowledge(selectedTicket.id)}
                  className="px-4 py-2 text-sm font-medium bg-ios-blue text-white rounded-lg hover:bg-ios-blue/90 transition"
                >Acknowledge</button>
              )}
              {selectedTicket.status !== 'resolved' && (
                <button onClick={() => setShowResolveModal(true)}
                  className="px-4 py-2 text-sm font-medium bg-health-healthy text-white rounded-lg hover:bg-health-healthy/90 transition"
                >Resolve</button>
              )}
              <button onClick={() => setSelectedTicket(null)} className="px-4 py-2 text-sm text-slate-600 hover:text-slate-900 transition">Close</button>
            </div>
          </div>
        </div>
      )}

      {/* Resolve Modal */}
      {showResolveModal && selectedTicket && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
          <div className="bg-white rounded-2xl p-6 w-full max-w-md shadow-xl">
            <h3 className="text-lg font-semibold text-slate-900 mb-2">Resolve Ticket</h3>
            <p className="text-sm text-slate-500 mb-4">{selectedTicket.title}</p>
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">Resolution Notes</label>
              <textarea
                value={resolutionNotes}
                onChange={e => setResolutionNotes(e.target.value)}
                placeholder="Describe what was done to resolve this issue..."
                rows={4}
                className="w-full px-4 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-green-500 focus:border-transparent resize-none"
              />
            </div>
            <div className="flex gap-3 justify-end mt-4">
              <button onClick={() => { setShowResolveModal(false); setResolutionNotes(''); }} className="px-4 py-2 text-slate-600 hover:text-slate-900 transition">Cancel</button>
              <button onClick={handleResolve} disabled={submitting || !resolutionNotes.trim()}
                className="px-4 py-2 bg-health-healthy text-white font-medium rounded-lg hover:bg-health-healthy/90 disabled:opacity-50 transition"
              >{submitting ? 'Resolving...' : 'Resolve'}</button>
            </div>
          </div>
        </div>
      )}
      <DisclaimerFooter />
    </div>
  );
};

export default ClientEscalations;
