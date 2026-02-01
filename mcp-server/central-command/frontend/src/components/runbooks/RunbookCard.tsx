import React, { memo } from 'react';
import { GlassCard, LevelBadge } from '../shared';
import type { Runbook } from '../../types';

interface RunbookCardProps {
  runbook: Runbook;
  onClick?: () => void;
}

const formatTime = (ms: number): string => {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${(ms / 60000).toFixed(1)}m`;
};

export const RunbookCard: React.FC<RunbookCardProps> = memo(({ runbook, onClick }) => {
  const successColor = runbook.success_rate >= 95
    ? 'text-health-healthy'
    : runbook.success_rate >= 80
      ? 'text-health-warning'
      : 'text-health-critical';

  return (
    <GlassCard hover onClick={onClick} padding="md">
      <div className="flex items-start justify-between gap-4">
        {/* Left: Info */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <span className="text-xs font-mono text-label-tertiary">{runbook.id}</span>
            <LevelBadge level={runbook.level} />
            {runbook.is_disruptive && (
              <span className="px-1.5 py-0.5 text-xs bg-orange-100 text-orange-700 rounded">
                Disruptive
              </span>
            )}
          </div>
          <h3 className="font-semibold text-label-primary truncate">{runbook.name}</h3>
          <p className="text-sm text-label-secondary mt-1 line-clamp-2">{runbook.description}</p>

          {/* HIPAA Controls */}
          <div className="flex flex-wrap gap-1 mt-2">
            {runbook.hipaa_controls.map((control) => (
              <span
                key={control}
                className="px-1.5 py-0.5 text-xs bg-accent-tint text-accent-primary rounded"
              >
                {control}
              </span>
            ))}
          </div>
        </div>

        {/* Right: Stats */}
        <div className="flex-shrink-0 text-right space-y-1">
          <div>
            <p className="text-xs text-label-tertiary">Executions</p>
            <p className="text-lg font-semibold text-label-primary">
              {runbook.execution_count.toLocaleString()}
            </p>
          </div>
          <div>
            <p className="text-xs text-label-tertiary">Success Rate</p>
            <p className={`text-lg font-semibold ${successColor}`}>
              {runbook.success_rate.toFixed(1)}%
            </p>
          </div>
          <div>
            <p className="text-xs text-label-tertiary">Avg Time</p>
            <p className="text-sm font-medium text-label-secondary">
              {formatTime(runbook.avg_execution_time_ms)}
            </p>
          </div>
        </div>
      </div>
    </GlassCard>
  );
});

export default RunbookCard;
