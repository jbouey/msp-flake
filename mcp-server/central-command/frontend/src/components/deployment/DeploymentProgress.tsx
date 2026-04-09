import React from 'react';
import { GlassCard, Spinner, Badge } from '../shared';
import { useDeploymentStatus } from '../../hooks/useDeployment';
import { formatTimeAgo } from '../../constants';
import type { SiteDetail } from '../../utils/api';

interface DeploymentProgressProps {
  siteId: string;
  /**
   * Optional site record used to detect mature deployments.
   * When the site already has scan/baseline/active timestamps we collapse the
   * progress card to a compact "Deployment Complete" summary instead of
   * re-showing the 6-phase timeline (which was getting stuck at ~17% for
   * fully onboarded sites whose backend status hadn't been refreshed).
   */
  site?: Pick<SiteDetail, 'timestamps' | 'onboarding_stage' | 'last_checkin'> | null;
}

/**
 * Deployment progress component for zero-friction deployment pipeline.
 *
 * Shows real-time progress through:
 * 1. Discovering domain
 * 2. Awaiting credentials
 * 3. Enumerating AD
 * 4. Deploying agents
 * 5. First scan
 * 6. Complete
 *
 * For sites that have already completed deployment we render a compact
 * "Deployment Complete" state (see `isMatureSite`).
 */
