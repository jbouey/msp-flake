import React from 'react';

interface DeployProgressProps {
  status: string;
  error?: string;
}

export const DeployProgress: React.FC<DeployProgressProps> = ({ status, error }) => {
  const steps = ['Connecting', 'Uploading', 'Installing', 'Verifying'];
  const activeStep = status === 'deploying' ? 1 : status === 'agent_active' ? 4 : 0;

  return (
    <div className="flex items-center gap-2 text-xs">
      {steps.map((step, i) => (
        <span key={step} className={i < activeStep ? 'text-emerald-400' : i === activeStep ? 'text-amber-400' : 'text-slate-500'}>
          {i < activeStep ? '\u2713' : i === activeStep ? '\u25CB' : '\u00B7'} {step}
        </span>
      ))}
      {status === 'deploy_failed' && error && (
        <span className="text-red-400 ml-2" title={error}>Failed</span>
      )}
    </div>
  );
};

export default DeployProgress;
