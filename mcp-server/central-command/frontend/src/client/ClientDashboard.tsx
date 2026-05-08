import React, { useState, useEffect } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { useClient } from './ClientContext';
import { OsirisCareLeaf, WelcomeModal, InfoTip, DashboardErrorBoundary } from '../components/shared';
import { ClientDriftConfig } from './ClientDriftConfig';
import { ComplianceHealthInfographic } from './ComplianceHealthInfographic';
import { DevicesAtRisk } from './DevicesAtRisk';
import { ClientAppliances } from './ClientAppliances';
import { getScoreStatus } from '../constants';
import { DisclaimerFooter } from '../components/composed';

interface Site {
  site_id: string;
  clinic_name: string;
  status: string;
  tier: string;
  evidence_count: number;
  last_evidence: string | null;
}

interface AgentInstallInfo {
  sites: Array<{
    site_id: string;
    clinic_name: string;
    appliances: Array<{
      appliance_id: string;
      hostname: string;
      ip: string;
      grpc_addr: string;
    }>;
  }>;
  agent_version: string;
}

interface KPIs {
  // Stage 1 honest-defaults (round-table 2026-05-05): null when no
  // data so the UI can show "—" rather than a misleading 100%.
  compliance_score: number | null;
  score_status?: 'healthy' | 'partial' | 'no_data';
  // Stage 2: agent_compliance is a SIBLING signal, not blended.
  // score_source is now 'bundles' | 'none' from the canonical helper.
  score_source?: 'bundles' | 'none' | 'blended' | 'bundles_only' | 'agents_only';
  total_checks: number;
  passed: number;
  failed: number;
  warnings: number;
  last_check_at?: string | null;
  stale_check_count?: number;
  // Round-table 30 (2026-05-05): compute_compliance_score now bounds
  // at 90 days by default. The exact window comes from the server.
  window_description?: string;
}

interface AgentCompliance {
  total_agents: number;
  active_agents: number;
  avg_compliance: number;
}

interface DashboardData {
  org: {
    id: string;
    name: string;
    partner_name: string | null;
    partner_brand: string | null;
    provider_count: number;
  };
  sites: Site[];
  kpis: KPIs;
  agent_compliance: AgentCompliance | null;
  unread_notifications: number;
}

interface Notification {
  id: string;
  type: string;
  severity: 'info' | 'warning' | 'critical';
  title: string;
  message: string;
  is_read: boolean;
  created_at: string;
}

