import React from 'react';

interface KPICardProps {
  label: string;
  value: number;
  unit: string;
  status: 'pass' | 'warn' | 'fail';
}

export const KPICard: React.FC<KPICardProps> = ({ label, value, unit, status }) => {
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

  return (
    <div className={`rounded-xl border p-6 ${bgColors[status]}`}>
      <div className={`text-4xl font-bold ${colors[status]}`}>
        {typeof value === 'number' ? value.toFixed(value % 1 === 0 ? 0 : 1) : value}{unit}
      </div>
      <div className="text-gray-600 text-sm mt-2">{label}</div>
    </div>
  );
};
