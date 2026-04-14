/**
 * OperatorAckPanel — Session 206 round-table R5.
 *
 * Backend endpoint (POST /api/dashboard/flywheel-spine/acknowledge) has
 * existed since the spine landed; this is the frontend consumer.
 *
 * Shows any `promoted_rules` with `operator_ack_required=true` (generally
 * auto_disabled after a CanaryFailure or RegimeAbsoluteLow transition)
 * and lets a named operator either re-enable the rule or confirm the
 * disable — both paths write to the flywheel event ledger.
 *
 * The endpoint requires `acknowledged_by` to be a valid email and
 * `reason` to be ≥10 chars. We enforce both client-side so the button
 * disables until the form is well-formed.
 */

import React, { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { GlassCard } from '../shared';
import { useFlywheelSpine } from '../../hooks/useFleet';

type Decision = 're_enable' | 'confirm_disable';

interface ModalState {
  ruleId: string;
  decision: Decision;
}

export const OperatorAckPanel: React.FC = () => {
  const { data } = useFlywheelSpine();
  const qc = useQueryClient();
  const [open, setOpen] = useState<ModalState | null>(null);
  const [reason, setReason] = useState('');
  const [email, setEmail] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const ackList = data?.operator_ack_required ?? [];
  if (ackList.length === 0) return null;

  const reset = () => {
    setOpen(null);
    setReason('');
    setError(null);
  };

  const submit = async () => {
    if (!open) return;
    setSubmitting(true);
    setError(null);
    try {
      const res = await fetch('/api/dashboard/flywheel-spine/acknowledge', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          rule_id: open.ruleId,
          decision: open.decision,
          reason,
          acknowledged_by: email,
        }),
      });
      if (!res.ok) {
        let msg = `HTTP ${res.status}`;
        try {
          const body = await res.json();
          if (body?.detail) msg = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
        } catch { /* ignore */ }
        throw new Error(msg);
      }
      reset();
      qc.invalidateQueries({ queryKey: ['flywheel', 'spine'] });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  };

  const valid = reason.length >= 10 && email.includes('@');

  return (
    <>
      <GlassCard>
        <div className="flex items-start justify-between mb-3">
          <div>
            <h2 className="text-sm font-semibold text-label-primary">
              Rules awaiting operator acknowledgment
            </h2>
            <p className="text-[11px] text-label-tertiary mt-0.5">
              Auto-disabled by the spine — review and either re-enable or confirm the disable. Every decision is logged to the flywheel event ledger.
            </p>
          </div>
          <span className="px-2 py-0.5 rounded-full bg-amber-500/20 text-amber-400 text-xs font-medium shrink-0">
            {ackList.length} pending
          </span>
        </div>
        <div className="divide-y divide-glass-border">
          {ackList.map((r) => (
            <div key={r.rule_id} className="py-2 flex items-center justify-between gap-3 text-sm">
              <div className="flex-1 min-w-0">
                <div className="font-mono text-xs text-label-primary truncate" title={r.rule_id}>
                  {r.rule_id}
                </div>
                <div className="text-[11px] text-label-tertiary">
                  state: <span className="font-medium">{r.state}</span>
                  {r.site_id ? <> · site: <span className="font-mono">{r.site_id}</span></> : null}
                  {r.since ? <> · since: {new Date(r.since).toLocaleString()}</> : null}
                </div>
                {r.reason ? (
                  <div className="text-[11px] text-label-secondary italic truncate" title={r.reason}>
                    {r.reason}
                  </div>
                ) : null}
              </div>
              <div className="flex gap-2 shrink-0">
                <button
                  type="button"
                  onClick={() => setOpen({ ruleId: r.rule_id, decision: 're_enable' })}
                  className="px-2.5 py-1 text-xs rounded border border-emerald-500/40 text-emerald-400 hover:bg-emerald-500/10 transition-colors"
                >
                  Re-enable
                </button>
                <button
                  type="button"
                  onClick={() => setOpen({ ruleId: r.rule_id, decision: 'confirm_disable' })}
                  className="px-2.5 py-1 text-xs rounded border border-slate-500/40 text-slate-300 hover:bg-slate-500/10 transition-colors"
                >
                  Confirm disable
                </button>
              </div>
            </div>
          ))}
        </div>
      </GlassCard>

      {open ? (
        <div
          className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4"
          onClick={(e) => { if (e.target === e.currentTarget && !submitting) reset(); }}
        >
          <div className="bg-slate-900 border border-glass-border rounded-xl p-5 w-full max-w-lg shadow-xl">
            <h3 className="text-base font-semibold text-label-primary mb-1">
              {open.decision === 're_enable' ? 'Re-enable rule' : 'Confirm disable'}
            </h3>
            <div className="font-mono text-xs text-label-tertiary mb-4 break-all">
              {open.ruleId}
            </div>

            <label className="block mb-3">
              <span className="text-xs text-label-secondary">
                Your email (named operator) <span className="text-rose-400">*</span>
              </span>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="w-full mt-1 px-2 py-1.5 bg-slate-800 border border-glass-border rounded text-sm text-label-primary focus:outline-none focus:border-blue-500"
                placeholder="you@company.com"
                disabled={submitting}
              />
            </label>

            <label className="block mb-4">
              <span className="text-xs text-label-secondary">
                Reason <span className="text-rose-400">*</span>
                <span className={`ml-2 ${reason.length >= 10 ? 'text-emerald-400' : 'text-label-tertiary'}`}>
                  {reason.length}/10
                </span>
              </span>
              <textarea
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                rows={3}
                className="w-full mt-1 px-2 py-1.5 bg-slate-800 border border-glass-border rounded text-sm text-label-primary focus:outline-none focus:border-blue-500"
                placeholder={open.decision === 're_enable'
                  ? 'Why is this rule safe to re-enable? What changed?'
                  : 'Why are we keeping this disabled? What follow-up is tracked?'}
                disabled={submitting}
              />
            </label>

            {error ? (
              <div className="text-xs text-rose-400 mb-3 p-2 bg-rose-500/10 border border-rose-500/30 rounded">
                {error}
              </div>
            ) : null}

            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={reset}
                disabled={submitting}
                className="px-3 py-1.5 text-sm rounded border border-glass-border text-label-secondary hover:bg-slate-800 disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={submit}
                disabled={submitting || !valid}
                className="px-3 py-1.5 text-sm rounded bg-blue-600 text-white hover:bg-blue-500 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {submitting
                  ? 'Submitting…'
                  : open.decision === 're_enable'
                    ? 'Re-enable'
                    : 'Confirm disable'}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
};

export default OperatorAckPanel;
