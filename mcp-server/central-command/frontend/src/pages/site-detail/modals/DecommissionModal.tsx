import React, { useState } from 'react';
import { GlassCard, Spinner } from '../../../components/shared';
import type { SiteDetail as SiteDetailType } from '../../../utils/api';
import { decommissionApi } from '../../../utils/api';

export interface DecommissionModalProps {
  site: SiteDetailType;
  onClose: () => void;
  onDecommissioned: () => void;
  showToast: (msg: string, type: 'success' | 'error') => void;
}

/**
 * Decommission Site Modal
 */
export const DecommissionModal: React.FC<DecommissionModalProps> = ({ site, onClose, onDecommissioned, showToast }) => {
  const [isExporting, setIsExporting] = useState(false);
  const [isDecommissioning, setIsDecommissioning] = useState(false);
  const [exportDone, setExportDone] = useState(false);
  const [confirmText, setConfirmText] = useState('');
  // Double-confirm: user must explicitly acknowledge the irreversible nature
  // of this action. Without this checkbox, even a correctly typed confirmation
  // leaves the button disabled.
  const [ackIrreversible, setAckIrreversible] = useState(false);
  // Second-stage "arming" — once the user passes all guards and clicks the
  // final button, we arm it for 5s and require a second click before the
  // API call actually goes through. The timer cancels the arm state.
  const [armed, setArmed] = useState(false);

  React.useEffect(() => {
    if (!armed) return;
    const t = setTimeout(() => setArmed(false), 5_000);
    return () => clearTimeout(t);
  }, [armed]);

  const handleExport = async () => {
    setIsExporting(true);
    try {
      const data = await decommissionApi.exportSiteData(site.site_id);
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `site-export-${site.site_id}-${new Date().toISOString().slice(0, 10)}.json`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      setExportDone(true);
      showToast('Site data exported successfully', 'success');
    } catch (err) {
      showToast(`Export failed: ${err instanceof Error ? err.message : String(err)}`, 'error');
    } finally {
      setIsExporting(false);
    }
  };

  const handleDecommission = async () => {
    // First click arms, second click executes — this is the double-confirm.
    if (!armed) {
      setArmed(true);
      return;
    }
    setIsDecommissioning(true);
    try {
      const result = await decommissionApi.decommissionSite(site.site_id);
      showToast(`Site decommissioned: ${result.actions.join(', ')}`, 'success');
      onDecommissioned();
    } catch (err) {
      showToast(`Decommission failed: ${err instanceof Error ? err.message : String(err)}`, 'error');
    } finally {
      setIsDecommissioning(false);
    }
  };

  // Accept either the stable slug (site_id) or the human-readable clinic_name.
  // This matches the user's expectation of "typing the site name" while still
  // working for admins who reference the slug out of habit.
  const confirmTarget = confirmText.trim();
  const typedCorrectly =
    confirmTarget.length > 0 &&
    (confirmTarget === site.site_id || confirmTarget.toLowerCase() === (site.clinic_name || '').toLowerCase());
  // All three guards must pass before the primary button will fire:
  //   1) Correct name typed
  //   2) Irreversible checkbox ticked
  //   3) Data has been exported (offline archive exists)
  const canDecommission = typedCorrectly && ackIrreversible && exportDone;

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={onClose}>
      <div className="w-full max-w-lg" onClick={e => e.stopPropagation()}>
        <GlassCard>
          <div className="flex items-center gap-3 mb-4">
            <div className="w-10 h-10 rounded-full bg-health-critical/10 flex items-center justify-center flex-shrink-0">
              <svg className="w-5 h-5 text-health-critical" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z" />
              </svg>
            </div>
            <div>
              <h2 className="text-xl font-semibold text-label-primary">Decommission Site</h2>
              <p className="text-sm text-label-tertiary">This action cannot be undone</p>
            </div>
          </div>

          {/* Site summary */}
          <div className="bg-fill-secondary rounded-ios p-4 mb-4 space-y-2">
            <div className="flex justify-between text-sm">
              <span className="text-label-tertiary">Site</span>
              <span className="text-label-primary font-medium">{site.clinic_name}</span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-label-tertiary">Site ID</span>
              <span className="text-label-primary font-mono text-xs">{site.site_id}</span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-label-tertiary">Appliances</span>
              <span className="text-label-primary">{site.appliances.length}</span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-label-tertiary">Credentials</span>
              <span className="text-label-primary">{site.credentials.length}</span>
            </div>
          </div>

          {/* Warning */}
          <div className="bg-health-warning/10 border border-health-warning/20 rounded-ios p-3 mb-4">
            <p className="text-sm text-label-primary">
              <strong>What happens:</strong>
            </p>
            <ul className="text-sm text-label-secondary mt-1 space-y-1 list-disc list-inside">
              <li>All API keys for this site will be revoked</li>
              <li>Portal access tokens will be invalidated</li>
              <li>Appliances will receive a stop order</li>
              <li>Site status will be set to inactive</li>
            </ul>
            <p className="text-sm text-label-tertiary mt-2">
              Data is retained for HIPAA compliance (6-year requirement). Export first to create an offline archive.
            </p>
          </div>

          {/* Export button */}
          <div className="mb-4">
            <button
              onClick={handleExport}
              disabled={isExporting}
              className={`w-full px-4 py-2.5 rounded-ios text-sm font-medium transition-all flex items-center justify-center gap-2 ${
                exportDone
                  ? 'bg-health-healthy/10 text-health-healthy border border-health-healthy/20'
                  : 'bg-accent-primary/10 text-accent-primary hover:bg-accent-primary/20 border border-accent-primary/20'
              } disabled:opacity-50`}
            >
              {isExporting ? (
                <>
                  <Spinner size="sm" />
                  Exporting...
                </>
              ) : exportDone ? (
                <>
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                  </svg>
                  Export Downloaded
                </>
              ) : (
                <>
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                  </svg>
                  Export Site Data (JSON)
                </>
              )}
            </button>
          </div>

          {/* Confirmation input */}
          <div className="mb-3">
            <label className="block text-sm text-label-secondary mb-1.5">
              Type the site name <code className="bg-fill-secondary px-1.5 py-0.5 rounded text-xs font-mono">{site.clinic_name}</code>
              {' '}or slug <code className="bg-fill-secondary px-1.5 py-0.5 rounded text-xs font-mono">{site.site_id}</code> to confirm:
            </label>
            <input
              type="text"
              value={confirmText}
              onChange={e => setConfirmText(e.target.value)}
              placeholder={site.clinic_name}
              className="w-full px-3 py-2 rounded-ios bg-fill-secondary text-label-primary border border-separator-light focus:border-health-critical focus:outline-none text-sm"
              autoFocus
            />
          </div>

          {/* Irreversibility acknowledgment — second independent guard */}
          <label className="flex items-start gap-2 mb-4 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={ackIrreversible}
              onChange={e => setAckIrreversible(e.target.checked)}
              className="mt-0.5 w-4 h-4 rounded text-health-critical focus:ring-health-critical"
            />
            <span className="text-sm text-label-secondary">
              I understand this is irreversible and that the site data will be retained
              for HIPAA-required <strong>6 years</strong> but no new data can be collected.
            </span>
          </label>

          {/* Preflight status indicators */}
          <div className="mb-3 flex flex-col gap-1 text-xs">
            <div className={`flex items-center gap-1.5 ${exportDone ? 'text-health-healthy' : 'text-label-tertiary'}`}>
              <span>{exportDone ? '✓' : '○'}</span>
              <span>Export downloaded {exportDone ? '' : '(required)'}</span>
            </div>
            <div className={`flex items-center gap-1.5 ${typedCorrectly ? 'text-health-healthy' : 'text-label-tertiary'}`}>
              <span>{typedCorrectly ? '✓' : '○'}</span>
              <span>Site name confirmed</span>
            </div>
            <div className={`flex items-center gap-1.5 ${ackIrreversible ? 'text-health-healthy' : 'text-label-tertiary'}`}>
              <span>{ackIrreversible ? '✓' : '○'}</span>
              <span>Irreversibility acknowledged</span>
            </div>
          </div>

          {/* Actions */}
          <div className="flex gap-3">
            <button
              onClick={onClose}
              disabled={isDecommissioning}
              className="flex-1 px-4 py-2.5 rounded-ios bg-fill-secondary text-label-primary hover:bg-fill-tertiary transition-colors text-sm disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              onClick={handleDecommission}
              disabled={!canDecommission || isDecommissioning}
              className={`flex-1 px-4 py-2.5 rounded-ios font-semibold shadow-md transition-all disabled:opacity-50 disabled:cursor-not-allowed text-sm ${
                armed
                  ? 'bg-red-700 hover:bg-red-800 text-white ring-2 ring-red-400 animate-pulse'
                  : 'bg-gradient-to-r from-red-600 to-red-500 hover:from-red-700 hover:to-red-600 text-white'
              }`}
            >
              {isDecommissioning ? (
                <span className="flex items-center justify-center gap-2">
                  <Spinner size="sm" />
                  Decommissioning...
                </span>
              ) : armed ? (
                'Click again to confirm (5s)'
              ) : (
                'Confirm Decommission'
              )}
            </button>
          </div>
        </GlassCard>
      </div>
    </div>
  );
};
