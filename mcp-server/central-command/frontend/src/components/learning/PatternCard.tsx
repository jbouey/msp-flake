import React, { memo, useCallback } from 'react';
import { GlassCard } from '../shared';
import type { PromotionCandidate } from '../../types';

interface PatternCardProps {
  candidate: PromotionCandidate;
  onApprove: (id: string) => void;
  onReject: (id: string) => void;
  isPromoting?: boolean;
}

export const PatternCard: React.FC<PatternCardProps> = memo(({
  candidate,
  onApprove,
  onReject,
  isPromoting = false,
}) => {
  const handleApprove = useCallback(() => onApprove(candidate.id), [onApprove, candidate.id]);
  const handleReject = useCallback(() => onReject(candidate.id), [onReject, candidate.id]);
  const formatTime = (ms: number): string => {
    if (ms < 1000) return `${ms}ms`;
    return `${(ms / 1000).toFixed(1)}s`;
  };

  const formatDate = (dateStr: string): string => {
    const date = new Date(dateStr);
    return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  };

  const successColor =
    candidate.success_rate >= 95
      ? 'text-health-healthy'
      : candidate.success_rate >= 80
      ? 'text-health-warning'
      : 'text-health-critical';

  return (
    <GlassCard padding="md" className="relative">
      {isPromoting && (
        <div className="absolute inset-0 bg-white/50 rounded-ios-lg flex items-center justify-center z-10">
          <div className="w-6 h-6 border-2 border-accent-primary border-t-transparent rounded-full animate-spin" />
        </div>
      )}

      <div className="flex items-start justify-between gap-4">
        <div className="flex-1 min-w-0">
          {/* Pattern signature */}
          <div className="flex items-center gap-2 mb-2">
            <span className="px-2 py-0.5 text-xs font-medium bg-level-l2 text-white rounded">
              L2
            </span>
            <span className="text-xs text-label-tertiary">
              {formatDate(candidate.first_seen)} - {formatDate(candidate.last_seen)}
            </span>
          </div>

          <h3 className="font-mono text-sm font-medium text-label-primary mb-1">
            {candidate.pattern_signature}
          </h3>

          <p className="text-sm text-label-secondary mb-3">
            {candidate.description}
          </p>

          {/* Proposed rule */}
          <div className="bg-separator-light/50 rounded-ios-sm p-3 mb-3">
            <p className="text-xs text-label-tertiary mb-1">Proposed L1 Rule:</p>
            <p className="text-sm font-medium text-label-primary">
              {candidate.proposed_rule}
            </p>
          </div>

          {/* Stats */}
          <div className="flex items-center gap-4 text-xs">
            <div>
              <span className="text-label-tertiary">Occurrences: </span>
              <span className="font-medium text-label-primary">{candidate.occurrences}</span>
            </div>
            <div>
              <span className="text-label-tertiary">Success: </span>
              <span className={`font-medium ${successColor}`}>
                {candidate.success_rate.toFixed(1)}%
              </span>
            </div>
            <div>
              <span className="text-label-tertiary">Avg Time: </span>
              <span className="font-medium text-label-primary">
                {formatTime(candidate.avg_resolution_time_ms)}
              </span>
            </div>
          </div>
        </div>

        {/* Actions */}
        <div className="flex flex-col gap-2">
          <button
            onClick={handleApprove}
            disabled={isPromoting}
            className="btn-primary text-xs px-4 py-2 disabled:opacity-50"
          >
            Approve
          </button>
          <button
            onClick={handleReject}
            disabled={isPromoting}
            className="btn-secondary text-xs px-4 py-2 disabled:opacity-50"
          >
            Reject
          </button>
        </div>
      </div>
    </GlassCard>
  );
});

export default PatternCard;
