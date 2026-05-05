import React, { useState } from 'react';
import { csrfHeaders } from '../utils/csrf';

interface CredentialEntryModalProps {
  isOpen: boolean;
  onClose: () => void;
  siteId: string;
  siteName: string;
  alertId?: string;
}

type CredentialType = 'domain_admin' | 'winrm' | 'ssh_password' | 'ssh_key';

interface TypeCard {
  type: CredentialType;
  label: string;
  description: string;
  icon: React.ReactNode;
}

const ShieldIcon = () => (
  <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
      d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
  </svg>
);

const ComputerIcon = () => (
  <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
      d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
  </svg>
);

const TerminalIcon = () => (
  <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
      d="M8 9l3 3-3 3m5 0h3M5 20h14a2 2 0 002-2V6a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
  </svg>
);

const KeyIcon = () => (
  <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
      d="M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z" />
  </svg>
);

const TYPE_CARDS: TypeCard[] = [
  {
    type: 'domain_admin',
    label: 'Windows Domain',
    description: 'For Active Directory environments',
    icon: <ShieldIcon />,
  },
  {
    type: 'winrm',
    label: 'Windows Local',
    description: 'For standalone Windows machines',
    icon: <ComputerIcon />,
  },
  {
    type: 'ssh_password',
    label: 'SSH Password',
    description: 'For Linux/macOS with password auth',
    icon: <TerminalIcon />,
  },
  {
    type: 'ssh_key',
    label: 'SSH Key',
    description: 'For Linux/macOS with key-based auth',
    icon: <KeyIcon />,
  },
];

const DEFAULT_CREDENTIAL_NAME: Record<CredentialType, string> = {
  domain_admin: 'Domain Admin credential',
  winrm: 'Windows Local credential',
  ssh_password: 'SSH Password credential',
  ssh_key: 'SSH Key credential',
};

const inputClass =
  'w-full px-3 py-2 bg-background-secondary border border-border-primary rounded-md text-label-primary text-sm focus:outline-none focus:ring-2 focus:ring-blue-500';

