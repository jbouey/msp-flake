import React from 'react';
import { GlassCard } from '../shared';
import type { CoverageGap } from '../../types';
import { CHECK_TYPE_LABELS } from '../../types';

interface CoverageGapPanelProps {
  gaps: CoverageGap[];
  isLoading?: boolean;
}

export const CoverageGapPanel: React.FC<CoverageGapPanelProps> = ({
  gaps,
  isLoading = false,
}) => {
  const uncovered = gaps.filter((g) => !g.has_l1_rule);
  const covered = gaps.filter((g) => g.has_l1_rule);

  if (isLoading) {
    return (
      <GlassCard>
        <h2 className="text-lg font-semibold mb-4">Rule Coverage</h2>
        <div className="space-y-2">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-6 animate-pulse bg-separator-light rounded" />
          ))}
        </div>
      </GlassCard>
    );
  }

  const total = gaps.length;
  const coveredPct = total > 0 ? Math.round((covered.length / total) * 100) : 100;

  return (
    <GlassCard>
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-lg font-semibold">Rule Coverage</h2>
          <p className="text-sm text-label-tertiary mt-0.5">
            L1 rule coverage across active check types
          </p>
        </div>
        <div className="text-right">
          <p className={`text-2xl font-bold tabular-nums ${
            coveredPct >= 80 ? 'text-health-healthy' : coveredPct >= 60 ? 'text-health-warning' : 'text-health-critical'
          }`}>
            {coveredPct}%
          </p>
          <p className="text-xs text-label-tertiary">{covered.length}/{total} types</p>
        </div>
      </div>

      {/* Coverage bar */}
      <div className="w-full h-2 bg-separator-light rounded-full mb-4 overflow-hidden">
        <div
          className={`h-full rounded-full transition-all ${
            coveredPct >= 80 ? 'bg-health-healthy' : coveredPct >= 60 ? 'bg-health-warning' : 'bg-health-critical'
          }`}
          style={{ width: `${coveredPct}%` }}
        />
      </div>

      {uncovered.length > 0 && (
        <div className="mb-4">
          <p className="text-xs font-semibold text-health-critical uppercase tracking-wider mb-2">
            Uncovered ({uncovered.length})
          </p>
          <div className="space-y-1.5">
            {uncovered.map((gap) => (
              <div
                key={gap.check_type}
                className="flex items-center justify-between py-1.5 px-3 bg-health-critical/5 rounded-ios-sm"
              >
                <span className="text-xs font-medium text-label-primary">{CHECK_TYPE_LABELS[gap.check_type] || gap.check_type.replace(/_/g, ' ')}</span>
                <span className="text-xs text-label-tertiary tabular-nums">
                  {gap.incident_count_30d} incidents/30d
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {covered.length > 0 && (
        <div>
          <p className="text-xs font-semibold text-health-healthy uppercase tracking-wider mb-2">
            Covered ({covered.length})
          </p>
          <div className="flex flex-wrap gap-1.5">
            {covered.map((gap) => (
              <span
                key={gap.check_type}
                className="px-2 py-0.5 text-xs bg-health-healthy/10 text-health-healthy rounded"
              >
                {CHECK_TYPE_LABELS[gap.check_type] || gap.check_type.replace(/_/g, ' ')}
              </span>
            ))}
          </div>
        </div>
      )}
    </GlassCard>
  );
};

export default CoverageGapPanel;
