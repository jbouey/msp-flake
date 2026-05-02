import React, { useState, useEffect, useMemo } from 'react';
import { GlassCard, Spinner } from '../components/shared';
import { formatTimeAgo } from '../constants';

interface L4Ticket {
  id: string;
  partner_id: string;
  partner_name: string | null;
  site_id: string;
  site_name: string | null;
  incident_id: string;
  incident_type: string;
  severity: string;
  priority: string;
  title: string;
  summary: string;
  hipaa_controls: string[];
  attempted_actions: string | null;
  recommended_action: string | null;
  status: string;
  recurrence_count: number;
  previous_ticket_id: string | null;
  l4_escalated_at: string;
  l4_escalated_by: string;
  l4_notes: string;
  l4_resolved_at: string | null;
  l4_resolved_by: string | null;
  l4_resolution_notes: string | null;
  created_at: string;
  sla_breached: boolean;
}

function getCsrfToken(): string {
  const match = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : '';
}


export const L4Queue: React.FC = () => {
  const [tickets, setTickets] = useState<L4Ticket[]>([]);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState<'open' | 'resolved'>('open');
  const [selectedTicket, setSelectedTicket] = useState<L4Ticket | null>(null);
  const [showResolveModal, setShowResolveModal] = useState(false);
  const [resolvedBy, setResolvedBy] = useState('');
  const [resolutionNotes, setResolutionNotes] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [feedback, setFeedback] = useState<{ type: 'success' | 'error'; message: string } | null>(null);

  const loadTickets = async () => {
    setLoading(true);
    try {
      const res = await fetch(`/api/dashboard/l4-queue?status=${statusFilter}`, {
        credentials: 'include',
      });
      if (res.ok) {
        const data = await res.json();
        setTickets(data.tickets || []);
      }
    } catch {
      setFeedback({ type: 'error', message: 'Failed to load L4 queue' });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadTickets(); }, [statusFilter]);

  useEffect(() => {
    if (feedback) {
      const t = setTimeout(() => setFeedback(null), 4000);
      return () => clearTimeout(t);
    }
  }, [feedback]);

  const handleResolve = async () => {
    if (!selectedTicket || !resolvedBy.trim() || !resolutionNotes.trim()) return;
    setSubmitting(true);
    try {
      const res = await fetch(`/api/dashboard/l4-queue/${selectedTicket.id}/resolve`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': getCsrfToken() },
        body: JSON.stringify({ resolved_by: resolvedBy, resolution_notes: resolutionNotes }),
      });
      if (res.ok) {
        setFeedback({ type: 'success', message: 'L4 ticket resolved' });
        setShowResolveModal(false);
        setSelectedTicket(null);
        setResolvedBy('');
        setResolutionNotes('');
        loadTickets();
      } else {
        const err = await res.json();
        setFeedback({ type: 'error', message: err.detail || 'Failed to resolve' });
      }
    } catch {
      setFeedback({ type: 'error', message: 'Network error' });
    } finally {
      setSubmitting(false);
    }
  };

  const priorityColor = (p: string) => {
    switch (p) {
      case 'critical': return 'bg-health-critical/10 text-health-critical';
      case 'high': return 'bg-health-warning/10 text-health-warning';
      case 'medium': return 'bg-yellow-500/10 text-yellow-600';
      default: return 'bg-fill-tertiary text-label-secondary';
    }
  };

  const openCount = tickets.length;

  // Triage-priority sort (#63 closure 2026-05-02). Wrapped in useMemo
  // so re-renders don't re-sort. Adversarial round-table catches:
  // (a) NaN guard on malformed created_at (Brian); (b) memo on bounded
  // array (Brian).
  const sortedTickets = useMemo(() => {
    const sevRank: Record<string, number> = {
      critical: 0, high: 1, medium: 2, low: 3,
    };
    const tsOrZero = (s: string): number => {
      const t = new Date(s).getTime();
      return Number.isFinite(t) ? t : 0;
    };
    return [...tickets].sort((a, b) => {
      const sa = sevRank[a.severity?.toLowerCase()] ?? 99;
      const sb = sevRank[b.severity?.toLowerCase()] ?? 99;
      if (sa !== sb) return sa - sb;
      if (a.sla_breached !== b.sla_breached) return a.sla_breached ? -1 : 1;
      return tsOrZero(a.created_at) - tsOrZero(b.created_at);
    });
  }, [tickets]);

  return (
    <div className="space-y-6 page-enter">
      {/* Feedback */}
      {feedback && (
        <div className={`rounded-ios px-4 py-3 text-sm ${
          feedback.type === 'success'
            ? 'bg-health-healthy/10 text-health-healthy border border-health-healthy/20'
            : 'bg-health-critical/10 text-health-critical border border-health-critical/20'
        }`}>
          {feedback.message}
        </div>
      )}

      <GlassCard>
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-xl font-semibold text-label-primary tracking-tight flex items-center gap-2">
              <span className="w-8 h-8 rounded-lg flex items-center justify-center text-sm font-bold"
                style={{ background: 'linear-gradient(135deg, #7C3AED 0%, #5B21B6 100%)', color: 'white' }}>
                L4
              </span>
              Central Command Queue
            </h1>
            <p className="text-sm text-label-tertiary mt-1">
              Tickets escalated by partners that require direct admin intervention
            </p>
          </div>

          <div className="flex items-center gap-1.5">
            <button
              onClick={() => setStatusFilter('open')}
              className={`px-3 py-1.5 text-sm font-medium rounded-ios-sm transition-colors ${
                statusFilter === 'open'
                  ? 'bg-purple-600 text-white'
                  : 'bg-fill-tertiary text-label-secondary hover:bg-fill-secondary'
              }`}
            >
              Open{statusFilter === 'open' ? ` (${openCount})` : ''}
            </button>
            <button
              onClick={() => setStatusFilter('resolved')}
              className={`px-3 py-1.5 text-sm font-medium rounded-ios-sm transition-colors ${
                statusFilter === 'resolved'
                  ? 'bg-health-healthy text-white'
                  : 'bg-fill-tertiary text-label-secondary hover:bg-fill-secondary'
              }`}
            >
              Resolved
            </button>
          </div>
        </div>

        {loading && (
          <div className="text-center py-12">
            <Spinner size="lg" />
            <p className="text-label-tertiary mt-3">Loading L4 queue...</p>
          </div>
        )}

        {!loading && tickets.length === 0 && (
          <div className="text-center py-12">
            <div className="w-14 h-14 rounded-full bg-health-healthy/10 flex items-center justify-center mx-auto mb-3">
              <svg className="w-7 h-7 text-health-healthy" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            </div>
            <p className="text-label-secondary font-medium">No L4 escalations</p>
            <p className="text-label-tertiary text-sm mt-1">
              {statusFilter === 'open' ? 'No tickets require Central Command attention' : 'No resolved L4 tickets'}
            </p>
          </div>
        )}

        {!loading && tickets.length > 0 && (
          <div className="space-y-3 stagger-list">
            {sortedTickets.map(ticket => (
              <div
                key={ticket.id}
                onClick={() => setSelectedTicket(ticket)}
                className="p-4 rounded-xl border border-separator-light bg-fill-primary hover:bg-fill-secondary cursor-pointer transition-all group"
              >
                <div className="flex items-start justify-between gap-4">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <span className={`px-2 py-0.5 text-xs font-semibold rounded-full ${priorityColor(ticket.priority)}`}>
                        {ticket.priority}
                      </span>
                      {ticket.recurrence_count > 0 && (
                        <span className="px-1.5 py-0.5 text-[10px] font-bold rounded-full bg-health-warning/10 text-health-warning">
                          x{ticket.recurrence_count} recurrence
                        </span>
                      )}
                      {ticket.sla_breached && (
                        <span className="px-1.5 py-0.5 text-[10px] font-bold rounded-full bg-health-critical text-white">SLA</span>
                      )}
                    </div>
                    <p className="font-medium text-label-primary text-sm group-hover:text-accent-primary transition-colors">
                      {ticket.title}
                    </p>
                    <p className="text-xs text-label-tertiary mt-0.5 line-clamp-1">{ticket.summary}</p>
                  </div>
                  <div className="text-right shrink-0">
                    <p className="text-xs text-label-tertiary">{ticket.site_name || 'Unknown Site'}</p>
                    <p className="text-xs text-label-quaternary mt-0.5">
                      by {ticket.partner_name || 'Partner'}
                    </p>
                    <p className="text-xs text-label-quaternary mt-0.5">
                      {formatTimeAgo(ticket.l4_escalated_at)}
                    </p>
                  </div>
                </div>
                {ticket.l4_notes && (
                  <div className="mt-2 px-3 py-2 rounded-lg bg-purple-50 dark:bg-purple-900/20">
                    <p className="text-xs text-purple-700 dark:text-purple-300 line-clamp-2">{ticket.l4_notes}</p>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </GlassCard>

      {/* Detail Modal */}
      {selectedTicket && !showResolveModal && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
          <div className="bg-fill-primary rounded-2xl p-6 w-full max-w-2xl shadow-xl max-h-[85vh] overflow-y-auto border border-separator-light">
            <div className="flex items-start justify-between mb-4">
              <div>
                <div className="flex items-center gap-2 mb-1">
                  <span className="px-2 py-0.5 text-xs font-bold rounded-full bg-purple-600 text-white">L4</span>
                  <span className={`px-2 py-0.5 text-xs font-semibold rounded-full ${priorityColor(selectedTicket.priority)}`}>
                    {selectedTicket.priority}
                  </span>
                  {selectedTicket.recurrence_count > 0 && (
                    <span className="px-1.5 py-0.5 text-[10px] font-bold rounded-full bg-health-warning/10 text-health-warning">
                      x{selectedTicket.recurrence_count} recurrence
                    </span>
                  )}
                </div>
                <h3 className="text-lg font-semibold text-label-primary">{selectedTicket.title}</h3>
                <p className="text-sm text-label-tertiary">
                  {selectedTicket.site_name || 'Unknown Site'} &middot; Partner: {selectedTicket.partner_name || 'Unknown'}
                </p>
              </div>
              <button onClick={() => setSelectedTicket(null)} className="text-label-tertiary hover:text-label-primary text-xl">&times;</button>
            </div>

            <div className="space-y-4">
              <div>
                <p className="text-xs font-medium text-label-tertiary uppercase mb-1">Summary</p>
                <p className="text-sm text-label-secondary">{selectedTicket.summary}</p>
              </div>

              {selectedTicket.l4_notes && (
                <div className="bg-purple-50 dark:bg-purple-900/20 rounded-lg p-3">
                  <p className="text-xs font-medium text-purple-600 uppercase mb-1">Partner Escalation Notes</p>
                  <p className="text-sm text-purple-800 dark:text-purple-200">{selectedTicket.l4_notes}</p>
                  <p className="text-xs text-purple-500 mt-1">
                    Escalated by {selectedTicket.l4_escalated_by} &middot; {new Date(selectedTicket.l4_escalated_at).toLocaleString()}
                  </p>
                </div>
              )}

              {selectedTicket.recommended_action && (
                <div className="bg-accent-primary/5 rounded-lg p-3">
                  <p className="text-xs font-medium text-accent-primary uppercase mb-1">Recommended Action</p>
                  <p className="text-sm text-label-primary">{selectedTicket.recommended_action}</p>
                </div>
              )}

              {selectedTicket.hipaa_controls && selectedTicket.hipaa_controls.length > 0 && (
                <div>
                  <p className="text-xs font-medium text-label-tertiary uppercase mb-1">HIPAA Controls</p>
                  <div className="flex flex-wrap gap-1">
                    {selectedTicket.hipaa_controls.map(c => (
                      <span key={c} className="px-2 py-0.5 bg-accent-primary/10 text-accent-primary text-xs rounded-full font-mono">{c}</span>
                    ))}
                  </div>
                </div>
              )}

              <div className="grid grid-cols-2 gap-4 text-sm">
                <div>
                  <p className="text-xs text-label-tertiary">Original Incident</p>
                  <p className="text-label-primary font-mono text-xs">{selectedTicket.incident_type}</p>
                </div>
                <div>
                  <p className="text-xs text-label-tertiary">Created</p>
                  <p className="text-label-primary">{new Date(selectedTicket.created_at).toLocaleString()}</p>
                </div>
                <div>
                  <p className="text-xs text-label-tertiary">Escalated to L4</p>
                  <p className="text-label-primary">{new Date(selectedTicket.l4_escalated_at).toLocaleString()}</p>
                </div>
                <div>
                  <p className="text-xs text-label-tertiary">Recurrences</p>
                  <p className="text-label-primary font-semibold">{selectedTicket.recurrence_count}</p>
                </div>
              </div>

              {selectedTicket.l4_resolved_at && (
                <div className="bg-health-healthy/5 border border-health-healthy/20 rounded-lg p-3">
                  <p className="text-xs font-medium text-health-healthy uppercase mb-1">L4 Resolution</p>
                  <p className="text-sm text-label-primary">{selectedTicket.l4_resolution_notes}</p>
                  <p className="text-xs text-label-tertiary mt-1">
                    Resolved by {selectedTicket.l4_resolved_by} &middot; {new Date(selectedTicket.l4_resolved_at).toLocaleString()}
                  </p>
                </div>
              )}
            </div>

            <div className="flex gap-2 justify-end mt-6 pt-4 border-t border-separator-light">
              {!selectedTicket.l4_resolved_at && (
                <button
                  onClick={() => setShowResolveModal(true)}
                  className="px-4 py-2 text-sm font-medium bg-health-healthy text-white rounded-ios hover:bg-health-healthy/90 transition"
                >
                  Resolve L4
                </button>
              )}
              <button
                onClick={() => setSelectedTicket(null)}
                className="px-4 py-2 text-sm text-label-secondary hover:text-label-primary transition"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}

      {/* L4 Resolve Modal */}
      {showResolveModal && selectedTicket && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
          <div className="bg-fill-primary rounded-2xl p-6 w-full max-w-md shadow-xl border border-separator-light">
            <h3 className="text-lg font-semibold text-label-primary mb-2">Resolve L4 Escalation</h3>
            <p className="text-sm text-label-tertiary mb-4">{selectedTicket.title}</p>
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-label-secondary mb-1">Resolved By</label>
                <input
                  type="text"
                  value={resolvedBy}
                  onChange={e => setResolvedBy(e.target.value)}
                  placeholder="Your name"
                  className="w-full px-4 py-2 border border-separator-light rounded-ios bg-fill-secondary text-label-primary focus:ring-2 focus:ring-purple-500 focus:border-transparent"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-label-secondary mb-1">Resolution Notes</label>
                <textarea
                  value={resolutionNotes}
                  onChange={e => setResolutionNotes(e.target.value)}
                  placeholder="Describe root cause and fix applied..."
                  rows={4}
                  className="w-full px-4 py-2 border border-separator-light rounded-ios bg-fill-secondary text-label-primary focus:ring-2 focus:ring-purple-500 focus:border-transparent resize-none"
                />
              </div>
            </div>
            <div className="flex gap-3 justify-end mt-4">
              <button
                onClick={() => { setShowResolveModal(false); setResolvedBy(''); setResolutionNotes(''); }}
                className="px-4 py-2 text-label-secondary hover:text-label-primary transition"
              >
                Cancel
              </button>
              <button
                onClick={handleResolve}
                disabled={submitting || !resolvedBy.trim() || !resolutionNotes.trim()}
                className="px-4 py-2 bg-purple-600 text-white font-medium rounded-ios hover:bg-purple-700 disabled:opacity-50 transition"
              >
                {submitting ? 'Resolving...' : 'Resolve L4'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
