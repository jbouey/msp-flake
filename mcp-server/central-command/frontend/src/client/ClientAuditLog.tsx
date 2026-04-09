import React, { useState, useEffect, useMemo, useCallback } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { useClient } from './ClientContext';
import { formatTimeAgo } from '../constants';

/**
 * ClientAuditLog — HIPAA §164.528 disclosure-accounting view.
 *
 * Surfaces the rows in `client_audit_log` for the caller's organization.
 * Powered by `GET /api/client/audit-log` (paginated, scoped via RLS to
 * the org_id from the session). Used by practice managers to satisfy
 * disclosure-accounting requests without contacting their MSP — they
 * can self-serve a downloadable trail of who did what and when.
 *
 * Filtering:
 *   - action prefix (USER_, MFA_, CREDENTIAL_, etc.)
 *   - lookback window in days (1..2555)
 * Each row shows actor email, machine action label, target, timestamp,
 * IP address, and a humanised JSON summary of the per-action details.
 */

interface AuditEvent {
  id: number;
  actor_user_id: string | null;
  actor_email: string | null;
  action: string;
  target: string | null;
  details: Record<string, unknown> | null;
  ip_address: string | null;
  created_at: string | null;
}

interface AuditLogResponse {
  org_id: string;
  events: AuditEvent[];
  total: number;
  limit: number;
  offset: number;
  days: number;
  action_filter: string | null;
}

const PAGE_SIZE = 50;

const ACTION_LABELS: Record<string, string> = {
  USER_INVITED: 'User invited',
  USER_REMOVED: 'User removed',
  USER_ROLE_CHANGED: 'User role changed',
  PASSWORD_CHANGED: 'Password changed',
  MFA_ENABLED: 'Two-factor enabled',
  MFA_DISABLED: 'Two-factor disabled',
  CREDENTIAL_CREATED: 'Credential added',
  DRIFT_CONFIG_UPDATED: 'Compliance check config updated',
  DEVICE_REGISTERED: 'Device registered',
  DEVICE_IGNORED: 'Device ignored',
  ALERT_APPROVED: 'Alert approved',
  ALERT_DISMISSED: 'Alert dismissed',
  ALERT_ACKNOWLEDGED: 'Alert acknowledged',
  ALERT_IGNORED: 'Alert ignored',
  ESCALATION_ACKNOWLEDGED: 'Escalation acknowledged',
  ESCALATION_RESOLVED: 'Escalation resolved',
  ESCALATION_PREFS_UPDATED: 'Escalation preferences updated',
};

const FILTER_PRESETS: Array<{ label: string; value: string | null }> = [
  { label: 'All actions', value: null },
  { label: 'User management', value: 'USER_' },
  { label: 'Authentication', value: 'PASSWORD_' },
  { label: 'Two-factor', value: 'MFA_' },
  { label: 'Credentials', value: 'CREDENTIAL_' },
  { label: 'Devices', value: 'DEVICE_' },
  { label: 'Compliance config', value: 'DRIFT_CONFIG_' },
  { label: 'Alerts', value: 'ALERT_' },
  { label: 'Escalations', value: 'ESCALATION_' },
];

function labelFor(action: string): string {
  return ACTION_LABELS[action] || action.toLowerCase().replace(/_/g, ' ');
}

function formatDetails(details: Record<string, unknown> | null): string | null {
  if (!details) return null;
  // Just stringify keys → values for the summary line. Keep it short
  // (~100 chars) so the table row stays readable.
  const parts: string[] = [];
  for (const [key, value] of Object.entries(details)) {
    if (value === null || value === undefined) continue;
    if (typeof value === 'object') {
      parts.push(`${key}: ${JSON.stringify(value).slice(0, 60)}`);
    } else {
      parts.push(`${key}: ${String(value).slice(0, 60)}`);
    }
    if (parts.join(', ').length > 100) break;
  }
  return parts.length > 0 ? parts.join(', ') : null;
}

