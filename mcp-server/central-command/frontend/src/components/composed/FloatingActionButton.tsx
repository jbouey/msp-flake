import React, { useState, useEffect, useRef } from 'react';

export interface FloatingAction {
  /** Visible label shown to the left of the action icon */
  label: string;
  /** Inline SVG (or any ReactNode) for the button */
  icon: React.ReactNode;
  /** Click handler — fires when the user selects this action */
  onClick: () => void;
  /** Optional tint: used for destructive/primary actions */
  tone?: 'default' | 'primary' | 'danger';
  /** Whether this action is disabled */
  disabled?: boolean;
}

interface Props {
  /** Actions to expose when the FAB is expanded */
  actions: FloatingAction[];
  /** Position on the viewport. Default: bottom-right */
  position?: 'bottom-right' | 'bottom-left';
  /** Optional accessibility label for the main trigger button */
  ariaLabel?: string;
}

/**
 * FloatingActionButton — a viewport-anchored quick-action launcher.
 * Collapsed, it shows a single circular button; expanded, it fans
 * out a vertical stack of labelled actions (label on the left, icon
 * on the right, matching iOS/macOS inspector conventions).
 *
 * Closes on Escape, click-outside, and after any action fires. No
 * external dependencies — all state is local.
 *
 * Usage:
 *   <FloatingActionButton
 *     actions={[
 *       { label: 'Force scan', icon: <RefreshIcon />, onClick: handleScan },
 *       { label: 'Add device', icon: <PlusIcon />, onClick: openAddDevice },
 *       { label: 'Run runbook', icon: <BoltIcon />, onClick: openRunbook },
 *     ]}
 *   />
 */
export const FloatingActionButton: React.FC<Props> = ({
  actions,
  position = 'bottom-right',
  ariaLabel = 'Quick actions',
}) => {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  // Dismiss on click outside
  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
  }, [open]);

  // Dismiss on Escape
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open]);

  const posClasses =
    position === 'bottom-left' ? 'bottom-6 left-6' : 'bottom-6 right-6';

  return (
    <div
      ref={containerRef}
      className={`fixed ${posClasses} z-40 flex flex-col items-end gap-2`}
    >
      {/* Expanded action list */}
      {open && (
        <div className="flex flex-col items-end gap-2 animate-in fade-in slide-in-from-bottom-2 duration-150">
          {actions.map((action, i) => {
            const toneClasses =
              action.tone === 'danger'
                ? 'bg-health-critical text-white hover:bg-health-critical/90'
                : action.tone === 'primary'
                  ? 'bg-accent-primary text-white hover:bg-accent-primary/90'
                  : 'bg-background-secondary text-label-primary border border-glass-border hover:bg-fill-secondary';
            return (
              <button
                key={i}
                type="button"
                disabled={action.disabled}
                onClick={() => {
                  if (action.disabled) return;
                  action.onClick();
                  setOpen(false);
                }}
                className={`flex items-center gap-3 pl-4 pr-3 py-2 rounded-full shadow-lg text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${toneClasses}`}
              >
                <span>{action.label}</span>
                <span className="w-7 h-7 rounded-full bg-black/10 flex items-center justify-center">
                  {action.icon}
                </span>
              </button>
            );
          })}
        </div>
      )}

      {/* Main trigger */}
      <button
        type="button"
        aria-label={ariaLabel}
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className={`w-14 h-14 rounded-full shadow-xl bg-accent-primary text-white flex items-center justify-center hover:bg-accent-primary/90 transition-all ${
          open ? 'rotate-45' : ''
        }`}
      >
        <svg
          className="w-6 h-6 transition-transform"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth={2}
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
        </svg>
      </button>
    </div>
  );
};

export default FloatingActionButton;
