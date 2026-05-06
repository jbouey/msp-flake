/**
 * PracticeHomeCard — Session 206 round-table hero component.
 *
 * Psychology-first design for the end-customer (practice manager):
 *   - Single green-or-orange "Monitoring active" indicator
 *   - Audit-kit download front-and-center (via JS fetch→blob with
 *     CSRF + spinner + error states; raw <a download> regressed
 *     RT31 and was caught by 2026-05-06 round-table consistency
 *     coach)
 *   - "This month" summary that makes the 75%-cheaper-than-MSP
 *     value prop visible
 *   - Partner attribution (named human) as trust signal
 *
 * Data from GET /api/portal/site/{site_id}/home which does the
 * aggregation server-side. Frontend stays dumb and fast.
 *
 * 2026-05-06 round-table changes:
 *   - Carol P0: "protected" → "monitored" (CLAUDE.md banned words).
 *   - Consistency coach P1: <a download> → fetchBlob with
 *     401/429/network error states + spinner.
 */

import React, { useState } from 'react';

interface HomeData {
  site_id: string;
  // Round-table 2026-05-06: monitored_* are canonical; protected_*
  // are backwards-compat aliases that backend ships for one release
  // cycle. Frontend prefers monitored_* and falls back.
  monitored?: boolean;
  monitored_reason?: string;
  monitored_label?: string;
  protected?: boolean;
  protected_reason?: string;
  protected_label?: string;
  last_updated_at: string;
  this_month: {
    issues_found: number;
    auto_fixed: number;
    resolved_with_partner: number;
    period_start: string;
  };
  partner: {
    name: string | null;
    email: string | null;
    last_reviewed_at: string | null;
  };
  devices: { appliances: number; workstations: number };
  coverage_30d: Array<{ date: string; covered: boolean; incidents: number }>;
  auditor_kit_url: string;
  fleet_healing_state?: {
    disabled: boolean;
    paused_since?: string;
    paused_reason?: string;
  };
}

interface Props {
  data: HomeData | null | undefined;
  practiceName?: string;
  isLoading?: boolean;
}

function formatDate(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleDateString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    });
  } catch {
    return iso;
  }
}

