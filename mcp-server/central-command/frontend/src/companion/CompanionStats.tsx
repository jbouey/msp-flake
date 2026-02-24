import React from 'react';
import { useCompanionStats } from './useCompanionApi';
import { companionColors } from './companion-tokens';
import { Spinner } from '../components/shared';

export const CompanionStats: React.FC = () => {
  const { data, isLoading } = useCompanionStats();

  if (isLoading || !data) {
    return <div className="flex justify-center py-20"><Spinner size="lg" /></div>;
  }

  const moduleStats: { key: string; label: string; count: number }[] = [
    { key: 'sra', label: 'SRA Completed', count: data.modules.sra.clients_completed },
    { key: 'policies', label: 'Active Policies', count: data.modules.policies.clients_with_active },
    { key: 'training', label: 'Training Records', count: data.modules.training.clients_with_records },
    { key: 'baas', label: 'Active BAAs', count: data.modules.baas.clients_with_active },
    { key: 'ir_plan', label: 'IR Plans', count: data.modules.ir_plan.clients_with_plan },
    { key: 'contingency', label: 'DR Plans', count: data.modules.contingency.clients_with_plans },
    { key: 'workforce', label: 'Workforce Tracking', count: data.modules.workforce.clients_tracking },
    { key: 'physical', label: 'Physical Assessed', count: data.modules.physical.clients_assessed },
    { key: 'officers', label: 'Officers Designated', count: data.modules.officers.clients_designated },
    { key: 'gap_analysis', label: 'Gap Analysis Started', count: data.modules.gap_analysis.clients_started },
  ];

  const total = data.total_clients || 1;

  return (
    <div>
      <h1 className="text-2xl font-semibold mb-6" style={{ color: companionColors.textPrimary }}>
        Progress Dashboard
      </h1>

      {/* Summary cards */}
      <div className="grid grid-cols-4 gap-4 mb-8">
        {[
          { label: 'Total Clients', value: data.total_clients },
          { label: 'Notes Created', value: data.total_notes },
          { label: 'Actions This Week', value: data.companion_activity_7d },
          { label: 'Avg Module Coverage', value: `${Math.round(moduleStats.reduce((s, m) => s + m.count, 0) / (10 * total) * 100)}%` },
        ].map(card => (
          <div
            key={card.label}
            className="rounded-xl p-5"
            style={{ background: companionColors.cardBg, border: `1px solid ${companionColors.cardBorder}` }}
          >
            <p className="text-sm" style={{ color: companionColors.textSecondary }}>{card.label}</p>
            <p className="text-2xl font-bold mt-1" style={{ color: companionColors.textPrimary }}>{card.value}</p>
          </div>
        ))}
      </div>

      {/* Module completion rates */}
      <div
        className="rounded-xl p-6"
        style={{ background: companionColors.cardBg, border: `1px solid ${companionColors.cardBorder}` }}
      >
        <h3 className="font-semibold mb-4" style={{ color: companionColors.textPrimary }}>
          Module Completion Across Clients
        </h3>
        <div className="space-y-4">
          {moduleStats.map(mod => {
            const pct = total > 0 ? Math.round((mod.count / total) * 100) : 0;
            return (
              <div key={mod.key}>
                <div className="flex items-center justify-between mb-1">
                  <span className="text-sm font-medium" style={{ color: companionColors.textPrimary }}>
                    {mod.label}
                  </span>
                  <span className="text-sm" style={{ color: companionColors.textSecondary }}>
                    {mod.count}/{data.total_clients} clients ({pct}%)
                  </span>
                </div>
                <div className="w-full h-2.5 rounded-full" style={{ background: companionColors.notStartedLight }}>
                  <div
                    className="h-full rounded-full transition-all"
                    style={{
                      width: `${pct}%`,
                      background: pct >= 70 ? companionColors.complete
                        : pct >= 40 ? companionColors.amber
                        : companionColors.inProgress,
                    }}
                  />
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
};
