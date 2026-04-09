import React from 'react';
import type { SiteDetail as SiteDetailType } from '../../../utils/api';

export interface OnboardingProgressProps {
  timestamps: SiteDetailType['timestamps'];
  stage: string;
}

/**
 * Onboarding progress component
 */
export const OnboardingProgress: React.FC<OnboardingProgressProps> = ({ timestamps, stage }) => {
  const stages = [
    { key: 'lead_at', label: 'Lead', icon: '👤' },
    { key: 'discovery_at', label: 'Discovery', icon: '🔍' },
    { key: 'proposal_at', label: 'Proposal', icon: '📋' },
    { key: 'contract_at', label: 'Contract', icon: '✍️' },
    { key: 'intake_at', label: 'Intake', icon: '📝' },
    { key: 'creds_at', label: 'Credentials', icon: '🔑' },
    { key: 'shipped_at', label: 'Shipped', icon: '📦' },
    { key: 'received_at', label: 'Received', icon: '📬' },
    { key: 'connectivity_at', label: 'Connected', icon: '🔌' },
    { key: 'scanning_at', label: 'Scanning', icon: '🔬' },
    { key: 'baseline_at', label: 'Baseline', icon: '📊' },
    { key: 'active_at', label: 'Active', icon: '✅' },
  ];

  return (
    <div className="space-y-2">
      {stages.map((s) => {
        const completed = timestamps[s.key as keyof typeof timestamps] !== null;
        const isCurrent = stage === s.key.replace('_at', '');

        return (
          <div
            key={s.key}
            className={`flex items-center gap-3 py-2 px-3 rounded-ios transition-colors ${
              isCurrent ? 'bg-accent-primary/10' :
              completed ? 'bg-fill-secondary' : ''
            }`}
          >
            <span className="text-lg">{completed ? '✅' : s.icon}</span>
            <span className={`flex-1 ${completed ? 'text-label-primary' : 'text-label-tertiary'}`}>
              {s.label}
            </span>
            {timestamps[s.key as keyof typeof timestamps] && (
              <span className="text-xs text-label-tertiary">
                {new Date(timestamps[s.key as keyof typeof timestamps]!).toLocaleDateString()}
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
};
