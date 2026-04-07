import React, { useState, useEffect } from 'react';
import { usePartner } from './PartnerContext';
import { InfoTip } from '../components/shared';
import { formatTimeAgo } from '../constants';
import { csrfHeaders } from '../utils/csrf';

interface EscalationTicket {
  id: string;
  partner_id: string;
  site_id: string;
  site_name?: string;
  incident_id: string;
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
  acknowledged_by: string | null;
  resolved_at: string | null;
  resolved_by: string | null;
  resolution_notes: string | null;
  created_at: string;
  sla_target_at: string | null;
  sla_breached: boolean;
  updated_at: string;
  recurrence_count?: number;
  previous_ticket_id?: string | null;
  escalated_to_l4?: boolean;
}

interface TicketCounts {
  open_count: number;
  acknowledged_count: number;
  resolved_count: number;
  sla_breached_count: number;
}

interface SlaMetrics {
  total_tickets: number;
  sla_breaches: number;
  sla_compliance_rate: number;
  resolved_tickets: number;
  avg_response_minutes: number;
  avg_resolution_minutes: number;
  by_priority: Record<string, number>;
}

type StatusFilter = 'all' | 'open' | 'acknowledged' | 'resolved';

export const PartnerEscalations: React.FC = () => {
  const { apiKey, isAuthenticated } = usePartner();

  const [tickets, setTickets] = useState<EscalationTicket[]>([]);
  const [counts, setCounts] = useState<TicketCounts | null>(null);
  const [slaMetrics, setSlaMetrics] = useState<SlaMetrics | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');

  // Detail/action modals
  const [selectedTicket, setSelectedTicket] = useState<EscalationTicket | null>(null);
  const [showAckModal, setShowAckModal] = useState(false);
  const [showResolveModal, setShowResolveModal] = useState(false);
  const [showL4Modal, setShowL4Modal] = useState(false);
  const [ackBy, setAckBy] = useState('');
  const [resolveBy, setResolveBy] = useState('');
  const [resolutionNotes, setResolutionNotes] = useState('');
  const [l4EscalatedBy, setL4EscalatedBy] = useState('');
  const [l4Notes, setL4Notes] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [success, setSuccess] = useState<string | null>(null);

  const fetchOptions: RequestInit = apiKey
    ? { headers: { 'X-API-Key': apiKey } }
    : { credentials: 'include' };

  const postOptions = (body: Record<string, unknown>): RequestInit => apiKey
    ? { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-API-Key': apiKey }, body: JSON.stringify(body) }
    : { method: 'POST', headers: { 'Content-Type': 'application/json', ...csrfHeaders() }, credentials: 'include', body: JSON.stringify(body) };

  useEffect(() => {
    if (isAuthenticated) loadData();
  }, [isAuthenticated, statusFilter]);

  const loadData = async () => {
    setLoading(true);
    setError(null);

    const statusParam = statusFilter !== 'all' ? `?status=${statusFilter}` : '';

    try {
      const [ticketsRes, slaRes] = await Promise.all([
        fetch(`/api/partners/me/notifications/tickets${statusParam}`, fetchOptions),
        fetch('/api/partners/me/notifications/sla/metrics', fetchOptions),
      ]);

      if (ticketsRes.ok) {
        const data = await ticketsRes.json();
        setTickets(data.tickets || []);
        setCounts(data.counts || null);
      } else {
        setError('Failed to load escalation tickets');
      }

      if (slaRes.ok) {
        const data = await slaRes.json();
        setSlaMetrics(data.metrics || null);
      }
    } catch (e) {
      setError('Network error loading escalations');
    } finally {
      setLoading(false);
    }
  };

  const handleAcknowledge = async () => {
    if (!selectedTicket || !ackBy.trim()) return;
    setSubmitting(true);
    try {
      const res = await fetch(
        `/api/partners/me/notifications/tickets/${selectedTicket.id}/acknowledge`,
        postOptions({ acknowledged_by: ackBy.trim() })
      );
      if (res.ok) {
        setSuccess('Ticket acknowledged');
        setShowAckModal(false);
        setAckBy('');
        setSelectedTicket(null);
        loadData();
      } else {
        const err = await res.json();
        setError(err.detail || 'Failed to acknowledge');
      }
    } catch {
      setError('Network error');
    } finally {
      setSubmitting(false);
    }
  };

  const handleResolve = async () => {
    if (!selectedTicket || !resolveBy.trim() || !resolutionNotes.trim()) return;
    setSubmitting(true);
    try {
      const res = await fetch(
        `/api/partners/me/notifications/tickets/${selectedTicket.id}/resolve`,
        postOptions({ resolved_by: resolveBy.trim(), resolution_notes: resolutionNotes.trim() })
      );
      if (res.ok) {
        setSuccess('Ticket resolved — client has been notified');
        setShowResolveModal(false);
        setResolveBy('');
        setResolutionNotes('');
        setSelectedTicket(null);
        loadData();
      } else {
        const err = await res.json();
        setError(err.detail || 'Failed to resolve');
      }
    } catch {
      setError('Network error');
    } finally {
      setSubmitting(false);
    }
  };

  const handleEscalateToL4 = async () => {
    if (!selectedTicket || !l4EscalatedBy.trim() || !l4Notes.trim()) return;
    setSubmitting(true);
    try {
      const res = await fetch(
        `/api/partners/me/notifications/tickets/${selectedTicket.id}/escalate-to-l4`,
        postOptions({ escalated_by: l4EscalatedBy.trim(), notes: l4Notes.trim() })
      );
      if (res.ok) {
        setSuccess('Ticket escalated to Central Command (L4)');
        setShowL4Modal(false);
        setL4EscalatedBy('');
        setL4Notes('');
        setSelectedTicket(null);
        loadData();
      } else {
        const err = await res.json();
        setError(err.detail || 'Failed to escalate');
      }
    } catch {
      setError('Network error');
    } finally {
      setSubmitting(false);
    }
  };

  const priorityColor = (p: string) => {
    switch (p) {
      case 'critical': return 'bg-health-critical/10 text-health-critical';
      case 'high': return 'bg-ios-orange/10 text-ios-orange';
      case 'medium': return 'bg-health-warning/10 text-health-warning';
      case 'low': return 'bg-fill-secondary text-label-tertiary';
      default: return 'bg-fill-secondary text-label-tertiary';
    }
  };

  const statusColor = (s: string) => {
    switch (s) {
      case 'open': return 'bg-health-critical/10 text-health-critical';
      case 'acknowledged': return 'bg-ios-blue/10 text-ios-blue';
      case 'resolved': return 'bg-health-healthy/10 text-health-healthy';
      case 'escalated_to_l4': return 'bg-ios-purple/10 text-ios-purple';
      default: return 'bg-fill-secondary text-label-tertiary';
    }
  };


  const formatMinutes = (m: number) => {
    if (m < 60) return `${Math.round(m)}m`;
    if (m < 1440) return `${(m / 60).toFixed(1)}h`;
    return `${(m / 1440).toFixed(1)}d`;
  };

  const incidentTypeLabel = (type: string) => {
    return type
      .replace(/_/g, ' ')
      .replace(/\b\w/g, c => c.toUpperCase());
  };

  const severityColor = (s: string) => {
    switch (s) {
      case 'critical': return 'bg-health-critical text-white';
      case 'high': return 'bg-ios-orange text-white';
      case 'medium': return 'bg-health-warning text-white';
      case 'low': return 'bg-label-tertiary text-white';
      default: return 'bg-label-tertiary text-white';
    }
  };

  const extractRawDataFields = (raw: Record<string, unknown> | null) => {
    if (!raw) return null;
    const fields: { label: string; value: string }[] = [];
    const keyMap: Record<string, string> = {
      hostname: 'Hostname',
      appliance_id: 'Appliance',
      check_type: 'Check Type',
      message: 'Message',
      details: 'Details',
      drift_type: 'Issue Type',
      expected: 'Expected',
      actual: 'Actual',
      service_name: 'Service',
      package_name: 'Package',
      os_type: 'OS Type',
    };
    for (const [key, label] of Object.entries(keyMap)) {
      if (raw[key] !== undefined && raw[key] !== null) {
        fields.push({ label, value: String(raw[key]) });
      }
    }
    return fields.length > 0 ? fields : null;
  };

  const slaTimeRemaining = (ticket: EscalationTicket) => {
    if (!ticket.sla_target_at || ticket.status === 'resolved') return null;
    const remaining = new Date(ticket.sla_target_at).getTime() - Date.now();
    if (remaining <= 0) return 'BREACHED';
    const mins = Math.floor(remaining / 60000);
    if (mins < 60) return `${mins}m left`;
    return `${Math.floor(mins / 60)}h ${mins % 60}m left`;
  };

  // Clear success after 3s
  useEffect(() => {
    if (success) {
      const t = setTimeout(() => setSuccess(null), 3000);
      return () => clearTimeout(t);
    }
  }, [success]);

  if (loading && !tickets.length) {
    return (
      <div className="text-center py-12">
        <div className="w-8 h-8 border-2 border-indigo-300 border-t-indigo-600 rounded-full animate-spin mx-auto mb-3"></div>
        <p className="text-sm text-slate-500">Loading escalation queue...</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Feedback banners */}
      {error && (
        <div className="bg-health-critical/10 border border-health-critical/20 rounded-xl px-4 py-3 text-sm text-health-critical flex justify-between">
          {error}
          <button onClick={() => setError(null)} className="text-health-critical/60 hover:text-health-critical">x</button>
        </div>
      )}
      {success && (
        <div className="bg-health-healthy/10 border border-health-healthy/20 rounded-xl px-4 py-3 text-sm text-health-healthy">
          {success}
        </div>
      )}

      {/* SLA Summary Cards */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
        <div className="bg-white rounded-2xl p-5 shadow-sm border border-slate-100">
          <p className="text-xs font-medium text-slate-500 uppercase tracking-wide mb-1">Open<InfoTip text="Issues escalated to your team that need attention." /></p>
          <p className="text-2xl font-bold text-health-critical tabular-nums">{counts?.open_count ?? 0}</p>
        </div>
        <div className="bg-white rounded-2xl p-5 shadow-sm border border-slate-100">
          <p className="text-xs font-medium text-slate-500 uppercase tracking-wide mb-1">Acknowledged<InfoTip text="Tickets your team has seen and accepted responsibility for." /></p>
          <p className="text-2xl font-bold text-ios-blue tabular-nums">{counts?.acknowledged_count ?? 0}</p>
        </div>
        <div className="bg-white rounded-2xl p-5 shadow-sm border border-slate-100">
          <p className="text-xs font-medium text-slate-500 uppercase tracking-wide mb-1">Resolved<InfoTip text="Issues that have been fixed and closed." /></p>
          <p className="text-2xl font-bold text-health-healthy tabular-nums">{counts?.resolved_count ?? 0}</p>
        </div>
        <div className="bg-white rounded-2xl p-5 shadow-sm border border-slate-100">
          <p className="text-xs font-medium text-slate-500 uppercase tracking-wide mb-1">SLA Breached<InfoTip text="Tickets that exceeded the agreed response time." /></p>
          <p className={`text-2xl font-bold tabular-nums ${(counts?.sla_breached_count ?? 0) > 0 ? 'text-health-critical' : 'text-label-tertiary'}`}>
            {counts?.sla_breached_count ?? 0}
          </p>
        </div>
        <div className="bg-white rounded-2xl p-5 shadow-sm border border-slate-100">
          <p className="text-xs font-medium text-slate-500 uppercase tracking-wide mb-1">Avg Response<InfoTip text="Average time between ticket creation and first acknowledgment." /></p>
          <p className="text-2xl font-bold text-slate-700 tabular-nums">
            {slaMetrics ? formatMinutes(slaMetrics.avg_response_minutes) : '-'}
          </p>
        </div>
      </div>

      {/* Filter tabs */}
      <div className="flex gap-2">
        {(['all', 'open', 'acknowledged', 'resolved'] as StatusFilter[]).map(f => (
          <button
            key={f}
            onClick={() => setStatusFilter(f)}
            className={`px-4 py-2 text-sm font-medium rounded-lg transition ${
              statusFilter === f
                ? 'bg-indigo-600 text-white'
                : 'bg-white text-slate-600 hover:bg-indigo-50 border border-slate-200'
            }`}
          >
            {f.charAt(0).toUpperCase() + f.slice(1)}
          </button>
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
              {statusFilter === 'all'
                ? 'All clear — no L3 escalation tickets.'
                : `No ${statusFilter} tickets.`}
            </p>
          </div>
        ) : (
          <>
            {/* Mobile card view */}
            <div className="md:hidden space-y-2 p-4">
              {tickets.map(ticket => {
                const sla = slaTimeRemaining(ticket);
                return (
                  <div
                    key={ticket.id}
                    className="rounded-xl border border-slate-200 p-4 hover:bg-indigo-50/30 transition cursor-pointer"
                    onClick={() => setSelectedTicket(ticket)}
                  >
                    <div className="flex items-center justify-between mb-2">
                      <div className="flex items-center gap-2 min-w-0">
                        <span className={`px-2 py-1 text-xs font-semibold rounded-full flex-shrink-0 ${priorityColor(ticket.priority)}`}>
                          {ticket.priority}
                        </span>
                        <h3 className="text-sm font-semibold text-slate-900 truncate">{ticket.title}</h3>
                        {(ticket.recurrence_count ?? 0) > 0 && (
                          <span className="px-1.5 py-0.5 text-[10px] font-bold rounded-full bg-amber-100 text-amber-700 flex-shrink-0">
                            x{ticket.recurrence_count}
                          </span>
                        )}
                      </div>
                      <span className={`px-2 py-1 text-xs font-medium rounded-full flex-shrink-0 ml-2 ${statusColor(ticket.status)}`}>
                        {ticket.status}
                      </span>
                    </div>
                    <p className="text-xs text-slate-500 line-clamp-1 mb-2">{ticket.summary}</p>
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-3 text-xs text-slate-500">
                        <span>{ticket.site_name || ticket.site_id}</span>
                        <span className="tabular-nums">{formatTimeAgo(ticket.created_at)}</span>
                        {sla && (
                          <span className={`font-medium ${sla === 'BREACHED' ? 'text-health-critical' : 'text-health-warning'}`}>
                            {sla}
                          </span>
                        )}
                      </div>
                      <div className="flex items-center gap-1.5">
                        {ticket.status === 'open' && (
                          <button
                            onClick={(e) => { e.stopPropagation(); setSelectedTicket(ticket); setShowAckModal(true); }}
                            className="px-3 py-1.5 text-xs font-medium bg-ios-blue/10 text-ios-blue rounded-lg hover:bg-ios-blue/20 transition min-h-[44px] min-w-[44px] flex items-center justify-center"
                          >
                            Ack
                          </button>
                        )}
                        {(ticket.status === 'open' || ticket.status === 'acknowledged') && (
                          <button
                            onClick={(e) => { e.stopPropagation(); setSelectedTicket(ticket); setShowResolveModal(true); }}
                            className="px-3 py-1.5 text-xs font-medium bg-health-healthy/10 text-health-healthy rounded-lg hover:bg-health-healthy/20 transition min-h-[44px] min-w-[44px] flex items-center justify-center"
                          >
                            Resolve
                          </button>
                        )}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
            {/* Desktop table view */}
            <div className="hidden md:block">
              <table className="w-full">
                <thead className="bg-slate-50 border-b">
                  <tr>
                    <th className="px-5 py-3 text-left text-xs font-medium text-slate-500 uppercase">Priority</th>
                    <th className="px-5 py-3 text-left text-xs font-medium text-slate-500 uppercase">Title</th>
                    <th className="px-5 py-3 text-left text-xs font-medium text-slate-500 uppercase">Site</th>
                    <th className="px-5 py-3 text-left text-xs font-medium text-slate-500 uppercase">Status</th>
                    <th className="px-5 py-3 text-left text-xs font-medium text-slate-500 uppercase">SLA<InfoTip text="Time remaining before this ticket requires acknowledgment per your service agreement." /></th>
                    <th className="px-5 py-3 text-left text-xs font-medium text-slate-500 uppercase">Age</th>
                    <th className="px-5 py-3 text-right text-xs font-medium text-slate-500 uppercase">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {tickets.map(ticket => {
                    const sla = slaTimeRemaining(ticket);
                    return (
                      <tr key={ticket.id} className="hover:bg-indigo-50/30 transition">
                        <td className="px-5 py-4">
                          <span className={`px-2 py-1 text-xs font-semibold rounded-full ${priorityColor(ticket.priority)}`}>
                            {ticket.priority}
                          </span>
                        </td>
                        <td className="px-5 py-4">
                          <button
                            onClick={() => setSelectedTicket(ticket)}
                            className="text-left hover:text-indigo-600 transition"
                          >
                            <div className="flex items-center gap-1.5">
                              <p className="font-medium text-slate-900 text-sm">{ticket.title}</p>
                              {(ticket.recurrence_count ?? 0) > 0 && (
                                <span className="px-1.5 py-0.5 text-[10px] font-bold rounded-full bg-amber-100 text-amber-700">
                                  x{ticket.recurrence_count}
                                </span>
                              )}
                            </div>
                            <p className="text-xs text-slate-500 mt-0.5 line-clamp-1">{ticket.summary}</p>
                          </button>
                        </td>
                        <td className="px-5 py-4 text-sm text-slate-600">{ticket.site_name || ticket.site_id}</td>
                        <td className="px-5 py-4">
                          <span className={`px-2 py-1 text-xs font-medium rounded-full ${statusColor(ticket.status)}`}>
                            {ticket.status}
                          </span>
                        </td>
                        <td className="px-5 py-4">
                          {sla && (
                            <span className={`text-xs font-medium ${sla === 'BREACHED' ? 'text-health-critical' : 'text-health-warning'}`}>
                              {sla}
                            </span>
                          )}
                        </td>
                        <td className="px-5 py-4 text-sm text-slate-500 tabular-nums">{formatTimeAgo(ticket.created_at)}</td>
                        <td className="px-5 py-4 text-right">
                          <div className="flex items-center justify-end gap-2">
                            {ticket.status === 'open' && (
                              <button
                                onClick={() => { setSelectedTicket(ticket); setShowAckModal(true); }}
                                className="px-3 py-1.5 text-xs font-medium bg-ios-blue/10 text-ios-blue rounded-lg hover:bg-ios-blue/20 transition"
                              >
                                Acknowledge
                              </button>
                            )}
                            {(ticket.status === 'open' || ticket.status === 'acknowledged') && (
                              <button
                                onClick={() => { setSelectedTicket(ticket); setShowResolveModal(true); }}
                                className="px-3 py-1.5 text-xs font-medium bg-health-healthy/10 text-health-healthy rounded-lg hover:bg-health-healthy/20 transition"
                              >
                                Resolve
                              </button>
                            )}
                            {ticket.status !== 'escalated_to_l4' && (ticket.recurrence_count ?? 0) > 0 && (
                              <button
                                onClick={() => { setSelectedTicket(ticket); setShowL4Modal(true); }}
                                className="px-3 py-1.5 text-xs font-medium bg-purple-50 text-purple-700 rounded-lg hover:bg-purple-100 transition"
                                title="Recurring issue — escalate to Central Command"
                              >
                                L4
                              </button>
                            )}
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>

      {/* Ticket Detail Modal */}
      {selectedTicket && !showAckModal && !showResolveModal && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
          <div className="bg-white rounded-2xl p-6 w-full max-w-2xl shadow-xl max-h-[80vh] overflow-y-auto">
            <div className="flex items-start justify-between mb-4">
              <div>
                <div className="flex items-center gap-2 mb-1">
                  <span className={`px-2 py-0.5 text-xs font-semibold rounded-full ${priorityColor(selectedTicket.priority)}`}>
                    {selectedTicket.priority}
                  </span>
                  <span className={`px-2 py-0.5 text-xs font-medium rounded-full ${statusColor(selectedTicket.status)}`}>
                    {selectedTicket.status}
                  </span>
                  {selectedTicket.sla_breached && (
                    <span className="px-2 py-0.5 text-xs font-semibold rounded-full bg-health-critical text-white">SLA BREACHED</span>
                  )}
                </div>
                <h3 className="text-lg font-semibold text-slate-900">{selectedTicket.title}</h3>
                <p className="text-sm text-slate-500">{selectedTicket.site_name || selectedTicket.site_id}</p>
              </div>
              <button onClick={() => setSelectedTicket(null)} className="text-slate-400 hover:text-slate-600 text-xl">x</button>
            </div>

            <div className="space-y-4">
              {/* Incident type + severity row */}
              <div className="flex items-center gap-3">
                <span className={`px-2.5 py-1 text-xs font-bold rounded-lg ${severityColor(selectedTicket.severity)}`}>
                  {selectedTicket.severity.toUpperCase()}
                </span>
                <span className="text-sm font-medium text-slate-700">
                  {incidentTypeLabel(selectedTicket.incident_type)}
                </span>
              </div>

              <div>
                <p className="text-xs font-medium text-slate-500 uppercase mb-1">Summary</p>
                <p className="text-sm text-slate-700">{selectedTicket.summary}</p>
              </div>

              {/* Incident details from raw_data */}
              {(() => {
                const fields = extractRawDataFields(selectedTicket.raw_data);
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

              {/* Attempted auto-healing — formatted list */}
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
                        <span className="text-amber-800">
                          {typeof action === 'string' ? action : JSON.stringify(action)}
                        </span>
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

              <div className="grid grid-cols-2 gap-4 text-sm">
                <div>
                  <p className="text-xs text-slate-500">Created</p>
                  <p className="text-slate-700">{new Date(selectedTicket.created_at).toLocaleString()}</p>
                </div>
                {selectedTicket.acknowledged_at && (
                  <div>
                    <p className="text-xs text-slate-500">Acknowledged</p>
                    <p className="text-slate-700">{new Date(selectedTicket.acknowledged_at).toLocaleString()} by {selectedTicket.acknowledged_by}</p>
                  </div>
                )}
                {selectedTicket.resolved_at && (
                  <div>
                    <p className="text-xs text-slate-500">Resolved</p>
                    <p className="text-slate-700">{new Date(selectedTicket.resolved_at).toLocaleString()} by {selectedTicket.resolved_by}</p>
                  </div>
                )}
                {selectedTicket.resolution_notes && (
                  <div className="col-span-2">
                    <p className="text-xs text-slate-500">Resolution Notes</p>
                    <p className="text-slate-700">{selectedTicket.resolution_notes}</p>
                  </div>
                )}
              </div>
            </div>

            {/* Recurrence warning */}
            {(selectedTicket.recurrence_count ?? 0) > 0 && (
              <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 mt-4">
                <p className="text-xs font-semibold text-amber-800 uppercase mb-1">Recurring Issue</p>
                <p className="text-sm text-amber-700">
                  This issue has recurred {selectedTicket.recurrence_count} time{(selectedTicket.recurrence_count ?? 0) > 1 ? 's' : ''} after being resolved.
                  Consider escalating to Central Command (L4) if it cannot be permanently resolved.
                </p>
              </div>
            )}

            <div className="flex gap-2 justify-end mt-6 pt-4 border-t">
              {selectedTicket.status === 'open' && (
                <button
                  onClick={() => setShowAckModal(true)}
                  className="px-4 py-2 text-sm font-medium bg-ios-blue text-white rounded-lg hover:bg-ios-blue/90 transition"
                >
                  Acknowledge
                </button>
              )}
              {selectedTicket.status !== 'resolved' && selectedTicket.status !== 'escalated_to_l4' && (
                <button
                  onClick={() => setShowResolveModal(true)}
                  className="px-4 py-2 text-sm font-medium bg-health-healthy text-white rounded-lg hover:bg-health-healthy/90 transition"
                >
                  Resolve
                </button>
              )}
              {selectedTicket.status !== 'escalated_to_l4' && !selectedTicket.escalated_to_l4 && (
                <button
                  onClick={() => setShowL4Modal(true)}
                  className="px-4 py-2 text-sm font-medium bg-purple-600 text-white rounded-lg hover:bg-purple-700 transition"
                >
                  Escalate to L4
                </button>
              )}
              {selectedTicket.status === 'escalated_to_l4' && (
                <span className="px-4 py-2 text-sm font-medium text-purple-600">
                  Escalated to Central Command
                </span>
              )}
              <button
                onClick={() => setSelectedTicket(null)}
                className="px-4 py-2 text-sm text-slate-600 hover:text-slate-900 transition"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Acknowledge Modal */}
      {showAckModal && selectedTicket && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
          <div className="bg-white rounded-2xl p-6 w-full max-w-md shadow-xl">
            <h3 className="text-lg font-semibold text-slate-900 mb-2">Acknowledge Ticket</h3>
            <p className="text-sm text-slate-500 mb-4">{selectedTicket.title}</p>
            <div className="mb-4">
              <label className="block text-sm font-medium text-slate-700 mb-1">Your Name</label>
              <input
                type="text"
                value={ackBy}
                onChange={e => setAckBy(e.target.value)}
                placeholder="e.g., John Smith"
                className="w-full px-4 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              />
            </div>
            <div className="flex gap-3 justify-end">
              <button
                onClick={() => { setShowAckModal(false); setAckBy(''); }}
                className="px-4 py-2 text-slate-600 hover:text-slate-900 transition"
              >
                Cancel
              </button>
              <button
                onClick={handleAcknowledge}
                disabled={submitting || !ackBy.trim()}
                className="px-4 py-2 bg-ios-blue text-white font-medium rounded-lg hover:bg-ios-blue/90 disabled:opacity-50 transition"
              >
                {submitting ? 'Acknowledging...' : 'Acknowledge'}
              </button>
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
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">Resolved By</label>
                <input
                  type="text"
                  value={resolveBy}
                  onChange={e => setResolveBy(e.target.value)}
                  placeholder="Your name"
                  className="w-full px-4 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-green-500 focus:border-transparent"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">Resolution Notes</label>
                <textarea
                  value={resolutionNotes}
                  onChange={e => setResolutionNotes(e.target.value)}
                  placeholder="Describe what was done to resolve this issue..."
                  rows={4}
                  className="w-full px-4 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-green-500 focus:border-transparent resize-none"
                />
                <p className="text-xs text-slate-500 mt-1">The client will be notified when this ticket is resolved.</p>
              </div>
            </div>
            <div className="flex gap-3 justify-end mt-4">
              <button
                onClick={() => { setShowResolveModal(false); setResolveBy(''); setResolutionNotes(''); }}
                className="px-4 py-2 text-slate-600 hover:text-slate-900 transition"
              >
                Cancel
              </button>
              <button
                onClick={handleResolve}
                disabled={submitting || !resolveBy.trim() || !resolutionNotes.trim()}
                className="px-4 py-2 bg-health-healthy text-white font-medium rounded-lg hover:bg-health-healthy/90 disabled:opacity-50 transition"
              >
                {submitting ? 'Resolving...' : 'Resolve & Notify Client'}
              </button>
            </div>
          </div>
        </div>
      )}
      {/* L4 Escalation Modal */}
      {showL4Modal && selectedTicket && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
          <div className="bg-white rounded-2xl p-6 w-full max-w-md shadow-xl">
            <h3 className="text-lg font-semibold text-slate-900 mb-1">Escalate to Central Command</h3>
            <p className="text-sm text-purple-600 font-medium mb-2">L4 Escalation</p>
            <p className="text-sm text-slate-500 mb-4">{selectedTicket.title}</p>
            {(selectedTicket.recurrence_count ?? 0) > 0 && (
              <div className="bg-amber-50 rounded-lg p-2.5 mb-4">
                <p className="text-xs text-amber-700">
                  This issue has recurred {selectedTicket.recurrence_count} time(s). Central Command will investigate the root cause.
                </p>
              </div>
            )}
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">Escalated By</label>
                <input
                  type="text"
                  value={l4EscalatedBy}
                  onChange={e => setL4EscalatedBy(e.target.value)}
                  placeholder="Your name"
                  className="w-full px-4 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-purple-500 focus:border-transparent"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">Escalation Notes</label>
                <textarea
                  value={l4Notes}
                  onChange={e => setL4Notes(e.target.value)}
                  placeholder="Describe why this needs Central Command attention — what was tried, why it keeps recurring..."
                  rows={4}
                  className="w-full px-4 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-purple-500 focus:border-transparent resize-none"
                />
                <p className="text-xs text-slate-500 mt-1">This will be reviewed by the Central Command admin team.</p>
              </div>
            </div>
            <div className="flex gap-3 justify-end mt-4">
              <button
                onClick={() => { setShowL4Modal(false); setL4EscalatedBy(''); setL4Notes(''); }}
                className="px-4 py-2 text-slate-600 hover:text-slate-900 transition"
              >
                Cancel
              </button>
              <button
                onClick={handleEscalateToL4}
                disabled={submitting || !l4EscalatedBy.trim() || !l4Notes.trim()}
                className="px-4 py-2 bg-purple-600 text-white font-medium rounded-lg hover:bg-purple-700 disabled:opacity-50 transition"
              >
                {submitting ? 'Escalating...' : 'Escalate to L4'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default PartnerEscalations;
