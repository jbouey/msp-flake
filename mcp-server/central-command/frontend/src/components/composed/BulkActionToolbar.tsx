import { useState } from 'react';

/**
 * BulkActionToolbar — generic multi-select bulk-action toolbar.
 *
 * #71 closure 2026-05-02 (extraction from Fleet.tsx). Generic over
 * action enum + entity-noun so any table page can use it. Renders:
 *   - sticky toolbar showing N selected + per-action buttons + Clear
 *   - confirmation modal (typed-action confirmation, destructive
 *     actions get extra warning copy via `confirmWarning` prop)
 *   - progress modal showing N done / N failed during fan-out
 *
 * Wiring contract: parent owns `selectedIds`, passes `onRunBulk`
 * callback that fans out N HTTP calls + reports progress through
 * the controlled `progress` prop. Sequential fan-out is responsibility
 * of the parent — toolbar is presentation-only.
 *
 * Original instance: components/composed/BulkActionToolbar.tsx
 * (this file). Wired from: pages/Fleet.tsx. Future wiring (Sites,
 * Workstations, Devices) follows the same pattern with different
 * action enums.
 */

export interface BulkAction<TActionId extends string = string> {
  id: TActionId;
  label: string;
  /** Tailwind color class for the button (e.g. 'bg-blue-600/80'). */
  buttonClass: string;
  /** Extra warning shown in confirmation modal for destructive actions. */
  confirmWarning?: string;
}

export interface BulkActionToolbarProps<TActionId extends string = string> {
  /** Selected entity IDs. */
  selectedIds: Set<string>;
  /** Singular noun for confirmation copy ("appliance", "site", etc). */
  entityNoun: string;
  /** Available actions. */
  actions: ReadonlyArray<BulkAction<TActionId>>;
  /** Called when operator confirms an action. Parent fans out HTTP. */
  onRunBulk: (action: TActionId) => void;
  /** Called when operator clears selection. */
  onClear: () => void;
  /** Called when operator dismisses the progress modal at completion. */
  onClose: () => void;
  /** True while bulk fan-out is running. Disables action buttons. */
  inProgress: boolean;
  /** Progress state during fan-out; null when idle. */
  progress: { total: number; done: number; failed: number } | null;
}

export function BulkActionToolbar<TActionId extends string>({
  selectedIds,
  entityNoun,
  actions,
  onRunBulk,
  onClear,
  onClose,
  inProgress,
  progress,
}: BulkActionToolbarProps<TActionId>) {
  const [pendingAction, setPendingAction] = useState<TActionId | null>(null);
  const pendingCfg = pendingAction
    ? actions.find((a) => a.id === pendingAction) ?? null
    : null;

  return (
    <>
      <div className="rounded-lg border border-blue-500/40 bg-blue-950/30 p-3 flex items-center gap-3 sticky top-0 z-10 backdrop-blur">
        <div className="flex-1 text-sm text-blue-100">
          <span className="font-semibold">{selectedIds.size}</span>{' '}
          {entityNoun}{selectedIds.size === 1 ? '' : 's'} selected
        </div>
        {actions.map((a) => (
          <button
            key={a.id}
            onClick={() => setPendingAction(a.id)}
            disabled={inProgress}
            className={`px-3 py-1.5 text-xs rounded text-white disabled:opacity-50 ${a.buttonClass}`}
          >
            {a.label}
          </button>
        ))}
        <button
          onClick={onClear}
          disabled={inProgress}
          className="px-3 py-1.5 text-xs rounded text-blue-200 hover:text-white disabled:opacity-50"
        >
          Clear
        </button>
      </div>

      {pendingCfg && !progress && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
          <div className="bg-slate-900 rounded-xl border border-white/10 p-6 max-w-md w-full">
            <h3 className="text-white font-semibold">Confirm bulk action</h3>
            <p className="text-white/70 text-sm mt-2">
              About to issue{' '}
              <span className="font-mono text-amber-300">{pendingCfg.id}</span>{' '}
              to <span className="font-bold">{selectedIds.size}</span>{' '}
              {entityNoun}{selectedIds.size === 1 ? '' : 's'}.
              {pendingCfg.confirmWarning && (
                <span className="block mt-2 text-rose-300">
                  ⚠ {pendingCfg.confirmWarning}
                </span>
              )}
            </p>
            <div className="flex gap-2 mt-4 justify-end">
              <button
                onClick={() => setPendingAction(null)}
                className="px-4 py-2 rounded text-white/70 hover:text-white text-sm"
              >
                Cancel
              </button>
              <button
                onClick={() => {
                  onRunBulk(pendingCfg.id);
                  setPendingAction(null);
                }}
                className="px-4 py-2 rounded bg-amber-600 hover:bg-amber-500 text-white text-sm font-medium"
              >
                Issue {selectedIds.size} order{selectedIds.size === 1 ? '' : 's'}
              </button>
            </div>
          </div>
        </div>
      )}

      {progress && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
          <div className="bg-slate-900 rounded-xl border border-white/10 p-6 max-w-md w-full">
            <h3 className="text-white font-semibold">Bulk progress</h3>
            <div className="mt-4 space-y-2">
              <div className="flex justify-between text-sm text-white/80">
                <span>Issued</span>
                <span className="font-mono">
                  {progress.done} / {progress.total}
                </span>
              </div>
              <div className="w-full bg-white/10 rounded-full h-2 overflow-hidden">
                <div
                  className="h-full bg-emerald-500 transition-all"
                  style={{ width: `${(progress.done / Math.max(progress.total, 1)) * 100}%` }}
                />
              </div>
              {progress.failed > 0 && (
                <div className="text-xs text-rose-300">{progress.failed} failed</div>
              )}
            </div>
            {progress.done + progress.failed === progress.total && (
              <button
                onClick={onClose}
                className="mt-4 w-full px-4 py-2 rounded bg-blue-600 hover:bg-blue-500 text-white text-sm"
              >
                Done
              </button>
            )}
          </div>
        </div>
      )}
    </>
  );
}

export default BulkActionToolbar;
