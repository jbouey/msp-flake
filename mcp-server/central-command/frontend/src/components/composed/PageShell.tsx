import React from 'react';
import { LoadingScreen } from '../shared';
import { EmptyState } from '../shared';
import { DisclaimerFooter } from './DisclaimerFooter';

interface PageShellProps {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
  disclaimer?: boolean;
  loading?: boolean;
  error?: string;
  onRetry?: () => void;
  empty?: {
    icon: React.ReactNode;
    title: string;
    description: string;
    action?: { label: string; onClick: () => void };
  };
  actions?: React.ReactNode;
  className?: string;
}

/**
 * PageShell -- wraps every page with consistent structure.
 *
 * Handles loading, error, empty states, and disclaimer footer.
 * Renders page title + subtitle with consistent styling.
 */
export const PageShell: React.FC<PageShellProps> = ({
  title,
  subtitle,
  children,
  disclaimer = false,
  loading = false,
  error,
  onRetry,
  empty,
  actions,
  className = '',
}) => {
  return (
    <div className={`space-y-5 page-enter ${className}`}>
      {/* Page header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-label-primary tracking-tight">{title}</h1>
          {subtitle && (
            <p className="text-sm text-label-tertiary mt-1">{subtitle}</p>
          )}
        </div>
        {actions && <div className="flex items-center gap-2">{actions}</div>}
      </div>

      {/* Loading state */}
      {loading && <LoadingScreen />}

      {/* Error state */}
      {!loading && error && (
        <div className="flex flex-col items-center justify-center py-12 px-6 text-center">
          <div className="w-12 h-12 rounded-full bg-health-critical/10 flex items-center justify-center text-health-critical mb-4">
            <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
            </svg>
          </div>
          <h3 className="text-sm font-semibold text-label-primary mb-1">Something went wrong</h3>
          <p className="text-xs text-label-tertiary max-w-xs mb-4">{error}</p>
          {onRetry && (
            <button onClick={onRetry} className="text-xs font-medium text-accent-primary hover:underline">
              Try Again
            </button>
          )}
        </div>
      )}

      {/* Empty state */}
      {!loading && !error && empty && (
        <EmptyState
          icon={empty.icon}
          title={empty.title}
          description={empty.description}
          action={empty.action}
        />
      )}

      {/* Content */}
      {!loading && !error && !empty && children}

      {/* Disclaimer footer */}
      {disclaimer && <DisclaimerFooter />}
    </div>
  );
};

export default PageShell;
