/**
 * PartnerSiteAgents — Sprint-N+2 D3 sub-route.
 *
 * Read-only list of Go workstation agents reporting at this site,
 * partner-scoped via the existing GET /api/partners/me/sites/:id
 * detail endpoint (admin-only mutations omitted by the backend
 * role gates already in partners.py).
 */

import React, { useEffect, useState, useCallback } from 'react';
import { useNavigate, useParams, Link } from 'react-router-dom';
import { usePartner } from './PartnerContext';
import { getJson } from '../utils/portalFetch';

interface GoAgent {
  agent_id: string;
  hostname: string | null;
  os: string | null;
  version: string | null;
  status: string | null;
  last_heartbeat: string | null;
}

interface AgentResponse {
  agents: GoAgent[];
  online: number;
  total: number;
}

function formatTimeAgo(iso: string | null): string {
  if (!iso) return 'never';
  const t = new Date(iso).getTime();
  const diff = Math.max(0, Math.floor((Date.now() - t) / 1000));
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

export const PartnerSiteAgents: React.FC = () => {
  const { siteId } = useParams<{ siteId: string }>();
  const navigate = useNavigate();
  const { isAuthenticated, isLoading: authLoading } = usePartner();
  const [data, setData] = useState<AgentResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!authLoading && !isAuthenticated) navigate('/partner/login');
  }, [authLoading, isAuthenticated, navigate]);

  const load = useCallback(async () => {
    if (!siteId) return;
    setLoading(true);
    try {
      const resp = await getJson<AgentResponse>(
        `/api/partners/me/sites/${encodeURIComponent(siteId)}/go-agents`,
      );
      setData(resp ?? { agents: [], online: 0, total: 0 });
    } catch (e) {
      const err = e as { status?: number; message?: string };
      if (err.status === 401) {
        navigate('/partner/login');
        return;
      }
      setError(err.message || 'Failed to load agents');
    } finally {
      setLoading(false);
    }
  }, [siteId, navigate]);

  useEffect(() => {
    if (siteId && isAuthenticated) load();
  }, [siteId, isAuthenticated, load]);

  if (authLoading || !isAuthenticated) return null;

  return (
    <div className="p-6 space-y-4" data-testid="partner-site-agents">
      <nav aria-label="Breadcrumb" className="text-xs text-slate-500 flex items-center gap-1.5">
        <Link to="/partner/dashboard" className="hover:text-slate-800">Clinics</Link>
        <span aria-hidden>/</span>
        <Link to={`/partner/site/${siteId}`} className="hover:text-slate-800">{siteId}</Link>
        <span aria-hidden>/</span>
        <span className="text-slate-700">Workstation agents</span>
      </nav>

      <h1 className="text-xl font-semibold text-slate-900">Workstation agents</h1>

      {loading && <p className="text-sm text-slate-600">Loading agents…</p>}
      {error && <p className="text-sm text-rose-700">{error}</p>}
      {data && data.agents.length === 0 && (
        <p className="text-sm text-slate-500">
          No workstation agents reporting at this site.
        </p>
      )}
      {data && data.agents.length > 0 && (
        <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs text-slate-500 uppercase">
              <tr>
                <th className="px-4 py-2">Hostname</th>
                <th className="px-4 py-2">OS</th>
                <th className="px-4 py-2">Version</th>
                <th className="px-4 py-2">Last heartbeat</th>
                <th className="px-4 py-2">Status</th>
              </tr>
            </thead>
            <tbody>
              {data.agents.map((a) => (
                <tr key={a.agent_id} className="border-t border-slate-100">
                  <td className="px-4 py-2 font-medium text-slate-900">{a.hostname || a.agent_id}</td>
                  <td className="px-4 py-2 text-slate-600">{a.os || '—'}</td>
                  <td className="px-4 py-2 text-slate-600">{a.version || '—'}</td>
                  <td className="px-4 py-2 text-slate-600">{formatTimeAgo(a.last_heartbeat)}</td>
                  <td className="px-4 py-2">
                    <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${
                      a.status === 'online'
                        ? 'bg-emerald-100 text-emerald-800'
                        : 'bg-slate-100 text-slate-600'
                    }`}>{a.status || 'unknown'}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};

export default PartnerSiteAgents;
