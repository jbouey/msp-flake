/**
 * PartnerSiteDevices — Sprint-N+2 D3 sub-route.
 *
 * Read-only list of netscan-discovered devices for this site.
 * Reuses the existing GET /api/partners/me/sites/:id detail
 * endpoint's `assets` field.
 */

import React, { useEffect, useState, useCallback } from 'react';
import { useNavigate, useParams, Link } from 'react-router-dom';
import { usePartner } from './PartnerContext';
import { getJson } from '../utils/portalFetch';

interface Asset {
  id: string;
  ip_address: string;
  hostname: string | null;
  asset_type: string;
  os_info: string | null;
  monitoring_status: string | null;
  last_seen_at: string | null;
}

interface DetailResponse {
  assets: Asset[];
  asset_count: number;
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

export const PartnerSiteDevices: React.FC = () => {
  const { siteId } = useParams<{ siteId: string }>();
  const navigate = useNavigate();
  const { isAuthenticated, isLoading: authLoading } = usePartner();
  const [data, setData] = useState<DetailResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!authLoading && !isAuthenticated) navigate('/partner/login');
  }, [authLoading, isAuthenticated, navigate]);

  const load = useCallback(async () => {
    if (!siteId) return;
    setLoading(true);
    try {
      const resp = await getJson<DetailResponse>(
        `/api/partners/me/sites/${encodeURIComponent(siteId)}`,
      );
      setData(resp ?? { assets: [], asset_count: 0 });
    } catch (e) {
      const err = e as { status?: number; message?: string };
      if (err.status === 401) {
        navigate('/partner/login');
        return;
      }
      setError(err.message || 'Failed to load devices');
    } finally {
      setLoading(false);
    }
  }, [siteId, navigate]);

  useEffect(() => {
    if (siteId && isAuthenticated) load();
  }, [siteId, isAuthenticated, load]);

  if (authLoading || !isAuthenticated) return null;

  return (
    <div className="p-6 space-y-4" data-testid="partner-site-devices">
      <nav aria-label="Breadcrumb" className="text-xs text-slate-500 flex items-center gap-1.5">
        <Link to="/partner/dashboard" className="hover:text-slate-800">Clinics</Link>
        <span aria-hidden>/</span>
        <Link to={`/partner/site/${siteId}`} className="hover:text-slate-800">{siteId}</Link>
        <span aria-hidden>/</span>
        <span className="text-slate-700">Devices</span>
      </nav>

      <h1 className="text-xl font-semibold text-slate-900">Discovered devices</h1>

      {loading && <p className="text-sm text-slate-600">Loading devices…</p>}
      {error && <p className="text-sm text-rose-700">{error}</p>}
      {data && data.assets.length === 0 && (
        <p className="text-sm text-slate-500">No devices discovered yet at this site.</p>
      )}
      {data && data.assets.length > 0 && (
        <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs text-slate-500 uppercase">
              <tr>
                <th className="px-4 py-2">Hostname</th>
                <th className="px-4 py-2">IP</th>
                <th className="px-4 py-2">Type</th>
                <th className="px-4 py-2">OS</th>
                <th className="px-4 py-2">Last seen</th>
                <th className="px-4 py-2">Monitoring</th>
              </tr>
            </thead>
            <tbody>
              {data.assets.map((a) => (
                <tr key={a.id} className="border-t border-slate-100">
                  <td className="px-4 py-2 font-medium text-slate-900">{a.hostname || '—'}</td>
                  <td className="px-4 py-2 font-mono text-slate-700">{a.ip_address}</td>
                  <td className="px-4 py-2 text-slate-600">{a.asset_type}</td>
                  <td className="px-4 py-2 text-slate-600">{a.os_info || '—'}</td>
                  <td className="px-4 py-2 text-slate-600">{formatTimeAgo(a.last_seen_at)}</td>
                  <td className="px-4 py-2 text-slate-600">{a.monitoring_status || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};

export default PartnerSiteDevices;
