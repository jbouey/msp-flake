import React from 'react';

interface Props {
  children: React.ReactNode;
  /** Optional label shown in the fallback UI — "Dashboard", "Site Detail", etc. */
  section?: string;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

/**
 * DashboardErrorBoundary — wraps a full page section so an uncaught render
 * error degrades to a friendly retry screen instead of a white page.
 *
 * Distinct from the global `ErrorBoundary` (which is mounted at the app
 * root) — this one is scoped to a page so the chrome + sidebar stay
 * mounted and the user can navigate away. Useful for pages like Dashboard
 * where a single failing chart shouldn't nuke the entire nav.
 *
 * Reports errors to console (sentry hook stubbed for later integration).
 */
export class DashboardErrorBoundary extends React.Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo): void {
    console.error(`DashboardErrorBoundary[${this.props.section || 'unknown'}]`, error, info);
    // TODO: forward to Sentry / telemetry when wired in.
  }

  handleReset = (): void => {
    this.setState({ hasError: false, error: null });
  };

  render(): React.ReactNode {
    if (this.state.hasError) {
      return (
        <div className="flex items-center justify-center min-h-[50vh] p-6">
          <div className="max-w-md text-center">
            <div className="w-14 h-14 rounded-full bg-health-critical/10 flex items-center justify-center mx-auto mb-4">
              <svg
                className="w-7 h-7 text-health-critical"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={1.8}
                aria-hidden
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z"
                />
              </svg>
            </div>
            <h2 className="text-lg font-semibold text-label-primary mb-2">
              {this.props.section ? `${this.props.section} failed to render` : 'Something went wrong'}
            </h2>
            <p className="text-sm text-label-secondary mb-4">
              A widget on this page threw an error. The rest of the app is fine —
              navigate away or retry this page.
            </p>
            {this.state.error?.message && (
              <details className="text-left bg-fill-secondary rounded-ios p-3 mb-4">
                <summary className="text-xs text-label-tertiary cursor-pointer">
                  Error details
                </summary>
                <pre className="text-[11px] text-label-secondary mt-2 overflow-auto max-h-40">
                  {this.state.error.message}
                </pre>
              </details>
            )}
            <button
              onClick={this.handleReset}
              className="px-4 py-2 rounded-ios bg-accent-primary text-white text-sm font-medium hover:bg-accent-primary/90"
            >
              Retry
            </button>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}

export default DashboardErrorBoundary;
