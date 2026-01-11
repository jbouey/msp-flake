import React from 'react';
import { GlassCard, SkeletonText } from '../shared';
import type { ComplianceEvent } from '../../types';

interface EventFeedProps {
  events: ComplianceEvent[];
  isLoading?: boolean;
  error?: Error | null;
  title?: string;
  compact?: boolean;
  limit?: number;
}

const outcomeColors: Record<string, string> = {
  pass: 'bg-green-100 text-green-800',
  fail: 'bg-red-100 text-red-800',
  warning: 'bg-yellow-100 text-yellow-800',
  error: 'bg-red-200 text-red-900',
  unknown: 'bg-gray-100 text-gray-600',
};

const formatTimeAgo = (dateStr: string): string => {
  const date = new Date(dateStr);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMins / 60);
  const diffDays = Math.floor(diffHours / 24);

  if (diffMins < 1) return 'just now';
  if (diffMins < 60) return `${diffMins}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;
  return `${diffDays}d ago`;
};

export const EventFeed: React.FC<EventFeedProps> = ({
  events,
  isLoading = false,
  error = null,
  title = 'Recent Activity',
  compact = false,
  limit,
}) => {
  const displayEvents = limit ? events.slice(0, limit) : events;

  const passCount = events.filter(e => e.outcome === 'pass').length;
  const failCount = events.filter(e => e.outcome !== 'pass').length;

  return (
    <GlassCard padding={compact ? 'md' : 'lg'}>
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <h2 className="text-lg font-semibold text-label-primary">{title}</h2>
          {!isLoading && events.length > 0 && (
            <div className="flex items-center gap-2 text-xs">
              {failCount > 0 && (
                <span className="px-2 py-0.5 bg-red-100 text-red-700 rounded-full">
                  {failCount} issues
                </span>
              )}
              <span className="text-label-tertiary">
                {passCount} passing
              </span>
            </div>
          )}
        </div>
      </div>

      {error && (
        <div className="text-center py-4">
          <p className="text-health-critical text-sm">{error.message}</p>
        </div>
      )}

      {isLoading && (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <div key={i} className="flex items-center gap-4 p-3">
              <SkeletonText width="60px" height="12px" />
              <SkeletonText width="120px" height="14px" />
              <SkeletonText width="80px" height="12px" />
              <SkeletonText width="50px" height="20px" />
            </div>
          ))}
        </div>
      )}

      {!isLoading && !error && events.length === 0 && (
        <div className="text-center py-8">
          <div className="text-label-tertiary mb-2">
            <svg className="w-10 h-10 mx-auto" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
          </div>
          <p className="text-label-secondary text-sm">No recent activity</p>
          <p className="text-label-tertiary text-xs mt-1">Waiting for appliance check-ins</p>
        </div>
      )}

      {!isLoading && !error && events.length > 0 && (
        <div className={compact ? 'space-y-1' : 'space-y-2'}>
          {displayEvents.map((event) => (
            <div
              key={event.id}
              className="flex items-center justify-between p-3 rounded-lg bg-fill-tertiary/50 hover:bg-fill-tertiary transition-colors"
            >
              <div className="flex items-center gap-3 min-w-0 flex-1">
                <span className={`px-2 py-0.5 text-xs rounded-full ${outcomeColors[event.outcome] || outcomeColors.unknown}`}>
                  {event.outcome}
                </span>
                <div className="min-w-0">
                  <p className="text-sm font-medium text-label-primary truncate">
                    {event.check_name || event.check_type}
                  </p>
                  <p className="text-xs text-label-tertiary truncate">
                    {event.site_id}
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-2 flex-shrink-0">
                {event.resolution_level && (
                  <span className="px-1.5 py-0.5 text-xs bg-blue-100 text-blue-700 rounded">
                    {event.resolution_level}
                  </span>
                )}
                <span className="text-xs text-label-tertiary whitespace-nowrap">
                  {formatTimeAgo(event.created_at)}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}

      {limit && events.length > limit && (
        <div className="mt-4 pt-3 border-t border-separator-light text-center">
          <span className="text-sm text-label-tertiary">
            +{events.length - limit} more events
          </span>
        </div>
      )}
    </GlassCard>
  );
};

export default EventFeed;
