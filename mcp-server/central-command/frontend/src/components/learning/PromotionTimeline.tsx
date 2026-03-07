import React, { useState, useMemo } from 'react';
import { GlassCard } from '../shared';
import type { PromotionHistory } from '../../types';

interface PromotionTimelineProps {
  history: PromotionHistory[];
  isLoading?: boolean;
}

type TimeFilter = '7d' | '30d' | 'all';

export const PromotionTimeline: React.FC<PromotionTimelineProps> = ({
  history,
  isLoading = false,
}) => {
  const [filter, setFilter] = useState<TimeFilter>('30d');

  const filtered = useMemo(() => {
    if (filter === 'all') return history;
    const days = filter === '7d' ? 7 : 30;
    const cutoff = Date.now() - days * 86400000;
    return history.filter((h) => new Date(h.promoted_at).getTime() > cutoff);
  }, [history, filter]);

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
    if (rate >= 80) return 'text-health-healthy';
    if (rate >= 50) return 'text-health-warning';
    return 'text-health-critical';
  };

  const getSuccessBg = (rate: number): string => {
    if (rate >= 80) return 'bg-health-healthy/10';
    if (rate >= 50) return 'bg-health-warning/10';
    return 'bg-health-critical/10';
  };

  if (isLoading) {
    return (
      <GlassCard>
        <h2 className="text-lg font-semibold mb-4">Recently Promoted</h2>
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <div key={i} className="animate-pulse rounded-ios-sm bg-separator-light/40 h-16" />
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
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-lg font-semibold">Recently Promoted</h2>
          <p className="text-xs text-label-tertiary mt-0.5">
            {filtered.length} promotion{filtered.length !== 1 ? 's' : ''}
          </p>
        </div>
        <div className="flex gap-1 bg-fill-quaternary rounded-ios-sm p-0.5">
          {(['7d', '30d', 'all'] as TimeFilter[]).map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`px-3 py-1 text-xs font-medium rounded-ios-sm transition-colors ${
                filter === f
                  ? 'bg-background-secondary text-label-primary shadow-sm'
                  : 'text-label-tertiary hover:text-label-secondary'
              }`}
            >
              {f === 'all' ? 'All' : f}
            </button>
          ))}
        </div>
      </div>

      {filtered.length === 0 ? (
        <p className="text-label-tertiary text-sm text-center py-6">
          No promotions in this time period.
        </p>
      ) : (
        <div className="space-y-2">
          {filtered.map((item) => (
            <div
              key={item.id}
              className="flex items-center gap-3 px-3 py-2.5 rounded-ios-sm bg-fill-quaternary hover:bg-fill-tertiary transition-colors"
            >
              {/* Pattern signature */}
              <div className="flex-1 min-w-0">
                <p className="font-mono text-sm font-medium text-label-primary break-all leading-snug">
                  {item.pattern_signature}
                </p>
                <p className="text-xs text-label-tertiary mt-1">
                  {formatDate(item.promoted_at)}
                </p>
              </div>

              {/* Stats */}
              <div className="flex items-center gap-3 flex-shrink-0">
                <div className={`px-2 py-1 rounded-ios-sm text-center ${getSuccessBg(item.post_promotion_success_rate)}`}>
                  <p className={`text-sm font-semibold ${getSuccessColor(item.post_promotion_success_rate)}`}>
                    {item.post_promotion_success_rate.toFixed(0)}%
                  </p>
                  <p className="text-[10px] text-label-tertiary leading-tight">
                    {item.executions_since_promotion} exec
                  </p>
                </div>

                <span className="px-2 py-1 text-[10px] font-mono font-medium bg-level-l1 text-white rounded-ios-sm max-w-[120px] truncate" title={item.rule_id}>
                  {item.rule_id}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </GlassCard>
  );
};

export default PromotionTimeline;
