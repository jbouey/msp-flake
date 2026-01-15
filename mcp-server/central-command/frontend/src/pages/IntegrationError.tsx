/**
 * Integration Error Page
 *
 * Displays error messages from OAuth callbacks and integration failures.
 */

import { useSearchParams, Link } from 'react-router-dom';
import { GlassCard } from '../components/shared';

export const IntegrationError: React.FC = () => {
  const [searchParams] = useSearchParams();

  const error = searchParams.get('error') || 'unknown_error';
  const description = searchParams.get('description') || 'An unknown error occurred';

  // Map error codes to user-friendly messages
  const errorMessages: Record<string, { title: string; suggestion: string }> = {
    invalid_state: {
      title: 'Session Expired',
      suggestion: 'Your authorization session has expired. Please start the integration setup again.',
    },
    site_mismatch: {
      title: 'Security Error',
      suggestion: 'The authorization was initiated for a different site. Please try again.',
    },
    callback_failed: {
      title: 'Authorization Failed',
      suggestion: 'We could not complete the authorization with the provider. Please check your credentials and try again.',
    },
    access_denied: {
      title: 'Access Denied',
      suggestion: 'You denied access to the application. Please try again and grant the required permissions.',
    },
    unknown_error: {
      title: 'Something Went Wrong',
      suggestion: 'An unexpected error occurred. Please try again or contact support.',
    },
  };

  const errorInfo = errorMessages[error] || errorMessages.unknown_error;

  return (
    <div className="max-w-lg mx-auto mt-12">
      <GlassCard className="p-8 text-center">
        {/* Error icon */}
        <div className="w-16 h-16 bg-health-critical/20 rounded-full flex items-center justify-center mx-auto mb-6">
          <svg className="w-8 h-8 text-health-critical" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
          </svg>
        </div>

        <h1 className="text-2xl font-bold text-label-primary mb-2">
          {errorInfo.title}
        </h1>

        <p className="text-label-secondary mb-4">
          {errorInfo.suggestion}
        </p>

        {/* Show technical details in a collapsed section */}
        <details className="text-left mb-6">
          <summary className="text-sm text-label-tertiary cursor-pointer hover:text-label-secondary">
            Technical Details
          </summary>
          <div className="mt-2 p-3 bg-glass-bg rounded-lg">
            <p className="text-sm font-mono text-label-tertiary">
              <span className="text-label-secondary">Error:</span> {error}
            </p>
            <p className="text-sm font-mono text-label-tertiary mt-1">
              <span className="text-label-secondary">Details:</span> {decodeURIComponent(description)}
            </p>
          </div>
        </details>

        {/* Action buttons */}
        <div className="flex flex-col sm:flex-row gap-3 justify-center">
          <Link
            to="/sites"
            className="px-4 py-2 bg-accent-primary text-white rounded-lg hover:bg-accent-primary/80 transition-colors"
          >
            Back to Sites
          </Link>
          <button
            onClick={() => window.history.back()}
            className="px-4 py-2 bg-glass-bg text-label-secondary rounded-lg hover:bg-glass-bg/80 transition-colors"
          >
            Go Back
          </button>
        </div>
      </GlassCard>
    </div>
  );
};

export default IntegrationError;
