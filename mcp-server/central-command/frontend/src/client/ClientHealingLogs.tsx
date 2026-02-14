import React, { useState, useEffect, useCallback } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { useClient } from './ClientContext';

interface HealingLog {
  execution_id: string;
  site_id: string;
  clinic_name: string;
  runbook_id: string;
  incident_type: string | null;
  success: boolean;
  resolution_level: string | null;
  started_at: string | null;
  completed_at: string | null;
  duration_seconds: number | null;
  error_message: string | null;
  hostname: string;
}

interface PromotionCandidate {
  id: string;
  pattern_signature: string;
  site_id: string;
  clinic_name: string;
  check_type: string | null;
  total_occurrences: number;
  success_rate: number;
  recommended_action: string | null;
  first_seen: string | null;
  last_seen: string | null;
  approval_status: string;
  client_endorsed: boolean;
}

type TabKey = 'logs' | 'candidates';

const PAGE_SIZE = 25;

function relativeTime(iso: string | null): string {
  if (!iso) return '—';
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 30) return `${days}d ago`;
  return new Date(iso).toLocaleDateString();
}

function formatDuration(seconds: number | null): string {
  if (seconds === null || seconds === undefined) return '—';
  if (seconds < 1) return '<1s';
  if (seconds < 60) return `${Math.round(seconds)}s`;
  return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
}

