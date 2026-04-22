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
import RunbookDrawer from '../components/substrate/RunbookDrawer';
import CopyCliButton from '../components/substrate/CopyCliButton';
import ActionPreviewModal from '../components/substrate/ActionPreviewModal';

type ActionConfig = {
  actionKey: string;
  requiredReasonChars: number;
  cliFallback?: string;
  buildPlan: (details: Record<string, unknown>) => string;
  buildTargetRef: (details: Record<string, unknown>) => Record<string, unknown>;
};

const INVARIANT_ACTIONS: Record<string, ActionConfig> = {
  install_loop: {
    actionKey: 'cleanup_install_session',
    requiredReasonChars: 0,
    buildPlan: (d) => `Delete install_sessions row where mac=${d.mac}. Idempotent.`,
    buildTargetRef: (d) => ({ mac: d.mac, stage: d.stage }),
  },
  install_session_ttl: {
    actionKey: 'cleanup_install_session',
    requiredReasonChars: 0,
    buildPlan: (d) => `Delete install_sessions row where mac=${d.mac}. Idempotent.`,
    buildTargetRef: (d) => ({ mac: d.mac, stage: d.stage }),
  },
  auth_failure_lockout: {
    actionKey: 'unlock_platform_account',
    requiredReasonChars: 20,
    buildPlan: (d) =>
      `Unlock ${d.table}.email=${d.email}. Clears failed_login_attempts and locked_until.`,
    buildTargetRef: (d) => ({ table: d.table, email: d.email }),
  },
  agent_version_lag: {
    actionKey: 'reconcile_fleet_order',
    requiredReasonChars: 20,
    cliFallback: 'fleet_cli orders cancel ...',
    buildPlan: (d) =>
      `Mark fleet_orders[${d.order_id}] as completed. Use ONLY when upgrade was verified out-of-band.`,
    buildTargetRef: (d) => ({ order_id: d.order_id, site_id: d.site_id }),
  },
};

const CLI_TEMPLATES: Record<string, string> = {
  offline_appliance_over_1h:
    'mcp-server/central-command/backend/scripts/recover_legacy_appliance.sh {site_id} {mac} {ip}',
  agent_version_lag:
    'fleet_cli create update_daemon --site-id {site_id} --param appliance_id={appliance_id} --param binary_url={binary_url} --actor-email YOU@example.com --reason "..."',
};

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

