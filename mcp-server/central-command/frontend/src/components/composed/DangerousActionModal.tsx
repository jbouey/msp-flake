import React, {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
} from 'react';

/**
 * DangerousActionModal — Sprint-N+1 Decision 2 (2026-05-08).
 *
 * One confirmation primitive for every destructive partner-portal action.
 * Replaces the four coexisting shapes that grew organically pre-sprint:
 *   - window.prompt for reason/phrase capture (PartnerUsersScreen deactivate)
 *   - window.confirm for soft confirms (PartnerBilling cancel)
 *   - inline-typed-confirm modal (PartnerAttestations roster Revoke)
 *   - free-form modal forms with no typed gate (PartnerUsersScreen invite)
 *
 * Two tiers, picked by the caller:
 *
 *   tier="irreversible" — type-to-confirm + match indicator + ENTER-gated
 *     submit. For delete/revoke/cancel — anything that costs more than a few
 *     minutes to undo (or cannot be undone at all).
 *
 *   tier="reversible"   — simple "Are you sure?" with a Confirm button. For
 *     invite-style actions where a misclick is recoverable.
 *
 * The modal is presentational only — it does NOT perform the mutation.
 * Callers wire `onConfirm` to their existing `postJson`/`patchJson`/
 * `deleteJson` calls so the CSRF posture (baseline 0) is preserved.
 *
 * Accessibility:
 *   - role="dialog", aria-modal="true", aria-labelledby + aria-describedby
 *   - Focus trap: focus moves to the first focusable element on open;
 *     Tab cycles within the modal; Shift+Tab cycles backward.
 *   - ESC always closes (calls onCancel) unless busy.
 *   - ENTER submits when the confirm gate passes.
 *   - aria-live polite region for typed-confirm match status.
 */

export type DangerousActionTier = 'irreversible' | 'reversible';

interface DangerousActionModalCommonProps {
  open: boolean;
  title: string;
  /** The action verb shown on the primary button (e.g. "Delete", "Revoke"). */
  verb: string;
  /** Object of the action (e.g. user email, plan name, counterparty label). */
  target: string;
  /** Optional descriptive prose rendered above the typed-confirm input. */
  description?: React.ReactNode;
  onConfirm: () => Promise<void> | void;
  onCancel: () => void;
  /** Disables submit + shows spinner; ESC + cancel still close the modal. */
  busy?: boolean;
  /** Surfaced from a rejected onConfirm; rendered below buttons. */
  errorMessage?: string;
}

interface DangerousActionModalIrreversibleProps
  extends DangerousActionModalCommonProps {
  tier: 'irreversible';
  /**
   * What the user must type to enable the submit button.
   *   - 'target' (default) — user must type the literal `target` string.
   *   - any other string  — user must type that exact literal (e.g. "CANCEL").
   */
  confirmInput?: 'target' | string;
}

interface DangerousActionModalReversibleProps
  extends DangerousActionModalCommonProps {
  tier: 'reversible';
}

export type DangerousActionModalProps =
  | DangerousActionModalIrreversibleProps
  | DangerousActionModalReversibleProps;

const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'textarea:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