function relTime(iso: string): string {
  const d = new Date(iso);
  const diff = Date.now() - d.getTime();
  const min = Math.round(diff / 60000);
  if (min < 1) return 'just now';
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h ago`;
  return formatDate(iso);
}

export const PracticeHomeCard: React.FC<Props> = ({
  data,
  practiceName,
  isLoading,
}) => {
  const [kitState, setKitState] = useState<
    'idle' | 'downloading' | 'error'
  >('idle');
  const [kitError, setKitError] = useState<string | null>(null);

  const handleDownloadKit = async (): Promise<void> => {
    if (!data?.auditor_kit_url) return;
    setKitState('downloading');
    setKitError(null);
    try {
      const res = await fetch(data.auditor_kit_url, {
        credentials: 'include',
      });
      if (res.status === 401) {
        setKitState('error');
        setKitError(
          'Your session has expired. Sign in again and retry the download.'
        );
        return;
      }
      if (res.status === 429) {
        const retry = res.headers.get('Retry-After') || '3600';
        setKitState('error');
        setKitError(
          `Download limit reached (10/hr). Try again in ~${Math.ceil(
            Number(retry) / 60
          )} minutes.`
        );
        return;
      }
      if (!res.ok) {
        setKitState('error');
        setKitError(
          `Download failed (HTTP ${res.status}). Contact support@osiriscare.com if this persists.`
        );
        return;
      }
      const blob = await res.blob();
      const filename =
        res.headers
          .get('Content-Disposition')
          ?.match(/filename="?([^";]+)"?/i)?.[1] ||
        `auditor-kit-${data.site_id}.zip`;
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      setKitState('idle');
    } catch (e) {
      setKitState('error');
      setKitError(
        (e as Error)?.message ||
          'Network error preparing your download. Check your connection and retry.'
      );
    }
  };

  if (isLoading || !data) {
    return (
      <div className="max-w-3xl mx-auto space-y-4 animate-pulse">
        <div className="rounded-2xl bg-white/5 border border-white/10 p-8">
          <div className="h-12 w-3/4 bg-white/10 rounded" />
          <div className="mt-3 h-4 w-1/2 bg-white/10 rounded" />
        </div>
        <div className="grid grid-cols-2 gap-4">
          <div className="rounded-2xl bg-white/5 border border-white/10 p-6 h-40" />
          <div className="rounded-2xl bg-white/5 border border-white/10 p-6 h-40" />
        </div>
        <div className="rounded-2xl bg-white/5 border border-white/10 p-4">
          <div className="h-4 w-full bg-white/10 rounded" />
        </div>
      </div>
    );
  }

  // Prefer monitored_* (canonical); fall back to protected_* (legacy
  // alias). Backend ships both during the transition window.
  const isMonitored = data.monitored ?? data.protected ?? false;
  const monitoredReason =
    data.monitored_reason ?? data.protected_reason ?? '';
  const monitoredLabel =
    data.monitored_label ?? data.protected_label ?? 'Status';
  const monitoredIcon = isMonitored ? '✓' : '⚠';
  const monitoredColor = isMonitored
    ? 'text-emerald-400'
    : 'text-amber-400';
  const bannerBg = isMonitored
    ? 'bg-gradient-to-r from-emerald-500/10 to-emerald-400/5'
    : 'bg-gradient-to-r from-amber-500/10 to-amber-400/5';

  return (
    <div className="max-w-3xl mx-auto space-y-4">
      {data.fleet_healing_state?.disabled && (
        <div className="rounded-2xl bg-amber-500/10 border border-amber-500/30 p-4">
          <div className="flex items-start gap-3">
            <span className="text-xl text-amber-300 leading-none">⚠</span>
            <div className="flex-1 min-w-0">
              <h3 className="text-sm font-semibold text-amber-100">
                Auto-remediation is paused fleet-wide
              </h3>
              <p className="text-xs text-amber-200/90 mt-1">
                Your IT partner has paused automatic healing across all
                accounts they manage.
                {data.fleet_healing_state.paused_since && (
                  <> Since {formatDate(data.fleet_healing_state.paused_since)}.</>
                )}
                {data.fleet_healing_state.paused_reason && (
                  <> Reason: <span className="italic">"{data.fleet_healing_state.paused_reason}"</span></>
                )}
                {' '}New compliance issues are being routed to manual triage
                instead of being auto-fixed. Existing evidence (signing,
                anchoring, audit chain) continues to operate normally.
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Greeting + last-updated */}
      <div className="flex items-center justify-between px-2">
        <h1 className="text-lg font-semibold text-white/90">
          {practiceName ? `Hello, ${practiceName}` : 'Welcome'}
        </h1>
        <span className="text-xs text-white/50">
          Last updated {relTime(data.last_updated_at)}
        </span>
      </div>

      {/* Hero: monitoring status */}
      <div className={`rounded-2xl ${bannerBg} backdrop-blur-xl border border-white/10 p-8`}>
        <div className="flex items-start gap-4">
          <div className={`text-5xl ${monitoredColor} leading-none`}>{monitoredIcon}</div>
          <div className="flex-1">
            <h2 className={`text-2xl font-bold ${monitoredColor} mb-1`}>
              {monitoredLabel}
            </h2>
            <p className="text-sm text-white/70">
              {isMonitored ? (
                <>HIPAA compliance checks are passing across {data.devices.workstations} workstation{data.devices.workstations === 1 ? '' : 's'} and {data.devices.appliances} appliance{data.devices.appliances === 1 ? '' : 's'}.</>
              ) : (
                <>{monitoredReason}</>
              )}
            </p>
          </div>
        </div>
      </div>

      {/* Two-column: this month + audit kit */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* This month */}
        <div className="rounded-2xl bg-white/5 backdrop-blur-xl border border-white/10 p-6">
          <div className="flex items-center gap-2 mb-3">
            <span className="text-xl">🛡️</span>
            <h3 className="text-sm font-semibold text-white/80">This month</h3>
          </div>
          <ul className="space-y-1.5 text-sm text-white/70">
            <li className="flex justify-between">
              <span>Issues found</span>
              <span className="font-semibold text-white tabular-nums">{data.this_month.issues_found}</span>
            </li>
            <li className="flex justify-between">
              <span className="text-emerald-400">Fixed automatically</span>
              <span className="font-semibold text-emerald-400 tabular-nums">{data.this_month.auto_fixed}</span>
            </li>
            <li className="flex justify-between">
              <span>Resolved with your partner</span>
              <span className="font-semibold text-white tabular-nums">{data.this_month.resolved_with_partner}</span>
            </li>
          </ul>
          {data.partner.name && (
            <div className="mt-4 pt-3 border-t border-white/10 text-xs text-white/60">
              <div>Partner: <span className="text-white/80">{data.partner.name}</span></div>
              {data.partner.last_reviewed_at && (
                <div>Last reviewed: {formatDate(data.partner.last_reviewed_at)}</div>
              )}
              {data.partner.email && (
                <div className="mt-1">
                  Contact: <a href={`mailto:${data.partner.email}`} className="text-blue-400 hover:underline">{data.partner.email}</a>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Audit kit */}
        <div className="rounded-2xl bg-white/5 backdrop-blur-xl border border-white/10 p-6 flex flex-col">
          <div className="flex items-center gap-2 mb-3">
            <span className="text-xl">📄</span>
            <h3 className="text-sm font-semibold text-white/80">For your auditor</h3>
          </div>
          <p className="text-sm text-white/70 flex-1">
            Download a cryptographically signed evidence integrity package your auditor can verify independently. Pair with your designated record set for HIPAA §164.528 disclosure accounting.
          </p>
          <button
            type="button"
            onClick={handleDownloadKit}
            disabled={kitState === 'downloading'}
            className="mt-4 inline-flex items-center justify-center px-4 py-2.5 bg-blue-500 hover:bg-blue-600 disabled:bg-blue-500/50 disabled:cursor-not-allowed text-white text-sm font-semibold rounded-lg transition-colors"
          >
            {kitState === 'downloading' ? 'Preparing…' : 'Download evidence package'}
          </button>
          {kitError && (
            <p className="mt-2 text-xs text-amber-300" role="alert">
              {kitError}
            </p>
          )}
        </div>
      </div>

      {/* 30-day coverage */}
      {data.coverage_30d && data.coverage_30d.length > 0 && (
        <div className="rounded-2xl bg-white/5 backdrop-blur-xl border border-white/10 p-4">
          <div className="flex items-center gap-2 mb-3">
            <h3 className="text-sm font-semibold text-white/80">Coverage (last 30 days)</h3>
          </div>
          <div className="flex gap-1">
            {data.coverage_30d.map((day) => (
              <div
                key={day.date}
                title={`${formatDate(day.date)}${day.incidents > 0 ? ` — ${day.incidents} issue${day.incidents === 1 ? '' : 's'}` : ' — clean'}`}
                className={`flex-1 h-8 rounded ${
                  day.covered
                    ? day.incidents > 0
                      ? 'bg-amber-500/40'
                      : 'bg-emerald-500/40'
                    : 'bg-white/10'
                }`}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
};

export default PracticeHomeCard;
