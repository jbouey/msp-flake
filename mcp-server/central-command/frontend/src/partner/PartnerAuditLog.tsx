import React, { useEffect, useState, useCallback } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { usePartner } from './PartnerContext';

/**
 * PartnerAuditLog — Tier 3 H7-partner.
 *
 * Self-service audit log view for partners. Mirrors the client-side
 * `ClientAuditLog` shipped in Batch 7. Shows the partner their own
 * activity audit trail (logins, mutations, branding edits, drift
 * config changes, etc.) so they can satisfy HIPAA §164.528 disclosure
 * accounting + §164.316(b)(2)(i) 7-year retention requirements during
 * an audit.
 *
 * Backend: `GET /api/partners/me/audit-log` (returns events scoped to
 * the authenticated partner). Filter by category and lookback window.
 */

interface AuditEvent {
  id: number;
  partner_id: string;
  partner_name: string | null;
  event_type: string;
  event_category: string;
  event_data: Record<string, unknown> | string | null;
  target_type: string | null;
  target_id: string | null;
  actor_ip: string | null;
  success: boolean;
  error_message: string | null;
  created_at: string;
}

interface AuditLogResponse {
  partner_id: string;
  partner_name: string | null;
  events: AuditEvent[];
  total: number;
  limit: number;
  offset: number;
  days_lookback: number;
  category_filter: string | null;
}

const PAGE_SIZE = 50;

const CATEGORY_OPTIONS = [
  { value: '', label: 'All categories' },
  { value: 'auth', label: 'Authentication' },
  { value: 'admin', label: 'Admin actions' },
  { value: 'site', label: 'Site management' },
  { value: 'provision', label: 'Appliance provisioning' },
  { value: 'credential', label: 'Credentials' },
  { value: 'asset', label: 'Assets' },
  { value: 'discovery', label: 'Discovery' },
  { value: 'learning', label: 'Learning loop' },
];

const LOOKBACK_OPTIONS = [
  { value: 7, label: 'Last 7 days' },
  { value: 30, label: 'Last 30 days' },
  { value: 90, label: 'Last 90 days' },
  { value: 365, label: 'Last year' },
  { value: 2555, label: 'Last 7 years (HIPAA max)' },
];

const EVENT_LABELS: Record<string, string> = {
  oauth_login_started: 'OAuth login started',
  oauth_login_success: 'OAuth login success',
  oauth_login_failed: 'OAuth login failed',
  session_created: 'Session created',
  logout: 'Logout',
  partner_created: 'Partner created',
  partner_updated: 'Partner updated',
  partner_approved: 'Partner approved',
  partner_rejected: 'Partner rejected',
  api_key_regenerated: 'API key regenerated',
  profile_viewed: 'Profile viewed',
  branding_updated: 'Branding updated',
  sites_listed: 'Sites listed',
  site_viewed: 'Site viewed',
  provision_created: 'Appliance provision created',
  provision_revoked: 'Appliance provision revoked',
  provision_claimed: 'Appliance claimed',
  credential_added: 'Credential added',
  credential_validated: 'Credential validated',
  credential_deleted: 'Credential deleted',
  asset_updated: 'Asset updated',
  discovery_triggered: 'Discovery triggered',
  pattern_approved: 'Learning pattern approved',
  pattern_rejected: 'Learning pattern rejected',
  rule_status_changed: 'Rule status changed',
  drift_config_updated: 'Drift config updated',
  maintenance_window_set: 'Maintenance window set',
  maintenance_window_cancelled: 'Maintenance window cancelled',
  alert_config_updated: 'Alert config updated',
  site_transferred: 'Site transferred',
};

const humanizeEvent = (eventType: string): string =>
  EVENT_LABELS[eventType] || eventType.replace(/_/g, ' ');

