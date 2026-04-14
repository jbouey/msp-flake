/**
 * AdminConsentRollout — Migration 184 Phase 4 follow-up #6.
 *
 * Operator dashboard at /admin/consent-rollout. Read-only view of:
 *   * Which classes are currently enforced (from server env)
 *   * Per-class coverage percentage across the fleet
 *   * Last 30 consent ledger events (runbook.* only)
 *   * 7d totals for grants / revokes / executed-with-consent
 *   * Pending + expired token counts
 *
 * No mutation UI — toggling enforce is an env-var change, not a
 * button. Operator sees the state, infra changes the state.
 */

import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { GlassCard, Spinner } from '../components/shared';

interface ClassEntry {
  class_id: string;
  display_name: string;
  risk_level: 'low' | 'medium' | 'high';
  hipaa_controls: string[];
  enforced: boolean;
  active_consent_count: number;
  total_sites: number;
  coverage_pct: number;
}

interface LedgerEvent {
  rule_id: string;
  event_type: string;
  actor: string | null;
  stage: string | null;
  outcome: string | null;
  reason: string | null;
  created_at: string | null;
}

interface Payload {
  enforce_classes: string[];
  enforce_all: boolean;
  consent_copy_version: string;
  classes: ClassEntry[];
  recent_events: LedgerEvent[];
  totals_7d: {
    grants: number;
    revokes: number;
    executed_with_consent: number;
    amended: number;
  };
  tokens_pending: number;
  tokens_expired_7d: number;
  generated_at: string;
}

const RISK_TONE: Record<string, string> = {
  low: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30',
  medium: 'bg-amber-500/10 text-amber-300 border-amber-500/30',
  high: 'bg-rose-500/10 text-rose-300 border-rose-500/30',
};

const EVENT_TONE: Record<string, string> = {
  'runbook.consented': 'text-emerald-400',
  'runbook.revoked': 'text-rose-400',
  'runbook.amended': 'text-amber-400',
  'runbook.executed_with_consent': 'text-sky-400',
};