export const DangerousActionModal: React.FC<DangerousActionModalProps> = (
  props,
) => {
  const {
    open,
    title,
    verb,
    target,
    description,
    onConfirm,
    onCancel,
    busy = false,
    errorMessage,
    tier,
  } = props;

  const dialogRef = useRef<HTMLDivElement | null>(null);
  const previouslyFocused = useRef<HTMLElement | null>(null);
  const [typed, setTyped] = React.useState('');

  const titleId = useId();
  const descId = useId();
  const liveId = useId();
  const inputId = useId();

  const expectedLiteral = useMemo(() => {
    if (tier !== 'irreversible') return '';
    const ci = (props as DangerousActionModalIrreversibleProps).confirmInput;
    if (!ci || ci === 'target') return target;
    return ci;
  }, [tier, target, props]);

  const matched =
    tier === 'reversible' ? true : typed === expectedLiteral && typed.length > 0;

  // Reset typed value whenever the modal closes/opens or the expected
  // literal changes (e.g. caller reuses the same modal for a different row).
  useEffect(() => {
    if (!open) setTyped('');
  }, [open, expectedLiteral]);

  // Focus management: on open, snapshot previous focus, move into dialog.
  // On close, restore previous focus.
  useEffect(() => {
    if (!open) return;
    previouslyFocused.current = document.activeElement as HTMLElement | null;

    // Defer one frame so the dialog is mounted before focus moves.
    const id = window.requestAnimationFrame(() => {
      const dlg = dialogRef.current;
      if (!dlg) return;
      const focusables = dlg.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR);
      const first = focusables[0];
      if (first) first.focus();
    });

    return () => {
      window.cancelAnimationFrame(id);
      const prev = previouslyFocused.current;
      if (prev && typeof prev.focus === 'function') {
        prev.focus();
      }
    };
  }, [open]);

  const handleConfirm = useCallback(async () => {
    if (busy) return;
    if (tier === 'irreversible' && !matched) return;
    await onConfirm();
  }, [busy, tier, matched, onConfirm]);

  // Key handler: ESC closes; ENTER submits (when matched / Tier-2);
  // Tab cycles within the dialog (focus trap).
  const handleKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Escape') {
      if (!busy) {
        e.stopPropagation();
        onCancel();
      }
      return;
    }
    if (e.key === 'Enter') {
      // Only intercept ENTER when the confirm gate is open. Allow normal
      // ENTER behavior (e.g. textarea newline) when the gate is closed.
      if (matched && !busy) {
        // If focus is on the cancel button, do NOT submit — let the
        // browser's native button activation handle it instead.
        const active = document.activeElement as HTMLElement | null;
        if (active && active.dataset.dangerousModalRole === 'cancel') return;
        e.preventDefault();
        void handleConfirm();
      }
      return;
    }
    if (e.key === 'Tab') {
      const dlg = dialogRef.current;
      if (!dlg) return;
      const focusables = Array.from(
        dlg.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR),
      ).filter((el) => !el.hasAttribute('disabled'));
      if (focusables.length === 0) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      const active = document.activeElement as HTMLElement | null;
      if (e.shiftKey) {
        if (active === first || !dlg.contains(active)) {
          e.preventDefault();
          last.focus();
        }
      } else {
        if (active === last) {
          e.preventDefault();
          first.focus();
        }
      }
    }
  };

  if (!open) return null;

  const liveMessage = (() => {
    if (tier !== 'irreversible') return '';
    if (typed.length === 0) return '';
    return matched
      ? 'Typed value matches; submit available.'
      : 'Typed value does not yet match.';
  })();

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onClick={(e) => {
        if (e.target === e.currentTarget && !busy) onCancel();
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descId : undefined}
        className="bg-white rounded-2xl shadow-xl w-full max-w-md overflow-hidden"
        onKeyDown={handleKeyDown}
      >
        {/* Danger header band */}
        <div className="px-6 py-4 bg-red-600 text-white">
          <h3 id={titleId} className="text-lg font-semibold tracking-tight">
            {title}
          </h3>
          <p className="mt-0.5 text-sm text-red-50/90">
            {verb} <span className="font-mono break-all">{target}</span>
          </p>
        </div>

        {/* Body */}
        <div className="px-6 py-5 space-y-4">
          {description && (
            <div id={descId} className="text-sm text-slate-700 leading-relaxed">
              {description}
            </div>
          )}

          {tier === 'irreversible' && (
            <div className="space-y-2">
              <label
                htmlFor={inputId}
                className="block text-sm font-medium text-slate-700"
              >
                To confirm, type{' '}
                <code className="bg-slate-100 px-1.5 py-0.5 rounded text-xs text-slate-900 font-mono break-all">
                  {expectedLiteral}
                </code>
              </label>
              <input
                id={inputId}
                type="text"
                value={typed}
                onChange={(e) => setTyped(e.target.value)}
                disabled={busy}
                autoComplete="off"
                spellCheck={false}
                className="w-full px-3 py-2 bg-slate-50 border border-slate-300 rounded-lg outline-none focus:ring-2 focus:ring-red-500/40 disabled:opacity-50 font-mono text-sm"
                aria-describedby={liveId}
                data-testid="dangerous-action-confirm-input"
              />
              <p
                id={liveId}
                aria-live="polite"
                className={`text-xs ${
                  matched ? 'text-emerald-700' : 'text-slate-500'
                }`}
              >
                {liveMessage || ' '}
              </p>
            </div>
          )}

          {errorMessage && (
            <div
              role="alert"
              className="px-3 py-2 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700"
            >
              {errorMessage}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-6 py-4 bg-slate-50 border-t border-slate-200 flex justify-end gap-2">
          <button
            type="button"
            data-dangerous-modal-role="cancel"
            onClick={() => {
              if (!busy) onCancel();
            }}
            disabled={busy}
            className="px-4 py-2 text-sm rounded-lg border border-slate-300 text-slate-700 hover:bg-slate-100 disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            data-dangerous-modal-role="confirm"
            onClick={() => void handleConfirm()}
            disabled={busy || (tier === 'irreversible' && !matched)}
            className="inline-flex items-center gap-2 px-4 py-2 text-sm rounded-lg bg-red-600 text-white font-medium hover:bg-red-700 disabled:opacity-50 disabled:hover:bg-red-600"
          >
            {busy && (
              <span
                aria-hidden="true"
                className="w-3.5 h-3.5 border-2 border-white border-t-transparent rounded-full animate-spin"
              />
            )}
            {busy ? `${verb}…` : verb}
          </button>
        </div>
      </div>
    </div>
  );
};

export default DangerousActionModal;
