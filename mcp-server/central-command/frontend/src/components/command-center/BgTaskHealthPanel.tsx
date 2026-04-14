/**
 * BgTaskHealthPanel — Session 206 round-table P1.
 *
 * Operator visibility into the ~30 background tasks the server runs.
 * Calls /api/admin/health/loops (already exists — Phase 15). Surfaces
 * STUCK loops (running but not heartbeat-progressing) distinct from
 * CRASHED tasks.
 *
 * Collapsed by default; click the summary to expand the full list.
 */

import React, { useEffect, useState } from 'react';
import { GlassCard } from '../shared';

interface LoopEntry {
  loop_name: string;
  task_state: string;
  expected_interval_s: number | null;
  instrumented: boolean;
  iterations?: number;
  errors?: number;
  age_s?: number;
  status?: 'fresh' | 'stale' | 'unknown';
  last_heartbeat?: string;
}

interface LoopsResponse {
  loops: LoopEntry[];
  summary?: { fresh?: number; stale?: number; unknown?: number; crashed?: number };
  generated_at?: string;
}

export const BgTaskHealthPanel: React.FC = () => {
  const [data, setData] = useState<LoopsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      fetch('/api/admin/health/loops', { credentials: 'include' })
        .then((r) => (r.ok ? r.json() : null))
        .then((d: LoopsResponse | null) => {
          if (!cancelled) { setData(d); setLoading(false); }
        })
        .catch(() => { if (!cancelled) setLoading(false); });
    };
    load();
    const int = setInterval(load, 60 * 1000);
    return () => { cancelled = true; clearInterval(int); };
  }, []);

  if (loading || !data) return null;
  const loops = data.loops || [];
  if (loops.length === 0) return null;

  const stale = loops.filter((l) => l.status === 'stale').length;
  const crashed = loops.filter((l) => l.task_state.startsWith('crashed')).length;
  const running = loops.filter((l) => l.task_state === 'running').length;
  const healthBadge =
    crashed > 0 ? { tone: 'bg-rose-500/20 text-rose-400', label: `${crashed} crashed` }
    : stale > 0 ? { tone: 'bg-amber-500/20 text-amber-400', label: `${stale} stuck` }
    : { tone: 'bg-emerald-500/20 text-emerald-400', label: 'all healthy' };

  return (
    <GlassCard>
      <button
        type="button"
        onClick={() => setExpanded((x) => !x)}
        className="w-full flex items-center justify-between text-left"
      >
        <div>
          <h2 className="text-sm font-semibold text-label-primary">Background tasks</h2>
          <p className="text-[11px] text-label-tertiary">
            {running} running · {loops.length} total · refreshes 60s
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${healthBadge.tone}`}>
            {healthBadge.label}
          </span>
          <span className="text-label-tertiary text-xs">{expanded ? '▴' : '▾'}</span>
        </div>
      </button>

      {expanded && (
        <div className="mt-3 pt-3 border-t border-glass-border max-h-64 overflow-y-auto">
          <table className="w-full text-xs">
            <thead className="text-[10px] uppercase tracking-wide text-label-tertiary">
              <tr>
                <th className="py-1 text-left font-medium">Loop</th>
                <th className="py-1 text-left font-medium">State</th>
                <th className="py-1 text-right font-medium">Iterations</th>
                <th className="py-1 text-right font-medium">Errors</th>
                <th className="py-1 text-right font-medium">Heartbeat age</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-glass-border/40">
              {loops
                .sort((a, b) => {
                  // crashed + stale sorted to top
                  const score = (l: LoopEntry) =>
                    l.task_state.startsWith('crashed') ? 0
                    : l.status === 'stale' ? 1
                    : 2;
                  return score(a) - score(b);
                })
                .map((l) => {
                  const crashed = l.task_state.startsWith('crashed');
                  const rowTone = crashed ? 'text-rose-400'
                    : l.status === 'stale' ? 'text-amber-400'
                    : 'text-label-secondary';
                  return (
                    <tr key={l.loop_name} className={`${rowTone}`}>
                      <td className="py-1 font-mono">{l.loop_name}</td>
                      <td className="py-1">{crashed ? l.task_state : (l.status ?? l.task_state)}</td>
                      <td className="py-1 text-right tabular-nums">{l.iterations ?? '—'}</td>
                      <td className={`py-1 text-right tabular-nums ${(l.errors ?? 0) > 0 ? 'text-rose-400' : ''}`}>
                        {l.errors ?? '—'}
                      </td>
                      <td className="py-1 text-right tabular-nums">
                        {l.age_s !== undefined && l.age_s !== null ? `${l.age_s}s` : '—'}
                      </td>
                    </tr>
                  );
                })}
            </tbody>
          </table>
        </div>
      )}
    </GlassCard>
  );
};

export default BgTaskHealthPanel;
