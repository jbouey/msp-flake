import React, { useState, useEffect } from 'react';
import { useSearchParams, useNavigate, useParams } from 'react-router-dom';

interface LoginState {
  status: 'idle' | 'loading' | 'sent' | 'validating' | 'error';
  message?: string;
}

export const PortalLogin: React.FC = () => {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const { siteId } = useParams<{ siteId: string }>();

  const [email, setEmail] = useState('');
  const [state, setState] = useState<LoginState>({ status: 'idle' });

  // Check for magic link token in URL
  const magicToken = searchParams.get('magic');
  const legacyToken = searchParams.get('token');

  useEffect(() => {
    // If we have a magic link token, validate it
    if (magicToken && siteId) {
      validateMagicLink(magicToken);
    }
    // If we have a legacy token, redirect to dashboard
    else if (legacyToken && siteId) {
      navigate(`/portal/site/${siteId}/dashboard?token=${legacyToken}`, { replace: true });
    }
  }, [magicToken, legacyToken, siteId]);

  const validateMagicLink = async (token: string) => {
    setState({ status: 'validating', message: 'Validating your access...' });

    try {
      const response = await fetch(`/api/portal/auth/validate?magic=${token}`);

      if (response.ok) {
        const data = await response.json();
        // Session cookie is now set - redirect to dashboard
        navigate(`/portal/site/${data.site_id}/dashboard`, { replace: true });
      } else {
        const error = await response.json();
        setState({
          status: 'error',
          message: error.detail || 'Invalid or expired link. Please request a new one.',
        });
      }
    } catch (e) {
      setState({
        status: 'error',
        message: 'Failed to validate access link. Please try again.',
      });
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!email || !siteId) return;

    setState({ status: 'loading' });

    try {
      const response = await fetch(`/api/portal/sites/${siteId}/request-access`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email }),
      });

      if (response.ok) {
        setState({
          status: 'sent',
          message: 'Check your email for the access link.',
        });
      } else {
        setState({
          status: 'error',
          message: 'Failed to send access link. Please try again.',
        });
      }
    } catch (e) {
      setState({
        status: 'error',
        message: 'Network error. Please check your connection.',
      });
    }
  };

  // Validating magic link
  if (state.status === 'validating') {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center p-6">
        <div className="max-w-md w-full bg-white rounded-2xl shadow-lg p-8 text-center">
          <div className="w-16 h-16 mx-auto mb-4 flex items-center justify-center">
            <div className="animate-spin rounded-full h-12 w-12 border-4 border-blue-500 border-t-transparent" />
          </div>
          <h1 className="text-xl font-semibold text-gray-900 mb-2">Validating Access</h1>
          <p className="text-gray-600">{state.message}</p>
        </div>
      </div>
    );
  }

  // Email sent confirmation
  if (state.status === 'sent') {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center p-6">
        <div className="max-w-md w-full bg-white rounded-2xl shadow-lg p-8 text-center">
          <div className="w-16 h-16 bg-green-100 rounded-full flex items-center justify-center mx-auto mb-4">
            <svg className="w-8 h-8 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
            </svg>
          </div>
          <h1 className="text-xl font-semibold text-gray-900 mb-2">Check Your Email</h1>
          <p className="text-gray-600 mb-6">{state.message}</p>
          <p className="text-sm text-gray-400">
            Didn't receive it? Check your spam folder or{' '}
            <button
              onClick={() => setState({ status: 'idle' })}
              className="text-blue-600 hover:underline"
            >
              try again
            </button>
          </p>
        </div>
      </div>
    );
  }

  // Error state
  if (state.status === 'error') {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center p-6">
        <div className="max-w-md w-full bg-white rounded-2xl shadow-lg p-8 text-center">
          <div className="w-16 h-16 bg-red-100 rounded-full flex items-center justify-center mx-auto mb-4">
            <svg className="w-8 h-8 text-red-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
            </svg>
          </div>
          <h1 className="text-xl font-semibold text-gray-900 mb-2">Access Error</h1>
          <p className="text-gray-600 mb-6">{state.message}</p>
          <button
            onClick={() => setState({ status: 'idle' })}
            className="px-4 py-2 bg-blue-600 text-white font-medium rounded-lg hover:bg-blue-700"
          >
            Try Again
          </button>
        </div>
      </div>
    );
  }

  // Login form
  return (
    <div className="min-h-screen bg-gray-50 flex items-center justify-center p-6">
      <div className="max-w-md w-full">
        {/* Logo */}
        <div className="text-center mb-8">
          <h1 className="text-3xl font-bold text-gray-900">OsirisCare</h1>
          <p className="text-gray-600 mt-2">HIPAA Monitoring Portal</p>
        </div>

        {/* Login Card */}
        <div className="bg-white rounded-2xl shadow-lg p-8">
          <h2 className="text-xl font-semibold text-gray-900 mb-2 text-center">
            Access Your Dashboard
          </h2>
          <p className="text-gray-600 text-center mb-6">
            Enter your email to receive a secure login link.
          </p>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label htmlFor="email" className="block text-sm font-medium text-gray-700 mb-1">
                Email Address
              </label>
              <input
                type="email"
                id="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@company.com"
                required
                className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent outline-none transition"
              />
            </div>

            <button
              type="submit"
              disabled={state.status === 'loading' || !email}
              className="w-full py-3 bg-blue-600 text-white font-medium rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition"
            >
              {state.status === 'loading' ? (
                <span className="flex items-center justify-center gap-2">
                  <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
                  Sending...
                </span>
              ) : (
                'Send Login Link'
              )}
            </button>
          </form>

          <p className="mt-6 text-center text-sm text-gray-400">
            No password required. We'll send you a secure link.
          </p>
        </div>

        {/* Footer */}
        <p className="mt-8 text-center text-sm text-gray-400">
          Questions?{' '}
          <a href="mailto:support@osiriscare.net" className="text-blue-600 hover:underline">
            Contact Support
          </a>
        </p>
        <p className="mt-4 text-center text-xs text-gray-300 max-w-sm mx-auto">
          This portal provides monitoring data only. OsirisCare does not certify compliance.
        </p>
      </div>
    </div>
  );
};

export default PortalLogin;
