import React from 'react';
import { useNavigate } from 'react-router-dom';
import { GlassCard, SkeletonText } from '../shared';
import { IncidentRow } from './IncidentRow';
import type { Incident } from '../../types';

interface IncidentFeedProps {
  incidents: Incident[];
  isLoading?: boolean;
  error?: Error | null;
  title?: string;
  showViewAll?: boolean;
  compact?: boolean;
  limit?: number;
}

export const IncidentFeed: React.FC<IncidentFeedProps> = ({
  incidents,
  isLoading = false,
  error = null,
  title = 'Recent Incidents',
  showViewAll = true,
  compact = false,
  limit,
}) => {
  const navigate = useNavigate();

  const displayIncidents = limit ? incidents.slice(0, limit) : incidents;

  const activeCount = incidents.filter(i => !i.resolved).length;
  const resolvedCount = incidents.filter(i => i.resolved).length;

  return (
    <GlassCard padding={compact ? 'md' : 'lg'}>
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <h2 className="text-lg font-semibold text-label-primary">{title}</h2>
          {!isLoading && incidents.length > 0 && (
            <div className="flex items-center gap-2 text-xs">
              {activeCount > 0 && (
                <span className="px-2 py-0.5 bg-orange-100 text-health-warning rounded-full">
                  {activeCount} active
                </span>
              )}
              <span className="text-label-tertiary">
                {resolvedCount} resolved
              </span>
            </div>
          )}
        </div>
        {showViewAll && incidents.length > 0 && (
          <button
            onClick={() => navigate('/incidents')}
            className="text-sm text-accent-primary hover:underline"
          >
            View All
          </button>
        )}
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

      {!isLoading && !error && incidents.length === 0 && (
        <div className="text-center py-8">
          <div className="text-health-healthy mb-2">
            <svg className="w-10 h-10 mx-auto" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
          </div>
          <p className="text-label-secondary text-sm">No recent incidents</p>
          <p className="text-label-tertiary text-xs mt-1">All systems operating normally</p>
        </div>
      )}

      {!isLoading && !error && incidents.length > 0 && (
        <div className={compact ? 'space-y-1' : 'space-y-2'}>
          {displayIncidents.map((incident) => (
            <IncidentRow
              key={incident.id}
              incident={incident}
              compact={compact}
              onClick={() => console.log('View incident', incident.id)}
            />
          ))}
        </div>
      )}

      {limit && incidents.length > limit && (
        <div className="mt-4 pt-3 border-t border-separator-light text-center">
          <button
            onClick={() => navigate('/incidents')}
            className="text-sm text-accent-primary hover:underline"
          >
            +{incidents.length - limit} more incidents
          </button>
        </div>
      )}
    </GlassCard>
  );
};

export default IncidentFeed;
