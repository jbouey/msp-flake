/**
 * Integration Resources Page
 *
 * Displays resources collected from a cloud integration with:
 * - Compliance check results
 * - Risk level indicators
 * - Resource filtering by type and risk
 * - Sync status and manual sync trigger
 */

import { useState, useEffect } from 'react';
import { useParams, Link } from 'react-router-dom';
import {
  useIntegration,
  useIntegrationResources,
  useTriggerSync,
  useSyncJob,
  useRefreshResources,
} from '../hooks/useIntegrations';
import {
  IntegrationResource,
  RiskLevel,
  PROVIDER_INFO,
  RISK_LEVEL_CONFIG,
  RESOURCE_TYPE_LABELS,
  IntegrationProvider,
} from '../utils/integrationsApi';

// Risk level badge
function RiskBadge({ level }: { level: RiskLevel | null | undefined }) {
  const effectiveLevel = level || 'unknown';
  const config = RISK_LEVEL_CONFIG[effectiveLevel] || RISK_LEVEL_CONFIG.unknown;
  return (
    <span
      className="px-2 py-0.5 text-xs font-medium rounded"
      style={{ color: config.color, backgroundColor: config.bgColor }}
    >
      {config.label}
    </span>
  );
}

// Compliance check status icon
function CheckStatusIcon({ status }: { status: string }) {
  const icons: Record<string, { icon: string; color: string }> = {
    pass: { icon: '✓', color: 'text-green-400' },
    fail: { icon: '✗', color: 'text-red-400' },
    critical: { icon: '!', color: 'text-red-500' },
    warning: { icon: '⚠', color: 'text-yellow-400' },
    info: { icon: 'i', color: 'text-blue-400' },
  };

  const { icon, color } = icons[status] || icons.info;
  return <span className={`font-bold ${color}`}>{icon}</span>;
}