function relTime(iso: string | null): string {
  if (!iso) return '—';
  const diff = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

export const AdminConsentRollout: React.FC = () => {
  const [data, setData] = useState<Payload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      fetch('/api/dashboard/consent/rollout', { credentials: 'include' })
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
        .then((d: Payload) => { if (!cancelled) { setData(d); setLoading(false); } })
        .catch((e) => {
          if (!cancelled) {
            setError(e instanceof Error ? e.message : String(e));
            setLoading(false);
          }
        });
    };
    load();
    const int = setInterval(load, 60_000);
    return () => { cancelled = true; clearInterval(int); };
  }, []);

  if (loading && !data) {
    return <div className="p-6"><Spinner /></div>;
  }
  if (error && !data) {
    return <div className="p-6 text-rose-400 text-sm">Failed to load: {error}</div>;
  }
  if (!data) return null;

  const enforcedCount = data.enforce_all
    ? data.classes.length
    : data.classes.filter((c) => c.enforced).length;

  return (
    <div className="p-6 max-w-6xl mx-auto space-y-4">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-xl font-semibold text-label-primary">Consent rollout</h1>
          <p className="text-[11px] text-label-tertiary mt-1">
            Read-only view of Migration 184 class-level consent state. Refreshes every 60s.
          </p>
        </div>
        <Link to="/docs/phase-4-consent-ui-brief.md"
              className="text-xs text-blue-400 hover:underline">
          View Phase 4 brief →
        </Link>
      </div>

      {/* Top-line status */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
        <GlassCard>
          <div className="text-[11px] uppercase tracking-wide text-label-tertiary">Enforced classes</div>
          <div className="text-3xl font-bold tabular-nums text-label-primary mt-1">
            {enforcedCount}<span className="text-sm text-label-tertiary">/{data.classes.length}</span>
          </div>
          <div className="text-[11px] text-label-tertiary mt-1 truncate"
               title={data.enforce_classes.join(', ') || 'none'}>
            {data.enforce_all ? '* (all)' : (data.enforce_classes.join(', ') || 'shadow only')}
          </div>
        </GlassCard>
        <GlassCard>
          <div className="text-[11px] uppercase tracking-wide text-label-tertiary">Grants · 7d</div>
          <div className="text-3xl font-bold tabular-nums text-emerald-400 mt-1">
            {data.totals_7d.grants}
          </div>
          <div className="text-[11px] text-label-tertiary mt-1">
            {data.totals_7d.revokes} revokes · {data.totals_7d.amended} amended
          </div>
        </GlassCard>
        <GlassCard>
          <div className="text-[11px] uppercase tracking-wide text-label-tertiary">Executed · 7d</div>
          <div className="text-3xl font-bold tabular-nums text-sky-400 mt-1">
            {data.totals_7d.executed_with_consent}
          </div>
          <div className="text-[11px] text-label-tertiary mt-1">
            runbook.executed_with_consent
          </div>
        </GlassCard>
        <GlassCard>
          <div className="text-[11px] uppercase tracking-wide text-label-tertiary">Tokens</div>
          <div className="text-3xl font-bold tabular-nums text-label-primary mt-1">
            {data.tokens_pending}
          </div>
          <div className="text-[11px] text-label-tertiary mt-1">
            pending · {data.tokens_expired_7d} expired 7d
          </div>
        </GlassCard>
      </div>

      {/* Classes table */}
      <GlassCard>
        <h2 className="text-sm font-semibold text-label-primary mb-3">Per-class coverage</h2>
        <table className="w-full text-sm">
          <thead className="text-[11px] uppercase tracking-wide text-label-tertiary border-b border-glass-border">
            <tr>
              <th className="py-1.5 text-left font-medium">Class</th>
              <th className="py-1.5 text-left font-medium">Risk</th>
              <th className="py-1.5 text-center font-medium">Enforce</th>
              <th className="py-1.5 text-right font-medium">Active</th>
              <th className="py-1.5 text-right font-medium">Coverage</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-glass-border/40">
            {data.classes.map((c) => (
              <tr key={c.class_id}>
                <td className="py-1.5 text-label-primary">
                  <div className="font-medium">{c.display_name}</div>
                  <div className="text-[10px] font-mono text-label-tertiary">{c.class_id}</div>
                </td>
                <td className="py-1.5">
                  <span className={`px-2 py-0.5 text-[10px] rounded-full border uppercase ${RISK_TONE[c.risk_level]}`}>
                    {c.risk_level}
                  </span>
                </td>
                <td className="py-1.5 text-center">
                  {c.enforced ? (
                    <span className="px-2 py-0.5 text-[10px] rounded-full bg-rose-500/20 text-rose-300">
                      ENFORCE
                    </span>
                  ) : (
                    <span className="px-2 py-0.5 text-[10px] rounded-full bg-slate-500/20 text-slate-400">
                      shadow
                    </span>
                  )}
                </td>
                <td className="py-1.5 text-right tabular-nums text-label-secondary">
                  {c.active_consent_count}/{c.total_sites}
                </td>
                <td className={`py-1.5 text-right tabular-nums font-semibold ${
                  c.coverage_pct >= 75 ? 'text-emerald-400'
                  : c.coverage_pct >= 25 ? 'text-amber-400'
                  : 'text-label-tertiary'
                }`}>
                  {c.coverage_pct.toFixed(1)}%
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </GlassCard>

      {/* Recent events */}
      <GlassCard>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold text-label-primary">Recent consent events</h2>
          <span className="text-[11px] text-label-tertiary">last 30 · refreshes 60s</span>
        </div>
        <div className="divide-y divide-glass-border/40 max-h-96 overflow-y-auto">
          {data.recent_events.length === 0 && (
            <div className="py-4 text-xs text-label-tertiary italic">
              No consent events yet.
            </div>
          )}
          {data.recent_events.map((ev, i) => (
            <div key={`${ev.rule_id}-${i}`} className="py-2 text-xs">
              <div className="flex items-center justify-between gap-2">
                <span className={`font-medium ${EVENT_TONE[ev.event_type] ?? 'text-label-primary'}`}>
                  {ev.event_type}
                </span>
                <span className="text-label-tertiary shrink-0">{relTime(ev.created_at)}</span>
              </div>
              <div className="text-label-tertiary mt-0.5 font-mono truncate" title={ev.rule_id}>
                {ev.rule_id}
              </div>
              <div className="text-label-secondary mt-0.5">
                {ev.actor}
                {ev.outcome && <span className="text-label-tertiary"> · {ev.outcome}</span>}
              </div>
              {ev.reason && (
                <div className="text-[11px] text-label-tertiary italic mt-0.5 truncate" title={ev.reason}>
                  {ev.reason}
                </div>
              )}
            </div>
          ))}
        </div>
      </GlassCard>

      <div className="text-[11px] text-label-tertiary text-center pt-2">
        Consent copy version: <span className="font-mono">{data.consent_copy_version}</span>
        {' · '}Data computed {relTime(data.generated_at)}
      </div>
    </div>
  );
};

export default AdminConsentRollout;