export const CredentialEntryModal: React.FC<CredentialEntryModalProps> = ({
  isOpen,
  onClose,
  siteId,
  siteName,
  alertId,
}) => {
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [selectedType, setSelectedType] = useState<CredentialType | null>(null);
  const [credentialName, setCredentialName] = useState('');
  const [formData, setFormData] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!isOpen) return null;

  const handleTypeSelect = (type: CredentialType) => {
    setSelectedType(type);
    setCredentialName(DEFAULT_CREDENTIAL_NAME[type]);
    setFormData({});
    setError(null);
    setStep(2);
  };

  const handleFieldChange = (field: string, value: string) => {
    setFormData((prev) => ({ ...prev, [field]: value }));
  };

  const handleBack = () => {
    if (step === 2) {
      setStep(1);
      setSelectedType(null);
    }
  };

  const handleSubmit = async () => {
    if (!selectedType) return;

    // Basic validation
    const required: Record<CredentialType, string[]> = {
      domain_admin: ['domain', 'username', 'password'],
      winrm: ['username', 'password'],
      ssh_password: ['username', 'password'],
      ssh_key: ['username', 'private_key'],
    };

    const missing = required[selectedType].filter((f) => !formData[f]?.trim());
    if (missing.length > 0 || !credentialName.trim()) {
      setError('Please fill in all required fields.');
      return;
    }

    setSubmitting(true);
    setError(null);

    try {
      const res = await fetch('/api/client/credentials', {
        method: 'POST',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
          ...csrfHeaders(),
        },
        body: JSON.stringify({
          site_id: siteId,
          credential_type: selectedType,
          credential_name: credentialName,
          alert_id: alertId,
          data: formData,
        }),
      });

      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setError(body.detail || `Server error (${res.status}). Please try again.`);
        return;
      }

      setStep(3);
    } catch (e) {
      setError('Network error. Please check your connection and try again.');
    } finally {
      setSubmitting(false);
    }
  };

  const handleClose = () => {
    setStep(1);
    setSelectedType(null);
    setCredentialName('');
    setFormData({});
    setError(null);
    onClose();
  };

  return (
    <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center">
      <div className="bg-background-secondary rounded-lg p-6 max-w-lg w-full mx-4 shadow-xl">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div>
            <h2 className="text-lg font-semibold text-label-primary">Enter Credentials</h2>
            <p className="text-sm text-label-secondary mt-0.5">{siteName}</p>
          </div>
          <button
            onClick={handleClose}
            className="p-1.5 rounded-md text-label-secondary hover:text-label-primary hover:bg-gray-100 transition-colors"
            aria-label="Close"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Step indicator */}
        <div className="flex items-center gap-2 mb-6">
          {[1, 2, 3].map((s) => (
            <React.Fragment key={s}>
              <div
                className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-semibold transition-colors ${
                  step >= s
                    ? 'bg-blue-600 text-white'
                    : 'bg-gray-100 text-label-secondary'
                }`}
              >
                {s}
              </div>
              {s < 3 && (
                <div className={`flex-1 h-0.5 ${step > s ? 'bg-blue-600' : 'bg-gray-200'}`} />
              )}
            </React.Fragment>
          ))}
        </div>

        {/* Step 1: Type Selection */}
        {step === 1 && (
          <div>
            <p className="text-sm text-label-secondary mb-4">
              Select the type of credentials to enter for this site.
            </p>
            <div className="grid grid-cols-2 gap-3">
              {TYPE_CARDS.map((card) => (
                <button
                  key={card.type}
                  onClick={() => handleTypeSelect(card.type)}
                  className="flex flex-col items-center gap-2 p-4 rounded-lg border border-border-primary hover:border-blue-500 hover:bg-blue-50/50 transition-colors text-center group"
                >
                  <div className="text-label-secondary group-hover:text-blue-600 transition-colors">
                    {card.icon}
                  </div>
                  <span className="text-sm font-medium text-label-primary">{card.label}</span>
                  <span className="text-xs text-label-secondary">{card.description}</span>
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Step 2: Form Fields */}
        {step === 2 && selectedType && (
          <div>
            <p className="text-sm text-label-secondary mb-4">
              Enter your {TYPE_CARDS.find((c) => c.type === selectedType)?.label} credentials.
              All fields marked * are required.
            </p>

            <div className="space-y-4">
              {/* Credential Name — always shown */}
              <div>
                <label className="block text-sm font-medium text-label-primary mb-1">
                  Credential Name *
                </label>
                <input
                  type="text"
                  value={credentialName}
                  onChange={(e) => setCredentialName(e.target.value)}
                  className={inputClass}
                  placeholder="e.g. Domain Admin credential"
                />
              </div>

              {/* Domain field — domain_admin only */}
              {selectedType === 'domain_admin' && (
                <div>
                  <label className="block text-sm font-medium text-label-primary mb-1">
                    Domain *
                  </label>
                  <input
                    type="text"
                    value={formData.domain || ''}
                    onChange={(e) => handleFieldChange('domain', e.target.value)}
                    className={inputClass}
                    placeholder="e.g. NORTHVALLEY"
                  />
                </div>
              )}

              {/* Username — all types */}
              <div>
                <label className="block text-sm font-medium text-label-primary mb-1">
                  Username *
                </label>
                <input
                  type="text"
                  value={formData.username || ''}
                  onChange={(e) => handleFieldChange('username', e.target.value)}
                  className={inputClass}
                  placeholder={
                    selectedType === 'domain_admin'
                      ? 'e.g. Administrator'
                      : selectedType === 'winrm'
                      ? 'e.g. localadmin'
                      : 'e.g. root'
                  }
                />
              </div>

              {/* Password — domain_admin, winrm, ssh_password */}
              {(selectedType === 'domain_admin' ||
                selectedType === 'winrm' ||
                selectedType === 'ssh_password') && (
                <div>
                  <label className="block text-sm font-medium text-label-primary mb-1">
                    Password *
                  </label>
                  <input
                    type="password"
                    value={formData.password || ''}
                    onChange={(e) => handleFieldChange('password', e.target.value)}
                    className={inputClass}
                    placeholder="Enter password"
                  />
                </div>
              )}

              {/* Private Key — ssh_key */}
              {selectedType === 'ssh_key' && (
                <>
                  <div>
                    <label className="block text-sm font-medium text-label-primary mb-1">
                      Private Key *
                    </label>
                    <textarea
                      value={formData.private_key || ''}
                      onChange={(e) => handleFieldChange('private_key', e.target.value)}
                      className={`${inputClass} font-mono text-xs`}
                      rows={6}
                      placeholder="-----BEGIN OPENSSH PRIVATE KEY-----&#10;..."
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-label-primary mb-1">
                      Passphrase{' '}
                      <span className="text-label-secondary font-normal">(optional)</span>
                    </label>
                    <input
                      type="password"
                      value={formData.passphrase || ''}
                      onChange={(e) => handleFieldChange('passphrase', e.target.value)}
                      className={inputClass}
                      placeholder="Leave blank if key has no passphrase"
                    />
                  </div>
                </>
              )}
            </div>

            {error && (
              <div className="mt-4 px-3 py-2 rounded-md bg-red-50 border border-red-200 text-sm text-red-700">
                {error}
              </div>
            )}

            <div className="flex gap-3 mt-6">
              <button
                onClick={handleBack}
                disabled={submitting}
                className="px-4 py-2 text-sm font-medium bg-gray-100 text-gray-700 rounded-md hover:bg-gray-200 disabled:opacity-50 transition-colors"
              >
                Back
              </button>
              <button
                onClick={handleSubmit}
                disabled={submitting}
                className="flex-1 px-4 py-2 text-sm font-medium bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:opacity-50 transition-colors"
              >
                {submitting ? 'Saving...' : 'Save Credentials'}
              </button>
            </div>
          </div>
        )}

        {/* Step 3: Confirmation */}
        {step === 3 && (
          <div className="text-center py-4">
            <div className="w-14 h-14 rounded-full bg-green-100 flex items-center justify-center mx-auto mb-4">
              <svg className="w-7 h-7 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
              </svg>
            </div>
            <h3 className="text-base font-semibold text-label-primary mb-2">
              Credentials Saved
            </h3>
            <p className="text-sm text-label-secondary mb-6">
              Credentials saved successfully. Your devices will be scanned on the next
              check-in cycle.
            </p>
            <button
              onClick={handleClose}
              className="px-6 py-2 text-sm font-medium bg-blue-600 text-white rounded-md hover:bg-blue-700 transition-colors"
            >
              Done
            </button>
          </div>
        )}
      </div>
    </div>
  );
};

export default CredentialEntryModal;
