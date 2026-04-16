/**
 * AdminSubstrateHealth — single page that surfaces the Substrate
 * Integrity Engine output: active invariant violations + the
 * provisioning-latency SLA. Polls every 60s.
 *
 * The deliberate design: an enterprise customer (or auditor, or
 * sales prospect on a demo call) can land on /admin/substrate-health
 * and immediately see whether the platform is meeting its own
 * promises. Green = nothing is silently broken. Red = the
 * substrate has noticed a problem before the customer did.
 */

import React, { useEffect, useState } from 'react';
import { GlassCard, Spinner } from '../components/shared';

interface ActiveViolation {
  invariant: string;
  severity: 'sev1' | 'sev2' | 'sev3';
  site_id: string | null;
  detected_at: string;
  last_seen_at: string;
  minutes_open: number;
  details: Record<string, unknown>;
  // v36: human-facing taxonomy pulled from assertions._DISPLAY_METADATA.
  // When present, the UI renders display_name + recommended_action as
  // the primary row; invariant + details collapse behind a "View raw".
  display_name?: string;
  recommended_action?: string;
  description?: string;
}

interface ResolvedViolation {
  invariant: string;
  severity: string;
  site_id: string | null;
  detected_at: string;
  resolved_at: string;
  display_name?: string;
}

interface ViolationsPayload {
  active: ActiveViolation[];
  rollup: Record<string, number>;
  active_total: number;
  resolved_24h: ResolvedViolation[];
}

interface SlaPayload {
  sample_count: number;
  target_minutes: number;
  breaches_over_target: number;
  p50_minutes: number | null;
  p95_minutes: number | null;
  p99_minutes: number | null;
  max_minutes: number | null;
  min_minutes: number | null;
}

const SEVERITY_COLOR: Record<string, string> = {
  sev1: 'bg-rose-500/20 text-rose-300 border-rose-500/40',
  sev2: 'bg-amber-500/20 text-amber-300 border-amber-500/40',
  sev3: 'bg-sky-500/20 text-sky-300 border-sky-500/40',
};

