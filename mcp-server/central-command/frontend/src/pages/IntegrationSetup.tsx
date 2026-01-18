/**
 * Integration Setup Wizard
 *
 * Multi-step wizard for adding new cloud integrations:
 * 1. Select provider
 * 2. Configure credentials (OAuth or AWS IAM)
 * 3. Test connection
 * 4. Start initial sync
 */

import { useState } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import {
  useCreateIntegration,
  useAWSSetupInstructions,
  useGenerateAWSExternalId,
} from '../hooks/useIntegrations';
import {
  IntegrationProvider,
  IntegrationCreateRequest,
  PROVIDER_INFO,
} from '../utils/integrationsApi';

type SetupStep = 'provider' | 'credentials' | 'complete';

// SECURITY: Whitelist of allowed OAuth redirect domains
const ALLOWED_OAUTH_DOMAINS = [
  'accounts.google.com',
  'login.microsoftonline.com',
  'login.live.com',
  'login.windows.net',
  'oauth.okta.com',
  '.okta.com', // Allow all Okta subdomains
];

/**
 * SECURITY: Validate that an OAuth redirect URL is from a trusted provider.
 * Prevents open redirect attacks where a malicious server could return
 * an attacker-controlled URL.
 */
function isValidOAuthRedirect(url: string): boolean {
  try {
    const parsed = new URL(url);

    // Must be HTTPS
    if (parsed.protocol !== 'https:') {
      return false;
    }

    const hostname = parsed.hostname.toLowerCase();

    // Check against whitelist
    return ALLOWED_OAUTH_DOMAINS.some(domain => {
      if (domain.startsWith('.')) {
        // Wildcard domain match (e.g., .okta.com matches company.okta.com)
        return hostname.endsWith(domain) || hostname === domain.slice(1);
      }
      return hostname === domain;
    });
  } catch {
    return false;
  }
}

// Provider selection card
function ProviderCard({
  provider,
  selected,
  onSelect,
}: {
  provider: IntegrationProvider;
  selected: boolean;
  onSelect: () => void;
}) {
  const info = PROVIDER_INFO[provider];

  return (
    <button
      onClick={onSelect}
      className={`text-left p-4 rounded-lg border-2 transition-all ${
        selected
          ? 'border-blue-500 bg-blue-500/10'
          : 'border-gray-700 bg-gray-800 hover:border-gray-600'
      }`}
    >
      <div className="flex items-center gap-3 mb-2">
        <div
          className="w-10 h-10 rounded-lg flex items-center justify-center font-bold"
          style={{ backgroundColor: info.color + '20', color: info.color }}
        >
          {provider === 'aws' ? 'AWS' : provider === 'google_workspace' ? 'G' : provider === 'okta' ? 'O' : provider === 'microsoft_security' ? '🛡' : 'M'}
        </div>
        <div>
          <h3 className="font-semibold text-white">{info.name}</h3>
          <span className="text-xs text-gray-400">
            {info.setupType === 'oauth' ? 'OAuth 2.0' : 'IAM Role'}
          </span>
        </div>
      </div>
      <p className="text-sm text-gray-400">{info.description}</p>
    </button>
  );
}

