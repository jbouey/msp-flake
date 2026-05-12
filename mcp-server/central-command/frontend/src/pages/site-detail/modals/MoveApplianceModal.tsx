import React, { useState } from 'react';
import { GlassCard, Spinner } from '../../../components/shared';

export interface RelocateResult {
  status: 'pending' | 'needs_manual_push';
  fleet_order_id?: string;
  ssh_snippet?: string;
  next_step: string;
  agent_version: string | null;
  // Session 213 F1-followup signal: source-site appliance count after
  // the relocate. 0 = source is empty; UX may surface
  // canonical_alias_recommended as a hint to the operator.
  source_site_remaining_appliance_count?: number;
  canonical_alias_recommended?: string;
}

export interface MoveApplianceModalProps {
  applianceId: string;
  currentSiteId: string;
  onClose: () => void;
  /**
   * Relocate API call. Resolves with the response body so the modal
   * can render the appropriate post-action UX (fleet-order receipt
   * for v0.4.11+ or ssh_snippet for older daemons). Returns null if
   * the API call failed (toast surfaces the error).
   */
  onRelocate: (
    applianceId: string,
    targetSiteId: string,
    reason: string,
  ) => Promise<RelocateResult | null>;
}

/**
 * Relocate Appliance Modal — Round-table RT-8 (Session 210-B 2026-04-25).
 *
 * Replaces the legacy "Move Appliance" UX which called /api/sites/.../move
 * (a dumb DB site_id flip that left the daemon out of sync — same orphan
 * class we spent 2026-04-25 hunting). New UX calls /api/sites/.../relocate
 * which:
 *   1. Pre-creates target row + mints api_key
 *   2. Marks source as 'relocating' (deferred soft-delete)
 *   3. Issues reprovision fleet_order (daemon ≥ 0.4.11) OR returns
 *      ssh_snippet (legacy daemons)
 *   4. Audit-logs + writes evidence_chain bundle
 *
 * Required: target site + reason ≥ 20 chars (audit context).
 *
 * Post-action: shows fleet-order ID (auto-completion) OR a copy-paste
 * ssh_snippet box (manual completion).
 */
