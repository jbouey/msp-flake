/**
 * FleetStatusPanel — fleet-wide appliance health, read from the
 * appliance_status_rollup MV (Migration 191).
 *
 * Replaces the old pattern of polling /api/sites/{id}/appliances per
 * viewer per refresh. Single rollup query, narrow row, refreshed
 * server-side every 60s; client polls every 30s.
 *
 * Three KPIs (online/stale/offline) + a "needs attention" list of the
 * worst N appliances by stale_seconds. No motion: timestamps are
 * stable "Xs/Xm ago" via formatTimeAgo.
 */
import React from 'react';
import { Link } from 'react-router-dom';
import { GlassCard } from '../shared';
import { useApplianceRollup } from '../../hooks';
import { formatTimeAgo } from '../../constants';

const STATUS_TONE = {
  online: 'text-emerald-400',
  stale: 'text-amber-400',
  offline: 'text-rose-400',
} as const;

const STATUS_DOT = {
  online: 'bg-emerald-500',
  stale: 'bg-amber-500',
  offline: 'bg-rose-500',
} as const;

interface FleetStatusPanelProps {
  /** Cap the "Needs attention" list. Default 10. */
  worstLimit?: number;
}

export const FleetStatusPanel: React.FC<FleetStatusPanelProps> = ({ worstLimit = 10 }) => {
  const { data, isLoading, error } = useApplianceRollup();

  if (isLoading && !data) {
    return (
      <GlassCard className="p-4">
        <div className="text-sm text-label-tertiary">Loading fleet status...</div>
      </GlassCard>
    );
  }
  if (error && !data) {
    return (
      <GlassCard className="p-4">
        <div className="text-sm text-rose-400">
          Failed to load fleet status: {error instanceof Error ? error.message : String(error)}
        </div>
      </GlassCard>
    );
  }
  if (!data) return null;

  const { totals, appliances, generated_at } = data;
  // "Needs attention" = stale + offline, sorted by stale_seconds desc
  const worst = appliances
    .filter((a) => a.live_status !== 'online')
    .slice(0, worstLimit);

  return (
    <div className="space-y-3">
      {/* KPI strip — three counts. Static, no motion. */}
      <div className="grid grid-cols-3 gap-3">
        {(['online', 'stale', 'offline'] as const).map((s) => (
          <GlassCard key={s} className="p-4">
            <div className="flex items-center gap-2">
              <span className={`inline-block w-2 h-2 rounded-full ${STATUS_DOT[s]}`} />
              <span className="text-[11px] uppercase tracking-wide text-label-tertiary">
                {s}
              </span>
            </div>
            <div className={`mt-1 text-3xl font-bold tabular-nums ${STATUS_TONE[s]}`}>
              {totals[s]}
            </div>
          </GlassCard>
        ))}
      </div>

      {/* Needs attention list */}
      <GlassCard className="p-4">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold text-label-primary">Needs attention</h2>
          <span className="text-[11px] text-label-tertiary">
            Updated {formatTimeAgo(generated_at)}
          </span>
        </div>
        {worst.length === 0 ? (
          <div className="text-sm text-label-tertiary italic py-4 text-center">
            All appliances are online.
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-[11px] uppercase tracking-wide text-label-tertiary border-b border-glass-border">
              <tr>
                <th className="py-1.5 text-left font-medium">Appliance</th>
                <th className="py-1.5 text-left font-medium">Site</th>
                <th className="py-1.5 text-left font-medium">Status</th>
                <th className="py-1.5 text-right font-medium" title="Last heartbeat (ground truth)">Last heartbeat</th>
                <th className="py-1.5 text-right font-medium">24h uptime</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-glass-border/40">
              {worst.map((a) => {
                const hasDrift = Math.abs(a.liveness_drift_seconds || 0) > 60;
                return (
                <tr key={a.appliance_id}>
                  <td className="py-1.5 text-label-primary">
                    <div className="flex items-center gap-2">
                      <div className="font-medium">
                        {a.display_name || a.hostname || a.appliance_id}
                      </div>
                      {hasDrift && (
                        <span
                          className="px-1.5 py-0.5 text-[10px] font-semibold rounded bg-rose-500/15 text-rose-400 border border-rose-500/30"
                          title={`last_checkin disagrees with heartbeats by ${a.liveness_drift_seconds}s — investigate`}
                        >
                          drift
                        </span>
                      )}
                    </div>
                    <div className="text-[10px] font-mono text-label-tertiary">
                      {a.mac_address || '-'}
                    </div>
                  </td>
                  <td className="py-1.5 text-label-secondary">
                    <Link
                      to={`/sites/${a.site_id}`}
                      className="hover:underline text-blue-400"
                    >
                      {a.site_id}
                    </Link>
                  </td>
                  <td className="py-1.5">
                    <span className={`inline-flex items-center gap-1.5 ${STATUS_TONE[a.live_status]}`}>
                      <span className={`w-2 h-2 rounded-full ${STATUS_DOT[a.live_status]}`} />
                      {a.live_status}
                    </span>
                  </td>
                  <td className="py-1.5 text-right tabular-nums text-label-secondary" title="Ground truth from appliance_heartbeats">
                    {formatTimeAgo(a.last_heartbeat_at || a.last_checkin)}
                  </td>
                  <td className="py-1.5 text-right tabular-nums text-label-secondary">
                    {a.checkin_count_24h > 0
                      ? `${(a.uptime_ratio_24h * 100).toFixed(0)}%`
                      : '—'}
                  </td>
                </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </GlassCard>
    </div>
  );
};

export default FleetStatusPanel;
