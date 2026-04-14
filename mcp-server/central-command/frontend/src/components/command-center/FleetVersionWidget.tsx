/**
 * FleetVersionWidget — Session 206 round-table P2 for operator admin.
 *
 * Calls /api/dashboard/fleet-version-distribution. Renders one row per
 * agent_version with count + online/total ratio. Ordered newest
 * version first so fleet-drift ("why are 3 boxes still on v0.3.82?")
 * is obvious at a glance.
 */

import React, { useEffect, useState } from 'react';
import { GlassCard } from '../shared';

interface Version {
  version: string;
  count: number;
  online: number;
  most_recent_checkin: string | null;
}

interface Response {
  versions: Version[];
  total_appliances: number;
  generated_at: string;
}

export const FleetVersionWidget: React.FC = () => {
  const [data, setData] = useState<Response | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    fetch('/api/dashboard/fleet-version-distribution', { credentials: 'include' })
      .then((r) => (r.ok ? r.json() : null))
      .then((d: Response | null) => { if (!cancelled) { setData(d); setLoading(false); } })
      .catch(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  if (loading) return null;
  if (!data || data.versions.length === 0) return null;

  const latest = data.versions[0]?.version;

  return (
    <GlassCard>
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold text-label-primary">Fleet — agent version distribution</h2>
        <span className="text-[11px] text-label-tertiary">{data.total_appliances} appliances</span>
      </div>
      <div className="space-y-1.5">
        {data.versions.map((v) => {
          const isLatest = v.version === latest;
          const pct = data.total_appliances > 0 ? (v.count / data.total_appliances) * 100 : 0;
          return (
            <div key={v.version} className="flex items-center gap-3 text-sm">
              <div className="font-mono text-xs w-24 shrink-0 text-label-primary truncate" title={v.version}>
                {v.version}
                {isLatest && <span className="ml-1 text-[9px] text-emerald-400 font-semibold">LATEST</span>}
              </div>
              <div className="flex-1 h-2 bg-slate-700/40 rounded-full overflow-hidden">
                <div
                  className={isLatest ? 'h-full bg-emerald-400' : 'h-full bg-amber-400'}
                  style={{ width: `${pct}%` }}
                />
              </div>
              <div className="text-xs text-label-secondary tabular-nums shrink-0 w-20 text-right">
                {v.online}/{v.count} online
              </div>
            </div>
          );
        })}
      </div>
      {data.versions.length > 1 && (
        <p className="text-[11px] text-label-tertiary mt-3">
          {data.versions.length - 1} version{data.versions.length === 2 ? '' : 's'} behind latest. Schedule fleet update.
        </p>
      )}
    </GlassCard>
  );
};

export default FleetVersionWidget;
