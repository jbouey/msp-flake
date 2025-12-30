import React from 'react';
import { GlassCard } from '../shared';
import type { OnboardingMetrics } from '../../types';

interface PipelineStagesProps {
  metrics?: OnboardingMetrics;
  isLoading?: boolean;
}

interface StageConfig {
  key: string;
  label: string;
  shortLabel: string;
}

const phase1Stages: StageConfig[] = [
  { key: 'lead', label: 'Lead', shortLabel: 'Lead' },
  { key: 'discovery', label: 'Discovery', shortLabel: 'Disc' },
  { key: 'proposal', label: 'Proposal', shortLabel: 'Prop' },
  { key: 'contract', label: 'Contract', shortLabel: 'Sign' },
  { key: 'intake', label: 'Intake', shortLabel: 'Form' },
  { key: 'creds', label: 'Credentials', shortLabel: 'Cred' },
  { key: 'shipped', label: 'Shipped', shortLabel: 'Ship' },
];

const phase2Stages: StageConfig[] = [
  { key: 'received', label: 'Received', shortLabel: 'Recv' },
  { key: 'connectivity', label: 'Connectivity', shortLabel: 'Conn' },
  { key: 'scanning', label: 'Scanning', shortLabel: 'Scan' },
  { key: 'baseline', label: 'Baseline', shortLabel: 'Base' },
  { key: 'compliant', label: 'Compliant', shortLabel: 'Comp' },
  { key: 'active', label: 'Active', shortLabel: 'Live' },
];

export const PipelineStages: React.FC<PipelineStagesProps> = ({ metrics, isLoading }) => {
  const getCount = (phase: 'acquisition' | 'activation', key: string): number => {
    if (!metrics) return 0;
    const phaseData = phase === 'acquisition' ? metrics.acquisition : metrics.activation;
    return phaseData[key] ?? 0;
  };

  const getMaxCount = (stages: StageConfig[], phase: 'acquisition' | 'activation'): number => {
    return Math.max(...stages.map(s => getCount(phase, s.key)), 1);
  };

  const renderStages = (
    stages: StageConfig[],
    phase: 'acquisition' | 'activation',
    color: string
  ) => {
    const maxCount = getMaxCount(stages, phase);

    return (
      <div className="flex gap-2">
        {stages.map((stage) => {
          const count = getCount(phase, stage.key);
          const heightPercent = (count / maxCount) * 100;

          return (
            <div key={stage.key} className="flex-1 text-center">
              <div className="relative h-16 bg-separator-light/50 rounded-ios-sm overflow-hidden mb-1">
                {/* Bar */}
                <div
                  className={`absolute bottom-0 left-0 right-0 ${color} transition-all duration-500`}
                  style={{ height: `${heightPercent}%` }}
                />
                {/* Count */}
                <div className="absolute inset-0 flex items-center justify-center">
                  <span className={`text-lg font-bold ${count > 0 ? 'text-white' : 'text-label-tertiary'}`}>
                    {isLoading ? '-' : count}
                  </span>
                </div>
              </div>
              <span className="text-xs text-label-tertiary" title={stage.label}>
                {stage.shortLabel}
              </span>
            </div>
          );
        })}
      </div>
    );
  };

  return (
    <GlassCard>
      {/* Phase 1: Acquisition */}
      <div className="mb-6">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold text-label-tertiary uppercase tracking-wide">
            Phase 1: Acquisition
          </h2>
          {metrics && (
            <span className="text-sm text-label-secondary">
              Avg {metrics.avg_days_to_ship.toFixed(0)} days to ship
            </span>
          )}
        </div>
        {renderStages(phase1Stages, 'acquisition', 'bg-accent-primary')}
      </div>

      {/* Divider */}
      <div className="flex items-center gap-4 mb-6">
        <div className="flex-1 h-px bg-separator-light" />
        <svg className="w-6 h-6 text-label-tertiary" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M19 14l-7 7m0 0l-7-7m7 7V3" />
        </svg>
        <div className="flex-1 h-px bg-separator-light" />
      </div>

      {/* Phase 2: Activation */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold text-label-tertiary uppercase tracking-wide">
            Phase 2: Activation
          </h2>
          {metrics && (
            <span className="text-sm text-label-secondary">
              Avg {metrics.avg_days_to_active.toFixed(0)} days to active
            </span>
          )}
        </div>
        {renderStages(phase2Stages, 'activation', 'bg-level-l2')}
      </div>
    </GlassCard>
  );
};

export default PipelineStages;
