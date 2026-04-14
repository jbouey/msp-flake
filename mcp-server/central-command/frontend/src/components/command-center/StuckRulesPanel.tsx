/**
 * StuckRulesPanel — Session 206 round-table P2.
 *
 * Different surface from OperatorAckPanel: that one shows rules the
 * ORCHESTRATOR flagged as needing ack (auto_disabled etc). THIS panel
 * shows rules stuck in ANY state for longer than expected — a rule
 * stuck in `rolling_out` for 5 days is a different failure mode than
 * a rule that auto-disabled cleanly.
 *
 * Data comes from /api/dashboard/flywheel-spine which we already fetch
 * for the hero; no new query needed.
 */

import React from 'react';
import { GlassCard } from '../shared';
import { useFlywheelSpine } from '../../hooks/useFleet';

export const StuckRulesPanel: React.FC = () => {
  const { data } = useFlywheelSpine();
  const stuck = data?.stuck_rules ?? [];
  if (stuck.length === 0) return null;

  return (
    <GlassCard>
      <div className="flex items-start justify-between mb-3">
        <div>
          <h2 className="text-sm font-semibold text-label-primary">Stuck rules</h2>
          <p className="text-[11px] text-label-tertiary mt-0.5">
            Rules that have sat in the same state longer than expected. Drill in to see the event history.
          </p>
        </div>
        <span className="px-2 py-0.5 rounded-full bg-slate-500/20 text-slate-300 text-xs font-medium shrink-0">
          {stuck.length}
        </span>
      </div>
      <table className="w-full text-sm">
        <thead className="text-[11px] uppercase tracking-wide text-label-tertiary border-b border-glass-border">
          <tr>
            <th className="py-1.5 text-left font-medium">Rule</th>
            <th className="py-1.5 text-left font-medium">State</th>
            <th className="py-1.5 text-right font-medium">Stuck days</th>
            <th className="py-1.5 text-left font-medium">Site</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-glass-border/50">
          {stuck.map((r) => (
            <tr key={r.rule_id} className="text-xs">
              <td className="py-1.5 font-mono text-label-primary truncate max-w-[240px]" title={r.rule_id}>
                {r.rule_id}
              </td>
              <td className="py-1.5 text-label-secondary">{r.state}</td>
              <td className={`py-1.5 text-right tabular-nums ${
                r.stuck_days >= 14 ? 'text-rose-400' : r.stuck_days >= 7 ? 'text-amber-400' : 'text-label-secondary'
              }`}>
                {r.stuck_days}
              </td>
              <td className="py-1.5 font-mono text-label-tertiary truncate max-w-[160px]">
                {r.site_id || '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </GlassCard>
  );
};

export default StuckRulesPanel;
