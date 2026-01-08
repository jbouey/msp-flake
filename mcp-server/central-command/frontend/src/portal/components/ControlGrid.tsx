import React from 'react';
import { ControlTile } from './ControlTile';

interface Control {
  rule_id: string;
  name: string;
  status: 'pass' | 'warn' | 'fail';
  severity: string;
  hipaa_controls: string[];
  checked_at?: string;
  scope_summary: string;
  auto_fix_triggered: boolean;
  fix_duration_sec?: number;
  exception_applied: boolean;
  exception_reason?: string;
  // Customer-friendly HIPAA explanations
  plain_english?: string;
  why_it_matters?: string;
  consequence?: string;
  what_we_check?: string;
  hipaa_section?: string;
}

interface ControlGridProps {
  controls: Control[];
}

export const ControlGrid: React.FC<ControlGridProps> = ({ controls }) => {
  // Sort by severity: critical > high > medium > low
  const severityOrder = { critical: 0, high: 1, medium: 2, low: 3 };
  const sortedControls = [...controls].sort((a, b) => {
    const aSev = severityOrder[a.severity as keyof typeof severityOrder] ?? 4;
    const bSev = severityOrder[b.severity as keyof typeof severityOrder] ?? 4;
    if (aSev !== bSev) return aSev - bSev;
    // Then by status: fail > warn > pass
    const statusOrder = { fail: 0, warn: 1, pass: 2 };
    return (statusOrder[a.status] ?? 3) - (statusOrder[b.status] ?? 3);
  });

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
      {sortedControls.map((control) => (
        <ControlTile key={control.rule_id} control={control} />
      ))}
    </div>
  );
};
