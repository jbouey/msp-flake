import React from 'react';
import { ClientCard } from './ClientCard';
import { SkeletonCard } from '../shared';
import type { ClientOverview } from '../../types';

interface FleetOverviewProps {
  clients: ClientOverview[];
  isLoading?: boolean;
  error?: Error | null;
}

export const FleetOverview: React.FC<FleetOverviewProps> = ({
  clients,
  isLoading = false,
  error = null,
}) => {
  if (error) {
    return (
      <div className="glass-card p-6 text-center">
        <div className="text-health-critical mb-2">
          <svg className="w-12 h-12 mx-auto" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
          </svg>
        </div>
        <h3 className="font-semibold text-label-primary">Failed to load fleet data</h3>
        <p className="text-sm text-label-tertiary mt-1">{error.message}</p>
      </div>
    );
  }

  if (isLoading) {
    return (
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {[1, 2, 3].map((i) => (
          <SkeletonCard key={i} lines={2} />
        ))}
      </div>
    );
  }

  if (clients.length === 0) {
    return (
      <div className="glass-card p-8 text-center">
        <div className="text-label-tertiary mb-2">
          <svg className="w-12 h-12 mx-auto" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
          </svg>
        </div>
        <h3 className="font-semibold text-label-primary">No clients yet</h3>
        <p className="text-sm text-label-tertiary mt-1">
          Clients will appear here once appliances start checking in.
        </p>
      </div>
    );
  }

  // Sort clients: critical first, then warning, then healthy
  const sortedClients = [...clients].sort((a, b) => {
    const statusOrder = { critical: 0, warning: 1, healthy: 2 };
    return statusOrder[a.health.status] - statusOrder[b.health.status];
  });

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
      {sortedClients.map((client) => (
        <ClientCard key={client.site_id} client={client} />
      ))}
    </div>
  );
};

export default FleetOverview;
