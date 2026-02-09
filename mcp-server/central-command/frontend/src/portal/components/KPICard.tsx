import React from 'react';

interface KPICardProps {
  label: string;
  value: number;
  unit: string;
  status: 'pass' | 'warn' | 'fail';
  description?: string;
}

export const KPICard: React.FC<KPICardProps> = ({ label, value, unit, status, description }) => {
  const colors = {
    pass: 'text-green-600',
    warn: 'text-orange-500',
    fail: 'text-red-600'
  };

  const bgColors = {
    pass: 'bg-green-50 border-green-200',
    warn: 'bg-orange-50 border-orange-200',
    fail: 'bg-red-50 border-red-200'
  };

  const statusLabels = {
    pass: 'On target',
    warn: 'Review suggested',
    fail: 'Attention recommended'
  };

  return (
    <div className={`rounded-xl border p-6 ${bgColors[status]}`}>
      <div className="flex justify-between items-start">
        <div className={`text-4xl font-bold ${colors[status]}`}>
          {typeof value === 'number' ? value.toFixed(value % 1 === 0 ? 0 : 1) : value}{unit}
        </div>
        <span className={`text-xs px-2 py-0.5 rounded-full ${
          status === 'pass' ? 'bg-green-100 text-green-700' :
          status === 'warn' ? 'bg-orange-100 text-orange-700' :
          'bg-red-100 text-red-700'
        }`}>
          {statusLabels[status]}
        </span>
      </div>
      <div className="text-slate-800 font-medium mt-2">{label}</div>
      {description && (
        <div className="text-slate-500 text-xs mt-1">{description}</div>
      )}
    </div>
  );
};