export const MoveApplianceModal: React.FC<MoveApplianceModalProps> = ({
  applianceId,
  currentSiteId,
  onClose,
  onRelocate,
}) => {
  const [targetSiteId, setTargetSiteId] = useState('');
  const [reason, setReason] = useState('');
  const [sites, setSites] = useState<Array<{ site_id: string; clinic_name: string }>>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<RelocateResult | null>(null);

  React.useEffect(() => {
    const fetchSites = async () => {
      try {
        const res = await fetch('/api/sites', {
          credentials: 'include',
        });
        if (res.ok) {
          const data = await res.json();
          setSites((data.sites || []).filter((s: { site_id: string }) => s.site_id !== currentSiteId));
        }
      } catch {
        // ignore
      } finally {
        setIsLoading(false);
      }
    };
    fetchSites();
  }, [currentSiteId]);

  const reasonValid = reason.trim().length >= 20;
  const canSubmit = !!targetSiteId && reasonValid && !submitting;

  const handleSubmit = async () => {
    if (!canSubmit) return;
    setSubmitting(true);
    const res = await onRelocate(applianceId, targetSiteId, reason.trim());
    setSubmitting(false);
    if (res) {
      setResult(res);
    }
  };

  // Post-relocate result UI: either a fleet-order receipt (auto path)
  // or a copy-paste ssh_snippet (legacy path). Stay in the modal so
  // the operator sees the next step BEFORE closing.
  if (result) {
    return (
      <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={onClose}>
        <div className="w-full max-w-2xl" onClick={e => e.stopPropagation()}>
          <GlassCard>
            <h2 className="text-xl font-semibold mb-2">Relocation Initiated</h2>
            <p className="text-sm text-label-secondary mb-4">
              Daemon agent_version: <span className="font-mono">{result.agent_version || 'unknown'}</span>
            </p>

            {result.status === 'pending' && result.fleet_order_id ? (
              <div className="space-y-3">
                <div className="rounded-ios bg-green-500/10 border border-green-500/30 p-3 text-sm">
                  <strong>Auto-completion:</strong> daemon will pick up the
                  reprovision order on its next checkin (≤60s) and self-relocate.
                </div>
                <div className="text-xs text-label-tertiary">
                  fleet_order_id: <span className="font-mono">{result.fleet_order_id}</span>
                </div>
                <p className="text-sm text-label-secondary">{result.next_step}</p>
              </div>
            ) : result.ssh_snippet ? (
              <div className="space-y-3">
                <div className="rounded-ios bg-amber-500/10 border border-amber-500/30 p-3 text-sm">
                  <strong>Legacy daemon ({result.agent_version}):</strong> manual SSH push required.
                  Copy the snippet below + run from any host on the appliance's LAN. Replace
                  <code className="px-1 mx-1 bg-fill-tertiary rounded">{'<APPLIANCE_LAN_IP>'}</code>
                  with the box's IP.
                </div>
                <textarea
                  readOnly
                  value={result.ssh_snippet}
                  className="w-full h-48 px-3 py-2 rounded-ios bg-fill-secondary border border-separator-light text-xs font-mono"
                  onFocus={e => e.target.select()}
                />
                <p className="text-sm text-label-secondary">{result.next_step}</p>
              </div>
            ) : null}

            <div className="flex justify-end pt-4">
              <button onClick={onClose}
                className="px-4 py-2 rounded-ios bg-accent-primary text-white hover:bg-accent-primary/90 transition-colors text-sm">
                Done
              </button>
            </div>
          </GlassCard>
        </div>
      </div>
    );
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={onClose}>
      <div className="w-full max-w-md" onClick={e => e.stopPropagation()}>
      <GlassCard>
        <h2 className="text-xl font-semibold mb-4">Relocate Appliance</h2>
        <p className="text-sm text-label-secondary mb-4">
          Move <span className="font-mono text-xs">{applianceId.slice(0, 30)}...</span> to a different site
          within the same organization. Cross-org moves require privileged-chain approval (not supported here).
        </p>
        {isLoading ? (
          <div className="flex justify-center py-8"><Spinner size="md" /></div>
        ) : sites.length === 0 ? (
          <p className="text-label-tertiary text-center py-8">No other sites available.</p>
        ) : (
          <div className="space-y-3">
            <label className="block">
              <div className="text-xs font-medium text-label-secondary mb-1">Target site</div>
              <select value={targetSiteId} onChange={e => setTargetSiteId(e.target.value)}
                className="w-full px-3 py-2 rounded-ios bg-fill-secondary border border-separator-light focus:border-accent-primary focus:outline-none text-sm">
                <option value="">Select target site...</option>
                {sites.map(s => (
                  <option key={s.site_id} value={s.site_id}>{s.clinic_name}</option>
                ))}
              </select>
            </label>

            <label className="block">
              <div className="flex items-center justify-between mb-1">
                <div className="text-xs font-medium text-label-secondary">Reason (audit context)</div>
                <div className={`text-xs ${reasonValid ? 'text-green-500' : 'text-label-tertiary'}`}>
                  {reason.trim().length}/20 min
                </div>
              </div>
              <textarea value={reason} onChange={e => setReason(e.target.value)}
                placeholder="Why is this appliance being moved? (≥20 chars, written to admin_audit_log)"
                rows={3}
                className="w-full px-3 py-2 rounded-ios bg-fill-secondary border border-separator-light focus:border-accent-primary focus:outline-none text-sm" />
            </label>

            <div className="flex gap-3 pt-2">
              <button onClick={onClose} disabled={submitting}
                className="flex-1 px-4 py-2 rounded-ios bg-fill-secondary text-label-primary hover:bg-fill-tertiary transition-colors text-sm disabled:opacity-50">
                Cancel
              </button>
              <button onClick={handleSubmit} disabled={!canSubmit}
                className="flex-1 px-4 py-2 rounded-ios bg-accent-primary text-white hover:bg-accent-primary/90 transition-colors disabled:opacity-50 text-sm">
                {submitting ? 'Relocating...' : 'Relocate Appliance'}
              </button>
            </div>
          </div>
        )}
      </GlassCard>
      </div>
    </div>
  );
};
