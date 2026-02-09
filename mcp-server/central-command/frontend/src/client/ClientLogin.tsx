import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useClient } from './ClientContext';
import { OsirisCareLeaf } from '../components/shared';

export const ClientLogin: React.FC = () => {
  const navigate = useNavigate();
  const { isAuthenticated, isLoading } = useClient();

  const [email, setEmail] = useState('');
  const [status, setStatus] = useState<'idle' | 'loading' | 'sent' | 'error'>('idle');
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (isAuthenticated && !isLoading) {
      navigate('/client/dashboard', { replace: true });
    }
  }, [isAuthenticated, isLoading, navigate]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email.trim()) return;

    setStatus('loading');
    setError(null);

    try {
      const response = await fetch('/api/client/auth/request-magic-link', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: email.trim().toLowerCase() }),
      });

      if (response.ok) {
        setStatus('sent');
      } else {
        // Always show success to prevent email enumeration
        setStatus('sent');
      }
    } catch (e) {
      setError('Failed to connect. Please try again.');
      setStatus('error');
    }
  };

  if (isLoading) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-teal-950 via-cyan-900 to-teal-900 flex items-center justify-center p-6 relative overflow-hidden">
        <div className="absolute top-1/4 -left-32 w-96 h-96 bg-teal-600/20 rounded-full blur-3xl" />
        <div className="absolute bottom-1/4 -right-32 w-96 h-96 bg-cyan-600/20 rounded-full blur-3xl" />
        <div className="max-w-md w-full relative" style={{ background: 'rgba(255,255,255,0.85)', backdropFilter: 'blur(40px) saturate(180%)', WebkitBackdropFilter: 'blur(40px) saturate(180%)', borderRadius: '20px', boxShadow: '0 8px 32px rgba(0,0,0,0.12), inset 0 1px 0 rgba(255,255,255,0.5)' }}>
          <div className="p-8 text-center">
            <div className="w-14 h-14 mx-auto mb-4 rounded-2xl flex items-center justify-center animate-pulse-soft" style={{ background: 'linear-gradient(135deg, #14A89E 0%, #3CBCB4 100%)' }}>
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
              If <strong>{email}</strong> is registered, you'll receive a login link shortly.
            </p>
            <p className="text-sm text-slate-500">
              The link expires in 60 minutes. Check your spam folder if you don't see it.
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
          <div
            className="w-16 h-16 rounded-2xl mx-auto mb-4 flex items-center justify-center shadow-lg"
            style={{ background: 'linear-gradient(135deg, #14A89E 0%, #3CBCB4 100%)', boxShadow: '0 4px 20px rgba(60, 188, 180, 0.4)' }}
          >
            <OsirisCareLeaf className="w-9 h-9" color="white" />
          </div>
          <h1 className="text-3xl font-bold text-white tracking-tight">Client Portal</h1>
          <p className="text-teal-200/80 mt-2">Access your HIPAA compliance dashboard</p>
        </div>

        {/* Login Card */}
        <div className="p-8" style={{ background: 'rgba(255,255,255,0.88)', backdropFilter: 'blur(40px) saturate(180%)', WebkitBackdropFilter: 'blur(40px) saturate(180%)', borderRadius: '20px', boxShadow: '0 8px 32px rgba(0,0,0,0.12), inset 0 1px 0 rgba(255,255,255,0.5)' }}>
          <h2 className="text-xl font-semibold text-slate-900 mb-2 text-center">
            Sign In
          </h2>
          <p className="text-slate-600 text-center mb-6">
            Enter your email to receive a secure login link.
          </p>

          {error && (
            <div className="mb-6 p-4 bg-red-50 border border-red-200 rounded-lg">
              <p className="text-red-700 text-sm">{error}</p>
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label htmlFor="email" className="block text-sm font-medium text-slate-700 mb-1">
                Email Address
              </label>
              <input
                type="email"
                id="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@yourpractice.com"
                required
                className="w-full px-4 py-3 bg-slate-50/80 border border-slate-200 rounded-xl focus:ring-2 focus:ring-teal-500/40 focus:border-teal-300 outline-none transition"
              />
            </div>

            <button
              type="submit"
              disabled={!email.trim() || status === 'loading'}
              className="w-full py-3 text-white font-semibold rounded-xl disabled:opacity-50 disabled:cursor-not-allowed transition-all hover:brightness-110 flex items-center justify-center gap-2"
              style={{ background: 'linear-gradient(135deg, #14A89E 0%, #3CBCB4 100%)', boxShadow: '0 4px 14px rgba(60, 188, 180, 0.35)' }}
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
          </form>

          <div className="mt-6 pt-6 border-t border-slate-200">
            <p className="text-center text-sm text-slate-500">
              No password required. We'll send you a secure link.
            </p>
          </div>
        </div>

        {/* Footer */}
        <p className="mt-8 text-center text-sm text-teal-300/60">
          Powered by OsirisCare HIPAA Compliance Platform
        </p>
      </div>
    </div>
  );
};

export default ClientLogin;
