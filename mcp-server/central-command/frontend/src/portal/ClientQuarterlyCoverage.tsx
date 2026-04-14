/**
 * ClientQuarterlyCoverage — Session 206 round-table P2.
 *
 * 90-day (13-week) coverage trend. Extends the 30-day daily timeline
 * already on the hero card with a longer-horizon view — answers the
 * practice manager's "how have we been doing lately" without asking
 * them to click through to an analytics page.
 */

import React from 'react';

interface Week {
  week_start: string;
  incidents: number;
  pct_covered: number;
}

export const ClientQuarterlyCoverage: React.FC<{ weeks?: Week[] }> = ({ weeks }) => {
  if (!weeks || weeks.length === 0) return null;
  const totalIncidents = weeks.reduce((s, w) => s + w.incidents, 0);
  const avgCovered = weeks.length
    ? Math.round((weeks.reduce((s, w) => s + w.pct_covered, 0) / weeks.length) * 10) / 10
    : 100;

  return (
    <div className="rounded-2xl bg-white/5 backdrop-blur-xl border border-white/10 p-6 max-w-3xl mx-auto">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <span className="text-xl">📈</span>
          <h3 className="text-sm font-semibold text-white/80">Last 90 days</h3>
        </div>
        <div className="text-xs text-white/60">
          <b className={avgCovered >= 95 ? 'text-emerald-400' : avgCovered >= 85 ? 'text-amber-300' : 'text-red-400'}>
            {avgCovered.toFixed(1)}%
          </b>{' '}avg resolved · {totalIncidents} total events
        </div>
      </div>
      <div className="flex gap-1 items-end h-12">
        {weeks.map((w) => {
          const ht = Math.max(8, w.pct_covered);
          const color = w.pct_covered >= 95
            ? 'bg-emerald-400/80'
            : w.pct_covered >= 85
              ? 'bg-amber-400/80'
              : 'bg-red-400/80';
          return (
            <div
              key={w.week_start}
              className="flex-1 flex items-end"
              title={`${w.week_start}: ${w.pct_covered.toFixed(1)}% (${w.incidents} events)`}
            >
              <div className={`${color} rounded-sm w-full`} style={{ height: `${ht}%` }} />
            </div>
          );
        })}
      </div>
      <div className="mt-2 flex justify-between text-[11px] text-white/50">
        <span>{weeks[0]?.week_start}</span>
        <span>This week</span>
      </div>
    </div>
  );
};

export default ClientQuarterlyCoverage;
