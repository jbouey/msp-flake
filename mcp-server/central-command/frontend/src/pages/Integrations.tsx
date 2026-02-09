/**
 * Cloud Integrations Page
 *
 * Lists all cloud integrations for a site with health status,
 * resource counts, and sync information.
 */

import { useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import {
  useIntegrations,
  useIntegrationsHealth,
  useTriggerSync,
  useDeleteIntegration,
} from '../hooks/useIntegrations';
import {
  Integration,
  PROVIDER_INFO,
  IntegrationProvider,
} from '../utils/integrationsApi';

// Provider icons (simple SVG representations)
function ProviderIcon({ provider, size = 24 }: { provider: IntegrationProvider; size?: number }) {
  const info = PROVIDER_INFO[provider];
  return (
    <div
      className="flex items-center justify-center rounded-lg"
      style={{
        width: size + 8,
        height: size + 8,
        backgroundColor: info.color + '20',
      }}
    >
      <span
        className="font-bold text-sm"
        style={{ color: info.color }}
      >
        {provider === 'aws' ? 'AWS' : provider === 'google_workspace' ? 'G' : provider === 'okta' ? 'O' : provider === 'microsoft_security' ? '🛡' : 'M'}
      </span>
    </div>
  );
}

// Status badge component
function StatusBadge({ status }: { status: string }) {
  const statusConfig: Record<string, { label: string; color: string; bgColor: string }> = {
    active: { label: 'Active', color: '#16A34A', bgColor: '#DCFCE7' },
    connected: { label: 'Connected', color: '#16A34A', bgColor: '#DCFCE7' },
    pending_oauth: { label: 'Pending OAuth', color: '#CA8A04', bgColor: '#FEF3C7' },
    configuring: { label: 'Configuring', color: '#CA8A04', bgColor: '#FEF3C7' },
    pending: { label: 'Pending', color: '#6B7280', bgColor: '#F3F4F6' },
    error: { label: 'Error', color: '#DC2626', bgColor: '#FEE2E2' },
    paused: { label: 'Paused', color: '#6B7280', bgColor: '#F3F4F6' },
    disabled: { label: 'Disabled', color: '#6B7280', bgColor: '#F3F4F6' },
    disconnected: { label: 'Disconnected', color: '#9CA3AF', bgColor: '#F3F4F6' },
  };

  const config = statusConfig[status] || statusConfig.disconnected;

  return (
    <span
      className="px-2 py-1 text-xs font-medium rounded-full"
      style={{ color: config.color, backgroundColor: config.bgColor }}
    >
      {config.label}
    </span>
  );
}

// Health badge component
function HealthBadge({ health }: { health: { status: string; critical_count: number; high_count: number } }) {
  const healthConfig: Record<string, { label: string; color: string; bgColor: string }> = {
    healthy: { label: 'Healthy', color: '#16A34A', bgColor: '#DCFCE7' },
    warning: { label: 'Warning', color: '#CA8A04', bgColor: '#FEF3C7' },
    critical: { label: 'Critical', color: '#DC2626', bgColor: '#FEE2E2' },
    error: { label: 'Error', color: '#9CA3AF', bgColor: '#F3F4F6' },
  };

  const config = healthConfig[health.status] || healthConfig.error;

  return (
    <div className="flex items-center gap-2">
      <span
        className="px-2 py-1 text-xs font-medium rounded-full"
        style={{ color: config.color, backgroundColor: config.bgColor }}
      >
        {config.label}
      </span>
      {health.critical_count > 0 && (
        <span className="text-xs text-red-400">{health.critical_count} critical</span>
      )}
      {health.high_count > 0 && (
        <span className="text-xs text-orange-400">{health.high_count} high</span>
      )}
    </div>
  );
}

// Integration card component
function IntegrationCard({
  integration,
  siteId,
  onSync,
  onDelete,
  syncing,
  deleting,
}: {
  integration: Integration;
  siteId: string;
  onSync: () => void;
  onDelete: () => Promise<void>;
  syncing: boolean;
  deleting: boolean;
}) {
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);

  const handleDeleteClick = async () => {
    try {
      await onDelete();
      // Card will be removed from list on success
    } catch (err) {
      // Reset confirm state on error so user can try again
      setShowDeleteConfirm(false);
    }
  };
  const info = PROVIDER_INFO[integration.provider];

  const formatDate = (date: string | null) => {
    if (!date) return 'Never';
    return new Date(date).toLocaleString();
  };

  return (
    <div className="bg-slate-800 rounded-lg p-4 border border-slate-700 hover:border-slate-600 transition-colors">
      <div className="flex items-start justify-between mb-4">
        <div className="flex items-center gap-3">
          <ProviderIcon provider={integration.provider} />
          <div>
            <h3 className="text-lg font-semibold text-white">{integration.name}</h3>
            <p className="text-sm text-slate-400">{info.name}</p>
          </div>
        </div>
        <StatusBadge status={integration.status} />
      </div>

      <div className="grid grid-cols-2 gap-4 mb-4">
        <div>
          <p className="text-xs text-slate-500 uppercase">Resources</p>
          <p className="text-lg font-semibold text-white">{integration.resource_count}</p>
        </div>
        <div>
          <p className="text-xs text-slate-500 uppercase">Health</p>
          <HealthBadge health={integration.health} />
        </div>
        <div>
          <p className="text-xs text-slate-500 uppercase">Last Sync</p>
          <p className="text-sm text-slate-300">{formatDate(integration.last_sync)}</p>
        </div>
        <div>
          <p className="text-xs text-slate-500 uppercase">Next Sync</p>
          <p className="text-sm text-slate-300">{formatDate(integration.next_sync)}</p>
        </div>
      </div>

      {integration.health.last_error && (
        <div className="mb-4 p-2 bg-red-900/20 border border-red-800 rounded text-sm text-red-400">
          {integration.health.last_error}
        </div>
      )}

      <div className="flex items-center gap-2 pt-4 border-t border-slate-700">
        <Link
          to={`/sites/${siteId}/integrations/${integration.id}`}
          className="px-3 py-1.5 text-sm bg-blue-600 text-white rounded hover:bg-blue-700 transition-colors"
        >
          View Resources
        </Link>
        <button
          onClick={onSync}
          disabled={syncing || !['active', 'connected', 'error'].includes(integration.status)}
          className="px-3 py-1.5 text-sm bg-slate-700 text-white rounded hover:bg-slate-600 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {syncing ? 'Syncing...' : 'Sync Now'}
        </button>
        {!showDeleteConfirm ? (
          <button
            onClick={() => setShowDeleteConfirm(true)}
            disabled={deleting}
            className="px-3 py-1.5 text-sm text-red-400 hover:text-red-300 transition-colors ml-auto disabled:opacity-50"
          >
            Delete
          </button>
        ) : (
          <div className="flex items-center gap-2 ml-auto">
            <span className="text-sm text-slate-400">Delete?</span>
            <button
              onClick={handleDeleteClick}
              disabled={deleting}
              className="px-2 py-1 text-sm bg-red-600 text-white rounded hover:bg-red-700 disabled:opacity-50"
            >
              {deleting ? 'Deleting...' : 'Yes'}
            </button>
            <button
              onClick={() => setShowDeleteConfirm(false)}
              disabled={deleting}
              className="px-2 py-1 text-sm bg-blue-600 text-white rounded hover:bg-blue-500 disabled:opacity-50"
            >
              No
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

// Health overview component
function HealthOverview({ siteId }: { siteId: string }) {
  const { data: health, isLoading } = useIntegrationsHealth(siteId);

  if (isLoading || !health) return null;

  const statusColors: Record<string, string> = {
    healthy: 'text-green-400',
    warning: 'text-yellow-400',
    critical: 'text-red-400',
  };

  return (
    <div className="bg-slate-800 rounded-lg p-4 border border-slate-700 mb-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <div>
            <p className="text-sm text-slate-400">Overall Status</p>
            <p className={`text-xl font-bold ${statusColors[health.overall_status] || 'text-slate-400'}`}>
              {health.overall_status.charAt(0).toUpperCase() + health.overall_status.slice(1)}
            </p>
          </div>
          <div className="h-10 w-px bg-slate-700" />
          <div>
            <p className="text-sm text-slate-400">Integrations</p>
            <p className="text-xl font-bold text-white">{health.total_integrations}</p>
          </div>
        </div>
        <div className="flex items-center gap-6">
          {health.total_critical > 0 && (
            <div className="text-center">
              <p className="text-2xl font-bold text-red-400">{health.total_critical}</p>
              <p className="text-xs text-slate-400">Critical</p>
            </div>
          )}
          {health.total_high > 0 && (
            <div className="text-center">
              <p className="text-2xl font-bold text-orange-400">{health.total_high}</p>
              <p className="text-xs text-slate-400">High</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// Empty state component
function EmptyState({ siteId }: { siteId: string }) {
  return (
    <div className="bg-slate-800 rounded-lg p-8 border border-slate-700 text-center">
      <svg
        className="mx-auto h-12 w-12 text-slate-500"
        fill="none"
        viewBox="0 0 24 24"
        stroke="currentColor"
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth={1.5}
          d="M3.055 11H5a2 2 0 012 2v1a2 2 0 002 2 2 2 0 012 2v2.945M8 3.935V5.5A2.5 2.5 0 0010.5 8h.5a2 2 0 012 2 2 2 0 104 0 2 2 0 012-2h1.064M15 20.488V18a2 2 0 012-2h3.064M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
        />
      </svg>
      <h3 className="mt-4 text-lg font-medium text-white">No cloud integrations</h3>
      <p className="mt-2 text-slate-400">
        Connect your cloud accounts to collect compliance evidence automatically.
      </p>
      <Link
        to={`/sites/${siteId}/integrations/setup`}
        className="mt-4 inline-flex items-center px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors"
      >
        <svg className="w-5 h-5 mr-2" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
        </svg>
        Add Integration
      </Link>
    </div>
  );
}

// Main component
export default function Integrations() {
  const { siteId } = useParams<{ siteId: string }>();
  const [syncingId, setSyncingId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const { data: integrations, isLoading, error } = useIntegrations(siteId || null);
  const triggerSync = useTriggerSync();
  const deleteIntegration = useDeleteIntegration();

  if (!siteId) {
    return (
      <div className="p-6 text-center text-slate-400">
        Please select a site to view integrations.
      </div>
    );
  }

  const handleSync = async (integrationId: string) => {
    setSyncingId(integrationId);
    try {
      await triggerSync.mutateAsync({ siteId, integrationId });
    } catch (err) {
      console.error('Sync failed:', err);
    } finally {
      setSyncingId(null);
    }
  };

  const handleDelete = async (integrationId: string) => {
    setDeletingId(integrationId);
    try {
      await deleteIntegration.mutateAsync({ siteId, integrationId });
    } catch (err) {
      console.error('Delete failed:', err);
      throw err; // Re-throw so IntegrationCard can handle it
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <div className="min-h-screen bg-slate-900 p-6">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <div className="flex items-center gap-2 text-sm text-slate-400 mb-1">
            <Link to="/sites" className="hover:text-white">Sites</Link>
            <span>/</span>
            <Link to={`/sites/${siteId}`} className="hover:text-white">{siteId}</Link>
            <span>/</span>
            <span className="text-white">Integrations</span>
          </div>
          <h1 className="text-2xl font-bold text-white">Cloud Integrations</h1>
          <p className="text-slate-400 mt-1">
            Connect cloud accounts to collect compliance evidence
          </p>
        </div>
        <Link
          to={`/sites/${siteId}/integrations/setup`}
          className="inline-flex items-center px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors"
        >
          <svg className="w-5 h-5 mr-2" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
          </svg>
          Add Integration
        </Link>
      </div>

      {/* Health overview */}
      <HealthOverview siteId={siteId} />

      {/* Error state */}
      {error && (
        <div className="bg-red-900/20 border border-red-800 rounded-lg p-4 mb-6">
          <p className="text-red-400">Failed to load integrations: {(error as Error).message}</p>
        </div>
      )}

      {/* Loading state */}
      {isLoading && (
        <div className="flex items-center justify-center p-12">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500"></div>
        </div>
      )}

      {/* Empty state */}
      {!isLoading && integrations?.length === 0 && <EmptyState siteId={siteId} />}

      {/* Integration cards */}
      {!isLoading && integrations && integrations.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {integrations.map((integration) => (
            <IntegrationCard
              key={integration.id}
              integration={integration}
              siteId={siteId}
              onSync={() => handleSync(integration.id)}
              onDelete={() => handleDelete(integration.id)}
              syncing={syncingId === integration.id}
              deleting={deletingId === integration.id}
            />
          ))}
        </div>
      )}

      {/* Provider info */}
      <div className="mt-8 pt-8 border-t border-slate-800">
        <h2 className="text-lg font-semibold text-white mb-4">Supported Providers</h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          {Object.entries(PROVIDER_INFO).map(([provider, info]) => (
            <div key={provider} className="bg-slate-800/50 rounded-lg p-4">
              <div className="flex items-center gap-2 mb-2">
                <ProviderIcon provider={provider as IntegrationProvider} size={20} />
                <span className="font-medium text-white">{info.name}</span>
              </div>
              <p className="text-xs text-slate-400">{info.description}</p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
