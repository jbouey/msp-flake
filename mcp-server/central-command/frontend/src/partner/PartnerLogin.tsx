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

  // Email/password login state
  const [loginEmail, setLoginEmail] = useState('');
  const [loginPassword, setLoginPassword] = useState('');
  const [loginMode, setLoginMode] = useState<'email' | 'apikey'>('email');

  // Email signup state
  const [showEmailSignup, setShowEmailSignup] = useState(false);
  const [signupName, setSignupName] = useState('');
  const [signupEmail, setSignupEmail] = useState('');
  const [signupCompany, setSignupCompany] = useState('');
  const [signupPassword, setSignupPassword] = useState('');
  const [signupStatus, setSignupStatus] = useState<'idle' | 'loading' | 'success' | 'error'>('idle');
  const [signupMessage, setSignupMessage] = useState('');

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

  const handleEmailLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!loginEmail.trim() || !loginPassword.trim()) return;

    setStatus('loading');
    setError(null);

    try {
      const response = await fetch('/api/partner-auth/email-login-api', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: loginEmail.trim(), password: loginPassword }),
      });

      if (response.ok) {
        // Session cookie is set by the response — reload to pick it up
        window.location.href = '/partner/dashboard';
      } else {
        const err = await response.json();
        setError(err.detail || 'Login failed');
        setStatus('error');
      }
    } catch (e) {
      setError('Network error. Please try again.');
      setStatus('error');
    }
  };

  const handleOAuthLogin = (provider: 'microsoft' | 'google') => {
    setOauthLoading(provider);
    setError(null);
    // Redirect to OAuth endpoint
    window.location.href = `/api/partner-auth/${provider}`;
  };

  const handleEmailSignup = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!signupName.trim() || !signupEmail.trim()) return;

    setSignupStatus('loading');
    setError(null);

    try {
      const response = await fetch('/api/partner-auth/email-signup', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: signupName.trim(),
          email: signupEmail.trim(),
          company: signupCompany.trim() || signupName.trim(),
          password: signupPassword,
        }),
      });

      const data = await response.json();

      if (response.ok) {
        setSignupStatus('success');
        setSignupMessage(data.message);
      } else {
        setSignupStatus('error');
        setSignupMessage(data.detail || 'Signup failed');
      }
    } catch (err) {
      setSignupStatus('error');
      setSignupMessage('Network error. Please try again.');
    }
  };

  const hasOAuthProviders = providers.microsoft || providers.google;

  if (isLoading || status === 'loading') {
    return (
      <div className="min-h-screen bg-gradient-to-br from-indigo-950 via-purple-900 to-indigo-900 flex items-center justify-center p-6 relative overflow-hidden">
        <div className="absolute top-1/4 -left-32 w-96 h-96 bg-indigo-600/20 rounded-full blur-3xl" />
        <div className="absolute bottom-1/4 -right-32 w-96 h-96 bg-purple-600/20 rounded-full blur-3xl" />
        <div className="max-w-md w-full relative" style={{ background: 'rgba(255,255,255,0.85)', backdropFilter: 'blur(40px) saturate(180%)', WebkitBackdropFilter: 'blur(40px) saturate(180%)', borderRadius: '20px', boxShadow: '0 8px 32px rgba(0,0,0,0.12), inset 0 1px 0 rgba(255,255,255,0.5)' }}>
          <div className="p-8 text-center">
            <div className="w-14 h-14 mx-auto mb-4 rounded-2xl flex items-center justify-center" style={{ background: 'linear-gradient(135deg, #4F46E5 0%, #7C3AED 100%)' }}>
              <svg className="w-7 h-7 text-white animate-pulse-soft" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
              </svg>
            </div>
            <h1 className="text-xl font-semibold text-slate-900 mb-2">
              {magicToken ? 'Validating Access' : 'Loading'}
            </h1>
            <p className="text-slate-500">Please wait...</p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-indigo-950 via-purple-900 to-indigo-900 flex items-center justify-center p-6 relative overflow-hidden animate-fade-in">
      <div className="absolute top-1/4 -left-32 w-96 h-96 bg-indigo-600/20 rounded-full blur-3xl" />
      <div className="absolute bottom-1/4 -right-32 w-96 h-96 bg-purple-600/20 rounded-full blur-3xl" />
      <div className="max-w-md w-full relative z-10">
        {/* Logo */}
        <div className="text-center mb-8">
          <div
            className="w-16 h-16 rounded-2xl mx-auto mb-4 flex items-center justify-center shadow-lg"
            style={{ background: 'linear-gradient(135deg, #4F46E5 0%, #7C3AED 100%)', boxShadow: '0 4px 20px rgba(79, 70, 229, 0.4)' }}
          >
            <svg className="w-9 h-9 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
            </svg>
          </div>
          <h1 className="text-3xl font-bold text-white tracking-tight">Partner Portal</h1>
          <p className="text-indigo-200/80 mt-2">OsirisCare Reseller Dashboard</p>
        </div>

        {/* Login Card */}
        <div className="p-8" style={{ background: 'rgba(255,255,255,0.88)', backdropFilter: 'blur(40px) saturate(180%)', WebkitBackdropFilter: 'blur(40px) saturate(180%)', borderRadius: '20px', boxShadow: '0 8px 32px rgba(0,0,0,0.12), inset 0 1px 0 rgba(255,255,255,0.5)' }}>
          <h2 className="text-xl font-semibold text-slate-900 mb-2 text-center">
            Partner Login
          </h2>
          <p className="text-slate-600 text-center mb-6">
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
                  className="w-full flex items-center justify-center gap-3 px-4 py-3 bg-slate-50/80 border border-slate-200 rounded-xl hover:bg-indigo-50/80 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  <svg className="w-5 h-5" viewBox="0 0 21 21">
                    <rect fill="#f25022" x="1" y="1" width="9" height="9"/>
                    <rect fill="#7fba00" x="11" y="1" width="9" height="9"/>
                    <rect fill="#05a6f0" x="1" y="11" width="9" height="9"/>
                    <rect fill="#ffba08" x="11" y="11" width="9" height="9"/>
                  </svg>
                  <span className="font-medium text-slate-900">
                    {oauthLoading === 'microsoft' ? 'Redirecting...' : 'Sign in with Microsoft'}
                  </span>
                </button>
              )}

              {providers.google && (
                <button
                  type="button"
                  onClick={() => handleOAuthLogin('google')}
                  disabled={oauthLoading !== null}
                  className="w-full flex items-center justify-center gap-3 px-4 py-3 bg-slate-50/80 border border-slate-200 rounded-xl hover:bg-indigo-50/80 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  <svg className="w-5 h-5" viewBox="0 0 24 24">
                    <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>
                    <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
                    <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/>
                    <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/>
                  </svg>
                  <span className="font-medium text-slate-900">
                    {oauthLoading === 'google' ? 'Redirecting...' : 'Sign in with Google'}
                  </span>
                </button>
              )}

              <div className="relative my-4">
                <div className="absolute inset-0 flex items-center">
                  <div className="w-full border-t border-slate-200" />
                </div>
                <div className="relative flex justify-center text-sm">
                  <span className="px-2 bg-white text-slate-500">Or sign in manually</span>
                </div>
              </div>
            </div>
          )}

          {/* Login mode tabs */}
          <div className="flex rounded-lg bg-slate-100 p-1 mb-4">
            <button
              type="button"
              onClick={() => setLoginMode('email')}
              className={`flex-1 py-2 text-sm font-medium rounded-md transition-colors ${
                loginMode === 'email'
                  ? 'bg-white text-slate-900 shadow-sm'
                  : 'text-slate-500 hover:text-slate-700'
              }`}
            >
              Email &amp; Password
            </button>
            <button
              type="button"
              onClick={() => setLoginMode('apikey')}
              className={`flex-1 py-2 text-sm font-medium rounded-md transition-colors ${
                loginMode === 'apikey'
                  ? 'bg-white text-slate-900 shadow-sm'
                  : 'text-slate-500 hover:text-slate-700'
              }`}
            >
              API Key
            </button>
          </div>

          {loginMode === 'email' ? (
            <form onSubmit={handleEmailLogin} className="space-y-4">
              <div>
                <label htmlFor="loginEmail" className="block text-sm font-medium text-slate-700 mb-1">
                  Email
                </label>
                <input
                  type="email"
                  id="loginEmail"
                  value={loginEmail}
                  onChange={(e) => setLoginEmail(e.target.value)}
                  placeholder="you@company.com"
                  required
                  className="w-full px-4 py-3 bg-slate-50/80 border border-slate-200 rounded-xl focus:ring-2 focus:ring-indigo-500/40 focus:border-indigo-300 outline-none transition text-sm"
                />
              </div>
              <div>
                <label htmlFor="loginPassword" className="block text-sm font-medium text-slate-700 mb-1">
                  Password
                </label>
                <input
                  type="password"
                  id="loginPassword"
                  value={loginPassword}
                  onChange={(e) => setLoginPassword(e.target.value)}
                  placeholder="Enter your password"
                  required
                  className="w-full px-4 py-3 bg-slate-50/80 border border-slate-200 rounded-xl focus:ring-2 focus:ring-indigo-500/40 focus:border-indigo-300 outline-none transition text-sm"
                />
              </div>
              <button
                type="submit"
                disabled={!loginEmail.trim() || !loginPassword.trim()}
                className="w-full py-3 text-white font-semibold rounded-xl disabled:opacity-50 disabled:cursor-not-allowed transition-all hover:brightness-110"
                style={{ background: 'linear-gradient(135deg, #4F46E5 0%, #7C3AED 100%)', boxShadow: '0 4px 14px rgba(79, 70, 229, 0.35)' }}
              >
                Sign In
              </button>
            </form>
          ) : (
            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label htmlFor="apiKey" className="block text-sm font-medium text-slate-700 mb-1">
                  API Key
                </label>
                <input
                  type="password"
                  id="apiKey"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder="Enter your partner API key"
                  required
                  className="w-full px-4 py-3 bg-slate-50/80 border border-slate-200 rounded-xl focus:ring-2 focus:ring-indigo-500/40 focus:border-indigo-300 outline-none transition font-mono text-sm"
                />
              </div>
              <button
                type="submit"
                disabled={!apiKey.trim()}
                className="w-full py-3 text-white font-semibold rounded-xl disabled:opacity-50 disabled:cursor-not-allowed transition-all hover:brightness-110"
                style={{ background: 'linear-gradient(135deg, #4F46E5 0%, #7C3AED 100%)', boxShadow: '0 4px 14px rgba(79, 70, 229, 0.35)' }}
              >
                Sign In
              </button>
            </form>
          )}

          <div className="mt-6 pt-6 border-t border-slate-200">
            {!showEmailSignup ? (
              <p className="text-center text-sm text-slate-500">
                New partner?{' '}
                <button
                  type="button"
                  onClick={() => setShowEmailSignup(true)}
                  className="text-indigo-600 hover:underline font-medium"
                >
                  Request a partner account
                </button>
              </p>
            ) : signupStatus === 'success' ? (
              <div className="p-4 bg-emerald-50 border border-emerald-200 rounded-lg">
                <div className="flex items-start gap-3">
                  <svg className="w-5 h-5 text-emerald-500 mt-0.5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                  <div>
                    <p className="text-emerald-800 font-medium">Request Submitted</p>
                    <p className="text-emerald-700 text-sm mt-1">{signupMessage}</p>
                  </div>
                </div>
              </div>
            ) : (
              <div>
                <h3 className="text-sm font-semibold text-slate-700 mb-3 text-center">Request Partner Account</h3>
                <form onSubmit={handleEmailSignup} className="space-y-3">
                  <input
                    type="text"
                    value={signupName}
                    onChange={(e) => setSignupName(e.target.value)}
                    placeholder="Your name"
                    required
                    className="w-full px-3 py-2.5 bg-slate-50/80 border border-slate-200 rounded-xl focus:ring-2 focus:ring-indigo-500/40 focus:border-indigo-300 outline-none transition text-sm"
                  />
                  <input
                    type="email"
                    value={signupEmail}
                    onChange={(e) => setSignupEmail(e.target.value)}
                    placeholder="Business email"
                    required
                    className="w-full px-3 py-2.5 bg-slate-50/80 border border-slate-200 rounded-xl focus:ring-2 focus:ring-indigo-500/40 focus:border-indigo-300 outline-none transition text-sm"
                  />
                  <input
                    type="text"
                    value={signupCompany}
                    onChange={(e) => setSignupCompany(e.target.value)}
                    placeholder="Company name (optional)"
                    className="w-full px-3 py-2.5 bg-slate-50/80 border border-slate-200 rounded-xl focus:ring-2 focus:ring-indigo-500/40 focus:border-indigo-300 outline-none transition text-sm"
                  />
                  <input
                    type="password"
                    value={signupPassword}
                    onChange={(e) => setSignupPassword(e.target.value)}
                    placeholder="Password (min 8 characters)"
                    minLength={8}
                    className="w-full px-3 py-2.5 bg-slate-50/80 border border-slate-200 rounded-xl focus:ring-2 focus:ring-indigo-500/40 focus:border-indigo-300 outline-none transition text-sm"
                  />
                  {signupStatus === 'error' && (
                    <p className="text-red-600 text-sm">{signupMessage}</p>
                  )}
                  <button
                    type="submit"
                    disabled={signupStatus === 'loading' || !signupName.trim() || !signupEmail.trim()}
                    className="w-full py-2.5 text-white font-medium rounded-xl disabled:opacity-50 disabled:cursor-not-allowed transition-all text-sm"
                    style={{ background: 'linear-gradient(135deg, #4F46E5 0%, #7C3AED 100%)' }}
                  >
                    {signupStatus === 'loading' ? 'Submitting...' : 'Request Account'}
                  </button>
                  <button
                    type="button"
                    onClick={() => { setShowEmailSignup(false); setSignupStatus('idle'); }}
                    className="w-full text-center text-sm text-slate-500 hover:text-slate-700"
                  >
                    Back to login
                  </button>
                </form>
              </div>
            )}
          </div>
        </div>

        {/* Footer */}
        <p className="mt-8 text-center text-sm text-indigo-300/60">
          Powered by OsirisCare HIPAA Compliance Platform
        </p>
      </div>
    </div>
  );
};

export default PartnerLogin;
