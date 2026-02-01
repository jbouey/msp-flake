import React, { memo } from 'react';
import { GlassCard } from '../shared';
import type { OnboardingClient, OnboardingStage } from '../../types';

interface OnboardingCardProps {
  client: OnboardingClient;
  onClick?: () => void;
}

const stageLabels: Record<OnboardingStage, string> = {
  lead: 'Lead',
  discovery: 'Discovery',
  proposal: 'Proposal',
  contract: 'Contract',
  intake: 'Intake',
  creds: 'Credentials',
  shipped: 'Shipped',
  received: 'Received',
  connectivity: 'Connectivity',
  scanning: 'Scanning',
  baseline: 'Baseline',
  compliant: 'Compliant',
  active: 'Active',
};

const getStagePhase = (stage: OnboardingStage): number => {
  const phase1: OnboardingStage[] = ['lead', 'discovery', 'proposal', 'contract', 'intake', 'creds', 'shipped'];
  return phase1.includes(stage) ? 1 : 2;
};

const getCheckinStatusColor = (status?: string): string => {
  switch (status) {
    case 'connected': return 'bg-health-healthy';
    case 'pending': return 'bg-health-warning';
    case 'failed': return 'bg-health-critical';
    default: return 'bg-separator-light';
  }
};

export const OnboardingCard: React.FC<OnboardingCardProps> = memo(({ client, onClick }) => {
  const phase = getStagePhase(client.stage);
  const isAtRisk = (client.days_in_stage ?? 0) > 7;
  const hasBlockers = (client.blockers?.length ?? 0) > 0;

  return (
    <GlassCard hover onClick={onClick} className="cursor-pointer">
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1 min-w-0">
          {/* Header */}
          <div className="flex items-center gap-2 mb-2">
            <span className={`px-2 py-0.5 text-xs font-medium rounded ${
              phase === 1 ? 'bg-accent-primary/20 text-accent-primary' : 'bg-level-l2/20 text-level-l2'
            }`}>
              Phase {phase}
            </span>
            <span className="text-xs text-label-tertiary">
              {stageLabels[client.stage]}
            </span>
            {isAtRisk && (
              <span className="px-1.5 py-0.5 text-xs bg-health-warning/20 text-health-warning rounded">
                At Risk
              </span>
            )}
          </div>

          {/* Name */}
          <h3 className="font-semibold text-label-primary mb-1">{client.name}</h3>

          {/* Contact */}
          {client.contact_name && (
            <p className="text-sm text-label-secondary mb-2">
              {client.contact_name}
              {client.contact_email && ` - ${client.contact_email}`}
            </p>
          )}

          {/* Blockers */}
          {hasBlockers && (
            <div className="flex flex-wrap gap-1 mb-2">
              {(client.blockers || []).map((blocker, i) => (
                <span
                  key={i}
                  className="px-2 py-0.5 text-xs bg-health-critical/10 text-health-critical rounded"
                >
                  {blocker}
                </span>
              ))}
            </div>
          )}

          {/* Progress bar */}
          <div className="mt-3">
            <div className="flex items-center justify-between text-xs mb-1">
              <span className="text-label-tertiary">Progress</span>
              <span className="font-medium text-label-secondary">{client.progress_percent}%</span>
            </div>
            <div className="h-1.5 bg-separator-light rounded-full overflow-hidden">
              <div
                className="h-full bg-accent-primary rounded-full transition-all"
                style={{ width: `${client.progress_percent}%` }}
              />
            </div>
          </div>
        </div>

        {/* Right side - Stats */}
        <div className="flex flex-col items-end gap-2">
          {/* Days in stage */}
          <div className="text-right">
            <p className={`text-lg font-bold ${isAtRisk ? 'text-health-warning' : 'text-label-primary'}`}>
              {client.days_in_stage}
            </p>
            <p className="text-xs text-label-tertiary">days</p>
          </div>

          {/* Checkin status (Phase 2 only) */}
          {phase === 2 && client.checkin_status && (
            <div className="flex items-center gap-1.5">
              <div className={`w-2 h-2 rounded-full ${getCheckinStatusColor(client.checkin_status)}`} />
              <span className="text-xs text-label-tertiary capitalize">{client.checkin_status}</span>
            </div>
          )}

          {/* Compliance score (Phase 2 only) */}
          {phase === 2 && client.compliance_score != null && (
            <div className="text-right">
              <p className="text-sm font-medium text-label-secondary">
                {(client?.compliance_score ?? 0).toFixed(0)}%
              </p>
              <p className="text-xs text-label-tertiary">compliance</p>
            </div>
          )}
        </div>
      </div>
    </GlassCard>
  );
});

export default OnboardingCard;
