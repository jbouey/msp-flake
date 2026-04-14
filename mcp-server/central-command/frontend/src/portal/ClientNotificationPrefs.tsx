/**
 * ClientNotificationPrefs — Session 206 round-table P2.
 *
 * Inline toggle UI for the practice manager to opt into/out of email
 * digest, critical alerts, and the weekly summary. Single API surface
 * is /api/portal/site/{site_id}/notification-prefs (GET + PUT).
 */

import React, { useCallback, useState } from 'react';

interface Prefs {
  email_digest: boolean;
  critical_alerts: boolean;
  weekly_summary: boolean;
}

interface Props {
  siteId: string;
  token: string | null;
  initial: Prefs;
}

const FIELDS: Array<{ key: keyof Prefs; label: string; hint: string }> = [
  {
    key: 'critical_alerts',
    label: 'Critical alerts',
    hint: 'Email when something needs your attention right now. Recommended on.',
  },
  {
    key: 'email_digest',
    label: 'Daily digest',
    hint: 'Summary of yesterday\'s auto-fixes + anything still open.',
  },
  {
    key: 'weekly_summary',
    label: 'Weekly summary',
    hint: 'One roll-up email every Monday morning.',
  },
];

export const ClientNotificationPrefs: React.FC<Props> = ({ siteId, token, initial }) => {
  const [prefs, setPrefs] = useState<Prefs>(initial);
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  const save = useCallback(async (next: Prefs) => {
    setSaving(true);
    try {
      const qs = token ? `?token=${encodeURIComponent(token)}` : '';
      const res = await fetch(`/api/portal/site/${siteId}/notification-prefs${qs}`, {
        method: 'PUT',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(next),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setSavedAt(Date.now());
    } catch {
      // swallow — the toggle will still visually reflect the attempt
    } finally {
      setSaving(false);
    }
  }, [siteId, token]);

  const toggle = (k: keyof Prefs) => {
    const next = { ...prefs, [k]: !prefs[k] };
    setPrefs(next);
    save(next);
  };

  return (
    <div className="rounded-2xl bg-white/5 backdrop-blur-xl border border-white/10 p-6 max-w-3xl mx-auto">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <span className="text-xl">🔔</span>
          <h3 className="text-sm font-semibold text-white/80">Email preferences</h3>
        </div>
        {saving && <span className="text-[11px] text-white/50">saving…</span>}
        {!saving && savedAt && <span className="text-[11px] text-emerald-400">saved ✓</span>}
      </div>
      <div className="space-y-3">
        {FIELDS.map((f) => (
          <label key={f.key} className="flex items-start gap-3 cursor-pointer">
            <input
              type="checkbox"
              checked={prefs[f.key]}
              onChange={() => toggle(f.key)}
              className="mt-0.5 h-4 w-4 rounded border-white/20 bg-white/10 text-blue-500 focus:ring-blue-500"
            />
            <div className="flex-1">
              <div className="text-sm text-white/90">{f.label}</div>
              <div className="text-[11px] text-white/50">{f.hint}</div>
            </div>
          </label>
        ))}
      </div>
    </div>
  );
};

export default ClientNotificationPrefs;