export const ClientDashboard: React.FC = () => {
  const navigate = useNavigate();
  const { user, isAuthenticated, isLoading, logout } = useClient();

  const [dashboard, setDashboard] = useState<DashboardData | null>(null);
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showNotifications, setShowNotifications] = useState(false);
  const [driftConfigSite, setDriftConfigSite] = useState<{ id: string; name: string } | null>(null);
  const [agentInfo, setAgentInfo] = useState<AgentInstallInfo | null>(null);
  const [agentSiteExpanded, setAgentSiteExpanded] = useState<string | null>(null);
  const [downloadingConfig, setDownloadingConfig] = useState<string | null>(null);
  const [showWelcome, setShowWelcome] = useState(() => !localStorage.getItem('osiriscare_onboarded'));

  useEffect(() => {
    if (!isLoading && !isAuthenticated) {
      navigate('/client/login', { replace: true });
    }
  }, [isAuthenticated, isLoading, navigate]);

  useEffect(() => {
    if (isAuthenticated) {
      fetchDashboard();
      fetchNotifications();
      fetchAgentInfo();
    }
  }, [isAuthenticated]);

  const fetchDashboard = async () => {
    try {
      const response = await fetch('/api/client/dashboard', {
        credentials: 'include',
      });

      if (response.ok) {
        const data = await response.json();
        setDashboard(data);
      } else {
        setError('Failed to load dashboard');
      }
    } catch (e) {
      setError('Failed to connect to server');
    } finally {
      setLoading(false);
    }
  };

  const fetchNotifications = async () => {
    try {
      const response = await fetch('/api/client/notifications?limit=10', {
        credentials: 'include',
      });

      if (response.ok) {
        const data = await response.json();
        setNotifications(data.notifications || []);
      }
    } catch {
      // Notification fetch failed silently — non-critical
    }
  };

  const fetchAgentInfo = async () => {
    try {
      const response = await fetch('/api/client/agent/install-info', {
        credentials: 'include',
      });
      if (response.ok) {
        const data = await response.json();
        setAgentInfo(data);
      }
    } catch {
      // Agent info fetch failed silently — non-critical
    }
  };

  const downloadConfig = async (siteId: string) => {
    setDownloadingConfig(siteId);
    try {
      const response = await fetch(`/api/client/agent/config/${siteId}`, {
        credentials: 'include',
      });
      if (response.ok) {
        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'osiris-config.json';
        a.click();
        URL.revokeObjectURL(url);
      }
    } catch {
      // Config download failed silently
    } finally {
      setDownloadingConfig(null);
    }
  };

  const downloadInstallScript = async (siteId: string) => {
    try {
      const response = await fetch(`/api/client/agent/install-script/${siteId}`, {
        credentials: 'include',
      });
      if (response.ok) {
        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `install-osiriscare-${siteId}.sh`;
        a.click();
        URL.revokeObjectURL(url);
      }
    } catch {
      // Script download failed silently
    }
  };

  const downloadMobileConfig = async (siteId: string) => {
    try {
      const response = await fetch(`/api/client/agent/mobileconfig/${siteId}`, {
        credentials: 'include',
      });
      if (response.ok) {
        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `OsirisCare-${siteId}.mobileconfig`;
        a.click();
        URL.revokeObjectURL(url);
      }
    } catch {
      // Mobileconfig download failed silently
    }
  };

  const handleLogout = async () => {
    await logout();
    navigate('/client/login');
  };

  /**
   * F5 (sprint 2026-05-08) — Print Compliance Snapshot.
   *
   * Sets CSS variables on <html> so the @media print running
   * header + footer (defined in index.css) include the practice
   * name + the date the customer printed it. Pure window.print()
   * — no backend call, no PDF generation here. The audit-grade
   * artifact is the F1 attestation letter PDF (different endpoint).
   *
   * The dashboard view itself becomes the printable artifact via
   * the F5 print stylesheet: nav chrome hidden, light theme
   * forced, MetricCard/StatusBadge break-inside:avoid, Recharts
   * axes blackened for monochrome printers.
   */
  const handlePrintSnapshot = () => {
    const orgName = dashboard?.org.name || 'OsirisCare Compliance Snapshot';
    const printedAt = new Date().toLocaleDateString(undefined, {
      year: 'numeric', month: 'long', day: 'numeric',
    });
    // Header text — wrap in CSS string literal for @top-center.
    const safeOrg = orgName.replace(/"/g, '');
    document.documentElement.style.setProperty(
      '--print-running-header',
      `"${safeOrg} — Compliance Snapshot, printed ${printedAt}"`,
    );
    document.documentElement.style.setProperty(
      '--print-running-footer',
      `"Audit-supportive technical evidence — verify signed letter at osiriscare.io/verify"`,
    );
    window.print();
  };

  if (isLoading || loading) {
    return (
      <div className="min-h-screen bg-background-primary flex items-center justify-center">
        <div className="text-center">
          <div className="w-14 h-14 mx-auto mb-4 rounded-2xl flex items-center justify-center animate-pulse-soft" style={{ background: 'linear-gradient(135deg, #14A89E 0%, #3CBCB4 100%)' }}>
            <OsirisCareLeaf className="w-7 h-7" color="white" />
          </div>
          <p className="text-label-tertiary">Loading your dashboard...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="min-h-screen bg-background-primary flex items-center justify-center">
        <div className="text-center">
          <div className="w-16 h-16 bg-health-critical/10 rounded-full mx-auto mb-4 flex items-center justify-center">
            <svg className="w-8 h-8 text-health-critical" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
            </svg>
          </div>
          <h2 className="text-xl font-semibold text-label-primary mb-2">Error Loading Dashboard</h2>
          <p className="text-label-secondary mb-4">{error}</p>
          <button
            onClick={() => {
              setError(null);
              setLoading(true);
              fetchDashboard();
            }}
            className="px-4 py-2 bg-accent-primary text-white rounded-lg hover:bg-accent-primary/90"
          >
            Try Again
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-background-primary page-enter">
      <WelcomeModal
        isOpen={showWelcome}
        onClose={() => {
          setShowWelcome(false);
          localStorage.setItem('osiriscare_onboarded', 'true');
        }}
        portalType="client"
      />
      {/* Header */}
      <header className="sticky top-0 z-50 border-b border-separator-light" style={{ background: 'rgba(255,255,255,0.82)', backdropFilter: 'blur(20px) saturate(180%)', WebkitBackdropFilter: 'blur(20px) saturate(180%)' }}>
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex justify-between items-center h-14">
            <div className="flex items-center gap-4">
              <div className="w-10 h-10 rounded-xl flex items-center justify-center" style={{ background: 'linear-gradient(135deg, #14A89E 0%, #3CBCB4 100%)', boxShadow: '0 2px 10px rgba(60,188,180,0.3)' }}>
                <OsirisCareLeaf className="w-5 h-5" color="white" />
              </div>
              <div>
                <h1 className="text-lg font-semibold text-label-primary">{dashboard?.org.name}</h1>
                <p className="text-sm text-label-tertiary">
                  {dashboard?.org.partner_brand || 'OsirisCare'} Compliance Portal
                </p>
              </div>
            </div>

            <div className="flex items-center gap-4">
              {/* Notifications */}
              <div className="relative">
                <button
                  onClick={() => setShowNotifications(!showNotifications)}
                  className="relative p-2 text-label-tertiary hover:text-accent-primary rounded-lg hover:bg-accent-primary/10"
                >
                  <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
                  </svg>
                  {dashboard && dashboard.unread_notifications > 0 && (
                    <span className="absolute -top-1 -right-1 w-5 h-5 bg-health-critical text-white text-xs rounded-full flex items-center justify-center">
                      {dashboard.unread_notifications}
                    </span>
                  )}
                </button>

                {showNotifications && (
                  <div className="absolute right-0 mt-2 w-80 bg-white rounded-2xl shadow-lg border border-separator-light z-50">
                    <div className="p-4 border-b border-separator-light">
                      <h3 className="font-semibold text-label-primary">Notifications</h3>
                    </div>
                    <div className="max-h-96 overflow-y-auto">
                      {notifications.length === 0 ? (
                        <p className="p-4 text-label-tertiary text-sm text-center">No notifications</p>
                      ) : (
                        notifications.map((n) => (
                          <div
                            key={n.id}
                            className={`p-4 border-b border-separator-light ${!n.is_read ? 'bg-accent-primary/10' : ''}`}
                          >
                            <p className="font-medium text-label-primary text-sm">{n.title}</p>
                            <p className="text-label-secondary text-sm mt-1">{n.message}</p>
                          </div>
                        ))
                      )}
                    </div>
                  </div>
                )}
              </div>

              {/* Print Compliance Snapshot — F5 sprint 2026-05-08.
                  Triggers window.print(); the @media print stylesheet
                  in index.css hides nav chrome + dark theme + non-
                  print buttons and forces a B&W-printer-friendly
                  layout. Hidden in @media print itself via
                  data-print="hidden". */}
              <button
                onClick={handlePrintSnapshot}
                data-print="hidden"
                data-action="print"
                className="hidden sm:inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium text-label-secondary hover:text-accent-primary border border-separator-light rounded-lg hover:bg-accent-primary/10 focus-visible:ring-2 focus-visible:ring-accent-primary focus-visible:ring-offset-2"
                title="Print a snapshot of this dashboard for insurance underwriting or board review"
                aria-label="Print Compliance Snapshot"
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 17h2a2 2 0 002-2v-4a2 2 0 00-2-2H5a2 2 0 00-2 2v4a2 2 0 002 2h2m2 4h6a2 2 0 002-2v-4a2 2 0 00-2-2H9a2 2 0 00-2 2v4a2 2 0 002 2zm8-12V5a2 2 0 00-2-2H9a2 2 0 00-2 2v4h10z" />
                </svg>
                <span>Print Snapshot</span>
              </button>

              {/* User Menu */}
              <div className="flex items-center gap-3">
                <div className="text-right">
                  <p className="text-sm font-medium text-label-primary">{user?.name || user?.email}</p>
                  <p className="text-xs text-label-tertiary capitalize">{user?.role}</p>
                </div>
                <button
                  onClick={handleLogout}
                  className="p-2 text-label-tertiary hover:text-accent-primary rounded-lg hover:bg-accent-primary/10 focus-visible:ring-2 focus-visible:ring-accent-primary focus-visible:ring-offset-2"
                  title="Sign out"
                  aria-label="Sign out"
                >
                  <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" />
                  </svg>
                </button>
              </div>
            </div>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        {/* Print-only banner — F5 sprint 2026-05-08. Hidden in
            normal viewing; surfaces on the first page of the printed
            output as a self-contained title block with practice name +
            the date the customer printed it. The print stylesheet in
            index.css flips display:block under @media print. */}
        <div
          className="print-only mb-6 pb-4 border-b-2 border-accent-primary"
          aria-hidden="true"
        >
          <p className="text-xs uppercase tracking-widest text-label-tertiary">
            {dashboard?.org.partner_brand || 'OsirisCare'} Compliance Portal
          </p>
          <h2 className="text-2xl font-bold text-label-primary mt-1">
            {dashboard?.org.name} — Compliance Snapshot
          </h2>
          <p className="text-sm text-label-secondary mt-1">
            Printed {new Date().toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' })}.
            This snapshot is audit-supportive technical evidence and is not a substitute for the §164.528 disclosure accounting,
            designated record set, or §164.530(d) complaint log. Verify the signed Compliance Attestation Letter at osiriscare.io/verify.
          </p>
        </div>

        {driftConfigSite ? (
          <ClientDriftConfig
            siteId={driftConfigSite.id}
            siteName={driftConfigSite.name}
            onBack={() => setDriftConfigSite(null)}
          />
        ) : (
        <>
        {/* KPIs */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6 mb-8">
          {/* Compliance Score */}
          <div className="bg-white rounded-2xl shadow-sm border border-separator-light p-6 hover:shadow-md transition-shadow">
            <div className="flex items-center gap-3 mb-3">
              <div className="w-10 h-10 rounded-xl flex items-center justify-center" style={{ background: 'linear-gradient(135deg, #14A89E 0%, #3CBCB4 100%)' }}>
                <OsirisCareLeaf className="w-5 h-5" color="white" />
              </div>
              <p className="text-sm font-medium text-label-tertiary">Compliance Score<InfoTip text="Percentage of automated security checks passing. A high score means your systems are configured as expected." /></p>
            </div>
            {typeof dashboard?.kpis.compliance_score !== 'number' ? (
              <>
                <span className="text-4xl font-bold tabular-nums text-label-tertiary">—</span>
                <p className="mt-2 text-sm text-label-tertiary">
                  Awaiting first scan
                </p>
              </>
            ) : (
              <>
                <span className={`text-4xl font-bold tabular-nums ${getScoreStatus(dashboard.kpis.compliance_score).color}`}>
                  {dashboard.kpis.compliance_score.toFixed(1)}%
                </span>
                <div className={`mt-4 h-2 rounded-full ${getScoreStatus(dashboard.kpis.compliance_score).bgColor}`}>
                  <div
                    className={`h-full rounded-full transition-all ${getScoreStatus(dashboard.kpis.compliance_score).dotColor}`}
                    style={{ width: `${dashboard.kpis.compliance_score}%` }}
                  />
                </div>
                {dashboard.kpis.score_status === 'partial' &&
                  typeof dashboard.kpis.stale_check_count === 'number' &&
                  dashboard.kpis.stale_check_count > 0 && (
                    <p className="mt-2 text-xs text-label-tertiary">
                      {dashboard.kpis.stale_check_count} check
                      {dashboard.kpis.stale_check_count === 1 ? '' : 's'}{' '}
                      haven't run in 7+ days
                    </p>
                  )}
                {typeof dashboard.kpis.last_check_at === 'string' && (
                  <p className="mt-1 text-xs text-label-tertiary">
                    Last scan:{' '}
                    {new Date(dashboard.kpis.last_check_at).toLocaleString()}
                  </p>
                )}
                {/* Round-table 31 C4 (2026-05-05): surface
                    window_description so customers understand "Snapshot:
                    last 30 days" framing — pre-fix the field shipped as
                    dead data. */}
                {typeof dashboard.kpis.window_description === 'string' && (
                  <p className="mt-1 text-[11px] text-label-tertiary">
                    {dashboard.kpis.window_description}
                  </p>
                )}
              </>
            )}
          </div>

          {/* Checks Passed */}
          <div className="bg-white rounded-2xl shadow-sm border border-separator-light p-6 hover:shadow-md transition-shadow">
            <div className="flex items-center gap-3 mb-3">
              <div className="w-10 h-10 rounded-xl bg-health-healthy/10 flex items-center justify-center">
                <svg className="w-5 h-5 text-health-healthy" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                </svg>
              </div>
              <p className="text-sm font-medium text-label-tertiary">Controls Passed<InfoTip text="Security configuration checks that are currently in the expected state." /></p>
            </div>
            <div className="flex items-end gap-2">
              <span className="text-4xl font-bold text-health-healthy tabular-nums">{dashboard?.kpis.passed}</span>
              <span className="text-label-tertiary mb-1 tabular-nums">/ {dashboard?.kpis.total_checks}</span>
            </div>
            <p className="mt-2 text-sm text-label-tertiary">Last 24 hours</p>
          </div>

          {/* Issues */}
          <div className="bg-white rounded-2xl shadow-sm border border-separator-light p-6 hover:shadow-md transition-shadow">
            <div className="flex items-center gap-3 mb-3">
              <div className="w-10 h-10 rounded-xl bg-health-critical/10 flex items-center justify-center">
                <svg className="w-5 h-5 text-health-critical" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                </svg>
              </div>
              <p className="text-sm font-medium text-label-tertiary">Issues Detected<InfoTip text="Settings that are failing compliance checks and may need attention." /></p>
            </div>
            <div className="flex items-end gap-4">
              <div>
                <span className="text-4xl font-bold text-health-critical tabular-nums">{dashboard?.kpis.failed}</span>
                <p className="text-xs text-label-tertiary">Failed</p>
              </div>
              <div>
                <span className="text-2xl font-bold text-health-warning tabular-nums">{dashboard?.kpis.warnings}</span>
                <p className="text-xs text-label-tertiary">Warnings</p>
              </div>
            </div>
          </div>

          {/* Sites */}
          <div className="bg-white rounded-2xl shadow-sm border border-separator-light p-6 hover:shadow-md transition-shadow">
            <div className="flex items-center gap-3 mb-3">
              <div className="w-10 h-10 rounded-xl bg-ios-cyan/10 flex items-center justify-center">
                <svg className="w-5 h-5 text-ios-cyan" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
                </svg>
              </div>
              <p className="text-sm font-medium text-label-tertiary">Sites Monitored<InfoTip text="Number of your practice locations with active compliance monitoring." /></p>
            </div>
            <span className="text-4xl font-bold text-label-primary tabular-nums">{dashboard?.sites.length || 0}</span>
            <p className="mt-2 text-sm text-label-tertiary">
              {dashboard?.org.provider_count} provider{dashboard?.org.provider_count !== 1 ? 's' : ''}
            </p>
          </div>
        </div>

        {/* Workstation Agents — Stage 2 sibling signal (round-table 2026-05-05).
            Pre-Stage-2 agent compliance was blended into the headline at 30%
            weight, masking both signals. Now rendered separately so the
            customer can tell which source is healthy. */}
        {dashboard?.agent_compliance && (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6 mb-8">
            <div className="bg-white rounded-2xl shadow-sm border border-separator-light p-6">
              <div className="flex items-center gap-3 mb-3">
                <div className="w-10 h-10 rounded-xl bg-ios-blue/10 flex items-center justify-center">
                  <svg className="w-5 h-5 text-ios-blue" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
                  </svg>
                </div>
                <p className="text-sm font-medium text-label-tertiary">
                  Workstation Agents
                  <InfoTip text="Compliance reported by the lightweight agent installed on each workstation. A separate signal from drift bundles." />
                </p>
              </div>
              <div className="flex items-baseline gap-2">
                <span className={`text-3xl font-bold tabular-nums ${getScoreStatus(dashboard.agent_compliance.avg_compliance).color}`}>
                  {dashboard.agent_compliance.avg_compliance.toFixed(1)}%
                </span>
                <span className="text-sm text-label-tertiary">avg</span>
              </div>
              <p className="mt-2 text-sm text-label-tertiary">
                {dashboard.agent_compliance.active_agents} of {dashboard.agent_compliance.total_agents} agents active
              </p>
            </div>

            {typeof dashboard.kpis.last_check_at === 'string' && (
              <div className="bg-white rounded-2xl shadow-sm border border-separator-light p-6">
                <p className="text-sm font-medium text-label-tertiary mb-2">Data Freshness</p>
                <p className="text-base font-semibold text-label-primary">
                  Last scan {new Date(dashboard.kpis.last_check_at).toLocaleString()}
                </p>
                {typeof dashboard.kpis.stale_check_count === 'number' &&
                  dashboard.kpis.stale_check_count > 0 && (
                    <p className="mt-2 text-sm text-health-warning">
                      {dashboard.kpis.stale_check_count} check
                      {dashboard.kpis.stale_check_count === 1 ? '' : 's'}{' '}
                      haven't run in 7+ days
                    </p>
                  )}
              </div>
            )}
          </div>
        )}

        {/* Compliance Health Infographic */}
        {dashboard && dashboard.sites.length > 0 && (
          <ComplianceHealthInfographic
            sites={dashboard.sites.map(s => ({ site_id: s.site_id, clinic_name: s.clinic_name }))}
          />
        )}

        {/* Devices at Risk */}
        {dashboard && dashboard.sites.length > 0 && (
          <DevicesAtRisk
            siteId={dashboard.sites[0].site_id}
          />
        )}

        {/* Compliance appliances — RT33 (2026-05-05) */}
        {dashboard && dashboard.sites.length > 0 && (
          <div className="mb-8">
            <h2 className="text-lg font-medium text-label-primary mb-3">
              Compliance Appliances
            </h2>
            <ClientAppliances />
          </div>
        )}

        {/* Monitor Your Devices — Agent Downloads */}
        {agentInfo && agentInfo.sites.length > 0 && (
        <div className="mb-8 rounded-2xl overflow-hidden border-2 border-accent-primary/30" style={{ background: 'linear-gradient(135deg, rgba(20,168,158,0.06) 0%, rgba(60,188,180,0.03) 100%)' }}>
          <div className="p-6 sm:p-8">
            <div className="flex items-start justify-between gap-6 flex-wrap">
              <div className="flex items-center gap-4">
                <div className="w-14 h-14 rounded-2xl flex items-center justify-center flex-shrink-0" style={{ background: 'linear-gradient(135deg, #14A89E 0%, #3CBCB4 100%)', boxShadow: '0 4px 14px rgba(60,188,180,0.35)' }}>
                  <svg className="w-7 h-7 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 3v2m6-2v2M9 19v2m6-2v2M5 9H3m2 6H3m18-6h-2m2 6h-2M7 19h10a2 2 0 002-2V7a2 2 0 00-2-2H7a2 2 0 00-2 2v10a2 2 0 002 2zM9 9h6v6H9V9z" />
                  </svg>
                </div>
                <div>
                  <h2 className="text-xl font-bold text-label-primary">Monitor Your Devices</h2>
                  <p className="text-label-secondary mt-1">
                    Install the OsirisCare agent on your Mac and Windows workstations for real-time HIPAA compliance monitoring.
                  </p>
                </div>
              </div>
              <span className="px-3 py-1 bg-accent-primary/10 text-accent-primary text-xs font-semibold rounded-full whitespace-nowrap">
                Agent v{agentInfo.agent_version}
              </span>
            </div>

            {/* Platform cards */}
            <div className="mt-6 grid grid-cols-1 md:grid-cols-2 gap-4">
              {/* macOS Card */}
              <div className="bg-white rounded-xl border border-separator-light p-5">
                <div className="flex items-center gap-3 mb-3">
                  <div className="w-10 h-10 bg-label-primary rounded-lg flex items-center justify-center">
                    <svg className="w-5 h-5 text-white" viewBox="0 0 24 24" fill="currentColor">
                      <path d="M18.71 19.5c-.83 1.24-1.71 2.45-3.05 2.47-1.34.03-1.77-.79-3.29-.79-1.53 0-2 .77-3.27.82-1.31.05-2.3-1.32-3.14-2.53C4.25 17 2.94 12.45 4.7 9.39c.87-1.52 2.43-2.48 4.12-2.51 1.28-.02 2.5.87 3.29.87.78 0 2.26-1.07 3.8-.91.65.03 2.47.26 3.64 1.98-.09.06-2.17 1.28-2.15 3.81.03 3.02 2.65 4.03 2.68 4.04-.03.07-.42 1.44-1.38 2.83M13 3.5c.73-.83 1.94-1.46 2.94-1.5.13 1.17-.34 2.35-1.04 3.19-.69.85-1.83 1.51-2.95 1.42-.15-1.15.41-2.35 1.05-3.11z"/>
                    </svg>
                  </div>
                  <div>
                    <h3 className="font-semibold text-label-primary">macOS Agent</h3>
                    <p className="text-xs text-label-tertiary">Universal (Intel + Apple Silicon)</p>
                  </div>
                </div>
                <p className="text-sm text-label-secondary mb-4">
                  12 compliance checks: FileVault, Gatekeeper, SIP, Firewall, Updates, Screen Lock, and more. Attempts automated remediation of 6 common issues; escalates others to your administrator.
                </p>
                <div className="text-xs text-label-tertiary mb-4 flex items-center gap-2">
                  <svg className="w-4 h-4 text-accent-primary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                  Works with Jamf, Intune, Mosyle, Kandji, or manual install
                </div>
              </div>

              {/* Windows Card */}
              <div className="bg-white rounded-xl border border-separator-light p-5">
                <div className="flex items-center gap-3 mb-3">
                  <div className="w-10 h-10 bg-ios-blue rounded-lg flex items-center justify-center">
                    <svg className="w-5 h-5 text-white" viewBox="0 0 24 24" fill="currentColor">
                      <path d="M3 12V6.75l8-1.25V12H3zm0 .5h8v6.5l-8-1.25V12.5zM11.5 12V5.25L21 3.5V12h-9.5zm0 .5H21v8.5l-9.5-1.75V12.5z"/>
                    </svg>
                  </div>
                  <div>
                    <h3 className="font-semibold text-label-primary">Windows Agent</h3>
                    <p className="text-xs text-label-tertiary">Windows 10/11 (64-bit)</p>
                  </div>
                </div>
                <p className="text-sm text-label-secondary mb-4">
                  8 compliance checks: BitLocker, Defender, Patches, Firewall, Screen Lock, WinRM, and more. Auto-deployed via GPO on domain networks.
                </p>
                <div className="text-xs text-label-tertiary mb-4 flex items-center gap-2">
                  <svg className="w-4 h-4 text-accent-primary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                  Auto-deploys via Active Directory GPO when available
                </div>
              </div>
            </div>

            {/* Per-site download section */}
            <div className="mt-6">
              <h3 className="text-sm font-semibold text-label-secondary mb-3">Download for your sites</h3>
              <div className="space-y-3">
                {agentInfo.sites.map((site) => (
                  <div key={site.site_id} className="bg-white rounded-xl border border-separator-light">
                    <button
                      onClick={() => setAgentSiteExpanded(agentSiteExpanded === site.site_id ? null : site.site_id)}
                      className="w-full flex items-center justify-between p-4 hover:bg-fill-secondary transition rounded-xl"
                    >
                      <div className="flex items-center gap-3">
                        <div className={`w-2.5 h-2.5 rounded-full ${site.appliances.length > 0 ? 'bg-health-healthy' : 'bg-health-warning'}`} />
                        <div className="text-left">
                          <p className="font-medium text-label-primary">{site.clinic_name}</p>
                          <p className="text-xs text-label-tertiary">
                            {site.appliances.length > 0
                              ? `Appliance: ${site.appliances[0].ip}`
                              : 'No appliance connected'}
                          </p>
                        </div>
                      </div>
                      <svg className={`w-5 h-5 text-label-tertiary transition-transform ${agentSiteExpanded === site.site_id ? 'rotate-180' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                      </svg>
                    </button>

                    {agentSiteExpanded === site.site_id && (
                      <div className="border-t border-separator-light p-4">
                        {site.appliances.length === 0 ? (
                          <div className="text-center py-4">
                            <p className="text-sm text-health-warning font-medium">No appliance connected to this site yet.</p>
                            <p className="text-xs text-label-tertiary mt-1">Contact your MSP to set up an appliance before deploying agents.</p>
                          </div>
                        ) : (
                          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
                            <button
                              onClick={() => downloadConfig(site.site_id)}
                              disabled={downloadingConfig === site.site_id}
                              className="flex items-center gap-2 px-4 py-3 bg-accent-primary text-white rounded-lg hover:bg-accent-primary/90 transition text-sm font-medium disabled:opacity-50"
                            >
                              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                              </svg>
                              {downloadingConfig === site.site_id ? 'Downloading...' : 'Config File'}
                            </button>

                            <button
                              onClick={() => downloadInstallScript(site.site_id)}
                              className="flex items-center gap-2 px-4 py-3 bg-label-primary text-white rounded-lg hover:bg-label-primary/90 transition text-sm font-medium"
                            >
                              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 9l3 3-3 3m5 0h3M5 20h14a2 2 0 002-2V6a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
                              </svg>
                              Install Script
                            </button>

                            <button
                              onClick={() => downloadMobileConfig(site.site_id)}
                              className="flex items-center gap-2 px-4 py-3 bg-ios-indigo text-white rounded-lg hover:bg-ios-indigo/90 transition text-sm font-medium"
                            >
                              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 18h.01M8 21h8a2 2 0 002-2V5a2 2 0 00-2-2H8a2 2 0 00-2 2v14a2 2 0 002 2z" />
                              </svg>
                              MDM Profile
                            </button>

                            <a
                              href={`/api/client/agent/config/${site.site_id}`}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="flex items-center gap-2 px-4 py-3 bg-white text-label-secondary border border-separator-medium rounded-lg hover:bg-fill-secondary transition text-sm font-medium"
                            >
                              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                              </svg>
                              View Config
                            </a>
                          </div>
                        )}

                        {site.appliances.length > 0 && (
                          <div className="mt-4 p-3 bg-fill-secondary rounded-lg">
                            <p className="text-xs font-medium text-label-secondary mb-2">Quick Install (macOS)</p>
                            <div className="flex items-center gap-2">
                              <code className="flex-1 text-xs bg-label-primary text-health-healthy p-2 rounded font-mono overflow-x-auto">
                                curl -sL {window.location.origin}/api/client/agent/install-script/{site.site_id} | sudo bash
                              </code>
                              <button
                                onClick={() => {
                                  navigator.clipboard.writeText(
                                    `curl -sL ${window.location.origin}/api/client/agent/install-script/${site.site_id} | sudo bash`
                                  );
                                }}
                                className="p-2 text-label-tertiary hover:text-accent-primary rounded"
                                title="Copy to clipboard"
                              >
                                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
                                </svg>
                              </button>
                            </div>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
        )}

        {/* Sites List */}
        <div className="bg-white rounded-2xl shadow-sm border border-separator-light">
          <div className="p-6 border-b border-separator-light">
            <h2 className="text-lg font-semibold text-label-primary">Your Sites</h2>
            <p className="text-sm text-label-tertiary mt-1">Click a site to view detailed compliance status</p>
          </div>

          {dashboard?.sites.length === 0 ? (
            <div className="p-8 text-center">
              <svg className="w-12 h-12 text-label-tertiary/50 mx-auto mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
              </svg>
              <p className="text-label-secondary">No sites configured yet</p>
            </div>
          ) : (
            <div className="divide-y divide-separator-light">
              {dashboard?.sites.map((site) => (
                <div
                  key={site.site_id}
                  className="flex items-center justify-between p-6 hover:bg-accent-primary/5 transition"
                >
                  <div className="flex items-center gap-4">
                    <div className={`w-3 h-3 rounded-full ${site.status === 'active' ? 'bg-health-healthy' : 'bg-health-neutral'}`} />
                    <div>
                      <h3 className="font-medium text-label-primary">{site.clinic_name}</h3>
                    </div>
                  </div>
                  <div className="flex items-center gap-8">
                    <div className="text-right">
                      <p className="text-sm font-medium text-label-primary">{site.evidence_count} checks</p>
                      <p className="text-xs text-label-tertiary">
                        {site.last_evidence
                          ? `Last: ${new Date(site.last_evidence).toLocaleDateString()}`
                          : 'No data yet'
                        }
                      </p>
                    </div>
                    <span className={`px-3 py-1 text-xs font-medium rounded-full ${site.tier === 'essential' ? 'bg-ios-blue/10 text-ios-blue' : site.tier === 'professional' ? 'bg-ios-purple/10 text-ios-purple' : 'bg-fill-secondary text-label-secondary'}`}>
                      {site.tier || 'Standard'}
                    </span>
                    <button
                      onClick={() => setDriftConfigSite({ id: site.site_id, name: site.clinic_name })}
                      className="text-xs text-accent-primary hover:text-accent-primary/80 font-medium"
                    >
                      Security Checks
                    </button>
                    <Link
                      to="/client/evidence"
                      className="text-xs text-accent-primary hover:text-accent-primary/80 font-medium"
                    >
                      View Evidence
                    </Link>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Quick Links */}
        <div className="mt-8 grid grid-cols-1 md:grid-cols-3 lg:grid-cols-6 gap-6">
          <Link
            to="/client/compliance"
            className="bg-white rounded-2xl shadow-sm border border-separator-light p-6 hover:border-accent-primary hover:shadow-md transition-all"
          >
            <div className="flex items-center gap-4">
              <div className="w-12 h-12 bg-accent-primary/10 rounded-lg flex items-center justify-center">
                <svg className="w-6 h-6 text-accent-primary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4" />
                </svg>
              </div>
              <div>
                <h3 className="font-semibold text-label-primary">HIPAA Compliance</h3>
                <p className="text-sm text-label-tertiary">Risk assessments, policies & more</p>
              </div>
            </div>
          </Link>

          <Link
            to="/client/evidence"
            className="bg-white rounded-2xl shadow-sm border border-separator-light p-6 hover:border-accent-primary hover:shadow-md transition-all"
          >
            <div className="flex items-center gap-4">
              <div className="w-12 h-12 bg-accent-primary/10 rounded-lg flex items-center justify-center">
                <svg className="w-6 h-6 text-accent-primary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                </svg>
              </div>
              <div>
                <h3 className="font-semibold text-label-primary">Evidence Archive</h3>
                <p className="text-sm text-label-tertiary">View compliance evidence</p>
              </div>
            </div>
          </Link>

          <Link
            to="/client/reports"
            className="bg-white rounded-2xl shadow-sm border border-separator-light p-6 hover:border-accent-primary hover:shadow-md transition-all"
          >
            <div className="flex items-center gap-4">
              <div className="w-12 h-12 bg-ios-purple/10 rounded-lg flex items-center justify-center">
                <svg className="w-6 h-6 text-ios-purple" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 17v-2m3 2v-4m3 4v-6m2 10H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                </svg>
              </div>
              <div>
                <h3 className="font-semibold text-label-primary">Compliance Reports</h3>
                <p className="text-sm text-label-tertiary">Real-time & monthly PDFs</p>
              </div>
            </div>
          </Link>

          <Link
            to="/client/healing-logs"
            className="bg-white rounded-2xl shadow-sm border border-separator-light p-6 hover:border-accent-primary hover:shadow-md transition-all"
          >
            <div className="flex items-center gap-4">
              <div className="w-12 h-12 bg-health-healthy/10 rounded-lg flex items-center justify-center">
                <svg className="w-6 h-6 text-health-healthy" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
                </svg>
              </div>
              <div>
                <h3 className="font-semibold text-label-primary">Healing Activity</h3>
                <p className="text-sm text-label-tertiary">Auto-healing logs & approvals</p>
              </div>
            </div>
          </Link>

          <Link
            to="/client/escalations"
            className="bg-white rounded-2xl shadow-sm border border-separator-light p-6 hover:border-health-critical hover:shadow-md transition-all"
          >
            <div className="flex items-center gap-4">
              <div className="w-12 h-12 bg-health-critical/10 rounded-lg flex items-center justify-center">
                <svg className="w-6 h-6 text-health-critical" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4.5c-.77-.833-2.694-.833-3.464 0L3.34 16.5c-.77.833.192 2.5 1.732 2.5z" />
                </svg>
              </div>
              <div>
                <h3 className="font-semibold text-label-primary">Escalations</h3>
                <p className="text-sm text-label-tertiary">L3 tickets & routing</p>
              </div>
            </div>
          </Link>

          <Link
            to="/client/settings"
            className="bg-white rounded-2xl shadow-sm border border-separator-light p-6 hover:border-accent-primary hover:shadow-md transition-all"
          >
            <div className="flex items-center gap-4">
              <div className="w-12 h-12 bg-fill-secondary rounded-lg flex items-center justify-center">
                <svg className="w-6 h-6 text-label-secondary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                </svg>
              </div>
              <div>
                <h3 className="font-semibold text-label-primary">Account Settings</h3>
                <p className="text-sm text-label-tertiary">Manage users and preferences</p>
              </div>
            </div>
          </Link>

          <Link
            to="/client/help"
            className="bg-white rounded-2xl shadow-sm border border-separator-light p-6 hover:border-accent-primary hover:shadow-md transition-all"
          >
            <div className="flex items-center gap-4">
              <div className="w-12 h-12 bg-ios-blue/10 rounded-lg flex items-center justify-center">
                <svg className="w-6 h-6 text-ios-blue" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
              </div>
              <div>
                <h3 className="font-semibold text-label-primary">Help & Docs</h3>
                <p className="text-sm text-label-tertiary">How-to guides and FAQs</p>
              </div>
            </div>
          </Link>
        </div>
        </>
        )}
      </main>

      {/* Footer */}
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <DisclaimerFooter />
      </div>
    </div>
  );
};

// Session 203 Batch 6 H2 fix: wrap the dashboard in DashboardErrorBoundary
// so a single failing query doesn't blank the whole page. The boundary
// renders a friendly retry screen scoped to the page content (the
// branding header + sidebar stay mounted) and reports the error to
// console for ops triage.
const ClientDashboardWithBoundary: React.FC = () => (
  <DashboardErrorBoundary section="Client Dashboard">
    <ClientDashboard />
  </DashboardErrorBoundary>
);

export default ClientDashboardWithBoundary;
