import React, { useState, useEffect } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { usePartner } from './PartnerContext';
import { csrfHeaders } from '../utils/csrf';
import { PartnerAdminTransferModal } from './PartnerAdminTransferModal';

type Step = 'status' | 'setup' | 'verify' | 'disable';

interface SetupData {
  secret: string;
  uri: string;
  backup_codes: string[];
}

export const PartnerSecurity: React.FC = () => {
  const navigate = useNavigate();
  const { partner, apiKey, isAuthenticated, isLoading } = usePartner();

  const [totpEnabled, setTotpEnabled] = useState(false);
  const [loading, setLoading] = useState(true);
  const [step, setStep] = useState<Step>('status');
  // Task #18 phase 2 — partner_admin_transfer modal (mig 274)
  const [showAdminTransfer, setShowAdminTransfer] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  // Setup state
  const [setupData, setSetupData] = useState<SetupData | null>(null);
  const [verifyCode, setVerifyCode] = useState('');
  const [verifyPassword, setVerifyPassword] = useState('');
  const [verifying, setVerifying] = useState(false);
  const [finalBackupCodes, setFinalBackupCodes] = useState<string[] | null>(null);

  // Disable state
  const [disablePassword, setDisablePassword] = useState('');
  const [disabling, setDisabling] = useState(false);

  useEffect(() => {
    if (!isLoading && !isAuthenticated) {
      navigate('/partner/login', { replace: true });
    }
  }, [isAuthenticated, isLoading, navigate]);

  useEffect(() => {
    if (isAuthenticated) {
      checkTotpStatus();
    }
  }, [isAuthenticated]);

  const fetchOptions = (): RequestInit => {
    if (apiKey) {
      return { headers: { 'X-API-Key': apiKey } };
    }
    return { credentials: 'include', headers: { ...csrfHeaders() } };
  };

  const fetchWithAuth = (url: string, options: RequestInit = {}): Promise<Response> => {
    const base = fetchOptions();
    const merged: RequestInit = {
      ...options,
      credentials: base.credentials || options.credentials,
      headers: {
        ...(base.headers as Record<string, string>),
        ...(options.headers as Record<string, string>),
      },
    };
    return fetch(url, merged);
  };

  const checkTotpStatus = async () => {
    setLoading(true);
    try {
      const response = await fetchWithAuth('/api/partner-auth/me');
      if (response.ok) {
        const data = await response.json();
        setTotpEnabled(!!data.totp_enabled);
      }
    } catch {
      // TOTP status check failed — leave default state
    } finally {
      setLoading(false);
    }
  };

  const handleSetup = async () => {
    setError(null);
    setSuccess(null);
    try {
      const response = await fetchWithAuth('/api/partner-auth/me/totp/setup', {
        method: 'POST',
      });

      if (response.ok) {
        const data = await response.json();
        setSetupData(data);
        setStep('setup');
      } else {
        const data = await response.json();
        setError(data.detail || 'Failed to start 2FA setup');
      }
    } catch (e) {
      setError('Failed to start 2FA setup');
    }
  };

  const handleVerify = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setVerifying(true);

    try {
      const response = await fetchWithAuth('/api/partner-auth/me/totp/verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code: verifyCode, password: verifyPassword }),
      });

      if (response.ok) {
        const data = await response.json();
        setFinalBackupCodes(data.backup_codes || null);
        setTotpEnabled(true);
        setStep('status');
        setSetupData(null);
        setVerifyCode('');
        setVerifyPassword('');
        setSuccess('Two-factor authentication has been enabled.');
      } else {
        const data = await response.json();
        setError(data.detail || 'Verification failed. Check your code and try again.');
      }
    } catch (e) {
      setError('Verification failed');
    } finally {
      setVerifying(false);
    }
  };

  const handleDisable = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setDisabling(true);

    try {
      const response = await fetchWithAuth('/api/partner-auth/me/totp', {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password: disablePassword }),
      });

      if (response.ok) {
        setTotpEnabled(false);
        setStep('status');
        setDisablePassword('');
        setFinalBackupCodes(null);
        setSuccess('Two-factor authentication has been disabled.');
      } else {
        const data = await response.json();
        setError(data.detail || 'Failed to disable 2FA. Check your password.');
      }
    } catch (e) {
      setError('Failed to disable 2FA');
    } finally {
      setDisabling(false);
    }
  };

  const handleCopyBackupCodes = (codes: string[]) => {
    navigator.clipboard.writeText(codes.join('\n'));
    setSuccess('Backup codes copied to clipboard.');
  };

  if (isLoading || loading) {
    return (
      <div className="min-h-screen bg-slate-50/80 flex items-center justify-center">
        <div className="text-center">
          <div className="w-12 h-12 rounded-2xl mx-auto mb-4 flex items-center justify-center animate-pulse-soft" style={{ background: 'linear-gradient(135deg, #4F46E5 0%, #7C3AED 100%)' }}>
            <svg className="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
            </svg>
          </div>
          <p className="text-slate-500 text-sm">Loading security settings...</p>
        </div>
      </div>
    );
  }

  if (!partner) return null;

  const qrUrl = setupData
    ? `https://api.qrserver.com/v1/create-qr-code/?size=200x200&data=${encodeURIComponent(setupData.uri)}`
    : '';

  return (
    <div className="min-h-screen bg-slate-50/80 page-enter">
      {/* Header */}
      <header className="sticky top-0 z-30 border-b border-slate-200/60" style={{ background: 'rgba(255,255,255,0.82)', backdropFilter: 'blur(20px) saturate(180%)', WebkitBackdropFilter: 'blur(20px) saturate(180%)' }}>
        <div className="max-w-7xl mx-auto px-6 py-3 flex items-center justify-between">
          <div className="flex items-center gap-4">
            <Link to="/partner/dashboard" className="p-2 text-slate-500 hover:text-indigo-600 rounded-lg hover:bg-indigo-50 transition">
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
              </svg>
            </Link>
            <div>
              <h1 className="text-lg font-semibold text-slate-900 tracking-tight">Security Settings</h1>
              <p className="text-xs text-slate-500">Two-Factor Authentication</p>
            </div>
          </div>
        </div>
      </header>

      {/* Main */}
      <main className="max-w-2xl mx-auto px-6 py-8">
        {/* Alerts */}
        {error && (
          <div className="mb-6 p-4 bg-red-50 border border-red-200 rounded-xl text-red-700 text-sm">
            {error}
          </div>
        )}
        {success && (
          <div className="mb-6 p-4 bg-green-50 border border-green-200 rounded-xl text-green-700 text-sm">
            {success}
          </div>
        )}

        {/* Backup codes display (shown after enabling) */}
        {finalBackupCodes && (
          <div className="mb-6 bg-amber-50 border border-amber-200 rounded-2xl p-6">
            <div className="flex items-center gap-2 mb-3">
              <svg className="w-5 h-5 text-amber-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z" />
              </svg>
              <h3 className="font-semibold text-amber-800">Save Your Backup Codes</h3>
            </div>
            <p className="text-sm text-amber-700 mb-4">
              Store these codes in a safe place. Each code can only be used once. If you lose your authenticator, these codes are the only way to regain access.
            </p>
            <div className="grid grid-cols-2 gap-2 mb-4">
              {finalBackupCodes.map((code, i) => (
                <code key={i} className="px-3 py-2 bg-white border border-amber-200 rounded-lg text-center font-mono text-sm tracking-wider">
                  {code}
                </code>
              ))}
            </div>
            <div className="flex gap-3">
              <button
                onClick={() => handleCopyBackupCodes(finalBackupCodes)}
                className="px-4 py-2 text-sm font-medium text-amber-700 bg-white border border-amber-300 rounded-lg hover:bg-amber-50 transition flex items-center gap-2"
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
                </svg>
                Copy All
              </button>
              <button
                onClick={() => setFinalBackupCodes(null)}
                className="px-4 py-2 text-sm font-medium text-amber-700 hover:text-amber-900 transition"
              >
                I've saved them
              </button>
            </div>
          </div>
        )}

        {/* Status Card */}
        {step === 'status' && (
          <div className="bg-white rounded-2xl shadow-sm border border-slate-100 p-6">
            <div className="flex items-center justify-between mb-6">
              <div className="flex items-center gap-4">
                <div className="w-12 h-12 rounded-xl flex items-center justify-center" style={{ background: totpEnabled ? 'linear-gradient(135deg, rgba(34,197,94,0.12) 0%, rgba(22,163,74,0.08) 100%)' : 'linear-gradient(135deg, rgba(239,68,68,0.12) 0%, rgba(220,38,38,0.08) 100%)' }}>
                  <svg className={`w-6 h-6 ${totpEnabled ? 'text-green-600' : 'text-red-500'}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
                  </svg>
                </div>
                <div>
                  <h2 className="text-lg font-semibold text-slate-900">Two-Factor Authentication</h2>
                  <p className="text-sm text-slate-500">
                    {totpEnabled
                      ? 'Your account is protected with 2FA.'
                      : 'Add an extra layer of security to your account.'}
                  </p>
                </div>
              </div>
              <span className={`px-3 py-1 text-xs font-semibold rounded-full ${
                totpEnabled
                  ? 'bg-green-100 text-green-800'
                  : 'bg-red-100 text-red-800'
              }`}>
                {totpEnabled ? 'Enabled' : 'Disabled'}
              </span>
            </div>

            <div className="border-t border-slate-100 pt-6">
              {totpEnabled ? (
                <div>
                  <p className="text-sm text-slate-600 mb-4">
                    You will be prompted for a verification code from your authenticator app when signing in.
                  </p>
                  <button
                    onClick={() => { setStep('disable'); setError(null); setSuccess(null); }}
                    className="px-4 py-2 text-sm font-medium text-red-600 border border-red-200 rounded-xl hover:bg-red-50 transition"
                  >
                    Disable 2FA
                  </button>
                </div>
              ) : (
                <div>
                  <p className="text-sm text-slate-600 mb-4">
                    Use an authenticator app like Google Authenticator, Authy, or 1Password to generate verification codes.
                  </p>
                  <button
                    onClick={handleSetup}
                    className="px-6 py-2.5 text-sm font-medium text-white rounded-xl hover:brightness-110 transition-all"
                    style={{ background: 'linear-gradient(135deg, #4F46E5 0%, #7C3AED 100%)', boxShadow: '0 2px 10px rgba(79,70,229,0.3)' }}
                  >
                    Enable 2FA
                  </button>
                </div>
              )}
            </div>
          </div>
        )}

        {/* Setup Step */}
        {step === 'setup' && setupData && (
          <div className="bg-white rounded-2xl shadow-sm border border-slate-100 p-6">
            <h2 className="text-lg font-semibold text-slate-900 mb-2">Set Up Authenticator</h2>
            <p className="text-sm text-slate-500 mb-6">
              Scan the QR code with your authenticator app, then enter the 6-digit code to verify.
            </p>

            {/* QR Code */}
            <div className="flex justify-center mb-6">
              <div className="bg-white p-4 rounded-xl border-2 border-slate-200">
                <img
                  src={qrUrl}
                  alt="TOTP QR Code"
                  width={200}
                  height={200}
                />
              </div>
            </div>

            {/* Manual secret */}
            <div className="mb-6 text-center">
              <p className="text-xs text-slate-500 uppercase tracking-wide mb-2">Manual Entry Key</p>
              <code className="px-4 py-2 bg-slate-100 rounded-lg text-sm font-mono tracking-wider select-all">
                {setupData.secret}
              </code>
            </div>

            {/* Backup Codes */}
            <div className="mb-6 bg-amber-50 border border-amber-200 rounded-xl p-4">
              <div className="flex items-center gap-2 mb-2">
                <svg className="w-4 h-4 text-amber-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z" />
                </svg>
                <p className="text-sm font-semibold text-amber-800">Backup Codes</p>
              </div>
              <p className="text-xs text-amber-700 mb-3">Save these before continuing. You will need them if you lose your authenticator.</p>
              <div className="grid grid-cols-2 gap-2">
                {setupData.backup_codes.map((code, i) => (
                  <code key={i} className="px-3 py-1.5 bg-white border border-amber-200 rounded text-center font-mono text-xs tracking-wider">
                    {code}
                  </code>
                ))}
              </div>
              <button
                onClick={() => handleCopyBackupCodes(setupData.backup_codes)}
                className="mt-3 text-xs font-medium text-amber-700 hover:text-amber-900 flex items-center gap-1"
              >
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
                </svg>
                Copy codes
              </button>
            </div>

            {/* Verify Form */}
            <form onSubmit={handleVerify} className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">Verification Code</label>
                <input
                  type="text"
                  inputMode="numeric"
                  pattern="[0-9]*"
                  maxLength={6}
                  value={verifyCode}
                  onChange={(e) => setVerifyCode(e.target.value.replace(/\D/g, ''))}
                  placeholder="000000"
                  className="w-full px-4 py-2.5 bg-slate-50/80 border border-slate-200 rounded-xl text-center text-lg font-mono tracking-[0.5em] focus:ring-2 focus:ring-indigo-500/40 focus:border-indigo-300 outline-none transition"
                  required
                  autoFocus
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">Account Password</label>
                <input
                  type="password"
                  value={verifyPassword}
                  onChange={(e) => setVerifyPassword(e.target.value)}
                  placeholder="Confirm your password"
                  className="w-full px-4 py-2.5 bg-slate-50/80 border border-slate-200 rounded-xl focus:ring-2 focus:ring-indigo-500/40 focus:border-indigo-300 outline-none transition"
                  required
                />
              </div>
              <div className="flex gap-3 pt-2">
                <button
                  type="button"
                  onClick={() => { setStep('status'); setSetupData(null); setError(null); }}
                  className="flex-1 px-4 py-2.5 text-sm font-medium text-slate-600 border border-slate-200 rounded-xl hover:bg-slate-50 transition"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={verifying || verifyCode.length !== 6}
                  className="flex-1 px-4 py-2.5 text-sm font-medium text-white rounded-xl hover:brightness-110 transition-all disabled:opacity-50"
                  style={{ background: 'linear-gradient(135deg, #4F46E5 0%, #7C3AED 100%)' }}
                >
                  {verifying ? 'Verifying...' : 'Enable 2FA'}
                </button>
              </div>
            </form>
          </div>
        )}

        {/* Disable Step */}
        {step === 'disable' && (
          <div className="bg-white rounded-2xl shadow-sm border border-slate-100 p-6">
            <div className="flex items-center gap-3 mb-4">
              <div className="w-10 h-10 rounded-xl bg-red-50 flex items-center justify-center">
                <svg className="w-5 h-5 text-red-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z" />
                </svg>
              </div>
              <div>
                <h2 className="text-lg font-semibold text-slate-900">Disable Two-Factor Authentication</h2>
                <p className="text-sm text-slate-500">This will make your account less secure.</p>
              </div>
            </div>

            <div className="bg-red-50 border border-red-200 rounded-xl p-4 mb-6">
              <p className="text-sm text-red-700">
                Disabling 2FA removes the extra security layer from your account.
                Anyone with your password will be able to access your account.
              </p>
            </div>

            <form onSubmit={handleDisable} className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">Confirm Password</label>
                <input
                  type="password"
                  value={disablePassword}
                  onChange={(e) => setDisablePassword(e.target.value)}
                  placeholder="Enter your password"
                  className="w-full px-4 py-2.5 bg-slate-50/80 border border-slate-200 rounded-xl focus:ring-2 focus:ring-red-500/40 focus:border-red-300 outline-none transition"
                  required
                  autoFocus
                />
              </div>
              <div className="flex gap-3 pt-2">
                <button
                  type="button"
                  onClick={() => { setStep('status'); setDisablePassword(''); setError(null); }}
                  className="flex-1 px-4 py-2.5 text-sm font-medium text-slate-600 border border-slate-200 rounded-xl hover:bg-slate-50 transition"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={disabling || !disablePassword}
                  className="flex-1 px-4 py-2.5 text-sm font-medium text-white bg-red-600 rounded-xl hover:bg-red-700 transition disabled:opacity-50"
                >
                  {disabling ? 'Disabling...' : 'Disable 2FA'}
                </button>
              </div>
            </form>
          </div>
        )}

        {/* Info Card */}
        <div className="mt-6 bg-white/60 rounded-2xl border border-slate-100 p-6">
          <h3 className="text-sm font-semibold text-slate-700 mb-3">About Two-Factor Authentication</h3>
          <ul className="space-y-2 text-sm text-slate-500">
            <li className="flex items-start gap-2">
              <svg className="w-4 h-4 text-indigo-500 mt-0.5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
              </svg>
              Requires a time-based code from your authenticator app at each sign-in
            </li>
            <li className="flex items-start gap-2">
              <svg className="w-4 h-4 text-indigo-500 mt-0.5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
              </svg>
              Protects against unauthorized access even if your password is compromised
            </li>
            <li className="flex items-start gap-2">
              <svg className="w-4 h-4 text-indigo-500 mt-0.5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
              </svg>
              Compatible with Google Authenticator, Authy, 1Password, and other TOTP apps
            </li>
            <li className="flex items-start gap-2">
              <svg className="w-4 h-4 text-indigo-500 mt-0.5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
              </svg>
              HIPAA Security Rule recommends multi-factor authentication (§164.312(d))
            </li>
          </ul>
        </div>

        {/* Task #18 phase 2 — partner admin-role transfer entry point.
            Server-side enforces role gating (admin-only); the modal
            surfaces 403 detail if a non-admin tries to initiate. */}
        <div className="mt-6 bg-white rounded-2xl shadow-sm border border-slate-100 p-6">
          <div className="flex items-start justify-between gap-4">
            <div>
              <h2 className="text-base font-semibold text-slate-900">Admin Role Transfer</h2>
              <p className="mt-1 text-sm text-slate-600">
                Transfer the partner admin role to another existing
                user in your partner organization. Each transition is
                cryptographically attested in your auditor kit.
              </p>
            </div>
            <button
              onClick={() => setShowAdminTransfer(true)}
              className="px-4 py-2 text-sm rounded-lg border border-slate-300 text-slate-700 hover:bg-slate-50 whitespace-nowrap"
            >
              Manage admin transfer
            </button>
          </div>
        </div>
      </main>

      <PartnerAdminTransferModal
        isOpen={showAdminTransfer}
        onClose={() => setShowAdminTransfer(false)}
      />
    </div>
  );
};

export default PartnerSecurity;
