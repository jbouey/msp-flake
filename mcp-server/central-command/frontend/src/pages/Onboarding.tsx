import React, { useState } from 'react';
import { GlassCard, Spinner } from '../components/shared';
import { OnboardingCard, PipelineStages } from '../components/onboarding';
import { useOnboardingPipeline, useOnboardingMetrics } from '../hooks';
import type { OnboardingClient, OnboardingStage } from '../types';

/**
 * Onboarding page - Two-phase pipeline visualization
 */
export const Onboarding: React.FC = () => {
  const [selectedPhase, setSelectedPhase] = useState<1 | 2 | 'all'>('all');

  // Fetch data
  const { data: pipeline = [], isLoading: isLoadingPipeline } = useOnboardingPipeline();
  const { data: metrics, isLoading: isLoadingMetrics } = useOnboardingMetrics();

  // Filter by phase
  const phase1Stages: OnboardingStage[] = ['lead', 'discovery', 'proposal', 'contract', 'intake', 'creds', 'shipped'];
  const phase2Stages: OnboardingStage[] = ['received', 'connectivity', 'scanning', 'baseline', 'compliant', 'active'];

  const filteredPipeline = pipeline.filter((client) => {
    if (selectedPhase === 'all') return client.stage !== 'active';
    if (selectedPhase === 1) return phase1Stages.includes(client.stage);
    return phase2Stages.includes(client.stage) && client.stage !== 'active';
  });

  // Get active clients (recently activated)
  const activeClients = pipeline.filter((client) => client.stage === 'active');

  // Sort by days in stage (at risk first)
  const sortedPipeline = [...filteredPipeline].sort((a, b) => b.days_in_stage - a.days_in_stage);

  // Clients with blockers
  const blockedClients = pipeline.filter((client) => client.blockers.length > 0);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-label-primary">Onboarding Pipeline</h1>
          <p className="text-label-tertiary text-sm mt-1">
            {metrics?.total_prospects ?? 0} active prospects
          </p>
        </div>
        <button className="btn-primary">+ New Prospect</button>
      </div>

      {/* Pipeline visualization */}
      <PipelineStages metrics={metrics} isLoading={isLoadingMetrics} />

      {/* Stats row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <GlassCard padding="md" className="text-center">
          <p className={`text-2xl font-bold ${(metrics?.at_risk_count ?? 0) > 0 ? 'text-health-warning' : 'text-label-primary'}`}>
            {isLoadingMetrics ? '-' : metrics?.at_risk_count ?? 0}
          </p>
          <p className="text-xs text-label-tertiary">At Risk (&gt;7 days)</p>
        </GlassCard>
        <GlassCard padding="md" className="text-center">
          <p className={`text-2xl font-bold ${(metrics?.stalled_count ?? 0) > 0 ? 'text-health-critical' : 'text-label-primary'}`}>
            {isLoadingMetrics ? '-' : metrics?.stalled_count ?? 0}
          </p>
          <p className="text-xs text-label-tertiary">Stalled (&gt;14 days)</p>
        </GlassCard>
        <GlassCard padding="md" className="text-center">
          <p className={`text-2xl font-bold ${(metrics?.connectivity_issues ?? 0) > 0 ? 'text-health-critical' : 'text-label-primary'}`}>
            {isLoadingMetrics ? '-' : metrics?.connectivity_issues ?? 0}
          </p>
          <p className="text-xs text-label-tertiary">Connectivity Issues</p>
        </GlassCard>
        <GlassCard padding="md" className="text-center">
          <p className="text-2xl font-bold text-accent-primary">
            {isLoadingMetrics ? '-' : activeClients.length}
          </p>
          <p className="text-xs text-label-tertiary">Recently Activated</p>
        </GlassCard>
      </div>

      {/* Blockers alert */}
      {blockedClients.length > 0 && (
        <GlassCard className="border-l-4 border-health-warning">
          <div className="flex items-start gap-3">
            <div className="w-8 h-8 rounded-full bg-health-warning/20 flex items-center justify-center flex-shrink-0">
              <svg className="w-4 h-4 text-health-warning" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
              </svg>
            </div>
            <div>
              <h3 className="font-semibold text-label-primary">
                {blockedClients.length} Prospect{blockedClients.length > 1 ? 's' : ''} with Blockers
              </h3>
              <p className="text-sm text-label-secondary mt-1">
                {blockedClients.map((c) => c.name).join(', ')}
              </p>
            </div>
          </div>
        </GlassCard>
      )}

      {/* Phase filter */}
      <div className="flex items-center gap-2">
        <span className="text-sm text-label-tertiary">Show:</span>
        <div className="flex gap-1">
          {[
            { value: 'all' as const, label: 'All' },
            { value: 1 as const, label: 'Phase 1' },
            { value: 2 as const, label: 'Phase 2' },
          ].map((option) => (
            <button
              key={option.value}
              onClick={() => setSelectedPhase(option.value)}
              className={`px-3 py-1.5 text-sm rounded-ios-sm transition-colors ${
                selectedPhase === option.value
                  ? 'bg-accent-primary text-white'
                  : 'bg-separator-light text-label-secondary hover:bg-separator-light/80'
              }`}
            >
              {option.label}
            </button>
          ))}
        </div>
        <span className="text-sm text-label-tertiary ml-4">
          {sortedPipeline.length} prospect{sortedPipeline.length !== 1 ? 's' : ''}
        </span>
      </div>

      {/* Pipeline cards */}
      {isLoadingPipeline ? (
        <div className="flex items-center justify-center py-12">
          <Spinner size="lg" />
        </div>
      ) : sortedPipeline.length === 0 ? (
        <GlassCard className="text-center py-12">
          <div className="w-16 h-16 mx-auto mb-4 rounded-full bg-accent-primary/10 flex items-center justify-center">
            <svg className="w-8 h-8 text-accent-primary" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M18 9v3m0 0v3m0-3h3m-3 0h-3m-2-5a4 4 0 11-8 0 4 4 0 018 0zM3 20a6 6 0 0112 0v1H3v-1z" />
            </svg>
          </div>
          <h3 className="font-semibold text-label-primary mb-2">No prospects in pipeline</h3>
          <p className="text-label-tertiary text-sm mb-4">
            Click "New Prospect" to add your first client.
          </p>
          <button className="btn-primary">+ New Prospect</button>
        </GlassCard>
      ) : (
        <div className="space-y-4">
          {sortedPipeline.map((client: OnboardingClient) => (
            <OnboardingCard key={client.id} client={client} />
          ))}
        </div>
      )}

      {/* Recently Activated */}
      {activeClients.length > 0 && (
        <GlassCard>
          <h2 className="text-lg font-semibold mb-4">Recently Activated</h2>
          <div className="space-y-2">
            {activeClients.slice(0, 5).map((client: OnboardingClient) => (
              <div
                key={client.id}
                className="flex items-center justify-between py-3 border-b border-separator-light last:border-0"
              >
                <div className="flex items-center gap-3">
                  <div className="w-8 h-8 rounded-full bg-health-healthy/20 flex items-center justify-center">
                    <svg className="w-4 h-4 text-health-healthy" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                    </svg>
                  </div>
                  <div>
                    <p className="font-medium text-label-primary">{client.name}</p>
                    <p className="text-xs text-label-tertiary">
                      {client.site_id && `Site: ${client.site_id}`}
                    </p>
                  </div>
                </div>
                <div className="text-right">
                  <p className="text-sm font-medium text-health-healthy">Active</p>
                  <p className="text-xs text-label-tertiary">
                    {client.active_at
                      ? new Date(client.active_at).toLocaleDateString()
                      : 'Recently'}
                  </p>
                </div>
              </div>
            ))}
          </div>
        </GlassCard>
      )}
    </div>
  );
};

export default Onboarding;
