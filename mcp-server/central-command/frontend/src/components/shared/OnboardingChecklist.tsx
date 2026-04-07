import React from 'react';
import type { SiteDetail } from '../../utils/api';

interface OnboardingChecklistProps {
  site: SiteDetail;
  onNavigateDevices: () => void;
  onAddCredential: () => void;
}

interface ChecklistStep {
  label: string;
  description: string;
  completed: boolean;
  actionLabel?: string;
  onAction?: () => void;
}

const CheckIcon: React.FC = () => (
  <svg className="w-5 h-5 text-health-healthy" fill="none" viewBox="0 0 24 24" stroke="currentColor">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
  </svg>
);

const PendingIcon: React.FC = () => (
  <div className="w-5 h-5 rounded-full border-2 border-label-tertiary" />
);

export const OnboardingChecklist: React.FC<OnboardingChecklistProps> = ({
  site,
  onNavigateDevices,
  onAddCredential,
}) => {
  const hasAppliance = site.appliances.length > 0;
  const hasCheckin = site.appliances.some(a => a.last_checkin !== null);
  const hasCredentials = site.credentials.length > 0;
  const hasScanning = site.timestamps.scanning_at !== null;
  const hasBaseline = site.timestamps.baseline_at !== null;

  const steps: ChecklistStep[] = [
    {
      label: 'Appliance connected',
      description: hasCheckin
        ? 'Appliance is online and reporting in.'
        : 'Plug in the appliance, connect Ethernet, and boot from USB. It calls home automatically.',
      completed: hasCheckin,
    },
    {
      label: 'Network discovered',
      description: hasAppliance
        ? 'Devices on the network have been found.'
        : 'The appliance scans the local network automatically once connected.',
      completed: hasAppliance && hasCheckin,
      actionLabel: !hasAppliance ? 'View Devices' : undefined,
      onAction: !hasAppliance ? onNavigateDevices : undefined,
    },
    {
      label: 'Credentials entered',
      description: hasCredentials
        ? `${site.credentials.length} credential${site.credentials.length !== 1 ? 's' : ''} configured. The appliance can now scan these systems.`
        : 'Enter domain admin or SSH credentials so the appliance can check system configurations.',
      completed: hasCredentials,
      actionLabel: !hasCredentials ? 'Add credentials' : undefined,
      onAction: !hasCredentials ? onAddCredential : undefined,
    },
    {
      label: 'First scan complete',
      description: hasScanning
        ? 'Scanning is active. Incidents and evidence are being collected.'
        : 'Scans start automatically once credentials are in place. Usually takes 5-10 minutes.',
      completed: hasScanning,
    },
    {
      label: 'Monitoring baseline set',
      description: hasBaseline
        ? 'Baseline established. You will see alerts when systems deviate from this state.'
        : 'Set automatically after the first full scan cycle. This becomes the reference point for compliance checks.',
      completed: hasBaseline,
    },
  ];

  const completedCount = steps.filter(s => s.completed).length;

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold text-label-primary">Getting Started</h2>
        <span className="text-xs text-label-tertiary">
          {completedCount}/{steps.length} complete
        </span>
      </div>

      <div className="space-y-1">
        {steps.map((step, i) => (
          <div
            key={i}
            className={`flex items-start gap-3 py-3 px-3 rounded-ios transition-colors ${
              step.completed ? 'bg-fill-secondary' : ''
            }`}
          >
            <div className="mt-0.5 flex-shrink-0">
              {step.completed ? <CheckIcon /> : <PendingIcon />}
            </div>
            <div className="flex-1 min-w-0">
              <p className={`text-sm font-medium ${
                step.completed ? 'text-label-primary' : 'text-label-tertiary'
              }`}>
                {step.label}
              </p>
              <p className="text-xs text-label-tertiary mt-0.5">{step.description}</p>
              {!step.completed && step.actionLabel && step.onAction && (
                <button
                  onClick={step.onAction}
                  className="text-xs font-medium text-accent-primary hover:underline mt-1.5"
                >
                  {step.actionLabel}
                </button>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

export default OnboardingChecklist;
