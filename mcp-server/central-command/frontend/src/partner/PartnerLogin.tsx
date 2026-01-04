import React, { useState, useEffect } from 'react';
import { useSearchParams, useNavigate } from 'react-router-dom';
import { usePartner } from './PartnerContext';

export const PartnerLogin: React.FC = () => {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const { login, isAuthenticated, isLoading, error: authError } = usePartner();

  const [apiKey, setApiKey] = useState('');
  const [status, setStatus] = useState<'idle' | 'loading' | 'error'>('idle');
  const [error, setError] = useState<string | null>(null);

  // Check for magic link token in URL
  const magicToken = searchParams.get('token');

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
      const response = await fetch(`/api/partners/auth/magic?token=${token}`);

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
            Enter your API key to access your dashboard.
          </p>

          {(error || status === 'error') && (
            <div className="mb-6 p-4 bg-red-50 border border-red-200 rounded-lg">
              <p className="text-red-700 text-sm">{error || 'Authentication failed'}</p>
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