function relTime(iso: string): string {
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

export const AdminSubstrateHealth: React.FC = () => {
  const [violations, setViolations] = useState<ViolationsPayload | null>(null);
  const [sla, setSla] = useState<SlaPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const [vRes, sRes] = await Promise.all([
          fetch('/api/dashboard/admin/substrate-violations', { credentials: 'include' }),
          fetch('/api/dashboard/admin/substrate-installation-sla', { credentials: 'include' }),
        ]);
        if (!vRes.ok) throw new Error(`violations HTTP ${vRes.status}`);
        if (!sRes.ok) throw new Error(`sla HTTP ${sRes.status}`);
        const v = await vRes.json();
        const s = await sRes.json();
        if (!cancelled) {
          setViolations(v);
          setSla(s);
          setLoading(false);
          setError(null);
        }
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : String(e));
          setLoading(false);
        }
      }
    };
    load();
    const int = setInterval(load, 60_000);
    return () => { cancelled = true; clearInterval(int); };
  }, []);

  if (loading && !violations) return <div className="p-6"><Spinner /></div>;
  if (error && !violations) return <div className="p-6 text-rose-400 text-sm">Failed to load: {error}</div>;
  if (!violations) return null;

  const totalActive = violations.active_total;
  const sev1 = violations.rollup.sev1 || 0;
  const sev2 = violations.rollup.sev2 || 0;
  const sev3 = violations.rollup.sev3 || 0;

  const overallTone =
    sev1 > 0 ? 'bg-rose-500/30 text-rose-200 border-rose-500'
    : sev2 > 0 ? 'bg-amber-500/30 text-amber-200 border-amber-500'
    : sev3 > 0 ? 'bg-sky-500/30 text-sky-200 border-sky-500'
    : 'bg-emerald-500/30 text-emerald-200 border-emerald-500';
  const overallLabel =
    sev1 > 0 ? 'CRITICAL — Sev-1 substrate violation active'
    : sev2 > 0 ? 'Degraded — Sev-2 violations active'
    : sev3 > 0 ? 'Informational — Sev-3 only'
    : 'Healthy — 0 active substrate violations';

  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-white">Substrate Integrity</h1>
        <p className="text-white/60 text-sm mt-1">
          Continuous self-assertion of platform invariants. Updated every 60s.
        </p>
      </div>

      <div className={`px-5 py-4 rounded-lg border ${overallTone}`}>
        <div className="text-lg font-medium">{overallLabel}</div>
        <div className="text-sm opacity-80 mt-1">
          {totalActive} active · {violations.resolved_24h.length} auto-resolved in last 24h
        </div>
      </div>

      <GlassCard>
        <div className="px-5 py-4 border-b border-white/10">
          <h2 className="text-lg font-medium text-white">Active violations</h2>
        </div>
        <div className="px-5 py-4">
          {violations.active.length === 0 ? (
            <div className="text-emerald-300 text-sm">All invariants passing. Nothing requires action.</div>
          ) : (
            <table className="w-full text-sm">
              <thead className="text-white/60 text-left">
                <tr>
                  <th className="py-2 pr-4 w-24">Severity</th>
                  <th className="py-2 pr-4" colSpan={4}>Issue · recommended action</th>
                </tr>
              </thead>
              <tbody>
                {violations.active.map((v, i) => (
                  <tr key={i} className="border-t border-white/5">
                    <td className="py-2 pr-4 align-top">
                      <span className={`px-2 py-0.5 rounded text-xs border ${SEVERITY_COLOR[v.severity] || ''}`}>
                        {v.severity}
                      </span>
                    </td>
                    <td className="py-2 pr-4 align-top" colSpan={4}>
                      {/* v36: human-facing row. display_name + recommended_action
                          render as the primary content; engineering name +
                          raw details live behind a collapsible for the auditor /
                          engineer who wants the full picture. */}
                      <div className="text-white font-medium">
                        {v.display_name ?? v.invariant}
                      </div>
                      {v.recommended_action && (
                        <div className="mt-1 text-emerald-200/80 text-xs leading-relaxed">
                          <span className="font-semibold text-emerald-300">Recommended:</span>{' '}
                          {v.recommended_action}
                        </div>
                      )}
                      <div className="mt-2 flex items-center gap-3 text-[11px] text-white/50">
                        <span className="font-mono">{v.invariant}</span>
                        <span>·</span>
                        <span>{v.site_id || 'global'}</span>
                        <span>·</span>
                        <span>open {Math.round(v.minutes_open)}m</span>
                      </div>
                      <details className="mt-2 text-white/60 text-xs">
                        <summary className="cursor-pointer select-none text-white/50 hover:text-white/80">
                          View raw details
                        </summary>
                        <pre className="whitespace-pre-wrap break-all mt-2 p-2 rounded bg-black/30">
                          {JSON.stringify(v.details, null, 2)}
                        </pre>
                      </details>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </GlassCard>

      {sla && (
        <GlassCard>
          <div className="px-5 py-4 border-b border-white/10">
            <h2 className="text-lg font-medium text-white">Provisioning latency SLA</h2>
            <p className="text-white/60 text-xs mt-1">
              Time from live-USB first checkin to installed-system first checkin · 30 day window · target {sla.target_minutes} min
            </p>
          </div>
          <div className="px-5 py-4 grid grid-cols-2 md:grid-cols-5 gap-4 text-sm">
            <div>
              <div className="text-white/60 text-xs">Sample size</div>
              <div className="text-white font-medium">{sla.sample_count}</div>
            </div>
            <div>
              <div className="text-white/60 text-xs">p50</div>
              <div className="text-white font-medium">{sla.p50_minutes ?? '—'}m</div>
            </div>
            <div>
              <div className="text-white/60 text-xs">p95</div>
              <div className="text-white font-medium">{sla.p95_minutes ?? '—'}m</div>
            </div>
            <div>
              <div className="text-white/60 text-xs">p99</div>
              <div className={`font-medium ${(sla.p99_minutes ?? 0) > sla.target_minutes ? 'text-amber-300' : 'text-emerald-300'}`}>
                {sla.p99_minutes ?? '—'}m
              </div>
            </div>
            <div>
              <div className="text-white/60 text-xs">Over target</div>
              <div className={`font-medium ${sla.breaches_over_target > 0 ? 'text-rose-300' : 'text-emerald-300'}`}>
                {sla.breaches_over_target}
              </div>
            </div>
          </div>
        </GlassCard>
      )}

      {violations.resolved_24h.length > 0 && (
        <GlassCard>
          <div className="px-5 py-4 border-b border-white/10">
            <h2 className="text-lg font-medium text-white">Auto-resolved in last 24h</h2>
          </div>
          <div className="px-5 py-4">
            <table className="w-full text-sm">
              <thead className="text-white/60 text-left">
                <tr>
                  <th className="py-2 pr-4">Severity</th>
                  <th className="py-2 pr-4">Invariant</th>
                  <th className="py-2 pr-4">Site</th>
                  <th className="py-2 pr-4">Detected</th>
                  <th className="py-2 pr-4">Resolved</th>
                </tr>
              </thead>
              <tbody>
                {violations.resolved_24h.map((v, i) => (
                  <tr key={i} className="border-t border-white/5">
                    <td className="py-2 pr-4 text-white/70 text-xs">{v.severity}</td>
                    <td className="py-2 pr-4 text-white font-mono text-xs">{v.invariant}</td>
                    <td className="py-2 pr-4 text-white/70 text-xs">{v.site_id || '—'}</td>
                    <td className="py-2 pr-4 text-white/60 text-xs">{relTime(v.detected_at)}</td>
                    <td className="py-2 pr-4 text-emerald-300 text-xs">{relTime(v.resolved_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </GlassCard>
      )}
    </div>
  );
};

export default AdminSubstrateHealth;