export const DeploymentProgress: React.FC<DeploymentProgressProps> = ({ siteId, site }) => {
  const { data: status, isLoading, error } = useDeploymentStatus(siteId);

  // A site is "mature" when any of the post-scan milestones are set, when it's
  // sitting in the `active` onboarding stage, or when the backend has already
  // reported the deployment phase as `complete`. This prevents the 6-step
  // timeline from rendering at 17% for sites that have been online for weeks.
  const ts = site?.timestamps;
  const isMatureSite = Boolean(
    (status && status.phase === 'complete') ||
    status?.details?.first_scan_complete ||
    (ts && (ts.scanning_at || ts.baseline_at || ts.active_at)) ||
    site?.onboarding_stage === 'active'
  );

  if (isLoading) {
    return (
      <GlassCard className="p-6">
        <div className="flex items-center gap-3">
          <Spinner />
          <span className="text-label-secondary">Loading deployment status...</span>
        </div>
      </GlassCard>
    );
  }

  if (error) {
    return (
      <GlassCard className="p-6">
        <div className="text-health-critical">
          Failed to load deployment status: {error instanceof Error ? error.message : 'Unknown error'}
        </div>
      </GlassCard>
    );
  }

  // Compact "complete" state for mature sites. We show this even when the
  // status endpoint returns no data, because the site.timestamps alone are
  // enough evidence that onboarding is long done.
  if (isMatureSite) {
    const completedAt = ts?.baseline_at || ts?.active_at || ts?.scanning_at || site?.last_checkin || null;
    return (
      <GlassCard className="p-4">
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-3 min-w-0">
            <div className="w-10 h-10 rounded-full bg-health-healthy/15 text-health-healthy flex items-center justify-center flex-shrink-0">
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
              </svg>
            </div>
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <h3 className="text-sm font-semibold text-label-primary">Deployment Complete</h3>
                <Badge variant="success" className="text-[10px]">Onboarded</Badge>
              </div>
              <p className="text-xs text-label-tertiary truncate">
                Zero-friction pipeline finished {completedAt ? formatTimeAgo(completedAt) : 'recently'}
              </p>
            </div>
          </div>
          {status?.details && (
            <div className="hidden sm:flex items-center gap-4 text-xs text-label-secondary flex-shrink-0">
              {typeof status.details.servers_found === 'number' && (
                <div className="text-right">
                  <div className="font-semibold text-label-primary">{status.details.servers_found}</div>
                  <div className="text-label-tertiary">Servers</div>
                </div>
              )}
              {typeof status.details.workstations_found === 'number' && (
                <div className="text-right">
                  <div className="font-semibold text-label-primary">{status.details.workstations_found}</div>
                  <div className="text-label-tertiary">Workstations</div>
                </div>
              )}
              {typeof status.details.agents_deployed === 'number' && (
                <div className="text-right">
                  <div className="font-semibold text-label-primary">{status.details.agents_deployed}</div>
                  <div className="text-label-tertiary">Agents</div>
                </div>
              )}
            </div>
          )}
        </div>
      </GlassCard>
    );
  }

  if (!status) {
    return (
      <GlassCard className="p-6">
        <div className="text-label-secondary">No deployment in progress</div>
      </GlassCard>
    );
  }

  const phases = [
    { key: 'discovering', label: 'Discovering Domain', icon: '🔍', description: 'Detecting AD domain via DNS' },
    { key: 'awaiting_credentials', label: 'Awaiting Credentials', icon: '🔐', description: 'Enter domain admin credentials' },
    { key: 'enumerating', label: 'Enumerating AD', icon: '📋', description: 'Discovering servers and workstations' },
    { key: 'deploying', label: 'Deploying Agents', icon: '🚀', description: 'Installing Go agents on workstations' },
    { key: 'scanning', label: 'First Scan', icon: '✅', description: 'Running initial compliance scan' },
    { key: 'complete', label: 'Complete', icon: '🎉', description: 'Deployment finished' },
  ];

  const currentIndex = phases.findIndex(p => p.key === status.phase);
  const progressPercent = status.phase === 'complete' ? 100 : ((currentIndex + 1) / phases.length) * 100;

  return (
    <GlassCard className="p-6">
      <div className="mb-6">
        <h3 className="text-lg font-semibold text-label-primary mb-2">Deployment Progress</h3>
        <p className="text-sm text-label-secondary">
          Zero-friction deployment pipeline status
        </p>
      </div>

      {/* Progress Bar */}
      <div className="mb-6">
        <div className="h-2 bg-separator-light/50 rounded-full overflow-hidden">
          <div
            className="h-full bg-gradient-to-r from-blue-500 to-purple-500 transition-all duration-500"
            style={{ width: `${progressPercent}%` }}
          />
        </div>
        <div className="mt-2 text-sm text-label-secondary text-right">
          {progressPercent.toFixed(0)}% complete
        </div>
      </div>

      {/* Phase Timeline */}
      <div className="space-y-4">
        {phases.map((phase, idx) => {
          const isComplete = idx < currentIndex;
          const isCurrent = idx === currentIndex;

          return (
            <div
              key={phase.key}
              className={`flex items-start gap-4 p-3 rounded-lg transition-all ${
                isCurrent
                  ? 'bg-blue-500/20 border border-blue-500/50'
                  : isComplete
                  ? 'bg-health-healthy/10'
                  : 'bg-separator-light/20'
              }`}
            >
              {/* Icon */}
              <div
                className={`text-2xl flex-shrink-0 ${
                  isComplete ? 'opacity-100' : isCurrent ? 'opacity-100 animate-pulse' : 'opacity-40'
                }`}
              >
                {phase.icon}
              </div>

              {/* Content */}
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-1">
                  <span
                    className={`font-medium ${
                      isCurrent ? 'text-blue-400' : isComplete ? 'text-health-healthy' : 'text-label-tertiary'
                    }`}
                  >
                    {phase.label}
                  </span>
                  {isCurrent && (
                    <Badge className="bg-blue-500 text-white text-xs">In Progress</Badge>
                  )}
                  {isComplete && (
                    <Badge className="bg-health-healthy text-white text-xs">✓ Complete</Badge>
                  )}
                </div>
                <p className="text-sm text-label-secondary">{phase.description}</p>

                {/* Phase-specific details */}
                {isCurrent && phase.key === 'awaiting_credentials' && status.details?.domain_discovered && (
                  <div className="mt-3 p-3 bg-blue-500/10 rounded border border-blue-500/30">
                    <p className="text-sm text-label-primary mb-2">
                      <strong>Domain Discovered:</strong> {status.details.domain_discovered}
                    </p>
                    <a
                      href={`/sites/${siteId}/credentials`}
                      className="inline-block px-4 py-2 bg-blue-500 text-white rounded-lg text-sm font-medium hover:bg-blue-600 transition-colors"
                    >
                      Enter Domain Credentials →
                    </a>
                  </div>
                )}

                {isCurrent && phase.key === 'enumerating' && (
                  <div className="mt-2 text-sm text-label-secondary">
                    {status.details?.servers_found !== undefined && (
                      <span>Found {status.details.servers_found} servers, {status.details.workstations_found} workstations</span>
                    )}
                  </div>
                )}

                {isCurrent && phase.key === 'deploying' && (
                  <div className="mt-2 text-sm text-label-secondary">
                    {status.details?.agents_deployed !== undefined && (
                      <span>Deployed to {status.details.agents_deployed} workstations</span>
                    )}
                  </div>
                )}

                {isCurrent && phase.key === 'scanning' && status.details?.first_scan_complete && (
                  <div className="mt-2 text-sm text-health-healthy">
                    ✓ First compliance report generated
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* Stats Summary */}
      {status.details && (status.details.servers_found !== undefined || status.details.workstations_found !== undefined) && (
        <div className="mt-6 pt-6 border-t border-separator-light">
          <h4 className="text-sm font-semibold text-label-primary mb-3">Discovery Summary</h4>
          <div className="grid grid-cols-3 gap-4">
            {status.details.servers_found !== undefined && (
              <div className="text-center">
                <div className="text-2xl font-bold text-label-primary">{status.details.servers_found}</div>
                <div className="text-xs text-label-secondary">Servers</div>
              </div>
            )}
            {status.details.workstations_found !== undefined && (
              <div className="text-center">
                <div className="text-2xl font-bold text-label-primary">{status.details.workstations_found}</div>
                <div className="text-xs text-label-secondary">Workstations</div>
              </div>
            )}
            {status.details.agents_deployed !== undefined && (
              <div className="text-center">
                <div className="text-2xl font-bold text-label-primary">{status.details.agents_deployed}</div>
                <div className="text-xs text-label-secondary">Agents Deployed</div>
              </div>
            )}
          </div>
        </div>
      )}
    </GlassCard>
  );
};
