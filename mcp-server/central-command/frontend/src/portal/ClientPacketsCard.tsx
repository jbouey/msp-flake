/**
 * ClientPacketsCard — Session 206 round-table P1 for client portal.
 *
 * Surfaces the monthly compliance packets (server-side signed,
 * hash-chained, OTS-anchored) as simple download links. The practice
 * manager keeps these for their own records; they're also what the
 * client's auditor asks for if they need a single PDF-like deliverable.
 */

import React from 'react';

interface Packet {
  year: number;
  month: number;
  framework: string;
  compliance_score: number | null;
  critical_issues: number;
  auto_fixes: number;
  generated_at: string | null;
  download_url: string;
}

const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];

export const ClientPacketsCard: React.FC<{ packets?: Packet[] }> = ({ packets }) => {
  if (!packets || packets.length === 0) return null;
  return (
    <div className="rounded-2xl bg-white/5 backdrop-blur-xl border border-white/10 p-6 max-w-3xl mx-auto">
      <div className="flex items-center gap-2 mb-3">
        <span className="text-xl">📚</span>
        <h3 className="text-sm font-semibold text-white/80">Your monthly compliance packets</h3>
      </div>
      <p className="text-xs text-white/60 mb-4">
        Archive these — each packet is cryptographically signed and counts toward your HIPAA documentation retention.
      </p>
      <div className="divide-y divide-white/5">
        {packets.slice(0, 6).map((p) => (
          <div key={`${p.year}-${p.month}`} className="py-2 flex items-center justify-between">
            <div className="flex-1 min-w-0">
              <div className="text-sm text-white/90 font-medium">
                {MONTHS[p.month - 1]} {p.year}
              </div>
              <div className="text-[11px] text-white/50">
                {p.framework.toUpperCase()}
                {p.compliance_score !== null && ` · score ${p.compliance_score}%`}
                {' · '}{p.auto_fixes} auto-fixes
                {p.critical_issues > 0 && <span className="text-amber-300"> · {p.critical_issues} critical</span>}
              </div>
            </div>
            <a
              href={p.download_url}
              className="ml-3 px-3 py-1.5 bg-white/10 hover:bg-white/20 text-white text-xs rounded transition-colors"
              download
            >
              Download
            </a>
          </div>
        ))}
      </div>
    </div>
  );
};

export default ClientPacketsCard;
