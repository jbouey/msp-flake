/**
 * FlywheelEventStream — Session 206 round-table P2.
 *
 * Live feed of the flywheel spine's append-only event ledger. Filters
 * unnecessary noise by default (shows top 30 events newest-first).
 * Each event is the authoritative record of a lifecycle transition;
 * seeing them scroll past live is how operators confirm the
 * orchestrator is actually doing work.
 */

import React, { useEffect, useState } from 'react';
import { GlassCard } from '../shared';

interface Event {
  event_id: string;
  rule_id: string;
  event_type: string;
  from_state: string | null;
  to_state: string | null;
  actor: string | null;
  stage: string | null;
  outcome: string | null;
  reason: string | null;
  created_at: string | null;
}

interface Response {
  events: Event[];
  count: number;
  generated_at: string;
}

const OUTCOME_COLOR: Record<string, string> = {
  success: 'text-emerald-400',
  failure: 'text-rose-400',
  noop: 'text-slate-400',
  pending: 'text-amber-400',
};

function relTime(iso: string | null): string {
  if (!iso) return '—';
  const diff = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

export const FlywheelEventStream: React.FC = () => {
  const [data, setData] = useState<Response | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      fetch('/api/dashboard/flywheel-events?limit=30', { credentials: 'include' })
        .then((r) => (r.ok ? r.json() : null))
        .then((d: Response | null) => {
          if (!cancelled) { setData(d); setLoading(false); }
        })
        .catch(() => { if (!cancelled) setLoading(false); });
    };
    load();
    const int = setInterval(load, 60 * 1000);
    return () => { cancelled = true; clearInterval(int); };
  }, []);

  if (loading) return null;
  if (!data || data.events.length === 0) {
    return (
      <GlassCard>
        <h2 className="text-sm font-semibold text-label-primary mb-2">Flywheel event stream</h2>
        <div className="text-xs text-label-tertiary py-3">
          No events yet. The orchestrator writes here on every lifecycle transition.
        </div>
      </GlassCard>
    );
  }

  return (
    <GlassCard>
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold text-label-primary">Flywheel event stream</h2>
        <span className="text-[11px] text-label-tertiary">
          {data.count} event{data.count === 1 ? '' : 's'} · refreshes 60s
        </span>
      </div>
      <div className="max-h-96 overflow-y-auto divide-y divide-glass-border">
        {data.events.map((ev) => {
          const tone = OUTCOME_COLOR[ev.outcome ?? ''] ?? 'text-slate-300';
          return (
            <div key={ev.event_id} className="py-2 text-xs">
              <div className="flex items-center justify-between gap-2">
                <span className="font-mono text-label-primary truncate" title={ev.rule_id}>
                  {ev.rule_id}
                </span>
                <span className="text-label-tertiary shrink-0">{relTime(ev.created_at)}</span>
              </div>
              <div className="mt-0.5 text-label-secondary flex items-center gap-2 flex-wrap">
                <span className="font-medium text-label-primary">{ev.event_type}</span>
                {ev.from_state && ev.to_state && (
                  <span className="text-label-tertiary">
                    {ev.from_state} → {ev.to_state}
                  </span>
                )}
                {ev.outcome && <span className={tone}>· {ev.outcome}</span>}
                {ev.actor && <span className="text-label-tertiary italic">· {ev.actor}</span>}
              </div>
              {ev.reason && (
                <div className="text-[11px] text-label-tertiary italic mt-0.5 truncate" title={ev.reason}>
                  {ev.reason}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </GlassCard>
  );
};

export default FlywheelEventStream;
