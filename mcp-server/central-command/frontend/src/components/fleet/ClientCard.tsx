import React, { memo, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { GlassCard } from '../shared';
import { HealthGauge } from './HealthGauge';
import type { ClientOverview } from '../../types';

interface ClientCardProps {
  client: ClientOverview;
}

export const ClientCard: React.FC<ClientCardProps> = memo(({ client }) => {
  const navigate = useNavigate();

  const handleClick = useCallback(() => {
    navigate(`/client/${client.site_id}`);
  }, [navigate, client.site_id]);

  return (
    <GlassCard hover onClick={handleClick} padding="md">
      <div className="flex items-start justify-between">
        <div className="flex-1 min-w-0">
          <h3 className="font-semibold text-label-primary truncate">
            {client.name}
          </h3>
          <p className="text-sm text-label-tertiary mt-1">
            {client.online_count}/{client.appliance_count} appliances online
          </p>
          {client.incidents_24h > 0 && (
            <p className="text-xs text-health-warning mt-2">
              {client.incidents_24h} incident{client.incidents_24h !== 1 ? 's' : ''} (24h)
            </p>
          )}
        </div>
        <HealthGauge score={client.health.overall} size="md" />
      </div>
    </GlassCard>
  );
});

// Compact variant for sidebar or lists
export const ClientCardCompact: React.FC<ClientCardProps & { selected?: boolean; onSelect?: () => void }> = memo(({
  client,
  selected = false,
  onSelect,
}) => {
  return (
    <button
      onClick={onSelect}
      className={`
        w-full flex items-center gap-3 p-3 rounded-ios-md text-left
        transition-all duration-150
        ${selected
          ? 'bg-accent-tint border border-accent-primary'
          : 'bg-white/50 hover:bg-white/80 border border-transparent'
        }
      `}
    >
      <HealthGauge score={client.health.overall} size="sm" showLabel={false} />
      <div className="flex-1 min-w-0">
        <p className={`font-medium truncate ${selected ? 'text-accent-primary' : 'text-label-primary'}`}>
          {client.name}
        </p>
        <p className="text-xs text-label-tertiary">
          {client.online_count}/{client.appliance_count} online
        </p>
      </div>
      {client.incidents_24h > 0 && (
        <span className="text-xs text-health-warning font-medium">
          {client.incidents_24h}
        </span>
      )}
    </button>
  );
});

export default ClientCard;