export const ClientAuditLog: React.FC = () => {
  const navigate = useNavigate();
  const { isAuthenticated, isLoading } = useClient();

  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionFilter, setActionFilter] = useState<string | null>(null);
  const [days, setDays] = useState(90);
  const [page, setPage] = useState(0);

  const offset = page * PAGE_SIZE;

  const fetchEvents = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({
        days: String(days),
        limit: String(PAGE_SIZE),
        offset: String(offset),
      });
      if (actionFilter) {
        params.set('action', actionFilter);
      }
      const res = await fetch(`/api/client/audit-log?${params.toString()}`, {
        credentials: 'same-origin',
      });
      if (!res.ok) {
        throw new Error(`Failed to load audit log (${res.status})`);
      }
      const data: AuditLogResponse = await res.json();
      setEvents(data.events);
      setTotal(data.total);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [days, offset, actionFilter]);

  useEffect(() => {
    if (!isLoading && !isAuthenticated) {
      navigate('/client/login', { replace: true });
    }
  }, [isAuthenticated, isLoading, navigate]);

  useEffect(() => {
    if (isAuthenticated) {
      fetchEvents();
    }
  }, [isAuthenticated, fetchEvents]);

  const handleFilterChange = useCallback((value: string | null) => {
    setActionFilter(value);
    setPage(0);
  }, []);

  const handleDaysChange = useCallback((value: number) => {
    setDays(value);
    setPage(0);
  }, []);

  const exportCSV = useCallback(() => {
    // Generate a CSV of the CURRENT page so the user can hand it to an
    // auditor as supporting evidence. The full export (all pages) is a
    // future enhancement — for now this covers the typical "I need
    // proof of last 30 days of changes" use case.
    const rows = [
      ['When', 'Actor', 'Action', 'Target', 'Details', 'IP'],
      ...events.map((e) => [
        e.created_at || '',
        e.actor_email || '',
        e.action,
        e.target || '',
        formatDetails(e.details) || '',
        e.ip_address || '',
      ]),
    ];
    const csv = rows
      .map((row) =>
        row.map((cell) => `"${String(cell).replace(/"/g, '""')}"`).join(','),
      )
      .join('\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `audit-log-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }, [events]);

  const totalPages = useMemo(() => Math.max(1, Math.ceil(total / PAGE_SIZE)), [total]);

  if (isLoading || !isAuthenticated) {
    return (
      <div className="min-h-screen bg-slate-50 flex items-center justify-center">
        <div className="w-12 h-12 border-4 border-teal-500 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

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
              <h1 className="text-lg font-semibold text-slate-900">Audit Trail</h1>
            </div>
          </div>
        </div>
      </header>

      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
        {/* Description */}
        <div className="mb-6">
          <p className="text-sm text-slate-600">
            Self-serve disclosure accounting per HIPAA §164.528. Every change made
            to your organization (user invites, password changes, credential edits,
            scan-config updates, alert actions, escalations) is recorded here for
            7 years. Export to CSV to share with an auditor.
          </p>
        </div>

        {/* Filter bar */}
        <div className="bg-white rounded-xl border border-slate-200 p-4 mb-4 flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-2">
            <label htmlFor="action-filter" className="text-xs font-medium text-slate-600">
              Action:
            </label>
            <select
              id="action-filter"
              value={actionFilter || ''}
              onChange={(e) => handleFilterChange(e.target.value || null)}
              className="text-sm border border-slate-300 rounded-md px-2 py-1 bg-white"
            >
              {FILTER_PRESETS.map((f) => (
                <option key={f.label} value={f.value || ''}>
                  {f.label}
                </option>
              ))}
            </select>
          </div>

          <div className="flex items-center gap-2">
            <label htmlFor="days-filter" className="text-xs font-medium text-slate-600">
              Window:
            </label>
            <select
              id="days-filter"
              value={days}
              onChange={(e) => handleDaysChange(Number(e.target.value))}
              className="text-sm border border-slate-300 rounded-md px-2 py-1 bg-white"
            >
              <option value={7}>7 days</option>
              <option value={30}>30 days</option>
              <option value={90}>90 days</option>
              <option value={365}>1 year</option>
              <option value={2555}>7 years (full retention)</option>
            </select>
          </div>

          <div className="ml-auto flex items-center gap-2">
            <button
              type="button"
              onClick={exportCSV}
              disabled={events.length === 0}
              className="px-3 py-1.5 text-xs font-medium rounded-md bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              Export CSV
            </button>
            <button
              type="button"
              onClick={fetchEvents}
              className="px-3 py-1.5 text-xs font-medium rounded-md bg-slate-100 text-slate-700 hover:bg-slate-200"
            >
              Refresh
            </button>
          </div>
        </div>

        {/* Table */}
        <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
          {loading && (
            <div className="px-4 py-12 text-center text-slate-500">Loading audit events…</div>
          )}

          {error && !loading && (
            <div className="px-4 py-12 text-center text-red-700">{error}</div>
          )}

          {!loading && !error && events.length === 0 && (
            <div className="px-4 py-12 text-center">
              <p className="text-sm font-medium text-slate-700">No audit events in this window</p>
              <p className="text-xs text-slate-500 mt-1">
                Try widening the lookback window or removing the action filter.
              </p>
            </div>
          )}

          {!loading && !error && events.length > 0 && (
            <table className="min-w-full divide-y divide-slate-200">
              <thead className="bg-slate-50">
                <tr>
                  <th className="px-4 py-2 text-left text-[10px] font-semibold text-slate-500 uppercase tracking-wide">When</th>
                  <th className="px-4 py-2 text-left text-[10px] font-semibold text-slate-500 uppercase tracking-wide">Actor</th>
                  <th className="px-4 py-2 text-left text-[10px] font-semibold text-slate-500 uppercase tracking-wide">Action</th>
                  <th className="px-4 py-2 text-left text-[10px] font-semibold text-slate-500 uppercase tracking-wide">Target</th>
                  <th className="px-4 py-2 text-left text-[10px] font-semibold text-slate-500 uppercase tracking-wide">Details</th>
                  <th className="px-4 py-2 text-left text-[10px] font-semibold text-slate-500 uppercase tracking-wide">IP</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {events.map((event) => (
                  <tr key={event.id} className="hover:bg-slate-50">
                    <td className="px-4 py-2 text-xs text-slate-700 whitespace-nowrap">
                      {formatTimeAgo(event.created_at)}
                    </td>
                    <td className="px-4 py-2 text-xs text-slate-700 truncate max-w-[200px]" title={event.actor_email || ''}>
                      {event.actor_email || 'system'}
                    </td>
                    <td className="px-4 py-2 text-xs font-medium text-slate-900">
                      {labelFor(event.action)}
                    </td>
                    <td className="px-4 py-2 text-xs text-slate-600 font-mono truncate max-w-[180px]" title={event.target || ''}>
                      {event.target || '—'}
                    </td>
                    <td className="px-4 py-2 text-xs text-slate-500 truncate max-w-[280px]" title={formatDetails(event.details) || ''}>
                      {formatDetails(event.details) || '—'}
                    </td>
                    <td className="px-4 py-2 text-xs text-slate-500 font-mono">
                      {event.ip_address || '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {/* Pagination */}
        {!loading && !error && total > 0 && (
          <div className="mt-4 flex items-center justify-between text-sm text-slate-600">
            <span>
              Showing {offset + 1}–{Math.min(offset + events.length, total)} of {total}
            </span>
            <div className="flex items-center gap-2">
              <button
                type="button"
                disabled={page === 0}
                onClick={() => setPage((p) => Math.max(0, p - 1))}
                className="px-3 py-1 rounded-md bg-slate-100 hover:bg-slate-200 disabled:opacity-40 disabled:cursor-not-allowed text-xs"
              >
                Previous
              </button>
              <span className="text-xs text-slate-500">
                Page {page + 1} of {totalPages}
              </span>
              <button
                type="button"
                disabled={page + 1 >= totalPages}
                onClick={() => setPage((p) => p + 1)}
                className="px-3 py-1 rounded-md bg-slate-100 hover:bg-slate-200 disabled:opacity-40 disabled:cursor-not-allowed text-xs"
              >
                Next
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default ClientAuditLog;
