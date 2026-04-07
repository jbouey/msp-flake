import React, { useState } from 'react';
import { Modal } from './Modal';

type PortalType = 'client' | 'partner';

interface WelcomeStep {
  title: string;
  content: React.ReactNode;
}

interface WelcomeModalProps {
  isOpen: boolean;
  onClose: () => void;
  portalType: PortalType;
}

const CLIENT_STEPS: WelcomeStep[] = [
  {
    title: 'Welcome to OsirisCare',
    content: (
      <div className="space-y-4">
        <p className="text-label-secondary leading-relaxed">
          Your IT systems are being automatically monitored for security
          configuration issues.
        </p>
        <p className="text-label-secondary leading-relaxed">
          This dashboard shows you what we are checking and what needs
          attention.
        </p>
      </div>
    ),
  },
  {
    title: 'What You Will See',
    content: (
      <div className="space-y-4">
        <div className="p-3 rounded-xl bg-slate-50 border border-slate-100">
          <p className="font-medium text-label-primary mb-1">Monitoring Score</p>
          <p className="text-sm text-label-secondary">
            How many security checks are passing across your systems.
          </p>
        </div>
        <div className="p-3 rounded-xl bg-slate-50 border border-slate-100">
          <p className="font-medium text-label-primary mb-1">Incidents</p>
          <p className="text-sm text-label-secondary">
            Configuration issues that were detected and handled.
          </p>
        </div>
        <div className="p-3 rounded-xl bg-slate-50 border border-slate-100">
          <p className="font-medium text-label-primary mb-1">Evidence</p>
          <p className="text-sm text-label-secondary">
            Signed records of your system state for audit documentation.
          </p>
        </div>
      </div>
    ),
  },
  {
    title: 'How Issues Get Fixed',
    content: (
      <div className="space-y-4">
        <div className="space-y-3">
          <div className="flex items-start gap-3">
            <div className="mt-0.5 w-6 h-6 rounded-full bg-teal-100 text-teal-700 flex items-center justify-center text-xs font-semibold flex-shrink-0">
              L1
            </div>
            <p className="text-sm text-label-secondary">
              Most issues are resolved automatically within seconds.
            </p>
          </div>
          <div className="flex items-start gap-3">
            <div className="mt-0.5 w-6 h-6 rounded-full bg-blue-100 text-blue-700 flex items-center justify-center text-xs font-semibold flex-shrink-0">
              L2
            </div>
            <p className="text-sm text-label-secondary">
              Complex issues get AI-assisted analysis.
            </p>
          </div>
          <div className="flex items-start gap-3">
            <div className="mt-0.5 w-6 h-6 rounded-full bg-amber-100 text-amber-700 flex items-center justify-center text-xs font-semibold flex-shrink-0">
              L3
            </div>
            <p className="text-sm text-label-secondary">
              Anything unusual is escalated to your IT provider.
            </p>
          </div>
        </div>
        <p className="text-sm text-label-tertiary pt-2 border-t border-slate-100">
          You do not need to do anything -- your provider handles it.
        </p>
      </div>
    ),
  },
  {
    title: 'You Are All Set',
    content: (
      <div className="space-y-4">
        <div className="space-y-3">
          <p className="text-label-secondary leading-relaxed">
            Your systems are being monitored 24/7.
          </p>
          <p className="text-label-secondary leading-relaxed">
            You will receive email alerts if anything needs your attention.
          </p>
          <p className="text-label-secondary leading-relaxed">
            Questions? Contact your IT provider or email{' '}
            <a
              href="mailto:support@osiriscare.net"
              className="text-teal-600 hover:text-teal-700 font-medium"
            >
              support@osiriscare.net
            </a>
          </p>
        </div>
      </div>
    ),
  },
];

