import React, { useState, useEffect } from 'react';
import { useSearchParams, useNavigate } from 'react-router-dom';
import { usePartner } from './PartnerContext';

interface OAuthProviders {
  microsoft: boolean;
  google: boolean;
}

// OAuth error messages
const OAUTH_ERROR_MESSAGES: Record<string, string> = {
  invalid_state: 'Session expired. Please try again.',
  token_exchange_failed: 'Authentication failed. Please try again.',
  auth_failed: 'Authentication failed. Please try again.',
  missing_params: 'Invalid OAuth callback.',
  invalid_provider: 'Invalid authentication provider.',
};

export const PartnerLogin: React.FC = () => {
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const { login, isAuthenticated, isLoading, error: authError } = usePartner();

  const [apiKey, setApiKey] = useState('');
  const [status, setStatus] = useState<'idle' | 'loading' | 'error'>('idle');
  const [error, setError] = useState<string | null>(null);
  const [oauthLoading, setOauthLoading] = useState<string | null>(null);
  const [providers, setProviders] = useState<OAuthProviders>({ microsoft: false, google: false });
  const [providersLoading, setProvidersLoading] = useState(true);

  // Check for magic link token in URL
  const magicToken = searchParams.get('token');
  const oauthError = searchParams.get('error');
  const errorDescription = searchParams.get('error_description');
  const pendingApproval = searchParams.get('pending');
  const pendingEmail = searchParams.get('email');

  // Fetch OAuth providers on mount
  useEffect(() => {
    const fetchProviders = async () => {
      try {
        const response = await fetch('/api/partner-auth/providers');
        if (response.ok) {
          const data = await response.json();
          setProviders(data.providers || { microsoft: false, google: false });
        }
      } catch (err) {
        console.error('Failed to fetch OAuth providers:', err);
      } finally {
        setProvidersLoading(false);
      }
    };
    fetchProviders();
  }, []);

  // Handle OAuth errors from URL
  useEffect(() => {
    if (oauthError) {
      const message = OAUTH_ERROR_MESSAGES[oauthError] || errorDescription || `Authentication error: ${oauthError}`;
      setError(message);
      setStatus('error');
      // Clear URL params
      setSearchParams({});
    }
  }, [oauthError, errorDescription, setSearchParams]);

  useEffect(() => {
    if (magicToken) {
      handleMagicLink(magicToken);
    }
  }, [magicToken]);

  useEffect(() => {
    if (isAuthenticated && !isLoading) {
      navigate('/partner/dashboard', { replace: true });
    }
  }, [isAuthenticated, isLoading, navigate]);

  const handleMagicLink = async (token: string) => {
    setStatus('loading');
    setError(null);

    try {
      // SECURITY: POST token in body instead of URL to avoid exposure in server logs
      const response = await fetch('/api/partners/auth/magic', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token }),
      });

      if (response.ok) {
        const data = await response.json();
        // Magic link returns an API key, use it to login
        if (data.api_key) {
          await login(data.api_key);
        }
      } else {
        const err = await response.json();
        setError(err.detail || 'Invalid or expired magic link');
        setStatus('error');
      }
    } catch (e) {
      setError('Failed to validate magic link');
      setStatus('error');
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!apiKey.trim()) return;

    setStatus('loading');
    setError(null);

    const success = await login(apiKey.trim());
    if (!success) {
      setError(authError || 'Invalid API key');
      setStatus('error');
    }
  };

  const handleOAuthLogin = (provider: 'microsoft' | 'google') => {
    setOauthLoading(provider);
    setError(null);
    // Redirect to OAuth endpoint
    window.location.href = `/api/partner-auth/${provider}`;
  };

  const hasOAuthProviders = providers.microsoft || providers.google;

  if (isLoading || status === 'loading') {
    return (
      <div className="min-h-screen bg-gradient-to-br from-indigo-900 via-purple-900 to-indigo-800 flex items-center justify-center p-6">
        <div className="max-w-md w-full bg-white rounded-2xl shadow-xl p-8 text-center">
          <div className="w-16 h-16 mx-auto mb-4 flex items-center justify-center">
            <div className="animate-spin rounded-full h-12 w-12 border-4 border-indigo-500 border-t-transparent" />
          </div>
          <h1 className="text-xl font-semibold text-gray-900 mb-2">
            {magicToken ? 'Validating Access' : 'Loading'}
          </h1>
          <p className="text-gray-600">Please wait...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-indigo-900 via-purple-900 to-indigo-800 flex items-center justify-center p-6">
      <div className="max-w-md w-full">
        {/* Logo */}
        <div className="text-center mb-8">
          <div className="w-16 h-16 bg-white rounded-2xl shadow-lg mx-auto mb-4 flex items-center justify-center">
            <svg className="w-10 h-10 text-indigo-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
            </svg>
          </div>
          <h1 className="text-3xl font-bold text-white">Partner Portal</h1>
          <p className="text-indigo-200 mt-2">OsirisCare Reseller Dashboard</p>
        </div>

        {/* Login Card */}
        <div className="bg-white rounded-2xl shadow-xl p-8">
          <h2 className="text-xl font-semibold text-gray-900 mb-2 text-center">
            Partner Login
          </h2>
          <p className="text-gray-600 text-center mb-6">
            Sign in with your business identity to access your dashboard.
          </p>

          {pendingApproval && (
            <div className="mb-6 p-4 bg-amber-50 border border-amber-200 rounded-lg">
              <div className="flex items-start gap-3">
                <svg className="w-5 h-5 text-amber-500 mt-0.5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                <div>
                  <p className="text-amber-800 font-medium">Account Pending Approval</p>
                  <p className="text-amber-700 text-sm mt-1">
                    Your signup request for <strong>{pendingEmail}</strong> has been submitted and is awaiting administrator approval.
                  </p>
                  <p className="text-amber-600 text-sm mt-2">
                    You'll receive an email once your account is approved.
                  </p>
                </div>
              </div>
            </div>
          )}

          {(error || status === 'error') && !pendingApproval && (
            <div className="mb-6 p-4 bg-red-50 border border-red-200 rounded-lg">
              <p className="text-red-700 text-sm">{error || 'Authentication failed'}</p>
            </div>
          )}

          {/* OAuth Buttons */}
          {!providersLoading && hasOAuthProviders && (
            <div className="space-y-3 mb-6">
              {providers.microsoft && (
                <button
                  type="button"
                  onClick={() => handleOAuthLogin('microsoft')}
                  disabled={oauthLoading !== null}
                  className="w-full flex items-center justify-center gap-3 px-4 py-3 border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  <svg className="w-5 h-5" viewBox="0 0 21 21">
                    <rect fill="#f25022" x="1" y="1" width="9" height="9"/>
                    <rect fill="#7fba00" x="11" y="1" width="9" height="9"/>
                    <rect fill="#05a6f0" x="1" y="11" width="9" height="9"/>
                    <rect fill="#ffba08" x="11" y="11" width="9" height="9"/>
                  </svg>
                  <span className="font-medium text-gray-900">
                    {oauthLoading === 'microsoft' ? 'Redirecting...' : 'Sign in with Microsoft'}
                  </span>
                </button>
              )}

              {providers.google && (
                <button
                  type="button"
                  onClick={() => handleOAuthLogin('google')}
                  disabled={oauthLoading !== null}
                  className="w-full flex items-center justify-center gap-3 px-4 py-3 border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  <svg className="w-5 h-5" viewBox="0 0 24 24">
                    <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>
                    <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
                    <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/>
                    <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/>
                  </svg>
                  <span className="font-medium text-gray-900">
                    {oauthLoading === 'google' ? 'Redirecting...' : 'Sign in with Google Workspace'}
                  </span>
                </button>
              )}

              <div className="relative my-4">
                <div className="absolute inset-0 flex items-center">
                  <div className="w-full border-t border-gray-200" />
                </div>
                <div className="relative flex justify-center text-sm">
                  <span className="px-2 bg-white text-gray-500">Or use API key</span>
                </div>
              </div>
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label htmlFor="apiKey" className="block text-sm font-medium text-gray-700 mb-1">
                API Key
              </label>
              <input
                type="password"
                id="apiKey"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder="Enter your partner API key"
                required
                className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent outline-none transition font-mono text-sm"
              />
            </div>

            <button
              type="submit"
              disabled={!apiKey.trim()}
              className="w-full py-3 bg-indigo-600 text-white font-medium rounded-lg hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed transition"
            >
              Sign In
            </button>
          </form>

          <div className="mt-6 pt-6 border-t border-gray-200">
            <p className="text-center text-sm text-gray-500">
              Don't have an API key?{' '}
              <a href="mailto:partners@osiriscare.net" className="text-indigo-600 hover:underline">
                Contact us
              </a>
            </p>
          </div>
        </div>

        {/* Footer */}
        <p className="mt-8 text-center text-sm text-indigo-300">
          Powered by OsirisCare HIPAA Compliance Platform
        </p>
      </div>
    </div>
  );
};

export default PartnerLogin;
