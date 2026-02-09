import React, { useState } from 'react';

interface ControlTileProps {
  control: {
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
  };
}

export const ControlTile: React.FC<ControlTileProps> = ({ control }) => {
  const [expanded, setExpanded] = useState(false);

  const statusConfig = {
    pass: {
      border: 'border-green-400',
      bg: 'bg-green-50',
      icon: '✓',
      iconColor: 'text-green-600',
      label: 'EXPECTED',
      labelBg: 'bg-green-100 text-green-800'
    },
    warn: {
      border: 'border-orange-400',
      bg: 'bg-orange-50',
      icon: '⚠',
      iconColor: 'text-orange-600',
      label: 'REVIEW',
      labelBg: 'bg-orange-100 text-orange-800'
    },
    fail: {
      border: 'border-red-400',
      bg: 'bg-red-50',
      icon: '✗',
      iconColor: 'text-red-600',
      label: 'DRIFT DETECTED',
      labelBg: 'bg-red-100 text-red-800'
    }
  };

  const config = statusConfig[control.status] || statusConfig.pass;
  const displayName = control.plain_english || control.name;

  return (
    <div
      className={`border-2 rounded-xl p-4 ${config.border} ${config.bg} cursor-pointer transition-all hover:shadow-md`}
      onClick={() => setExpanded(!expanded)}
    >
      {/* Header */}
      <div className="flex justify-between items-start mb-2">
        <div className="flex-1 pr-2">
          <h3 className="font-semibold text-slate-900 text-sm leading-tight">
            {displayName}
          </h3>
          {control.plain_english && (
            <p className="text-xs text-slate-500 mt-0.5">{control.name}</p>
          )}
        </div>
        <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${config.labelBg}`}>
          {config.label}
        </span>
      </div>

      {/* Why it matters - always visible for non-passing */}
      {control.status !== 'pass' && control.consequence && (
        <div className="mt-2 p-2 bg-white/60 rounded-lg">
          <p className="text-xs text-slate-700">
            <span className="font-medium text-red-700">Risk: </span>
            {control.consequence}
          </p>
        </div>
      )}

      {/* Expanded content */}
      {expanded && (
        <div className="mt-3 pt-3 border-t border-slate-200/50 space-y-2">
          {control.why_it_matters && (
            <div className="text-xs">
              <span className="font-medium text-slate-700">Why it matters: </span>
              <span className="text-slate-600">{control.why_it_matters}</span>
            </div>
          )}

          {control.what_we_check && (
            <div className="text-xs">
              <span className="font-medium text-slate-700">What we check: </span>
              <span className="text-slate-600">{control.what_we_check}</span>
            </div>
          )}

          <div className="flex flex-wrap gap-2 mt-2">
            <span className="inline-flex items-center px-2 py-0.5 rounded text-xs bg-blue-50 text-blue-700 border border-blue-200">
              {control.hipaa_section || control.hipaa_controls.join(', ')}
            </span>
            <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs ${
              control.severity === 'critical' ? 'bg-red-50 text-red-700 border border-red-200' :
              control.severity === 'high' ? 'bg-orange-50 text-orange-700 border border-orange-200' :
              control.severity === 'medium' ? 'bg-yellow-50 text-yellow-700 border border-yellow-200' :
              'bg-slate-50 text-slate-700 border border-slate-200'
            }`}>
              {control.severity.toUpperCase()} priority
            </span>
          </div>

          {/* HIPAA citation - shown on expand */}
          <div className="text-xs text-slate-400 mt-2">
            HIPAA: {control.hipaa_controls.join(', ')}
          </div>
        </div>
      )}

      {/* Auto-fix indicator */}
      {control.auto_fix_triggered && (
        <div className="mt-3 text-xs text-green-700 bg-green-100 rounded-lg px-2 py-1.5 flex items-center gap-1">
          <span>✓</span>
          <span>Remediation attempted ({control.fix_duration_sec ?? 0}s)</span>
        </div>
      )}

      {/* Exception indicator */}
      {control.exception_applied && (
        <div className="mt-2 text-xs text-orange-700 bg-orange-100 rounded-lg px-2 py-1.5 flex items-center gap-1">
          <span>⚠</span>
          <span>Exception: {control.exception_reason || 'Active'}</span>
        </div>
      )}

      {/* Last checked + expand hint */}
      <div className="mt-3 flex justify-between items-center text-xs text-slate-400">
        <span>
          {control.checked_at
            ? `Checked ${new Date(control.checked_at).toLocaleString()}`
            : 'Pending check'}
        </span>
        <span className="text-slate-300">
          {expanded ? '▲ less' : '▼ more'}
        </span>
      </div>
    </div>
  );
};
