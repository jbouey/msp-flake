import React from 'react';
import { GlassCard } from '../shared';
import type { PromotionHistory } from '../../types';

interface PromotionTimelineProps {
  history: PromotionHistory[];
  isLoading?: boolean;
}

export const PromotionTimeline: React.FC<PromotionTimelineProps> = ({
  history,
  isLoading = false,
}) => {
  const formatDate = (dateStr: string): string => {
    const date = new Date(dateStr);
    return date.toLocaleDateString(undefined, {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  const getSuccessColor = (rate: number): string => {
    if (rate >= 95) return 'bg-health-healthy';
    if (rate >= 80) return 'bg-health-warning';
    return 'bg-health-critical';
  };

  if (isLoading) {
    return (
      <GlassCard>
        <h2 className="text-lg font-semibold mb-4">Recently Promoted</h2>
        <div className="space-y-4">
          {[1, 2, 3].map((i) => (
            <div key={i} className="flex gap-4 animate-pulse">
              <div className="w-3 h-3 bg-separator-light rounded-full mt-1.5" />
              <div className="flex-1">
                <div className="h-4 bg-separator-light rounded w-1/2 mb-2" />
                <div className="h-3 bg-separator-light rounded w-1/3" />
              </div>
            </div>
          ))}
        </div>
      </GlassCard>
    );
  }

  if (history.length === 0) {
    return (
      <GlassCard>
        <h2 className="text-lg font-semibold mb-4">Recently Promoted</h2>
        <p className="text-label-tertiary text-sm">
          No promotions yet. Approve patterns above to start building your L1 rule library.
        </p>
      </GlassCard>
    );
  }

  return (
    <GlassCard>
      <h2 className="text-lg font-semibold mb-4">Recently Promoted</h2>

      <div className="relative">
        {/* Timeline line */}
        <div className="absolute left-1.5 top-2 bottom-2 w-px bg-separator-light" />

        {/* Timeline items */}
        <div className="space-y-4">
          {history.map((item, index) => (
            <div key={item.id} className="flex gap-4 relative">
              {/* Timeline dot */}
              <div
                className={`w-3 h-3 rounded-full flex-shrink-0 mt-1.5 ${
                  index === 0 ? 'bg-accent-primary' : 'bg-separator-light'
                }`}
              />

              {/* Content */}
              <div className="flex-1 min-w-0">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="font-mono text-sm font-medium text-label-primary truncate">
                      {item.pattern_signature}
                    </p>
                    <p className="text-xs text-label-tertiary mt-0.5">
                      {formatDate(item.promoted_at)}
                    </p>
                  </div>

                  {/* Stats badge */}
                  <div className="flex items-center gap-2 flex-shrink-0">
                    <span className="px-2 py-0.5 text-xs font-medium bg-level-l1 text-white rounded">
                      {item.rule_id}
                    </span>
                  </div>
                </div>

                {/* Post-promotion stats */}
                <div className="flex items-center gap-3 mt-2 text-xs">
                  <div className="flex items-center gap-1">
                    <span
                      className={`w-2 h-2 rounded-full ${getSuccessColor(
                        item.post_promotion_success_rate
                      )}`}
                    />
                    <span className="text-label-tertiary">
                      {item.post_promotion_success_rate.toFixed(1)}% success
                    </span>
                  </div>
                  <div className="text-label-tertiary">
                    {item.executions_since_promotion} executions
                  </div>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </GlassCard>
  );
};

export default PromotionTimeline;
