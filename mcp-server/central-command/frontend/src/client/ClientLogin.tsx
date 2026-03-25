import React, { useState, useEffect, useCallback } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useClient } from './ClientContext';
import { OsirisCareLeaf } from '../components/shared';
import { BRANDING, SSO_LABELS, WHITE_LABEL } from '../constants';
import { useBranding } from '../hooks/useBranding';

interface ClientLoginProps {
  slug?: string;
}

export const ClientLogin: React.FC<ClientLoginProps> = ({ slug: slugProp }) => {
  const navigate = useNavigate();
  const params = useParams<{ slug?: string }>();
  const slug = slugProp ?? params.slug;
  const { isAuthenticated, isLoading } = useClient();
  const { branding, loading: brandingLoading } = useBranding(slug);

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loginMode, setLoginMode] = useState<'password' | 'magic'>('password');
  const [status, setStatus] = useState<'idle' | 'loading' | 'sent' | 'error'>('idle');
  const [error, setError] = useState<string | null>(null);

  // MFA state
  const [mfaRequired, setMfaRequired] = useState(false);
  const [mfaToken, setMfaToken] = useState('');
  const [totpCode, setTotpCode] = useState('');
  const [mfaError, setMfaError] = useState<string | null>(null);
  const [mfaLoading, setMfaLoading] = useState(false);

  // SSO state
  const [ssoLoading, setSsoLoading] = useState(false);
  const [ssoEnforced, setSsoEnforced] = useState(false);

  // Derived branding values
  const brandName = branding?.brand_name ?? WHITE_LABEL.DEFAULT_BRAND;
  const brandPrimary = branding?.primary_color ?? WHITE_LABEL.DEFAULT_PRIMARY;
  const brandTagline = branding?.tagline ?? WHITE_LABEL.DEFAULT_TAGLINE;
  const brandLogo = branding?.logo_url ?? null;
  const hasPartnerBranding = !!slug && !!branding && branding.partner_slug !== '';

  // Button gradient derived from partner primary color
  const buttonBg = `linear-gradient(135deg, ${brandPrimary} 0%, ${brandPrimary}cc 100%)`;
  const buttonShadow = `0 4px 14px ${brandPrimary}59`;

  // Footer text
  const footerText = hasPartnerBranding
    ? WHITE_LABEL.POWERED_BY
    : `Powered by ${BRANDING.name} ${BRANDING.tagline}`;

  useEffect(() => {
    if (isAuthenticated && !isLoading) {
      navigate('/client/dashboard', { replace: true });
    }
  }, [isAuthenticated, isLoading, navigate]);

  const handleSsoLogin = useCallback(async () => {
    const ssoEmail = email.trim().toLowerCase();
    if (!ssoEmail) {
      setError('Enter your email address to sign in with SSO.');
      return;
    }

    setSsoLoading(true);
    setError(null);

    try {
      const response = await fetch('/api/client/auth/sso/authorize', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: ssoEmail }),
      });

      if (response.ok) {
        const data = await response.json();
        if (data.auth_url) {
          window.location.href = data.auth_url;
          return;
        }
      } else if (response.status === 404) {
        setError(SSO_LABELS.sso_not_configured);
      } else {
        const err = await response.json().catch(() => null);
        setError(err?.detail || 'SSO authorization failed. Please try again.');
      }
    } catch {
      setError('Failed to connect. Please try again.');
    } finally {
      setSsoLoading(false);
    }
  }, [email]);

  // Check SSO enforcement when email loses focus
  const handleEmailBlur = useCallback(async () => {
    const trimmed = email.trim().toLowerCase();
    if (!trimmed || !trimmed.includes('@')) return;

    try {
      const response = await fetch('/api/client/auth/sso/authorize', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: trimmed }),
      });

      if (response.ok) {
        const data = await response.json();
        if (data.sso_enforced) {
          setSsoEnforced(true);
          return;
        }
      }
    } catch {
      // Check failed silently — non-critical
    }
    setSsoEnforced(false);
  }, [email]);

  const handlePasswordLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email.trim() || !password.trim()) return;

    setStatus('loading');
    setError(null);

    try {
      const response = await fetch('/api/client/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: email.trim().toLowerCase(), password }),
      });

      if (response.ok) {
        const data = await response.json();
        if (data.status === 'mfa_required' && data.mfa_token) {
          setMfaRequired(true);
          setMfaToken(data.mfa_token);
          setStatus('idle');
          return;
        }
        window.location.href = '/client/dashboard';
      } else {
        const err = await response.json();
        setError(err.detail || 'Invalid email or password');
        setStatus('error');
      }
    } catch {
      setError('Failed to connect. Please try again.');
      setStatus('error');
    }
  };

  const handleTotpVerify = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!totpCode.trim()) return;

    setMfaLoading(true);
    setMfaError(null);

    try {
      const response = await fetch('/api/client/auth/verify-totp', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mfa_token: mfaToken, totp_code: totpCode.trim() }),
      });

      if (response.ok) {
        window.location.href = '/client/dashboard';
      } else {
        setMfaError('Invalid code. Please try again.');
        setTotpCode('');
        setMfaLoading(false);
      }
    } catch {
      setMfaError('Network error. Please try again.');
      setMfaLoading(false);
    }
  };

  const handleMagicLink = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email.trim()) return;

    setStatus('loading');
    setError(null);

    try {
      await fetch('/api/client/auth/request-magic-link', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: email.trim().toLowerCase() }),
      });

      // Always show success to prevent email enumeration
      setStatus('sent');
    } catch {
      setError('Failed to connect. Please try again.');
      setStatus('error');
    }
  };

  // Logo element — partner logo or default OsirisCare leaf
  const renderLogo = (size: 'sm' | 'lg' = 'lg') => {
    const dimension = size === 'lg' ? 'w-16 h-16' : 'w-14 h-14';
    const iconDimension = size === 'lg' ? 'w-9 h-9' : 'w-7 h-7';

    if (brandLogo) {
      return (
        <img
          src={brandLogo}
          alt={`${brandName} logo`}
          className={`${dimension} rounded-2xl mx-auto mb-4 object-contain shadow-lg`}
        />
      );
    }

    return (
      <div
        className={`${dimension} rounded-2xl mx-auto mb-4 flex items-center justify-center shadow-lg`}
        style={{ background: buttonBg, boxShadow: `0 4px 20px ${brandPrimary}66` }}
      >
        <OsirisCareLeaf className={iconDimension} color="white" />
      </div>
    );
  };

  if (mfaRequired) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-teal-950 via-cyan-900 to-teal-900 flex items-center justify-center p-6 relative overflow-hidden animate-fade-in">
        <div className="absolute top-1/4 -left-32 w-96 h-96 bg-teal-600/20 rounded-full blur-3xl" />
        <div className="absolute bottom-1/4 -right-32 w-96 h-96 bg-cyan-600/20 rounded-full blur-3xl" />
        <div className="max-w-md w-full relative z-10">
          <div className="text-center mb-8">
            <div
              className="w-16 h-16 rounded-2xl mx-auto mb-4 flex items-center justify-center shadow-lg"
              style={{ background: buttonBg, boxShadow: `0 4px 20px ${brandPrimary}66` }}
            >
              <svg className="w-9 h-9 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
              </svg>
            </div>
            <h1 className="text-3xl font-bold text-white tracking-tight">Two-Factor Authentication</h1>
            <p className="text-teal-200/80 mt-2">One more step to verify your identity</p>
          </div>

          <div className="p-8" style={{ background: 'rgba(255,255,255,0.88)', backdropFilter: 'blur(40px) saturate(180%)', WebkitBackdropFilter: 'blur(40px) saturate(180%)', borderRadius: '20px', boxShadow: '0 8px 32px rgba(0,0,0,0.12), inset 0 1px 0 rgba(255,255,255,0.5)' }}>
            <h2 className="text-xl font-semibold text-slate-900 mb-2 text-center">
              Enter Verification Code
            </h2>
            <p className="text-slate-600 text-center mb-6">
              Enter the 6-digit code from your authenticator app.
            </p>

            {mfaError && (
              <div className="mb-6 p-4 bg-red-50 border border-red-200 rounded-lg">
                <p className="text-red-700 text-sm">{mfaError}</p>
              </div>
            )}

            <form onSubmit={handleTotpVerify} className="space-y-4">
              <div>
                <label htmlFor="totpCode" className="block text-sm font-medium text-slate-700 mb-1">
                  Verification Code
                </label>
                <input
                  type="text"
                  id="totpCode"
                  value={totpCode}
                  onChange={(e) => setTotpCode(e.target.value.replace(/[^0-9a-zA-Z]/g, ''))}
                  placeholder="000000"
                  inputMode="numeric"
                  maxLength={8}
                  autoComplete="one-time-code"
                  autoFocus
                  required
                  className="w-full px-4 py-3 bg-slate-50/80 border border-slate-200 rounded-xl focus:ring-2 focus:ring-teal-500/40 focus:border-teal-300 outline-none transition text-center text-2xl font-mono tracking-[0.3em]"
                />
              </div>
              <button
                type="submit"
                disabled={!totpCode.trim() || mfaLoading}
                className="w-full py-3 text-white font-semibold rounded-xl disabled:opacity-50 disabled:cursor-not-allowed transition-all hover:brightness-110 flex items-center justify-center gap-2"
                style={{ background: buttonBg, boxShadow: buttonShadow }}
              >
                {mfaLoading ? (
                  <>
                    <div className="w-5 h-5 border-2 border-white border-t-transparent rounded-full animate-spin" />
                    Verifying...
                  </>
                ) : (
                  'Verify'
                )}
              </button>
            </form>

            <p className="text-center text-sm text-slate-500 mt-4">
              Or use a backup code
            </p>

            <div className="mt-4 pt-4 border-t border-slate-200">
              <button
                type="button"
                onClick={() => {
                  setMfaRequired(false);
                  setMfaToken('');
                  setTotpCode('');
                  setMfaError(null);
                  setStatus('idle');
                }}
                className="w-full text-center text-sm text-teal-600 hover:text-teal-800 font-medium"
              >
                Back to login
              </button>
            </div>
          </div>

          <p className="mt-8 text-center text-sm text-teal-300/60">
            {footerText}
          </p>
        </div>
      </div>
    );
  }

  if (isLoading || brandingLoading) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-teal-950 via-cyan-900 to-teal-900 flex items-center justify-center p-6 relative overflow-hidden">
        <div className="absolute top-1/4 -left-32 w-96 h-96 bg-teal-600/20 rounded-full blur-3xl" />
        <div className="absolute bottom-1/4 -right-32 w-96 h-96 bg-cyan-600/20 rounded-full blur-3xl" />
        <div className="max-w-md w-full relative" style={{ background: 'rgba(255,255,255,0.85)', backdropFilter: 'blur(40px) saturate(180%)', WebkitBackdropFilter: 'blur(40px) saturate(180%)', borderRadius: '20px', boxShadow: '0 8px 32px rgba(0,0,0,0.12), inset 0 1px 0 rgba(255,255,255,0.5)' }}>
          <div className="p-8 text-center">
            <div className="w-14 h-14 mx-auto mb-4 rounded-2xl flex items-center justify-center animate-pulse-soft" style={{ background: buttonBg }}>
              <OsirisCareLeaf className="w-7 h-7" color="white" />
            </div>
            <p className="text-slate-500">Loading...</p>
          </div>
        </div>
      </div>
    );
  }

  if (status === 'sent') {
    return (
      <div className="min-h-screen bg-gradient-to-br from-teal-950 via-cyan-900 to-teal-900 flex items-center justify-center p-6 relative overflow-hidden">
        <div className="max-w-md w-full">
          <div className="text-center mb-8">
            <div className="w-16 h-16 bg-white rounded-2xl shadow-lg mx-auto mb-4 flex items-center justify-center">
              <svg className="w-10 h-10 text-teal-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
              </svg>
            </div>
            <h1 className="text-3xl font-bold text-white">Check Your Email</h1>
          </div>

          <div className="bg-white rounded-2xl shadow-xl p-8 text-center">
            <div className="w-16 h-16 bg-teal-100 rounded-full mx-auto mb-4 flex items-center justify-center">
              <svg className="w-8 h-8 text-teal-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
              </svg>
            </div>
            <h2 className="text-xl font-semibold text-slate-900 mb-2">
              Login Link Sent
            </h2>
            <p className="text-slate-600 mb-6">
              If <strong>{email}</strong> is registered, you&apos;ll receive a login link shortly.
            </p>
            <p className="text-sm text-slate-500">
              The link expires in 60 minutes. Check your spam folder if you don&apos;t see it.
            </p>
            <button
              onClick={() => {
                setStatus('idle');
                setEmail('');
              }}
              className="mt-6 text-teal-600 hover:underline text-sm"
            >
              Use a different email
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-teal-950 via-cyan-900 to-teal-900 flex items-center justify-center p-6 relative overflow-hidden animate-fade-in">
      <div className="absolute top-1/4 -left-32 w-96 h-96 bg-teal-600/20 rounded-full blur-3xl" />
      <div className="absolute bottom-1/4 -right-32 w-96 h-96 bg-cyan-600/20 rounded-full blur-3xl" />
      <div className="max-w-md w-full relative z-10">
        {/* Logo */}
        <div className="text-center mb-8">
          {renderLogo('lg')}
          <h1 className="text-3xl font-bold text-white tracking-tight">{brandName}</h1>
          {brandTagline && (
            <p className="text-teal-200/80 mt-2">{brandTagline}</p>
          )}
        </div>

        {/* Login Card */}
        <div className="p-8" style={{ background: 'rgba(255,255,255,0.88)', backdropFilter: 'blur(40px) saturate(180%)', WebkitBackdropFilter: 'blur(40px) saturate(180%)', borderRadius: '20px', boxShadow: '0 8px 32px rgba(0,0,0,0.12), inset 0 1px 0 rgba(255,255,255,0.5)' }}>
          <h2 className="text-xl font-semibold text-slate-900 mb-2 text-center">
            Sign In
          </h2>
          <p className="text-slate-600 text-center mb-6">
            Access your HIPAA monitoring dashboard.
          </p>

          {error && (
            <div className="mb-6 p-4 bg-red-50 border border-red-200 rounded-lg">
              <p className="text-red-700 text-sm">{error}</p>
            </div>
          )}

          {ssoEnforced ? (
            /* SSO-enforced mode: only show email + SSO button */
            <div className="space-y-4">
              <div className="p-3 bg-teal-50 border border-teal-200 rounded-lg">
                <p className="text-teal-800 text-sm">{SSO_LABELS.sso_enforced_message}</p>
              </div>
              <div>
                <label htmlFor="ssoEmail" className="block text-sm font-medium text-slate-700 mb-1">
                  Email Address
                </label>
                <input
                  type="email"
                  id="ssoEmail"
                  value={email}
                  onChange={(e) => { setEmail(e.target.value); setError(null); }}
                  placeholder="you@yourpractice.com"
                  required
                  className="w-full px-4 py-3 bg-slate-50/80 border border-slate-200 rounded-xl focus:ring-2 focus:ring-teal-500/40 focus:border-teal-300 outline-none transition"
                />
              </div>
              <button
                type="button"
                onClick={handleSsoLogin}
                disabled={!email.trim() || ssoLoading}
                className="w-full flex items-center justify-center gap-3 px-4 py-3 text-white font-semibold rounded-xl disabled:opacity-50 disabled:cursor-not-allowed transition-all hover:brightness-110"
                style={{ background: buttonBg, boxShadow: buttonShadow }}
              >
                {ssoLoading ? (
                  <>
                    <div className="w-5 h-5 border-2 border-white border-t-transparent rounded-full animate-spin" />
                    Redirecting...
                  </>
                ) : (
                  <>
                    <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z" />
                    </svg>
                    {SSO_LABELS.sign_in_with_sso}
                  </>
                )}
              </button>
              <button
                type="button"
                onClick={() => { setSsoEnforced(false); setError(null); }}
                className="w-full text-center text-sm text-teal-600 hover:text-teal-800 font-medium"
              >
                Use a different email
              </button>
            </div>
          ) : (
            /* Normal mode: tabs + SSO button */
            <>
              {/* Login mode tabs */}
              <div className="flex rounded-lg bg-slate-100 p-1 mb-4">
                <button
                  type="button"
                  onClick={() => { setLoginMode('password'); setError(null); }}
                  className={`flex-1 py-2 text-sm font-medium rounded-md transition-colors ${
                    loginMode === 'password'
                      ? 'bg-white text-slate-900 shadow-sm'
                      : 'text-slate-500 hover:text-slate-700'
                  }`}
                >
                  Email &amp; Password
                </button>
                <button
                  type="button"
                  onClick={() => { setLoginMode('magic'); setError(null); }}
                  className={`flex-1 py-2 text-sm font-medium rounded-md transition-colors ${
                    loginMode === 'magic'
                      ? 'bg-white text-slate-900 shadow-sm'
                      : 'text-slate-500 hover:text-slate-700'
                  }`}
                >
                  Magic Link
                </button>
              </div>

              {loginMode === 'password' ? (
                <form onSubmit={handlePasswordLogin} className="space-y-4">
                  <div>
                    <label htmlFor="email" className="block text-sm font-medium text-slate-700 mb-1">
                      Email Address
                    </label>
                    <input
                      type="email"
                      id="email"
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                      onBlur={handleEmailBlur}
                      placeholder="you@yourpractice.com"
                      required
                      className="w-full px-4 py-3 bg-slate-50/80 border border-slate-200 rounded-xl focus:ring-2 focus:ring-teal-500/40 focus:border-teal-300 outline-none transition"
                    />
                  </div>
                  <div>
                    <label htmlFor="password" className="block text-sm font-medium text-slate-700 mb-1">
                      Password
                    </label>
                    <input
                      type="password"
                      id="password"
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      placeholder="Enter your password"
                      required
                      className="w-full px-4 py-3 bg-slate-50/80 border border-slate-200 rounded-xl focus:ring-2 focus:ring-teal-500/40 focus:border-teal-300 outline-none transition"
                    />
                  </div>
                  <button
                    type="submit"
                    disabled={!email.trim() || !password.trim() || status === 'loading'}
                    className="w-full py-3 text-white font-semibold rounded-xl disabled:opacity-50 disabled:cursor-not-allowed transition-all hover:brightness-110 flex items-center justify-center gap-2"
                    style={{ background: buttonBg, boxShadow: buttonShadow }}
                  >
                    {status === 'loading' ? (
                      <>
                        <div className="w-5 h-5 border-2 border-white border-t-transparent rounded-full animate-spin" />
                        Signing in...
                      </>
                    ) : (
                      'Sign In'
                    )}
                  </button>
                </form>
              ) : (
                <form onSubmit={handleMagicLink} className="space-y-4">
                  <div>
                    <label htmlFor="magicEmail" className="block text-sm font-medium text-slate-700 mb-1">
                      Email Address
                    </label>
                    <input
                      type="email"
                      id="magicEmail"
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                      onBlur={handleEmailBlur}
                      placeholder="you@yourpractice.com"
                      required
                      className="w-full px-4 py-3 bg-slate-50/80 border border-slate-200 rounded-xl focus:ring-2 focus:ring-teal-500/40 focus:border-teal-300 outline-none transition"
                    />
                  </div>
                  <button
                    type="submit"
                    disabled={!email.trim() || status === 'loading'}
                    className="w-full py-3 text-white font-semibold rounded-xl disabled:opacity-50 disabled:cursor-not-allowed transition-all hover:brightness-110 flex items-center justify-center gap-2"
                    style={{ background: buttonBg, boxShadow: buttonShadow }}
                  >
                    {status === 'loading' ? (
                      <>
                        <div className="w-5 h-5 border-2 border-white border-t-transparent rounded-full animate-spin" />
                        Sending...
                      </>
                    ) : (
                      'Send Login Link'
                    )}
                  </button>
                  <p className="text-center text-sm text-slate-500">
                    No password required. We&apos;ll send you a secure link.
                  </p>
                </form>
              )}

              {/* SSO Divider + Button */}
              <div className="relative my-5">
                <div className="absolute inset-0 flex items-center">
                  <div className="w-full border-t border-slate-200" />
                </div>
                <div className="relative flex justify-center text-sm">
                  <span className="px-2 bg-white text-slate-500">or</span>
                </div>
              </div>

              <button
                type="button"
                onClick={handleSsoLogin}
                disabled={ssoLoading}
                className="w-full flex items-center justify-center gap-3 px-4 py-3 bg-slate-50/80 border border-slate-200 rounded-xl hover:bg-teal-50/80 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                <svg className="w-5 h-5 text-slate-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z" />
                </svg>
                <span className="font-medium text-slate-900">
                  {ssoLoading ? 'Redirecting...' : SSO_LABELS.sign_in_with_sso}
                </span>
              </button>
            </>
          )}
        </div>

        {/* Footer */}
        <p className="mt-8 text-center text-sm text-teal-300/60">
          {footerText}
        </p>
      </div>
    </div>
  );
};

export default ClientLogin;
