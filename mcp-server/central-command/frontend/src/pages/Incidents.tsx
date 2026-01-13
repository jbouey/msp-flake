import React, { useState } from 'react';
import { GlassCard } from '../components/shared';
import { IncidentRow } from '../components/incidents/IncidentRow';
import { useIncidents } from '../hooks';
import type { Incident } from '../types';

export const Incidents: React.FC = () => {
  const [filter, setFilter] = useState<'all' | 'active' | 'resolved'>('all');
  const { data: incidents = [], isLoading, error } = useIncidents({ limit: 100 });

  const filteredIncidents = incidents.filter((incident: Incident) => {
    if (filter === 'active') return !incident.resolved;
    if (filter === 'resolved') return incident.resolved;
    return true;
  });

  const activeCount = incidents.filter((i: Incident) => !i.resolved).length;
  const resolvedCount = incidents.filter((i: Incident) => i.resolved).length;

  return (
    <div className="space-y-6">
      <GlassCard>
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-xl font-semibold text-label-primary">All Incidents</h1>
            <p className="text-sm text-label-tertiary mt-1">
              {incidents.length} total incidents
            </p>
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={() => setFilter('all')}
              className={`px-3 py-1.5 text-sm rounded-ios-md transition-colors ${
                filter === 'all'
                  ? 'bg-accent-primary text-white'
                  : 'bg-fill-tertiary text-label-secondary hover:bg-fill-secondary'
              }`}
            >
              All ({incidents.length})
            </button>
            <button
              onClick={() => setFilter('active')}
              className={`px-3 py-1.5 text-sm rounded-ios-md transition-colors ${
                filter === 'active'
                  ? 'bg-health-warning text-white'
                  : 'bg-fill-tertiary text-label-secondary hover:bg-fill-secondary'
              }`}
            >
              Active ({activeCount})
            </button>
            <button
              onClick={() => setFilter('resolved')}
              className={`px-3 py-1.5 text-sm rounded-ios-md transition-colors ${
                filter === 'resolved'
                  ? 'bg-health-healthy text-white'
                  : 'bg-fill-tertiary text-label-secondary hover:bg-fill-secondary'
              }`}
            >
              Resolved ({resolvedCount})
            </button>
          </div>
        </div>

        {error && (
          <div className="text-center py-8">
            <p className="text-health-critical">{error.message}</p>
          </div>
        )}

        {isLoading && (
          <div className="text-center py-8">
            <div className="w-8 h-8 border-2 border-accent-primary border-t-transparent rounded-full animate-spin mx-auto mb-4" />
            <p className="text-label-tertiary">Loading incidents...</p>
          </div>
        )}

        {!isLoading && !error && filteredIncidents.length === 0 && (
          <div className="text-center py-8">
            <div className="text-health-healthy mb-2">
              <svg className="w-12 h-12 mx-auto" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            </div>
            <p className="text-label-secondary">No incidents to display</p>
            <p className="text-label-tertiary text-sm mt-1">
              {filter !== 'all' ? 'Try adjusting your filter' : 'All systems operating normally'}
            </p>
          </div>
        )}

        {!isLoading && !error && filteredIncidents.length > 0 && (
          <div className="space-y-2">
            {filteredIncidents.map((incident: Incident) => (
              <IncidentRow
                key={incident.id}
                incident={incident}
                compact={false}
                onClick={() => console.log('View incident', incident.id)}
              />
            ))}
          </div>
        )}
      </GlassCard>
    </div>
  );
};

export default Incidents;
