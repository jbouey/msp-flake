import React, { useEffect, useState } from 'react';
import { useSearchParams, useNavigate } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';

/**
 * OAuthCallback - Handles the OAuth success redirect
 *
 * This page is shown when the backend redirects after successful OAuth authentication.
 * URL: /auth/oauth/success?token=xxx&return_url=/dashboard
 *
 * It stores the token and redirects to the dashboard.
 */
export const OAuthCallback: React.FC = () => {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const { setTokenFromOAuth } = useAuth();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const token = searchParams.get('token');
    const returnUrl = searchParams.get('return_url') || '/';

    if (token) {
      // Store the token
      setTokenFromOAuth(token);
      // Redirect to the return URL
      navigate(returnUrl, { replace: true });
    } else {
      setError('No authentication token received');
    }
  }, [searchParams, navigate, setTokenFromOAuth]);

  if (error) {
    return (
      <div className="min-h-screen bg-background-primary flex items-center justify-center p-4">
        <div className="text-center">
          <div className="w-16 h-16 bg-red-100 rounded-full mx-auto flex items-center justify-center mb-4">
            <svg className="w-8 h-8 text-red-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </div>
          <h1 className="text-xl font-semibold text-label-primary mb-2">Authentication Failed</h1>
          <p className="text-label-secondary mb-4">{error}</p>
          <button
            onClick={() => navigate('/login', { replace: true })}
            className="px-6 py-2 bg-accent-primary text-white rounded-ios-md hover:bg-accent-primary/90 transition-colors"
          >
            Return to Login
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-background-primary flex items-center justify-center p-4">
      <div className="text-center">
        <div className="w-16 h-16 bg-accent-primary/10 rounded-full mx-auto flex items-center justify-center mb-4">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-accent-primary"></div>
        </div>
        <h1 className="text-xl font-semibold text-label-primary mb-2">Signing you in...</h1>
        <p className="text-label-secondary">Please wait while we complete authentication.</p>
      </div>
    </div>
  );
};

export default OAuthCallback;
