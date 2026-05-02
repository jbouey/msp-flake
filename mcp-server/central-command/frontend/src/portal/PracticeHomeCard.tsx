/**
 * PracticeHomeCard — Session 206 round-table hero component.
 *
 * Psychology-first design for the end-customer (practice manager):
 *   - Single green-or-orange "You are protected" indicator
 *   - Audit-kit download front-and-center
 *   - "This month" summary that makes the 75%-cheaper-than-MSP
 *     value prop visible
 *   - Partner attribution (named human) as trust signal
 *
 * Data from GET /api/portal/site/{site_id}/home which does the
 * aggregation server-side. Frontend stays dumb and fast.
 */

import React from 'react';

interface HomeData {
  site_id: string;
  protected: boolean;
  protected_reason: string;
  protected_label: string;
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
  // #73 closure 2026-05-02: fleet-wide healing pause state surfaces
  // to client portal so an auditor visiting during a paused window
  // can see auto-remediation was off.
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

function relTime(iso: string): string {
  const t = new Date(iso).getTime();
  const now = Date.now();
  const diff = Math.max(0, Math.floor((now - t) / 1000));
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

export const PracticeHomeCard: React.FC<Props> = ({ data, practiceName, isLoading }) => {
  if (isLoading || !data) {
    return (
      <div className="rounded-2xl bg-white/5 backdrop-blur-xl border border-white/10 p-8 max-w-3xl mx-auto">
        <div className="animate-pulse space-y-4">
          <div className="h-4 w-1/3 bg-white/10 rounded" />
          <div className="h-8 w-1/2 bg-white/10 rounded" />
          <div className="h-4 w-full bg-white/10 rounded" />
        </div>
      </div>
    );
  }

  const protectedIcon = data.protected ? '✓' : '⚠';
  const protectedColor = data.protected ? 'text-emerald-400' : 'text-amber-400';
  const bannerBg = data.protected
    ? 'bg-gradient-to-r from-emerald-500/10 to-emerald-400/5'
    : 'bg-gradient-to-r from-amber-500/10 to-amber-400/5';

  return (
    <div className="max-w-3xl mx-auto space-y-4">
      {/* #73 closure 2026-05-02: fleet-wide healing pause banner.
          Surfaces to client when MSP/admin has paused auto-remediation
          fleet-wide. Auditor-visible record per HIPAA chain-of-custody. */}
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

      {/* Hero: protection status */}
      <div className={`rounded-2xl ${bannerBg} backdrop-blur-xl border border-white/10 p-8`}>
        <div className="flex items-start gap-4">
          <div className={`text-5xl ${protectedColor} leading-none`}>{protectedIcon}</div>
          <div className="flex-1">
            <h2 className={`text-2xl font-bold ${protectedColor} mb-1`}>
              {data.protected_label}
            </h2>
            <p className="text-sm text-white/70">
              {data.protected ? (
                <>All HIPAA compliance checks passing across {data.devices.workstations} workstation{data.devices.workstations === 1 ? '' : 's'} and {data.devices.appliances} appliance{data.devices.appliances === 1 ? '' : 's'}.</>
              ) : (
                <>{data.protected_reason}</>
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
            Download a cryptographically signed proof package your auditor can verify independently on their own laptop. No OsirisCare infrastructure required to verify.
          </p>
          <a
            href={data.auditor_kit_url}
            className="mt-4 inline-flex items-center justify-center px-4 py-2.5 bg-blue-500 hover:bg-blue-600 text-white text-sm font-semibold rounded-lg transition-colors"
            download
          >
            Download proof package
          </a>
        </div>
      </div>

      {/* 30-day coverage */}
      {data.coverage_30d && data.coverage_30d.length > 0 && (
        <div className="rounded-2xl bg-white/5 backdrop-blur-xl border border-white/10 p-6">
          <h3 className="text-sm font-semibold text-white/80 mb-2">
            Coverage — last 30 days
          </h3>
          <div className="flex gap-0.5" aria-label="daily coverage history">
            {data.coverage_30d.map((d) => (
              <div
                key={d.date}
                title={`${d.date}: ${d.covered ? 'all clear' : `${d.incidents} unresolved`}`}
                className={`flex-1 h-8 rounded-sm ${
                  d.covered ? 'bg-emerald-500/60' : 'bg-amber-500/60'
                }`}
              />
            ))}
          </div>
          <div className="mt-2 flex justify-between text-[11px] text-white/50">
            <span>{data.coverage_30d[0]?.date}</span>
            <span>Today</span>
          </div>
        </div>
      )}
    </div>
  );
};