export const PartnerAuditLog: React.FC = () => {
  const navigate = useNavigate();
  const { apiKey, isAuthenticated, isLoading } = usePartner();

  const [data, setData] = useState<AuditLogResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [category, setCategory] = useState('');
  const [days, setDays] = useState(30);
  const [page, setPage] = useState(0);

  useEffect(() => {
    if (!isLoading && !isAuthenticated) {
      navigate('/partner/login', { replace: true });
    }
  }, [isAuthenticated, isLoading, navigate]);

  const fetchOptions = useCallback((): RequestInit => {
    if (apiKey) {
      return {
        headers: { 'X-API-Key': apiKey },
        credentials: 'include',
      };
    }
    return { credentials: 'include' };
  }, [apiKey]);

  const fetchAuditLog = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({
        days: String(days),
        limit: String(PAGE_SIZE),
        offset: String(page * PAGE_SIZE),
      });
      if (category) params.set('event_category', category);
      const res = await fetch(
        `/api/partners/me/audit-log?${params.toString()}`,
        fetchOptions(),
      );
      if (!res.ok) {
        throw new Error(`Audit log fetch failed: HTTP ${res.status}`);
      }
      const json = (await res.json()) as AuditLogResponse;
      setData(json);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [category, days, page, fetchOptions]);

  useEffect(() => {
    if (isAuthenticated) {
      fetchAuditLog();
    }
  }, [isAuthenticated, fetchAuditLog]);

  const exportCsv = () => {
    if (!data?.events) return;
    const headers = ['When', 'Event', 'Category', 'Target', 'IP', 'Success'];
    const rows = data.events.map((e) => [
      e.created_at,
      humanizeEvent(e.event_type),
      e.event_category,
      e.target_type ? `${e.target_type}:${e.target_id || ''}` : '',
      e.actor_ip || '',
      String(e.success),
    ]);
    const csv = [headers, ...rows]
      .map((r) => r.map((v) => `"${String(v).replace(/"/g, '""')}"`).join(','))
      .join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `partner-audit-log-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  if (isLoading || !isAuthenticated) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-50">
        <p className="text-slate-500">Loading…</p>
      </div>
    );
  }

  const totalPages = data ? Math.ceil(data.total / PAGE_SIZE) : 0;

  return (
    <div className="min-h-screen bg-slate-50/80">
      <header className="sticky top-0 z-10 bg-white/90 backdrop-blur border-b border-slate-200">
        <div className="max-w-6xl mx-auto px-6 py-4 flex items-center justify-between gap-4">
          <div>
            <p className="text-[11px] uppercase tracking-widest text-slate-500">
              Partner audit log
            </p>
            <h1 className="text-xl font-bold text-slate-900">
              Activity & disclosure accounting
            </h1>
          </div>
          <Link
            to="/partner/dashboard"
            className="text-sm text-slate-600 hover:text-blue-700 hover:underline"
          >
            ← Back to dashboard
          </Link>
        </div>
      </header>

      <main className="max-w-6xl mx-auto px-6 py-8">
        <div className="bg-white rounded-xl border border-slate-200 p-5 mb-6">
          <p className="text-sm text-slate-700 mb-4">
            Every action you and your team take in the partner portal is
            recorded here with timestamp, IP, and event details. This view
            satisfies HIPAA §164.528 disclosure accounting and §164.316(b)(2)(i)
            6-year retention. Use the lookback selector below to widen the
            window for an audit.
          </p>
          <div className="flex flex-wrap gap-3 items-end">
            <div>
              <label
                htmlFor="audit-category"
                className="block text-[11px] uppercase tracking-wide text-slate-500 mb-1"
              >
                Category
              </label>
              <select
                id="audit-category"
                value={category}
                onChange={(e) => {
                  setCategory(e.target.value);
                  setPage(0);
                }}
                className="px-3 py-1.5 rounded-md border border-slate-300 bg-white text-sm"
              >
                {CATEGORY_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label
                htmlFor="audit-lookback"
                className="block text-[11px] uppercase tracking-wide text-slate-500 mb-1"
              >
                Lookback window
              </label>
              <select
                id="audit-lookback"
                value={days}
                onChange={(e) => {
                  setDays(Number(e.target.value));
                  setPage(0);
                }}
                className="px-3 py-1.5 rounded-md border border-slate-300 bg-white text-sm"
              >
                {LOOKBACK_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </div>
            <button
              type="button"
              onClick={exportCsv}
              disabled={!data || data.events.length === 0}
              className="px-3 py-1.5 rounded-md border border-slate-300 bg-white text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
            >
              Export CSV
            </button>
          </div>
        </div>

        {error && (
          <div className="bg-red-50 border border-red-200 rounded-lg p-4 text-sm text-red-800 mb-4">
            {error}
          </div>
        )}

        <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
          {loading ? (
            <p className="p-8 text-center text-slate-500">Loading audit log…</p>
          ) : !data || data.events.length === 0 ? (
            <p className="p-8 text-center text-slate-500">
              No events recorded for this filter and lookback window.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-slate-50 border-b border-slate-200 text-[11px] uppercase tracking-wide text-slate-500">
                  <tr>
                    <th className="text-left px-4 py-2 font-medium">When</th>
                    <th className="text-left px-4 py-2 font-medium">Event</th>
                    <th className="text-left px-4 py-2 font-medium">Category</th>
                    <th className="text-left px-4 py-2 font-medium">Target</th>
                    <th className="text-left px-4 py-2 font-medium">IP</th>
                    <th className="text-left px-4 py-2 font-medium">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {data.events.map((event) => (
                    <tr
                      key={event.id}
                      className="border-b border-slate-100 even:bg-slate-50/40"
                    >
                      <td className="px-4 py-2 text-slate-700 whitespace-nowrap">
                        {new Date(event.created_at).toLocaleString()}
                      </td>
                      <td className="px-4 py-2 font-medium text-slate-900">
                        {humanizeEvent(event.event_type)}
                      </td>
                      <td className="px-4 py-2">
                        <span className="inline-block text-[10px] uppercase tracking-wide bg-slate-100 text-slate-700 px-2 py-0.5 rounded">
                          {event.event_category}
                        </span>
                      </td>
                      <td className="px-4 py-2 text-slate-600 font-mono text-xs">
                        {event.target_type
                          ? `${event.target_type}:${(event.target_id || '').slice(0, 18)}${(event.target_id || '').length > 18 ? '…' : ''}`
                          : '—'}
                      </td>
                      <td className="px-4 py-2 text-slate-600 font-mono text-xs">
                        {event.actor_ip || '—'}
                      </td>
                      <td className="px-4 py-2">
                        {event.success ? (
                          <span className="text-emerald-700 text-xs font-semibold">OK</span>
                        ) : (
                          <span className="text-red-700 text-xs font-semibold" title={event.error_message || ''}>
                            FAIL
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {data && data.total > PAGE_SIZE && (
          <div className="mt-4 flex items-center justify-between text-sm text-slate-600">
            <p>
              Showing {page * PAGE_SIZE + 1}–
              {Math.min((page + 1) * PAGE_SIZE, data.total)} of{' '}
              {data.total.toLocaleString()} events
            </p>
            <div className="flex gap-2">
              <button
                type="button"
                disabled={page === 0}
                onClick={() => setPage((p) => Math.max(0, p - 1))}
                className="px-3 py-1 rounded border border-slate-300 bg-white disabled:opacity-50 hover:bg-slate-50"
              >
                Previous
              </button>
              <button
                type="button"
                disabled={page + 1 >= totalPages}
                onClick={() => setPage((p) => p + 1)}
                className="px-3 py-1 rounded border border-slate-300 bg-white disabled:opacity-50 hover:bg-slate-50"
              >
                Next
              </button>
            </div>
          </div>
        )}
      </main>
    </div>
  );
};

export default PartnerAuditLog;
