/**
 * PartnerMeshTopology — Session 206 round-table P3.
 *
 * Visual mesh map for one site: appliances + the devices each one
 * scans. Layout is grid-based (no force-directed layout library —
 * keeps the bundle lean and the rendering deterministic).
 *
 * Helps partner triage:
 *   - Which appliance is covering which subnet?
 *   - Is the mesh degenerate (one appliance covering everything)?
 *   - Do online appliance counts match the site's expected fleet?
 */

import React, { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

interface Appliance {
  appliance_id: string;
  hostname: string;
  display_name: string;
  mac_address: string | null;
  status: string;
  agent_version: string | null;
  last_checkin: string | null;
  scan_target_count: number;
}

interface Device {
  id: string;
  hostname: string | null;
  ip_address: string | null;
  device_type: string | null;
  device_status: string | null;
  last_seen: string | null;
  assigned_mac: string | null;
  owner_appliance_id: string | null;
}

interface TopologyResponse {
  site_id: string;
  clinic_name: string;
  appliances: Appliance[];
  devices: Device[];
  online_appliance_count: number;
  total_appliance_count: number;
  total_devices: number;
  generated_at: string;
}

// Deterministic color palette per appliance position. Subtle enough to
// distinguish groups without shouting.
const PALETTE = [
  { bg: 'bg-sky-100', text: 'text-sky-700', dot: 'bg-sky-500', border: 'border-sky-300' },
  { bg: 'bg-violet-100', text: 'text-violet-700', dot: 'bg-violet-500', border: 'border-violet-300' },
  { bg: 'bg-emerald-100', text: 'text-emerald-700', dot: 'bg-emerald-500', border: 'border-emerald-300' },
  { bg: 'bg-amber-100', text: 'text-amber-700', dot: 'bg-amber-500', border: 'border-amber-300' },
  { bg: 'bg-rose-100', text: 'text-rose-700', dot: 'bg-rose-500', border: 'border-rose-300' },
  { bg: 'bg-cyan-100', text: 'text-cyan-700', dot: 'bg-cyan-500', border: 'border-cyan-300' },
];

function normalizeMac(m: string | null): string {
  if (!m) return '';
  return m.replace(/[^A-F0-9a-f]/g, '').toUpperCase();
}

function relTime(iso: string | null): string {
  if (!iso) return 'never';
  const t = new Date(iso).getTime();
  const diff = Math.max(0, Math.floor((Date.now() - t) / 1000));
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

export const PartnerMeshTopology: React.FC = () => {
  const { siteId } = useParams<{ siteId: string }>();
  const navigate = useNavigate();
  const [data, setData] = useState<TopologyResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!siteId) return;
    let cancelled = false;
    fetch(`/api/partners/me/sites/${siteId}/topology`, { credentials: 'include' })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((d: TopologyResponse) => {
        if (!cancelled) { setData(d); setLoading(false); }
      })
      .catch((e) => {
        if (!cancelled) { setError(String(e)); setLoading(false); }
      });
    return () => { cancelled = true; };
  }, [siteId]);

  if (loading) {
    return <div className="p-6 text-sm text-slate-500">Loading topology…</div>;
  }
  if (error || !data) {
    return (
      <div className="p-6">
        <div className="bg-rose-50 border border-rose-200 rounded p-4 text-sm text-rose-700">
          Topology failed to load: {error ?? 'no data'}
        </div>
        <button
          onClick={() => navigate('/partner/dashboard')}
          className="mt-3 text-sm text-blue-600 hover:underline"
        >
          ← Back to partner dashboard
        </button>
      </div>
    );
  }

  // Build color map for online appliances (offline get gray).
  const macToColor = new Map<string, typeof PALETTE[number]>();
  const onlineList = data.appliances.filter((a) => a.status === 'online');
  onlineList.forEach((a, idx) => {
    const mac = normalizeMac(a.mac_address);
    if (mac) macToColor.set(mac, PALETTE[idx % PALETTE.length]);
  });

  // Group devices by assigned_mac (from hash-ring result)
  const devicesByAppliance = new Map<string, Device[]>();
  const unassigned: Device[] = [];
  data.devices.forEach((d) => {
    const mac = d.assigned_mac || '';
    if (mac && macToColor.has(mac)) {
      if (!devicesByAppliance.has(mac)) devicesByAppliance.set(mac, []);
      devicesByAppliance.get(mac)!.push(d);
    } else {
      unassigned.push(d);
    }
  });

  return (
    <div className="min-h-screen bg-slate-50 p-6">
      <div className="max-w-7xl mx-auto">
        <button
          onClick={() => navigate('/partner/dashboard')}
          className="text-sm text-blue-600 hover:underline mb-3"
        >
          ← Partner dashboard
        </button>

        {/* Site header */}
        <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-5 mb-5">
          <div className="flex items-start justify-between">
            <div>
              <h1 className="text-xl font-bold text-slate-900">{data.clinic_name || data.site_id}</h1>
              <div className="text-sm text-slate-500">Site: {data.site_id}</div>
            </div>
            <div className="text-right text-sm">
              <div className="text-slate-700">
                <span className="font-semibold">{data.online_appliance_count}</span>
                {' '}/ {data.total_appliance_count} appliances online
              </div>
              <div className="text-slate-500 text-xs">
                {data.total_devices} discovered devices · computed {relTime(data.generated_at)}
              </div>
            </div>
          </div>
        </div>

        {/* Appliance row */}
        <div className="mb-5">
          <div className="text-[11px] uppercase tracking-wide text-slate-500 font-semibold mb-2">
            Appliances
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {data.appliances.map((a) => {
              const mac = normalizeMac(a.mac_address);
              const color = macToColor.get(mac);
              const isOnline = a.status === 'online';
              return (
                <div
                  key={a.appliance_id}
                  className={`rounded-lg border p-4 ${
                    isOnline && color ? `${color.bg} ${color.border}` : 'bg-slate-100 border-slate-200'
                  }`}
                >
                  <div className="flex items-center justify-between mb-1">
                    <div className="font-mono text-sm font-semibold text-slate-900 truncate">
                      {a.display_name}
                    </div>
                    <span
                      className={`w-2 h-2 rounded-full ${
                        isOnline ? (color?.dot ?? 'bg-emerald-500') : 'bg-slate-400'
                      }`}
                      title={a.status}
                    />
                  </div>
                  <div className="text-xs text-slate-600">
                    {a.status} · v{a.agent_version || '?'}
                  </div>
                  <div className="text-xs text-slate-500 mt-0.5">
                    Last checkin {relTime(a.last_checkin)}
                  </div>
                  <div className="mt-2 text-xs">
                    <span className={isOnline ? color?.text : 'text-slate-500'}>
                      scanning <b>{a.scan_target_count}</b> target{a.scan_target_count === 1 ? '' : 's'}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* Devices grouped by assignment */}
        <div>
          <div className="text-[11px] uppercase tracking-wide text-slate-500 font-semibold mb-2">
            Devices by appliance (hash-ring assignment)
          </div>
          {data.online_appliance_count === 0 ? (
            <div className="bg-amber-50 border border-amber-200 rounded p-3 text-sm text-amber-800">
              No appliances are currently online for this site — mesh assignment cannot be computed.
              Devices shown in the "unassigned" section below.
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
              {onlineList.map((a) => {
                const mac = normalizeMac(a.mac_address);
                const color = macToColor.get(mac);
                const devs = devicesByAppliance.get(mac) || [];
                return (
                  <div key={a.appliance_id} className={`rounded-lg border ${color?.border ?? 'border-slate-200'} overflow-hidden`}>
                    <div className={`${color?.bg ?? 'bg-slate-100'} px-3 py-1.5 text-xs font-medium ${color?.text ?? 'text-slate-700'}`}>
                      {a.display_name} · {devs.length} device{devs.length === 1 ? '' : 's'}
                    </div>
                    <div className="divide-y divide-slate-100 bg-white max-h-64 overflow-y-auto">
                      {devs.length === 0 ? (
                        <div className="px-3 py-2 text-xs text-slate-400 italic">No targets assigned.</div>
                      ) : (
                        devs.map((d) => (
                          <div key={d.id} className="px-3 py-1.5 flex items-center justify-between text-xs">
                            <div className="min-w-0 flex-1">
                              <div className="font-mono text-slate-900 truncate">
                                {d.ip_address || '—'}
                              </div>
                              <div className="text-slate-500 truncate">
                                {d.hostname || d.device_type || 'device'}
                              </div>
                            </div>
                            <span className={`ml-2 text-[10px] shrink-0 ${
                              d.device_status === 'online' ? 'text-emerald-600' : 'text-slate-400'
                            }`}>
                              {d.device_status || '—'}
                            </span>
                          </div>
                        ))
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {unassigned.length > 0 && (
            <div className="mt-4 rounded-lg border border-slate-200 overflow-hidden">
              <div className="bg-slate-100 px-3 py-1.5 text-xs font-medium text-slate-700">
                Unassigned · {unassigned.length} device{unassigned.length === 1 ? '' : 's'}
              </div>
              <div className="divide-y divide-slate-100 bg-white max-h-64 overflow-y-auto">
                {unassigned.slice(0, 50).map((d) => (
                  <div key={d.id} className="px-3 py-1.5 flex items-center justify-between text-xs">
                    <div className="min-w-0 flex-1">
                      <div className="font-mono text-slate-900 truncate">
                        {d.ip_address || '—'}
                      </div>
                      <div className="text-slate-500 truncate">
                        {d.hostname || d.device_type || 'device'}
                      </div>
                    </div>
                  </div>
                ))}
                {unassigned.length > 50 && (
                  <div className="px-3 py-1.5 text-[11px] text-slate-400 italic">
                    Showing 50 of {unassigned.length}.
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default PartnerMeshTopology;