// Resource card component
function ResourceCard({ resource }: { resource: IntegrationResource }) {
  const [expanded, setExpanded] = useState(false);
  const typeLabel = RESOURCE_TYPE_LABELS[resource.resource_type] || resource.resource_type;
  const checks = resource.compliance_checks || [];
  const failingChecks = checks.filter((c) => c.status === 'fail' || c.status === 'critical');
  const warningChecks = checks.filter((c) => c.status === 'warning');

  return (
    <div className="bg-gray-800 rounded-lg border border-gray-700">
      <div
        className="p-4 cursor-pointer hover:bg-gray-750 transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex items-start justify-between">
          <div className="flex-1">
            <div className="flex items-center gap-2 mb-1">
              <span className="text-xs text-gray-500 uppercase">{typeLabel}</span>
              <RiskBadge level={resource.risk_level} />
            </div>
            <h3 className="font-medium text-white">{resource.name}</h3>
            <p className="text-xs text-gray-500 font-mono mt-1">{resource.resource_id}</p>
          </div>
          <div className="flex items-center gap-4 text-sm">
            {failingChecks.length > 0 && (
              <span className="text-red-400">{failingChecks.length} failing</span>
            )}
            {warningChecks.length > 0 && (
              <span className="text-yellow-400">{warningChecks.length} warnings</span>
            )}
            <svg
              className={`w-5 h-5 text-gray-400 transition-transform ${expanded ? 'rotate-180' : ''}`}
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
            >
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
            </svg>
          </div>
        </div>
      </div>

      {expanded && (
        <div className="px-4 pb-4 border-t border-gray-700 pt-4">
          <h4 className="text-sm font-medium text-gray-300 mb-3">Compliance Checks</h4>
          <div className="space-y-2">
            {Object.entries(resource.compliance_checks).map(([key, check]) => (
              <div
                key={key}
                className={`p-3 rounded-lg ${
                  check.status === 'fail' || check.status === 'critical'
                    ? 'bg-red-900/20 border border-red-800'
                    : check.status === 'warning'
                    ? 'bg-yellow-900/20 border border-yellow-800'
                    : 'bg-gray-900/50 border border-gray-700'
                }`}
              >
                <div className="flex items-start gap-2">
                  <CheckStatusIcon status={check.status} />
                  <div className="flex-1">
                    <div className="flex items-center justify-between">
                      <span className="font-medium text-white">{check.check}</span>
                      <span className="text-xs text-gray-500">{check.control}</span>
                    </div>
                    <p className="text-sm text-gray-400 mt-1">{check.description}</p>
                    {check.details && (
                      <p className="text-xs text-gray-500 mt-1">{check.details}</p>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// Sync status banner
function SyncBanner({
  lastSync,
  onSync,
  syncing,
}: {
  lastSync: string | null;
  onSync: () => void;
  syncing: boolean;
}) {
  const formatDate = (date: string | null) => {
    if (!date) return 'Never';
    return new Date(date).toLocaleString();
  };

  return (
    <div className="bg-gray-800 rounded-lg p-4 border border-gray-700 flex items-center justify-between">
      <div>
        <p className="text-sm text-gray-400">Last synchronized</p>
        <p className="text-white font-medium">{formatDate(lastSync)}</p>
      </div>
      <button
        onClick={onSync}
        disabled={syncing}
        className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
      >
        {syncing ? (
          <>
            <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24">
              <circle
                className="opacity-25"
                cx="12"
                cy="12"
                r="10"
                stroke="currentColor"
                strokeWidth="4"
                fill="none"
              />
              <path
                className="opacity-75"
                fill="currentColor"
                d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
              />
            </svg>
            Syncing...
          </>
        ) : (
          <>
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
              />
            </svg>
            Sync Now
          </>
        )}
      </button>
    </div>
  );
}

// Filter controls
function FilterControls({
  resourceTypes,
  selectedType,
  onTypeChange,
  selectedRisk,
  onRiskChange,
}: {
  resourceTypes: string[];
  selectedType: string | null;
  onTypeChange: (type: string | null) => void;
  selectedRisk: RiskLevel | null;
  onRiskChange: (risk: RiskLevel | null) => void;
}) {
  return (
    <div className="flex flex-wrap gap-4 mb-4">
      <div>
        <label className="block text-xs text-gray-400 mb-1">Resource Type</label>
        <select
          value={selectedType || ''}
          onChange={(e) => onTypeChange(e.target.value || null)}
          className="px-3 py-1.5 bg-gray-700 border border-gray-600 rounded text-white text-sm"
        >
          <option value="">All Types</option>
          {resourceTypes.map((type) => (
            <option key={type} value={type}>
              {RESOURCE_TYPE_LABELS[type] || type}
            </option>
          ))}
        </select>
      </div>

      <div>
        <label className="block text-xs text-gray-400 mb-1">Risk Level</label>
        <select
          value={selectedRisk || ''}
          onChange={(e) => onRiskChange((e.target.value as RiskLevel) || null)}
          className="px-3 py-1.5 bg-gray-700 border border-gray-600 rounded text-white text-sm"
        >
          <option value="">All Levels</option>
          {(Object.keys(RISK_LEVEL_CONFIG) as RiskLevel[]).map((level) => (
            <option key={level} value={level}>
              {RISK_LEVEL_CONFIG[level].label}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
}

// Stats summary
function StatsSummary({
  total,
  byRisk,
}: {
  total: number;
  byRisk: Record<RiskLevel, number>;
}) {
  return (
    <div className="grid grid-cols-2 md:grid-cols-5 gap-4 mb-6">
      <div className="bg-gray-800 rounded-lg p-4 border border-gray-700">
        <p className="text-2xl font-bold text-white">{total}</p>
        <p className="text-sm text-gray-400">Total Resources</p>
      </div>
      {(['critical', 'high', 'medium', 'low'] as RiskLevel[]).map((level) => (
        <div
          key={level}
          className="bg-gray-800 rounded-lg p-4 border border-gray-700"
          style={{ borderLeftColor: RISK_LEVEL_CONFIG[level].color, borderLeftWidth: 3 }}
        >
          <p className="text-2xl font-bold" style={{ color: RISK_LEVEL_CONFIG[level].color }}>
            {byRisk[level] || 0}
          </p>
          <p className="text-sm text-gray-400">{RISK_LEVEL_CONFIG[level].label}</p>
        </div>
      ))}
    </div>
  );
}

// Main component
export default function IntegrationResources() {
  const { siteId, integrationId } = useParams<{ siteId: string; integrationId: string }>();
  const [selectedType, setSelectedType] = useState<string | null>(null);
  const [selectedRisk, setSelectedRisk] = useState<RiskLevel | null>(null);
  const [page, setPage] = useState(0);
  const [syncJobId, setSyncJobId] = useState<string | null>(null);
  const pageSize = 50;

  const { data: integration, isLoading: loadingIntegration } = useIntegration(
    siteId || null,
    integrationId || null
  );

  const { data: resourcesData, isLoading: loadingResources } = useIntegrationResources(
    siteId || null,
    integrationId || null,
    {
      resource_type: selectedType || undefined,
      risk_level: selectedRisk || undefined,
      limit: pageSize,
      offset: page * pageSize,
    }
  );

  const triggerSync = useTriggerSync();
  const refreshResources = useRefreshResources();

  // Poll sync job if running
  const { data: syncJob } = useSyncJob(
    siteId || null,
    integrationId || null,
    syncJobId
  );

  // When sync completes, refresh resources
  useEffect(() => {
    if (syncJob && ['completed', 'failed', 'timeout'].includes(syncJob.status)) {
      setSyncJobId(null);
      if (siteId && integrationId) {
        refreshResources(siteId, integrationId);
      }
    }
  }, [syncJob?.status]);

  if (!siteId || !integrationId) {
    return <div className="p-6 text-gray-400">Missing site or integration ID</div>;
  }

  const handleSync = async () => {
    try {
      const result = await triggerSync.mutateAsync({ siteId, integrationId });
      setSyncJobId(result.job_id);
    } catch (err) {
      console.error('Sync failed:', err);
    }
  };

  // Calculate stats
  const resources = resourcesData?.resources || [];
  const total = resourcesData?.total || 0;
  const byRisk: Record<RiskLevel, number> = {
    critical: 0,
    high: 0,
    medium: 0,
    low: 0,
    unknown: 0,
  };
  resources.forEach((r) => {
    const level = r.risk_level || 'unknown';
    byRisk[level] = (byRisk[level] || 0) + 1;
  });

  // Get unique resource types
  const resourceTypes = Array.from(new Set(resources.map((r) => r.resource_type)));

  const providerInfo = integration
    ? PROVIDER_INFO[integration.provider as IntegrationProvider]
    : null;

  const syncing = syncJobId !== null && syncJob?.status === 'running';

  return (
    <div className="min-h-screen bg-gray-900 p-6">
      {/* Header */}
      <div className="mb-6">
        <div className="flex items-center gap-2 text-sm text-gray-400 mb-1">
          <Link to="/sites" className="hover:text-white">Sites</Link>
          <span>/</span>
          <Link to={`/sites/${siteId}`} className="hover:text-white">{siteId}</Link>
          <span>/</span>
          <Link to={`/sites/${siteId}/integrations`} className="hover:text-white">Integrations</Link>
          <span>/</span>
          <span className="text-white">{integration?.name || integrationId}</span>
        </div>
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-white flex items-center gap-3">
              {integration?.name || 'Loading...'}
              {providerInfo && (
                <span
                  className="text-sm font-normal px-2 py-0.5 rounded"
                  style={{ backgroundColor: providerInfo.color + '20', color: providerInfo.color }}
                >
                  {providerInfo.name}
                </span>
              )}
            </h1>
            <p className="text-gray-400 mt-1">
              {total} resources collected
            </p>
          </div>
          <Link
            to={`/sites/${siteId}/integrations`}
            className="text-gray-400 hover:text-white flex items-center gap-1"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
            </svg>
            Back to Integrations
          </Link>
        </div>
      </div>

      {/* Sync banner */}
      {integration && (
        <div className="mb-6">
          <SyncBanner
            lastSync={integration.last_sync}
            onSync={handleSync}
            syncing={syncing}
          />
        </div>
      )}

      {/* Stats summary */}
      <StatsSummary total={total} byRisk={byRisk} />

      {/* Filters */}
      <FilterControls
        resourceTypes={resourceTypes}
        selectedType={selectedType}
        onTypeChange={(type) => {
          setSelectedType(type);
          setPage(0);
        }}
        selectedRisk={selectedRisk}
        onRiskChange={(risk) => {
          setSelectedRisk(risk);
          setPage(0);
        }}
      />

      {/* Loading state */}
      {(loadingIntegration || loadingResources) && (
        <div className="flex items-center justify-center p-12">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500"></div>
        </div>
      )}

      {/* Resources list */}
      {!loadingResources && resources.length > 0 && (
        <div className="space-y-3">
          {resources.map((resource) => (
            <ResourceCard key={resource.id} resource={resource} />
          ))}
        </div>
      )}

      {/* Empty state */}
      {!loadingResources && resources.length === 0 && (
        <div className="bg-gray-800 rounded-lg p-8 border border-gray-700 text-center">
          <p className="text-gray-400">
            {selectedType || selectedRisk
              ? 'No resources match the current filters'
              : 'No resources have been collected yet'}
          </p>
          {!selectedType && !selectedRisk && (
            <button
              onClick={handleSync}
              disabled={syncing}
              className="mt-4 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
            >
              Run Initial Sync
            </button>
          )}
        </div>
      )}

      {/* Pagination */}
      {total > pageSize && (
        <div className="mt-6 flex items-center justify-between">
          <p className="text-sm text-gray-400">
            Showing {page * pageSize + 1} - {Math.min((page + 1) * pageSize, total)} of {total}
          </p>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={page === 0}
              className="px-3 py-1 bg-gray-700 text-white rounded hover:bg-gray-600 disabled:opacity-50"
            >
              Previous
            </button>
            <button
              onClick={() => setPage((p) => p + 1)}
              disabled={(page + 1) * pageSize >= total}
              className="px-3 py-1 bg-gray-700 text-white rounded hover:bg-gray-600 disabled:opacity-50"
            >
              Next
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