const PARTNER_STEPS: WelcomeStep[] = [
  {
    title: 'Welcome to OsirisCare Partner Portal',
    content: (
      <div className="space-y-4">
        <p className="text-label-secondary leading-relaxed">
          Manage your client sites, monitor compliance posture, and handle
          escalations.
        </p>
      </div>
    ),
  },
  {
    title: 'Your Dashboard',
    content: (
      <div className="space-y-4">
        <div className="p-3 rounded-xl bg-slate-50 border border-slate-100">
          <p className="font-medium text-label-primary mb-1">Sites</p>
          <p className="text-sm text-label-secondary">
            All client sites with compliance scores and incident counts.
          </p>
        </div>
        <div className="p-3 rounded-xl bg-slate-50 border border-slate-100">
          <p className="font-medium text-label-primary mb-1">Escalations</p>
          <p className="text-sm text-label-secondary">
            L3/L4 tickets that need your attention.
          </p>
        </div>
        <div className="p-3 rounded-xl bg-slate-50 border border-slate-100">
          <p className="font-medium text-label-primary mb-1">Learning</p>
          <p className="text-sm text-label-secondary">
            Promote successful fix patterns to automatic rules.
          </p>
        </div>
      </div>
    ),
  },
  {
    title: 'Getting Clients Started',
    content: (
      <div className="space-y-3">
        <div className="flex items-start gap-3">
          <div className="mt-0.5 w-6 h-6 rounded-full bg-indigo-100 text-indigo-700 flex items-center justify-center text-xs font-semibold flex-shrink-0">
            1
          </div>
          <p className="text-sm text-label-secondary">
            Create a site and generate a provision code.
          </p>
        </div>
        <div className="flex items-start gap-3">
          <div className="mt-0.5 w-6 h-6 rounded-full bg-indigo-100 text-indigo-700 flex items-center justify-center text-xs font-semibold flex-shrink-0">
            2
          </div>
          <p className="text-sm text-label-secondary">
            Ship the appliance -- it calls home automatically.
          </p>
        </div>
        <div className="flex items-start gap-3">
          <div className="mt-0.5 w-6 h-6 rounded-full bg-indigo-100 text-indigo-700 flex items-center justify-center text-xs font-semibold flex-shrink-0">
            3
          </div>
          <p className="text-sm text-label-secondary">
            Add credentials for the client's systems.
          </p>
        </div>
        <div className="flex items-start gap-3">
          <div className="mt-0.5 w-6 h-6 rounded-full bg-indigo-100 text-indigo-700 flex items-center justify-center text-xs font-semibold flex-shrink-0">
            4
          </div>
          <p className="text-sm text-label-secondary">
            Monitoring begins within 10 minutes.
          </p>
        </div>
      </div>
    ),
  },
  {
    title: 'You Are Ready',
    content: (
      <div className="space-y-4">
        <p className="text-label-secondary leading-relaxed">
          Your clients' systems are monitored 24/7.
        </p>
      </div>
    ),
  },
];

export const WelcomeModal: React.FC<WelcomeModalProps> = ({
  isOpen,
  onClose,
  portalType,
}) => {
  const [currentStep, setCurrentStep] = useState(0);
  const steps = portalType === 'client' ? CLIENT_STEPS : PARTNER_STEPS;
  const step = steps[currentStep];
  const isLastStep = currentStep === steps.length - 1;
  const isFirstStep = currentStep === 0;

  const accentColor = portalType === 'client' ? '#14A89E' : '#4F46E5';
  const accentColorEnd = portalType === 'client' ? '#3CBCB4' : '#7C3AED';

  const handleClose = () => {
    setCurrentStep(0);
    onClose();
  };

  return (
    <Modal isOpen={isOpen} onClose={handleClose} size="md" showClose={false}>
      <div className="space-y-6">
        {/* Branding header */}
        <div className="text-center">
          <p
            className="text-xs font-semibold uppercase tracking-widest mb-3"
            style={{ color: accentColor }}
          >
            OsirisCare
          </p>
          <h2 className="text-xl font-semibold text-label-primary font-display">
            {step.title}
          </h2>
        </div>

        {/* Step content */}
        <div className="min-h-[180px]">{step.content}</div>

        {/* Step indicators */}
        <div className="flex justify-center gap-2">
          {steps.map((_, index) => (
            <button
              key={index}
              onClick={() => setCurrentStep(index)}
              className="transition-all duration-200"
              aria-label={`Go to step ${index + 1}`}
            >
              <div
                className="rounded-full transition-all duration-200"
                style={{
                  width: index === currentStep ? 24 : 8,
                  height: 8,
                  background:
                    index === currentStep
                      ? `linear-gradient(135deg, ${accentColor} 0%, ${accentColorEnd} 100%)`
                      : '#E2E8F0',
                }}
              />
            </button>
          ))}
        </div>

        {/* Navigation */}
        <div className="flex items-center justify-between pt-2">
          <div>
            {!isFirstStep && (
              <button
                onClick={() => setCurrentStep((s) => s - 1)}
                className="px-4 py-2 text-sm font-medium text-label-secondary hover:text-label-primary rounded-xl hover:bg-fill-secondary transition-all"
              >
                Back
              </button>
            )}
          </div>
          <div>
            {isLastStep ? (
              <button
                onClick={handleClose}
                className="px-6 py-2.5 text-sm font-medium text-white rounded-xl hover:brightness-110 transition-all"
                style={{
                  background: `linear-gradient(135deg, ${accentColor} 0%, ${accentColorEnd} 100%)`,
                  boxShadow: `0 4px 14px ${accentColor}59`,
                }}
              >
                Get Started
              </button>
            ) : (
              <button
                onClick={() => setCurrentStep((s) => s + 1)}
                className="px-6 py-2.5 text-sm font-medium text-white rounded-xl hover:brightness-110 transition-all"
                style={{
                  background: `linear-gradient(135deg, ${accentColor} 0%, ${accentColorEnd} 100%)`,
                  boxShadow: `0 4px 14px ${accentColor}59`,
                }}
              >
                Next
              </button>
            )}
          </div>
        </div>
      </div>
    </Modal>
  );
};

export default WelcomeModal;
