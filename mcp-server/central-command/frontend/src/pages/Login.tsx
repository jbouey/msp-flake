import React, { useState, useEffect } from 'react';
import { useAuth } from '../contexts/AuthContext';
import { useSearchParams } from 'react-router-dom';
import { OsirisCareLeaf } from '../components/shared';

interface LoginProps {
  onSuccess: () => void;
}

interface OAuthProviders {
  google: boolean;
  microsoft: boolean;
}

// OAuth error messages
const OAUTH_ERROR_MESSAGES: Record<string, string> = {
  invalid_state: 'Session expired. Please try again.',
  token_exchange_failed: 'Authentication failed. Please try again.',
  userinfo_failed: 'Could not retrieve account information.',
  domain_not_allowed: 'Your email domain is not authorized for this application.',
  account_disabled: 'Your account has been disabled.',
  pending_approval: 'Your account is pending admin approval.',
  email_exists: 'An account with this email already exists. Please sign in with your password.',
  registration_disabled: 'New account registration is not allowed via OAuth.',
  provider_not_configured: 'OAuth provider is not configured.',
  missing_params: 'Invalid OAuth callback.',
};

export const Login: React.FC<LoginProps> = ({ onSuccess }) => {
  const { login } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [oauthLoading, setOauthLoading] = useState<string | null>(null);
  const [providers, setProviders] = useState<OAuthProviders>({ google: false, microsoft: false });
  const [providersLoading, setProvidersLoading] = useState(true);

  // Fetch enabled OAuth providers
  useEffect(() => {
    const fetchProviders = async () => {
      try {
        const response = await fetch('/api/auth/oauth/config');
        if (response.ok) {
          const data = await response.json();
          setProviders(data.providers || { google: false, microsoft: false });
        }
      } catch (err) {
        console.error('Failed to fetch OAuth providers:', err);
      } finally {
        setProvidersLoading(false);
      }
    };
    fetchProviders();
  }, []);

  // Handle OAuth error from URL params
  useEffect(() => {
    const oauthError = searchParams.get('oauth_error');
    const errorDescription = searchParams.get('error_description');
    const isNewUser = searchParams.get('new_user') === 'true';

    if (oauthError) {
      let message = OAUTH_ERROR_MESSAGES[oauthError] || `OAuth error: ${oauthError}`;
      if (errorDescription) {
        message += ` (${errorDescription})`;
      }
      if (oauthError === 'pending_approval' && isNewUser) {
        message = 'Your account has been created and is pending admin approval. You will be notified when approved.';
      }
      setError(message);
      // Clear URL params
      setSearchParams({});
    }
  }, [searchParams, setSearchParams]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setIsLoading(true);

    const result = await login(username, password);
    setIsLoading(false);

    if (result.success) {
      onSuccess();
    } else {
      setError(result.error || 'Invalid username or password');
    }
  };

  const handleOAuthLogin = async (provider: 'google' | 'microsoft') => {
    setOauthLoading(provider);
    setError('');

    try {
      const response = await fetch(`/api/auth/oauth/${provider}/authorize`);
      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.detail || 'Failed to start OAuth flow');
      }

      const data = await response.json();
      // Redirect to OAuth provider
      window.location.href = data.auth_url;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'OAuth login failed');
      setOauthLoading(null);
    }
  };

  const hasOAuthProviders = providers.google || providers.microsoft;

  return (
    <div className="min-h-screen flex items-center justify-center p-4 relative overflow-hidden">
      {/* Gradient background */}
      <div
        className="absolute inset-0"
        style={{
          background: 'linear-gradient(135deg, #F2F2F7 0%, #E8E0F0 30%, #D6E8F7 60%, #F2F2F7 100%)',
        }}
      />
      {/* Subtle radial accent */}
      <div
        className="absolute top-[-20%] right-[-10%] w-[600px] h-[600px] rounded-full opacity-30"
        style={{
          background: 'radial-gradient(circle, rgba(0, 122, 255, 0.12) 0%, transparent 70%)',
        }}
      />
      <div
        className="absolute bottom-[-15%] left-[-5%] w-[500px] h-[500px] rounded-full opacity-25"
        style={{
          background: 'radial-gradient(circle, rgba(88, 86, 214, 0.1) 0%, transparent 70%)',
        }}
      />

      <div className="w-full max-w-[380px] relative z-10 animate-fade-in">
        {/* Logo */}
        <div className="text-center mb-8">
          <div
            className="w-16 h-16 rounded-ios-xl mx-auto flex items-center justify-center mb-4"
            style={{
              background: 'linear-gradient(135deg, #14A89E 0%, #3CBCB4 100%)',
              boxShadow: '0 2px 16px rgba(60, 188, 180, 0.4)',
            }}
          >
            <OsirisCareLeaf className="w-8 h-8" color="white" />
          </div>
          <h1 className="text-2xl font-bold text-label-primary tracking-tight">OsirisCare</h1>
          <p className="text-label-tertiary text-sm mt-1">MSP compliance dashboard</p>
        </div>

        {/* Login Card */}
        <div
          className="rounded-ios-xl p-7"
          style={{
            background: 'rgba(255, 255, 255, 0.82)',
            backdropFilter: 'blur(40px) saturate(200%)',
            WebkitBackdropFilter: 'blur(40px) saturate(200%)',
            border: '1px solid rgba(255, 255, 255, 0.5)',
            boxShadow: '0 2px 32px rgba(0, 0, 0, 0.06), 0 0.5px 0 rgba(255, 255, 255, 0.4) inset',
          }}
        >
          <form onSubmit={handleSubmit} className="space-y-5">
            {error && (
              <div className="p-3 bg-ios-red/8 border border-ios-red/15 rounded-ios-md animate-fade-in">
                <p className="text-sm text-health-critical">{error}</p>
              </div>
            )}

            <div>
              <label htmlFor="username" className="block text-sm font-medium text-label-primary mb-1.5">
                Username
              </label>
              <input
                id="username"
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                required
                autoComplete="username"
                className="w-full px-3.5 py-2.5 bg-fill-quaternary rounded-ios-md border border-separator-light text-label-primary placeholder-label-tertiary outline-none transition-all focus:border-accent-primary focus:ring-2 focus:ring-accent-primary/20 text-[15px]"
                placeholder="Enter username"
              />
            </div>

            <div>
              <label htmlFor="password" className="block text-sm font-medium text-label-primary mb-1.5">
                Password
              </label>
              <input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                autoComplete="current-password"
                className="w-full px-3.5 py-2.5 bg-fill-quaternary rounded-ios-md border border-separator-light text-label-primary placeholder-label-tertiary outline-none transition-all focus:border-accent-primary focus:ring-2 focus:ring-accent-primary/20 text-[15px]"
                placeholder="Enter password"
              />
            </div>

            <button
              type="submit"
              disabled={isLoading}
              className="w-full py-2.5 text-white font-semibold rounded-ios-md transition-all disabled:opacity-50 disabled:cursor-not-allowed text-[15px]"
              style={{
                background: 'linear-gradient(135deg, #14A89E 0%, #3CBCB4 100%)',
                boxShadow: isLoading ? 'none' : '0 2px 12px rgba(60, 188, 180, 0.35)',
              }}
            >
              {isLoading ? (
                <span className="flex items-center justify-center gap-2">
                  <svg className="animate-spin h-4 w-4" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                  </svg>
                  Signing in...
                </span>
              ) : 'Sign In'}
            </button>
          </form>

          {/* OAuth Buttons */}
          {!providersLoading && hasOAuthProviders && (
            <>
              <div className="relative my-5">
                <div className="absolute inset-0 flex items-center">
                  <div className="w-full border-t border-separator-light"></div>
                </div>
                <div className="relative flex justify-center text-sm">
                  <span className="px-3 text-label-tertiary text-xs" style={{ background: 'rgba(255, 255, 255, 0.82)' }}>or continue with</span>
                </div>
              </div>

              <div className="space-y-2.5">
                {providers.google && (
                  <button
                    type="button"
                    onClick={() => handleOAuthLogin('google')}
                    disabled={oauthLoading !== null}
                    className="w-full py-2.5 px-4 bg-white border border-separator-medium rounded-ios-md hover:bg-fill-quaternary transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-3"
                  >
                    <svg className="w-5 h-5" viewBox="0 0 24 24">
                      <path
                        fill="#4285F4"
                        d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"
                      />
                      <path
                        fill="#34A853"
                        d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
                      />
                      <path
                        fill="#FBBC05"
                        d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"
                      />
                      <path
                        fill="#EA4335"
                        d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"
                      />
                    </svg>
                    <span className="text-label-primary font-medium text-sm">
                      {oauthLoading === 'google' ? 'Redirecting...' : 'Sign in with Google'}
                    </span>
                  </button>
                )}

                {providers.microsoft && (
                  <button
                    type="button"
                    onClick={() => handleOAuthLogin('microsoft')}
                    disabled={oauthLoading !== null}
                    className="w-full py-2.5 px-4 bg-white border border-separator-medium rounded-ios-md hover:bg-fill-quaternary transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-3"
                  >
                    <svg className="w-5 h-5" viewBox="0 0 23 23">
                      <rect fill="#f35325" x="1" y="1" width="10" height="10"/>
                      <rect fill="#81bc06" x="12" y="1" width="10" height="10"/>
                      <rect fill="#05a6f0" x="1" y="12" width="10" height="10"/>
                      <rect fill="#ffba08" x="12" y="12" width="10" height="10"/>
                    </svg>
                    <span className="text-label-primary font-medium text-sm">
                      {oauthLoading === 'microsoft' ? 'Redirecting...' : 'Sign in with Microsoft'}
                    </span>
                  </button>
                )}
              </div>
            </>
          )}

        </div>

        {/* Footer */}
        <p className="text-[11px] text-label-tertiary text-center mt-8 tracking-wide">
          OsirisCare MSP Compliance Platform
        </p>
      </div>
    </div>
  );
};

export default Login;