// AWS credential form
function AWSCredentialsForm({
  values,
  onChange,
  onGenerateExternalId,
  generatingId,
}: {
  values: Partial<IntegrationCreateRequest>;
  onChange: (field: string, value: string | string[]) => void;
  onGenerateExternalId: () => void;
  generatingId: boolean;
}) {
  const { data: instructions } = useAWSSetupInstructions();
  const [showInstructions, setShowInstructions] = useState(false);

  return (
    <div className="space-y-4">
      <div>
        <label className="block text-sm font-medium text-gray-300 mb-1">
          Integration Name *
        </label>
        <input
          type="text"
          value={values.name || ''}
          onChange={(e) => onChange('name', e.target.value)}
          placeholder="e.g., Production AWS Account"
          className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-lg text-white focus:ring-2 focus:ring-blue-500 focus:border-transparent"
        />
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-300 mb-1">
          IAM Role ARN *
        </label>
        <input
          type="text"
          value={values.aws_role_arn || ''}
          onChange={(e) => onChange('aws_role_arn', e.target.value)}
          placeholder="arn:aws:iam::123456789012:role/OsirisCareAuditRole"
          className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-lg text-white focus:ring-2 focus:ring-blue-500 focus:border-transparent font-mono text-sm"
        />
        <p className="text-xs text-gray-500 mt-1">
          The ARN of the IAM role we will assume to access your AWS account
        </p>
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-300 mb-1">
          External ID *
        </label>
        <div className="flex gap-2">
          <input
            type="text"
            value={values.aws_external_id || ''}
            onChange={(e) => onChange('aws_external_id', e.target.value)}
            placeholder="Click 'Generate' to create a secure ID"
            className="flex-1 px-3 py-2 bg-gray-700 border border-gray-600 rounded-lg text-white focus:ring-2 focus:ring-blue-500 focus:border-transparent font-mono text-sm"
          />
          <button
            type="button"
            onClick={onGenerateExternalId}
            disabled={generatingId}
            className="px-3 py-2 bg-gray-600 text-white rounded-lg hover:bg-gray-500 disabled:opacity-50"
          >
            {generatingId ? 'Generating...' : 'Generate'}
          </button>
        </div>
        <p className="text-xs text-gray-500 mt-1">
          A unique ID to prevent confused deputy attacks. Include this in your role's trust policy.
        </p>
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-300 mb-1">
          Regions (comma-separated)
        </label>
        <input
          type="text"
          value={(values.aws_regions || ['us-east-1']).join(', ')}
          onChange={(e) =>
            onChange(
              'aws_regions',
              e.target.value.split(',').map((r) => r.trim()).filter(Boolean)
            )
          }
          placeholder="us-east-1, us-west-2"
          className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-lg text-white focus:ring-2 focus:ring-blue-500 focus:border-transparent"
        />
      </div>

      {/* Setup instructions toggle */}
      <div className="pt-4 border-t border-gray-700">
        <button
          type="button"
          onClick={() => setShowInstructions(!showInstructions)}
          className="text-blue-400 hover:text-blue-300 text-sm flex items-center gap-1"
        >
          {showInstructions ? 'Hide' : 'Show'} IAM Role Setup Instructions
          <svg
            className={`w-4 h-4 transition-transform ${showInstructions ? 'rotate-180' : ''}`}
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
          </svg>
        </button>

        {showInstructions && instructions && (
          <div className="mt-4 space-y-4">
            <div className="bg-gray-800 rounded-lg p-4 border border-gray-700">
              <h4 className="font-medium text-white mb-2">Setup Instructions</h4>
              <pre className="text-xs text-gray-300 whitespace-pre-wrap overflow-x-auto">
                {instructions.instructions}
              </pre>
            </div>

            <div className="bg-gray-800 rounded-lg p-4 border border-gray-700">
              <div className="flex items-center justify-between mb-2">
                <h4 className="font-medium text-white">CloudFormation Template</h4>
                <button
                  type="button"
                  onClick={() =>
                    navigator.clipboard.writeText(
                      instructions.cloudformation_template.replace(
                        'YOUR_EXTERNAL_ID_HERE',
                        values.aws_external_id || 'YOUR_EXTERNAL_ID_HERE'
                      )
                    )
                  }
                  className="text-xs text-blue-400 hover:text-blue-300"
                >
                  Copy
                </button>
              </div>
              <pre className="text-xs text-gray-300 overflow-x-auto max-h-48 overflow-y-auto bg-gray-900 p-2 rounded">
                {instructions.cloudformation_template.replace(
                  'YOUR_EXTERNAL_ID_HERE',
                  values.aws_external_id || 'YOUR_EXTERNAL_ID_HERE'
                )}
              </pre>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// OAuth credential form
function OAuthCredentialsForm({
  provider,
  values,
  onChange,
}: {
  provider: IntegrationProvider;
  values: Partial<IntegrationCreateRequest>;
  onChange: (field: string, value: string) => void;
}) {
  const info = PROVIDER_INFO[provider];

  return (
    <div className="space-y-4">
      <div>
        <label className="block text-sm font-medium text-gray-300 mb-1">
          Integration Name *
        </label>
        <input
          type="text"
          value={values.name || ''}
          onChange={(e) => onChange('name', e.target.value)}
          placeholder={`e.g., ${info.name} Production`}
          className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-lg text-white focus:ring-2 focus:ring-blue-500 focus:border-transparent"
        />
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-300 mb-1">
          OAuth Client ID *
        </label>
        <input
          type="text"
          value={values.oauth_client_id || ''}
          onChange={(e) => onChange('oauth_client_id', e.target.value)}
          placeholder="Client ID from your OAuth app"
          className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-lg text-white focus:ring-2 focus:ring-blue-500 focus:border-transparent font-mono text-sm"
        />
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-300 mb-1">
          OAuth Client Secret *
        </label>
        <input
          type="password"
          value={values.oauth_client_secret || ''}
          onChange={(e) => onChange('oauth_client_secret', e.target.value)}
          placeholder="Client secret from your OAuth app"
          className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-lg text-white focus:ring-2 focus:ring-blue-500 focus:border-transparent"
        />
      </div>

      {(provider === 'azure_ad' || provider === 'microsoft_security') && (
        <div>
          <label className="block text-sm font-medium text-gray-300 mb-1">
            Tenant ID *
          </label>
          <input
            type="text"
            value={values.oauth_tenant_id || ''}
            onChange={(e) => onChange('oauth_tenant_id', e.target.value)}
            placeholder="Azure AD tenant ID (GUID)"
            className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-lg text-white focus:ring-2 focus:ring-blue-500 focus:border-transparent font-mono text-sm"
          />
        </div>
      )}

      {provider === 'okta' && (
        <div>
          <label className="block text-sm font-medium text-gray-300 mb-1">
            Okta Domain *
          </label>
          <input
            type="text"
            value={values.okta_domain || ''}
            onChange={(e) => onChange('okta_domain', e.target.value)}
            placeholder="yourcompany.okta.com"
            className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-lg text-white focus:ring-2 focus:ring-blue-500 focus:border-transparent"
          />
        </div>
      )}

      {provider === 'google_workspace' && (
        <div>
          <label className="block text-sm font-medium text-gray-300 mb-1">
            Customer ID (optional)
          </label>
          <input
            type="text"
            value={values.google_customer_id || ''}
            onChange={(e) => onChange('google_customer_id', e.target.value)}
            placeholder="my_customer (default)"
            className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-lg text-white focus:ring-2 focus:ring-blue-500 focus:border-transparent"
          />
          <p className="text-xs text-gray-500 mt-1">
            Leave blank to use "my_customer" which refers to your own domain
          </p>
        </div>
      )}

      <div className="p-4 bg-blue-900/20 border border-blue-800 rounded-lg">
        <h4 className="font-medium text-blue-400 mb-2">OAuth App Setup</h4>
        <p className="text-sm text-gray-300">
          {provider === 'google_workspace' && (
            <>
              Create an OAuth app in the Google Cloud Console with the Admin SDK API enabled.
              Required scopes: <code className="text-xs bg-gray-800 px-1 rounded">admin.directory.user.readonly</code>,{' '}
              <code className="text-xs bg-gray-800 px-1 rounded">admin.directory.group.readonly</code>
            </>
          )}
          {provider === 'azure_ad' && (
            <>
              Register an application in Azure AD with the Microsoft Graph API permissions.
              Required permissions: <code className="text-xs bg-gray-800 px-1 rounded">User.Read.All</code>,{' '}
              <code className="text-xs bg-gray-800 px-1 rounded">Group.Read.All</code>,{' '}
              <code className="text-xs bg-gray-800 px-1 rounded">Policy.Read.All</code>
            </>
          )}
          {provider === 'microsoft_security' && (
            <>
              Register an application in Azure AD with Microsoft Graph security permissions.
              Required: <code className="text-xs bg-gray-800 px-1 rounded">SecurityEvents.Read.All</code>,{' '}
              <code className="text-xs bg-gray-800 px-1 rounded">DeviceManagementManagedDevices.Read.All</code>,{' '}
              <code className="text-xs bg-gray-800 px-1 rounded">Device.Read.All</code>
            </>
          )}
          {provider === 'okta' && (
            <>
              Create an OAuth app in your Okta Admin Console with the Okta API scopes.
              Required scopes: <code className="text-xs bg-gray-800 px-1 rounded">okta.users.read</code>,{' '}
              <code className="text-xs bg-gray-800 px-1 rounded">okta.groups.read</code>
            </>
          )}
        </p>
      </div>
    </div>
  );
}

// Main setup wizard component
export default function IntegrationSetup() {
  const { siteId } = useParams<{ siteId: string }>();
  const navigate = useNavigate();

  const [step, setStep] = useState<SetupStep>('provider');
  const [selectedProvider, setSelectedProvider] = useState<IntegrationProvider | null>(null);
  const [formValues, setFormValues] = useState<Partial<IntegrationCreateRequest>>({});
  const [error, setError] = useState<string | null>(null);

  const createIntegration = useCreateIntegration();
  const generateExternalId = useGenerateAWSExternalId();

  if (!siteId) {
    return <div className="p-6 text-gray-400">Site ID required</div>;
  }

  const handleProviderSelect = (provider: IntegrationProvider) => {
    setSelectedProvider(provider);
    setFormValues({ provider, aws_regions: ['us-east-1'] });
  };

  const handleFieldChange = (field: string, value: string | string[]) => {
    setFormValues((prev) => ({ ...prev, [field]: value }));
  };

  const handleGenerateExternalId = async () => {
    try {
      const result = await generateExternalId.mutateAsync();
      setFormValues((prev) => ({ ...prev, aws_external_id: result.external_id }));
    } catch (err) {
      setError('Failed to generate external ID');
    }
  };

  const handleSubmit = async () => {
    if (!selectedProvider || !formValues.name) {
      setError('Please fill in all required fields');
      return;
    }

    setError(null);

    try {
      const result = await createIntegration.mutateAsync({
        siteId,
        data: {
          ...formValues,
          provider: selectedProvider,
        } as IntegrationCreateRequest,
      });

      // For OAuth providers, redirect to auth URL
      // SECURITY: Validate the auth_url against our whitelist to prevent open redirect
      if (result.auth_url) {
        if (isValidOAuthRedirect(result.auth_url)) {
          window.location.href = result.auth_url;
          return;
        } else {
          setError('Invalid OAuth redirect URL. Please contact support.');
          return;
        }
      }

      // For AWS, go to complete step
      setStep('complete');
    } catch (err: any) {
      setError(err.message || 'Failed to create integration');
    }
  };

  const isFormValid = () => {
    if (!selectedProvider || !formValues.name) return false;

    if (selectedProvider === 'aws') {
      return !!(formValues.aws_role_arn && formValues.aws_external_id);
    }

    if (selectedProvider === 'azure_ad' || selectedProvider === 'microsoft_security') {
      return !!(formValues.oauth_client_id && formValues.oauth_client_secret && formValues.oauth_tenant_id);
    }

    if (selectedProvider === 'okta') {
      return !!(formValues.oauth_client_id && formValues.oauth_client_secret && formValues.okta_domain);
    }

    // google_workspace
    return !!(formValues.oauth_client_id && formValues.oauth_client_secret);
  };

  return (
    <div className="min-h-screen bg-gray-900 p-6">
      {/* Header */}
      <div className="mb-8">
        <div className="flex items-center gap-2 text-sm text-gray-400 mb-1">
          <Link to="/sites" className="hover:text-white">Sites</Link>
          <span>/</span>
          <Link to={`/sites/${siteId}`} className="hover:text-white">{siteId}</Link>
          <span>/</span>
          <Link to={`/sites/${siteId}/integrations`} className="hover:text-white">Integrations</Link>
          <span>/</span>
          <span className="text-white">Setup</span>
        </div>
        <h1 className="text-2xl font-bold text-white">Add Cloud Integration</h1>
      </div>

      {/* Progress indicator */}
      <div className="flex items-center gap-4 mb-8">
        {(['provider', 'credentials', 'complete'] as SetupStep[]).map((s, i) => (
          <div key={s} className="flex items-center">
            <div
              className={`w-8 h-8 rounded-full flex items-center justify-center font-medium ${
                step === s
                  ? 'bg-blue-600 text-white'
                  : i < ['provider', 'credentials', 'complete'].indexOf(step)
                  ? 'bg-green-600 text-white'
                  : 'bg-gray-700 text-gray-400'
              }`}
            >
              {i + 1}
            </div>
            <span className="ml-2 text-sm text-gray-400 capitalize">{s}</span>
            {i < 2 && <div className="w-8 h-px bg-gray-700 mx-4" />}
          </div>
        ))}
      </div>

      {/* Error display */}
      {error && (
        <div className="mb-6 p-4 bg-red-900/20 border border-red-800 rounded-lg">
          <p className="text-red-400">{error}</p>
        </div>
      )}

      {/* Step content */}
      <div className="max-w-2xl">
        {step === 'provider' && (
          <>
            <h2 className="text-lg font-semibold text-white mb-4">Select Provider</h2>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
              {(Object.keys(PROVIDER_INFO) as IntegrationProvider[]).map((provider) => (
                <ProviderCard
                  key={provider}
                  provider={provider}
                  selected={selectedProvider === provider}
                  onSelect={() => handleProviderSelect(provider)}
                />
              ))}
            </div>
            <button
              onClick={() => setStep('credentials')}
              disabled={!selectedProvider}
              className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              Continue
            </button>
          </>
        )}

        {step === 'credentials' && selectedProvider && (
          <>
            <div className="flex items-center gap-2 mb-4">
              <button
                onClick={() => setStep('provider')}
                className="text-gray-400 hover:text-white"
              >
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
                </svg>
              </button>
              <h2 className="text-lg font-semibold text-white">
                Configure {PROVIDER_INFO[selectedProvider].name}
              </h2>
            </div>

            {selectedProvider === 'aws' ? (
              <AWSCredentialsForm
                values={formValues}
                onChange={handleFieldChange}
                onGenerateExternalId={handleGenerateExternalId}
                generatingId={generateExternalId.isPending}
              />
            ) : (
              <OAuthCredentialsForm
                provider={selectedProvider}
                values={formValues}
                onChange={handleFieldChange}
              />
            )}

            <div className="mt-6 flex items-center gap-4">
              <button
                onClick={handleSubmit}
                disabled={!isFormValid() || createIntegration.isPending}
                className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {createIntegration.isPending
                  ? 'Creating...'
                  : selectedProvider === 'aws'
                  ? 'Test & Create'
                  : 'Continue to OAuth'}
              </button>
              <button
                onClick={() => navigate(`/sites/${siteId}/integrations`)}
                className="px-4 py-2 text-gray-400 hover:text-white"
              >
                Cancel
              </button>
            </div>
          </>
        )}

        {step === 'complete' && (
          <div className="text-center py-12">
            <div className="w-16 h-16 bg-green-600 rounded-full flex items-center justify-center mx-auto mb-4">
              <svg className="w-8 h-8 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
              </svg>
            </div>
            <h2 className="text-xl font-semibold text-white mb-2">Integration Created!</h2>
            <p className="text-gray-400 mb-6">
              Your {selectedProvider && PROVIDER_INFO[selectedProvider].name} integration has been configured.
              The initial sync will begin shortly.
            </p>
            <Link
              to={`/sites/${siteId}/integrations`}
              className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 inline-block"
            >
              View Integrations
            </Link>
          </div>
        )}
      </div>
    </div>
  );
}
