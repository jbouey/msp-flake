import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';

/**
 * KillSwitchBanner — global cross-page banner for fleet-wide healing
 * pause state.
 *
 * #75 closure 2026-05-02 (sub-followup of #64 P0 kill-switch).
 *
 * Pre-fix: kill-switch state was visible only on
 * AdminSubstrateHealth.tsx. Operator on Sites/Incidents/Fleet during
 * an incident wouldn't see the banner — could believe healing was
 * active when it was actually paused. Steve adversarial-round catch.
 *
 * Now: rendered above the route content in App.tsx so EVERY admin
 * page shows the banner when paused. Read-only (action button +
 * modal stay on AdminSubstrateHealth where operator goes to act).
 *
 * Polls /api/admin/healing/global-state every 60s. Silent on
 * fetch failure (banner is non-critical UX; fail-safe = render
 * nothing rather than wrong-state).
 */
export const KillSwitchBanner: React.FC = () => {
  const [state, setState] = useState<{
    disabled: boolean;
    actor?: string;
    reason?: string;
    set_at?: string;
  } | null>(null);

  useEffect(() => {
    let cancelled = false;
    const probe = async () => {
      try {
        const res = await fetch('/api/admin/healing/global-state', { credentials: 'include' });
        if (!res.ok) return;
        const data = await res.json();
        if (!cancelled) setState(data);
      } catch {
        // Silent — non-critical UX
      }
    };
    probe();
    const interval = setInterval(probe, 60 * 1000);
    return () => { cancelled = true; clearInterval(interval); };
  }, []);

  if (!state || !state.disabled) return null;

  return (
    <div className="bg-rose-700 text-white px-6 py-2 text-sm flex items-center justify-between gap-3 border-b border-rose-900 sticky top-0 z-30">
      <div className="flex items-center gap-3 min-w-0">
        <span className="text-base leading-none">⛔</span>
        <span className="font-semibold uppercase tracking-wide text-xs">
          Fleet healing globally paused
        </span>
        <span className="text-rose-100/90 text-xs truncate">
          by <span className="font-medium">{state.actor || 'unknown'}</span>
          {state.reason && <> · "{state.reason}"</>}
          {state.set_at && (
            <> · {new Date(state.set_at).toLocaleString()}</>
          )}
        </span>
      </div>
      <Link
        to="/admin/substrate-health"
        className="px-3 py-1 rounded bg-white/20 hover:bg-white/30 text-xs font-medium whitespace-nowrap"
      >
        Resume →
      </Link>
    </div>
  );
};

export default KillSwitchBanner;
