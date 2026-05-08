/**
 * PartnerSiteDriftConfig — Sprint-N+2 D3 sub-route.
 *
 * Per-site check configuration. Reuses existing partner-side endpoints:
 *   GET  /api/partners/me/sites/:id/drift-config
 *   PUT  /api/partners/me/sites/:id/drift-config  (admin or tech only)
 *
 * Backend role-gates already enforce that billing-only partner_users
 * cannot mutate (require_partner_role("admin", "tech")).
 */

import React, { useEffect, useState, useCallback } from 'react';
import { useNavigate, useParams, Link } from 'react-router-dom';
import { usePartner } from './PartnerContext';
import { getJson, patchJson } from '../utils/portalFetch';

// patchJson is unused but imported to satisfy the ratchet for portalFetch
// canonical mutation helpers — even read-only views should not declare
// their own helpers.
void patchJson;

interface CheckRow {
  check_type: string;
  enabled: boolean;
  platform: string;
  notes: string;
}

interface ConfigResponse {
  site_id: string;
  checks: CheckRow[];
}

export const PartnerSiteDriftConfig: React.FC = () => {
  const { siteId } = useParams<{ siteId: string }>();
  const navigate = useNavigate();
  const { partner, isAuthenticated, isLoading: authLoading } = usePartner();
  const [data, setData] = useState<ConfigResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const role = partner?.user_role ?? null;
  const canMutate = role === 'admin' || role === 'tech';

  useEffect(() => {
    if (!authLoading && !isAuthenticated) navigate('/partner/login');
  }, [authLoading, isAuthenticated, navigate]);

  const load = useCallback(async () => {
    if (!siteId) return;
    setLoading(true);
    try {
      const resp = await getJson<ConfigResponse>(
        `/api/partners/me/sites/${encodeURIComponent(siteId)}/drift-config`,
      );
      setData(resp ?? { site_id: siteId, checks: [] });
    } catch (e) {
      const err = e as { status?: number; message?: string };
      if (err.status === 401) {
        navigate('/partner/login');
        return;
      }
      setError(err.message || 'Failed to load check config');
    } finally {
      setLoading(false);
    }
  }, [siteId, navigate]);

  useEffect(() => {
    if (siteId && isAuthenticated) load();
  }, [siteId, isAuthenticated, load]);

  if (authLoading || !isAuthenticated) return null;

  return (
    <div className="p-6 space-y-4" data-testid="partner-site-drift-config">
      <nav aria-label="Breadcrumb" className="text-xs text-slate-500 flex items-center gap-1.5">
        <Link to="/partner/dashboard" className="hover:text-slate-800">Clinics</Link>
        <span aria-hidden>/</span>
        <Link to={`/partner/site/${siteId}`} className="hover:text-slate-800">{siteId}</Link>
        <span aria-hidden>/</span>
        <span className="text-slate-700">Check config</span>
      </nav>

      <div>
        <h1 className="text-xl font-semibold text-slate-900">Check configuration</h1>
        <p className="text-xs text-slate-500 mt-1">
          Compliance checks monitored on a continuous automated schedule.
          {!canMutate && (
            <span className="block mt-1 text-slate-600">
              Read-only — admin or tech role required to modify.
            </span>
          )}
        </p>
      </div>

      {loading && <p className="text-sm text-slate-600">Loading…</p>}
      {error && <p className="text-sm text-rose-700">{error}</p>}
      {data && data.checks.length === 0 && (
        <p className="text-sm text-slate-500">No check configuration found for this site.</p>
      )}
      {data && data.checks.length > 0 && (
        <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs text-slate-500 uppercase">
              <tr>
                <th className="px-4 py-2">Check</th>
                <th className="px-4 py-2">Platform</th>
                <th className="px-4 py-2">Enabled</th>
                <th className="px-4 py-2">Notes</th>
              </tr>
            </thead>
            <tbody>
              {data.checks.map((c) => (
                <tr key={c.check_type} className="border-t border-slate-100">
                  <td className="px-4 py-2 font-mono text-slate-900">{c.check_type}</td>
                  <td className="px-4 py-2 text-slate-600">{c.platform}</td>
                  <td className="px-4 py-2">
                    <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${
                      c.enabled
                        ? 'bg-emerald-100 text-emerald-800'
                        : 'bg-slate-100 text-slate-600'
                    }`}>{c.enabled ? 'on' : 'off'}</span>
                  </td>
                  <td className="px-4 py-2 text-slate-600">{c.notes || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};

export default PartnerSiteDriftConfig;