function formatCheckType(raw: string | null): string {
  if (!raw) return 'Unknown';
  return raw
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

export const ClientHealingLogs: React.FC = () => {
  const navigate = useNavigate();
  const { isAuthenticated, isLoading } = useClient();

  const [activeTab, setActiveTab] = useState<TabKey>('logs');

  // Healing logs state
  const [logs, setLogs] = useState<HealingLog[]>([]);
  const [logsTotal, setLogsTotal] = useState(0);
  const [logsOffset, setLogsOffset] = useState(0);
  const [logsLoading, setLogsLoading] = useState(true);

  // Promotion candidates state
  const [candidates, setCandidates] = useState<PromotionCandidate[]>([]);
  const [candidatesLoading, setCandidatesLoading] = useState(true);

  // Forward form state
  const [forwardingId, setForwardingId] = useState<string | null>(null);
  const [forwardNotes, setForwardNotes] = useState('');
  const [forwarding, setForwarding] = useState(false);
  const [forwardSuccess, setForwardSuccess] = useState<string | null>(null);

  useEffect(() => {
    if (!isLoading && !isAuthenticated) {
      navigate('/client/login', { replace: true });
    }
  }, [isAuthenticated, isLoading, navigate]);

  const fetchLogs = useCallback(async (offset = 0) => {
    setLogsLoading(true);
    try {
      const res = await fetch(
        `/api/client/healing-logs?limit=${PAGE_SIZE}&offset=${offset}`,
        { credentials: 'include' }
      );
      if (res.ok) {
        const data = await res.json();
        setLogs(data.logs || []);
        setLogsTotal(data.total || 0);
        setLogsOffset(offset);
      }
    } catch (e) {
      console.error('Failed to fetch healing logs:', e);
    } finally {
      setLogsLoading(false);
    }
  }, []);

  const fetchCandidates = useCallback(async () => {
    setCandidatesLoading(true);
    try {
      const res = await fetch('/api/client/promotion-candidates', {
        credentials: 'include',
      });
      if (res.ok) {
        const data = await res.json();
        setCandidates(data.candidates || []);
      }
    } catch (e) {
      console.error('Failed to fetch promotion candidates:', e);
    } finally {
      setCandidatesLoading(false);
    }
  }, []);

  useEffect(() => {
    if (isAuthenticated) {
      fetchLogs(0);
      fetchCandidates();
    }
  }, [isAuthenticated, fetchLogs, fetchCandidates]);

  const handleForward = async (candidateId: string) => {
    setForwarding(true);
    try {
      const res = await fetch(
        `/api/client/promotion-candidates/${candidateId}/forward`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'include',
          body: JSON.stringify({ notes: forwardNotes || null }),
        }
      );
      if (res.ok) {
        setCandidates((prev) =>
          prev.map((c) =>
            c.id === candidateId ? { ...c, client_endorsed: true } : c
          )
        );
        setForwardSuccess(candidateId);
        setForwardingId(null);
        setForwardNotes('');
        setTimeout(() => setForwardSuccess(null), 3000);
      }
    } catch (e) {
      console.error('Failed to forward candidate:', e);
    } finally {
      setForwarding(false);
    }
  };

  const getLevelBadge = (level: string | null) => {
    const styles: Record<string, string> = {
      L1: 'bg-blue-100 text-blue-700',
      L2: 'bg-purple-100 text-purple-700',
      L3: 'bg-orange-100 text-orange-700',
    };
    const key = level?.toUpperCase() || '';
    return (
      <span
        className={`inline-flex items-center px-2 py-0.5 text-xs font-medium rounded-full ${styles[key] || 'bg-slate-100 text-slate-600'}`}
      >
        {level || '—'}
      </span>
    );
  };

  const getStatusBadge = (success: boolean) =>
    success ? (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 text-xs font-medium rounded-full bg-green-100 text-green-700">
        <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
        </svg>
        Healed
      </span>
    ) : (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 text-xs font-medium rounded-full bg-red-100 text-red-700">
        <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M6 18L18 6M6 6l12 12" />
        </svg>
        Failed
      </span>
    );

  const getApprovalBadge = (status: string) => {
    const map: Record<string, { bg: string; label: string }> = {
      approved: { bg: 'bg-green-100 text-green-700', label: 'Approved' },
      rejected: { bg: 'bg-red-100 text-red-700', label: 'Rejected' },
      client_forwarded: { bg: 'bg-teal-100 text-teal-700', label: 'Forwarded' },
    };
    const info = map[status];
    if (!info) return null;
    return (
      <span className={`px-2 py-0.5 text-xs font-medium rounded-full ${info.bg}`}>
        {info.label}
      </span>
    );
  };

  if (isLoading) {
    return (
      <div className="min-h-screen bg-slate-50 flex items-center justify-center">
        <div className="w-12 h-12 border-4 border-teal-500 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  const pageStart = logsOffset + 1;
  const pageEnd = Math.min(logsOffset + PAGE_SIZE, logsTotal);
  const hasPrev = logsOffset > 0;
  const hasNext = logsOffset + PAGE_SIZE < logsTotal;

  return (
    <div className="min-h-screen bg-slate-50/80 page-enter">
      {/* Header */}
      <header
        className="sticky top-0 z-30 border-b border-slate-200/60"
        style={{
          background: 'rgba(255,255,255,0.82)',
          backdropFilter: 'blur(20px) saturate(180%)',
          WebkitBackdropFilter: 'blur(20px) saturate(180%)',
        }}
      >
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between h-14">
            <div className="flex items-center gap-4">
              <Link
                to="/client/dashboard"
                className="p-2 text-slate-500 hover:text-teal-600 rounded-lg hover:bg-teal-50"
              >
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
                </svg>
              </Link>
              <h1 className="text-lg font-semibold text-slate-900">Healing Activity</h1>
            </div>
          </div>
        </div>
      </header>

      {/* Tabs */}
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 pt-6">
        <div className="flex gap-6 border-b border-slate-200">
          <button
            onClick={() => setActiveTab('logs')}
            className={`pb-3 text-sm font-medium transition-colors ${
              activeTab === 'logs'
                ? 'text-teal-600 border-b-2 border-teal-500 font-semibold'
                : 'text-slate-500 hover:text-slate-700'
            }`}
          >
            Healing Activity
          </button>
          <button
            onClick={() => setActiveTab('candidates')}
            className={`pb-3 text-sm font-medium transition-colors ${
              activeTab === 'candidates'
                ? 'text-teal-600 border-b-2 border-teal-500 font-semibold'
                : 'text-slate-500 hover:text-slate-700'
            }`}
          >
            Promotion Candidates
            {candidates.filter((c) => !c.client_endorsed && c.approval_status === 'not_submitted').length > 0 && (
              <span className="ml-2 px-1.5 py-0.5 text-xs bg-teal-100 text-teal-700 rounded-full">
                {candidates.filter((c) => !c.client_endorsed && c.approval_status === 'not_submitted').length}
              </span>
            )}
          </button>
        </div>
      </div>

      {/* Main */}
      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
        {/* =========== HEALING LOGS TAB =========== */}
        {activeTab === 'logs' && (
          <div className="bg-white rounded-2xl shadow-sm border border-slate-100 overflow-hidden">
            {logsLoading ? (
              <div className="p-8 text-center">
                <div className="w-8 h-8 border-4 border-teal-500 border-t-transparent rounded-full animate-spin mx-auto" />
              </div>
            ) : logs.length === 0 ? (
              <div className="p-8 text-center text-slate-500">
                <svg className="w-12 h-12 mx-auto mb-4 text-slate-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
                </svg>
                <p className="font-medium text-slate-600">No healing activity yet</p>
                <p className="text-sm mt-1">Auto-healing logs will appear here as your systems are monitored.</p>
              </div>
            ) : (
              <>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-slate-200 bg-slate-50/50">
                        <th className="text-left px-4 py-3 font-medium text-slate-600">Site</th>
                        <th className="text-left px-4 py-3 font-medium text-slate-600">Hostname</th>
                        <th className="text-left px-4 py-3 font-medium text-slate-600">Runbook</th>
                        <th className="text-left px-4 py-3 font-medium text-slate-600">Status</th>
                        <th className="text-left px-4 py-3 font-medium text-slate-600">Level</th>
                        <th className="text-left px-4 py-3 font-medium text-slate-600">Duration</th>
                        <th className="text-left px-4 py-3 font-medium text-slate-600">Time</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {logs.map((log) => (
                        <tr
                          key={log.execution_id}
                          className="hover:bg-teal-50/30 transition-colors"
                        >
                          <td className="px-4 py-3">
                            <div className="font-medium text-slate-900 text-sm">{log.clinic_name}</div>
                            <div className="text-xs text-slate-400">{log.site_id}</div>
                          </td>
                          <td className="px-4 py-3 text-slate-700 text-sm">{log.hostname}</td>
                          <td className="px-4 py-3">
                            <code className="text-xs bg-slate-100 px-1.5 py-0.5 rounded font-mono text-slate-700">
                              {log.runbook_id}
                            </code>
                          </td>
                          <td className="px-4 py-3">{getStatusBadge(log.success)}</td>
                          <td className="px-4 py-3">{getLevelBadge(log.resolution_level)}</td>
                          <td className="px-4 py-3 text-slate-600 text-sm tabular-nums">
                            {formatDuration(log.duration_seconds)}
                          </td>
                          <td className="px-4 py-3">
                            <span
                              className="text-sm text-slate-500"
                              title={log.started_at ? new Date(log.started_at).toLocaleString() : ''}
                            >
                              {relativeTime(log.started_at)}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                {/* Pagination */}
                {logsTotal > PAGE_SIZE && (
                  <div className="flex items-center justify-between px-4 py-3 border-t border-slate-200 bg-slate-50/30">
                    <p className="text-sm text-slate-500">
                      Showing <span className="font-medium">{pageStart}</span>–
                      <span className="font-medium">{pageEnd}</span> of{' '}
                      <span className="font-medium">{logsTotal}</span>
                    </p>
                    <div className="flex gap-2">
                      <button
                        onClick={() => fetchLogs(logsOffset - PAGE_SIZE)}
                        disabled={!hasPrev}
                        className="px-3 py-1.5 text-sm font-medium rounded-lg border border-slate-200 text-slate-600 hover:bg-slate-100 disabled:opacity-40 disabled:cursor-not-allowed"
                      >
                        Previous
                      </button>
                      <button
                        onClick={() => fetchLogs(logsOffset + PAGE_SIZE)}
                        disabled={!hasNext}
                        className="px-3 py-1.5 text-sm font-medium rounded-lg border border-slate-200 text-slate-600 hover:bg-slate-100 disabled:opacity-40 disabled:cursor-not-allowed"
                      >
                        Next
                      </button>
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
        )}

        {/* =========== PROMOTION CANDIDATES TAB =========== */}
        {activeTab === 'candidates' && (
          <div>
            {candidatesLoading ? (
              <div className="p-8 text-center">
                <div className="w-8 h-8 border-4 border-teal-500 border-t-transparent rounded-full animate-spin mx-auto" />
              </div>
            ) : candidates.length === 0 ? (
              <div className="bg-white rounded-2xl shadow-sm border border-slate-100 p-8 text-center text-slate-500">
                <svg className="w-12 h-12 mx-auto mb-4 text-slate-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6" />
                </svg>
                <p className="font-medium text-slate-600">No promotion candidates</p>
                <p className="text-sm mt-1">
                  Patterns that heal consistently will appear here for your review.
                </p>
              </div>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {candidates.map((c) => {
                  const isForwarding = forwardingId === c.id;
                  const justForwarded = forwardSuccess === c.id;

                  return (
                    <div
                      key={c.id}
                      className="bg-white rounded-2xl shadow-sm border border-slate-100 overflow-hidden hover:shadow-md transition-shadow"
                    >
                      <div className="p-5">
                        {/* Header row */}
                        <div className="flex items-start justify-between mb-3">
                          <div>
                            <h3 className="font-semibold text-slate-900">
                              {formatCheckType(c.check_type)}
                            </h3>
                            <p className="text-sm text-slate-500 mt-0.5">{c.clinic_name}</p>
                          </div>
                          <div className="flex items-center gap-2">
                            {getApprovalBadge(c.approval_status)}
                            {c.client_endorsed && (
                              <span className="px-2 py-0.5 text-xs font-medium rounded-full bg-green-100 text-green-700">
                                Forwarded
                              </span>
                            )}
                          </div>
                        </div>

                        {/* Stats */}
                        <div className="grid grid-cols-2 gap-3 mb-4">
                          <div className="bg-slate-50 rounded-lg p-3">
                            <p className="text-xs text-slate-500 mb-1">Occurrences</p>
                            <p className="text-lg font-bold text-slate-900 tabular-nums">
                              {c.total_occurrences}
                            </p>
                          </div>
                          <div className="bg-slate-50 rounded-lg p-3">
                            <p className="text-xs text-slate-500 mb-1">Success Rate</p>
                            <p className="text-lg font-bold text-slate-900 tabular-nums">
                              {(c.success_rate * 100).toFixed(0)}%
                            </p>
                          </div>
                        </div>

                        {/* Success rate bar */}
                        <div className="h-1.5 bg-slate-100 rounded-full overflow-hidden mb-4">
                          <div
                            className="h-full rounded-full transition-all"
                            style={{
                              width: `${c.success_rate * 100}%`,
                              background:
                                c.success_rate >= 0.9
                                  ? '#10b981'
                                  : c.success_rate >= 0.7
                                    ? '#f59e0b'
                                    : '#ef4444',
                            }}
                          />
                        </div>

                        {/* Action / metadata */}
                        {c.recommended_action && (
                          <p className="text-xs text-slate-500 mb-3">
                            <span className="font-medium">Action:</span> {c.recommended_action}
                          </p>
                        )}

                        {/* Forward button or forwarded state */}
                        {!c.client_endorsed && c.approval_status === 'not_submitted' && !isForwarding && (
                          <button
                            onClick={() => {
                              setForwardingId(c.id);
                              setForwardNotes('');
                            }}
                            className="w-full py-2 px-4 bg-teal-600 text-white text-sm font-medium rounded-lg hover:bg-teal-700 transition-colors"
                          >
                            Forward to Partner
                          </button>
                        )}

                        {justForwarded && (
                          <div className="text-center py-2 text-sm text-green-600 font-medium">
                            Forwarded successfully
                          </div>
                        )}
                      </div>

                      {/* Inline forward form */}
                      {isForwarding && (
                        <div className="border-t border-slate-100 bg-slate-50/50 p-4">
                          <label className="block text-xs font-medium text-slate-600 mb-1.5">
                            Notes for your partner (optional)
                          </label>
                          <textarea
                            value={forwardNotes}
                            onChange={(e) => setForwardNotes(e.target.value)}
                            placeholder="Any context about this pattern..."
                            rows={2}
                            className="w-full px-3 py-2 text-sm border border-slate-200 rounded-lg resize-none focus:outline-none focus:ring-2 focus:ring-teal-500/30 focus:border-teal-400"
                          />
                          <div className="flex gap-2 mt-2">
                            <button
                              onClick={() => handleForward(c.id)}
                              disabled={forwarding}
                              className="flex-1 py-2 px-4 bg-teal-600 text-white text-sm font-medium rounded-lg hover:bg-teal-700 disabled:opacity-50 transition-colors"
                            >
                              {forwarding ? 'Sending...' : 'Submit'}
                            </button>
                            <button
                              onClick={() => {
                                setForwardingId(null);
                                setForwardNotes('');
                              }}
                              className="px-4 py-2 text-sm text-slate-600 hover:text-slate-800 rounded-lg border border-slate-200 hover:bg-slate-100"
                            >
                              Cancel
                            </button>
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}
      </main>
    </div>
  );
};

export default ClientHealingLogs;
