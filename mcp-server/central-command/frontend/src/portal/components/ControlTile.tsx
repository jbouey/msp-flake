import React from 'react';

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
  };
}

export const ControlTile: React.FC<ControlTileProps> = ({ control }) => {
  const statusConfig = {
    pass: {
      border: 'border-green-400',
      bg: 'bg-green-50',
      icon: '✓',
      iconColor: 'text-green-600',
      label: 'PASS'
    },
    warn: {
      border: 'border-orange-400',
      bg: 'bg-orange-50',
      icon: '⚠',
      iconColor: 'text-orange-600',
      label: 'WARNING'
    },
    fail: {
      border: 'border-red-400',
      bg: 'bg-red-50',
      icon: '✗',
      iconColor: 'text-red-600',
      label: 'FAIL'
    }
  };

  const config = statusConfig[control.status] || statusConfig.pass;

  return (
    <div className={`border-2 rounded-xl p-4 ${config.border} ${config.bg}`}>
      <div className="flex justify-between items-start mb-3">
        <h3 className="font-semibold text-gray-900 text-sm leading-tight pr-2">
          {control.name}
        </h3>
        <span className={`text-xl ${config.iconColor}`}>{config.icon}</span>
      </div>

      <div className="space-y-2 text-xs text-gray-600">
        <div className="flex justify-between">
          <span className="font-medium">Status:</span>
          <span className={config.iconColor}>{config.label}</span>
        </div>
        <div className="flex justify-between">
          <span className="font-medium">HIPAA:</span>
          <span className="text-right max-w-[60%] truncate" title={control.hipaa_controls.join(', ')}>
            {control.hipaa_controls.slice(0, 2).join(', ')}
            {control.hipaa_controls.length > 2 && '...'}
          </span>
        </div>
        <div>
          <span className="font-medium">Scope:</span>
          <span className="ml-1">{control.scope_summary}</span>
        </div>
      </div>

      {control.auto_fix_triggered && (
        <div className="mt-3 text-xs text-green-700 bg-green-100 rounded-lg px-2 py-1.5 flex items-center gap-1">
          <span>✓</span>
          <span>Auto-fixed in {control.fix_duration_sec ?? 0}s</span>
        </div>
      )}

      {control.exception_applied && (
        <div className="mt-2 text-xs text-orange-700 bg-orange-100 rounded-lg px-2 py-1.5 flex items-center gap-1">
          <span>⚠</span>
          <span>Exception: {control.exception_reason || 'Active'}</span>
        </div>
      )}

      {control.checked_at && (
        <div className="mt-3 text-xs text-gray-400">
          Last checked: {new Date(control.checked_at).toLocaleString()}
        </div>
      )}
    </div>
  );
};