interface FleetUpdateHealthPayload {
  nixos_rebuild: {
    success_7d: number;
    failed_7d: number;
    expired_7d: number;
    success_30d: number;
    failed_30d: number;
    expired_30d: number;
    last_success_at: string | null;
    last_failure_at: string | null;
    days_since_last_success: number | null;
  };
  update_daemon: {
    completed_7d: number;
    skipped_7d: number;
    failed_7d: number;
    last_completion_at: string | null;
  };
  agent_versions: Array<{ version: string; count: number }>;
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
  const [updateHealth, setUpdateHealth] = useState<FleetUpdateHealthPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [drawerInvariant, setDrawerInvariant] = useState<string | null>(null);
  const [modal, setModal] = useState<{ cfg: ActionConfig; details: Record<string, unknown> } | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const [vRes, sRes, uRes] = await Promise.all([
          fetch('/api/dashboard/admin/substrate-violations', { credentials: 'include' }),
          fetch('/api/dashboard/admin/substrate-installation-sla', { credentials: 'include' }),
          fetch('/api/dashboard/admin/substrate-fleet-update-health', { credentials: 'include' }),
        ]);
        if (!vRes.ok) throw new Error(`violations HTTP ${vRes.status}`);
        if (!sRes.ok) throw new Error(`sla HTTP ${sRes.status}`);
        const v = await vRes.json();
        const s = await sRes.json();
        // Tolerate 404 on the update-health endpoint in case it lags
        // behind a backend deploy — the rest of the page still renders.
        const u = uRes.ok ? await uRes.json() : null;
        if (!cancelled) {
          setViolations(v);
          setSla(s);
          setUpdateHealth(u);
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
  }, [refreshKey]);

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
                {violations.active.map((v, i) => {
                  const actionCfg = INVARIANT_ACTIONS[v.invariant];
                  const cliTemplate = CLI_TEMPLATES[v.invariant] ?? '';
                  return (
                    <tr key={i} data-testid="violation-row" className="border-t border-white/5">
                      <td className="py-2 pr-4 align-top">
                        <span className={`px-2 py-0.5 rounded text-xs border ${SEVERITY_COLOR[v.severity] || ''}`}>
                          {v.severity}
                        </span>
                      </td>
                      <td className="py-2 pr-4 align-top" colSpan={4}>
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
                        <div className="mt-2 flex items-center gap-2 flex-wrap">
                          <button
                            type="button"
                            onClick={() => setDrawerInvariant(v.invariant)}
                            className="px-2 py-1 text-xs rounded bg-white/10 hover:bg-white/20 text-white"
                          >View runbook</button>
                          <CopyCliButton template={cliTemplate} details={v.details} />
                          {actionCfg && (
                            <button
                              type="button"
                              data-action="run"
                              onClick={() => setModal({ cfg: actionCfg, details: v.details })}
                              className="px-2 py-1 text-xs rounded bg-emerald-600/80 hover:bg-emerald-500 text-white"
                            >Run action</button>
                          )}
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
                  );
                })}
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

      {updateHealth && (
        <GlassCard>
          <div className="px-5 py-4 border-b border-white/10">
            <h2 className="text-lg font-medium text-white">Fleet update health</h2>
            <p className="text-white/60 text-xs mt-1">
              NixOS-level (nixos_rebuild) + daemon-level (update_daemon) delivery paths.
              Paired with the <span className="font-mono">nixos_rebuild_success_drought</span> invariant.
            </p>
          </div>
          <div className="px-5 py-4 space-y-4 text-sm">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <div>
                <div className="text-white/60 text-xs">nixos_rebuild · 7d success</div>
                <div
                  className={`font-medium ${
                    updateHealth.nixos_rebuild.success_7d === 0 &&
                    (updateHealth.nixos_rebuild.failed_7d + updateHealth.nixos_rebuild.expired_7d) > 0
                      ? 'text-rose-300'
                      : 'text-emerald-300'
                  }`}
                >
                  {updateHealth.nixos_rebuild.success_7d} / {
                    updateHealth.nixos_rebuild.success_7d +
                    updateHealth.nixos_rebuild.failed_7d +
                    updateHealth.nixos_rebuild.expired_7d
                  }
                </div>
              </div>
              <div>
                <div className="text-white/60 text-xs">30d success</div>
                <div className="text-white font-medium">
                  {updateHealth.nixos_rebuild.success_30d} / {
                    updateHealth.nixos_rebuild.success_30d +
                    updateHealth.nixos_rebuild.failed_30d +
                    updateHealth.nixos_rebuild.expired_30d
                  }
                </div>
              </div>
              <div>
                <div className="text-white/60 text-xs">Days since last success</div>
                <div
                  className={`font-medium ${
                    (updateHealth.nixos_rebuild.days_since_last_success ?? 0) > 7
                      ? 'text-amber-300'
                      : 'text-emerald-300'
                  }`}
                >
                  {updateHealth.nixos_rebuild.days_since_last_success ?? '—'}
                </div>
              </div>
              <div>
                <div className="text-white/60 text-xs">Last failure</div>
                <div className="text-white/80 text-xs">
                  {updateHealth.nixos_rebuild.last_failure_at
                    ? relTime(updateHealth.nixos_rebuild.last_failure_at)
                    : 'never'}
                </div>
              </div>
            </div>

            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 pt-2 border-t border-white/5">
              <div>
                <div className="text-white/60 text-xs">update_daemon · 7d completed</div>
                <div className="text-emerald-300 font-medium">
                  {updateHealth.update_daemon.completed_7d}
                </div>
              </div>
              <div>
                <div className="text-white/60 text-xs">skipped (already at version)</div>
                <div className="text-white font-medium">
                  {updateHealth.update_daemon.skipped_7d}
                </div>
              </div>
              <div>
                <div className="text-white/60 text-xs">failed</div>
                <div
                  className={`font-medium ${
                    updateHealth.update_daemon.failed_7d > 0 ? 'text-rose-300' : 'text-emerald-300'
                  }`}
                >
                  {updateHealth.update_daemon.failed_7d}
                </div>
              </div>
              <div>
                <div className="text-white/60 text-xs">Last completion</div>
                <div className="text-white/80 text-xs">
                  {updateHealth.update_daemon.last_completion_at
                    ? relTime(updateHealth.update_daemon.last_completion_at)
                    : 'never'}
                </div>
              </div>
            </div>

            <div className="pt-2 border-t border-white/5">
              <div className="text-white/60 text-xs mb-2">
                Daemon version distribution (live appliances, last 7d)
              </div>
              {updateHealth.agent_versions.length === 0 ? (
                <div className="text-white/50 text-xs">No live appliances checked in</div>
              ) : (
                <div className="flex flex-wrap gap-2">
                  {updateHealth.agent_versions.map((v) => (
                    <span
                      key={v.version}
                      className="px-2 py-0.5 rounded text-xs font-mono bg-white/5 border border-white/10 text-white/80"
                    >
                      {v.version} · <span className="text-white/60">{v.count}</span>
                    </span>
                  ))}
                </div>
              )}
            </div>
          </div>
        </GlassCard>
      )}

      {drawerInvariant && (
        <RunbookDrawer
          invariant={drawerInvariant}
          onClose={() => setDrawerInvariant(null)}
        />
      )}

      {modal && (
        <ActionPreviewModal
          actionKey={modal.cfg.actionKey}
          requiredReasonChars={modal.cfg.requiredReasonChars}
          plan={modal.cfg.buildPlan(modal.details)}
          targetRef={modal.cfg.buildTargetRef(modal.details)}
          cliFallback={modal.cfg.cliFallback}
          onClose={() => setModal(null)}
          onDone={() => { setModal(null); setRefreshKey((k) => k + 1); }}
        />
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
