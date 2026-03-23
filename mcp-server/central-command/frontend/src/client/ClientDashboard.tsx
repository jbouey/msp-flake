import React, { useState, useEffect } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { useClient } from './ClientContext';
import { OsirisCareLeaf } from '../components/shared';
import { ClientDriftConfig } from './ClientDriftConfig';
import { ComplianceHealthInfographic } from './ComplianceHealthInfographic';
import { DevicesAtRisk } from './DevicesAtRisk';

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
  compliance_score: number;
  total_checks: number;
  passed: number;
  failed: number;
  warnings: number;
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
    } catch (e) {
      console.error('Failed to fetch notifications:', e);
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
    } catch (e) {
      console.error('Failed to fetch agent info:', e);
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
    } catch (e) {
      console.error('Config download failed:', e);
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
    } catch (e) {
      console.error('Script download failed:', e);
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
    } catch (e) {
      console.error('Mobileconfig download failed:', e);
    }
  };

  const handleLogout = async () => {
    await logout();
    navigate('/client/login');
  };

  const getScoreColor = (score: number) => {
    if (score >= 95) return 'text-green-600';
    if (score >= 80) return 'text-yellow-600';
    return 'text-red-600';
  };

  const getScoreBg = (score: number) => {
    if (score >= 95) return 'bg-green-100';
    if (score >= 80) return 'bg-yellow-100';
    return 'bg-red-100';
  };

  if (isLoading || loading) {
    return (
      <div className="min-h-screen bg-slate-50/80 flex items-center justify-center">
        <div className="text-center">
          <div className="w-14 h-14 mx-auto mb-4 rounded-2xl flex items-center justify-center animate-pulse-soft" style={{ background: 'linear-gradient(135deg, #14A89E 0%, #3CBCB4 100%)' }}>
            <OsirisCareLeaf className="w-7 h-7" color="white" />
          </div>
          <p className="text-slate-500">Loading your dashboard...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="min-h-screen bg-slate-50 flex items-center justify-center">
        <div className="text-center">
          <div className="w-16 h-16 bg-red-100 rounded-full mx-auto mb-4 flex items-center justify-center">
            <svg className="w-8 h-8 text-red-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
            </svg>
          </div>
          <h2 className="text-xl font-semibold text-slate-900 mb-2">Error Loading Dashboard</h2>
          <p className="text-slate-600 mb-4">{error}</p>
          <button
            onClick={() => {
              setError(null);
              setLoading(true);
              fetchDashboard();
            }}
            className="px-4 py-2 bg-teal-600 text-white rounded-lg hover:bg-teal-700"
          >
            Try Again
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-50/80 page-enter">
      {/* Header */}
      <header className="sticky top-0 z-50 border-b border-slate-200/60" style={{ background: 'rgba(255,255,255,0.82)', backdropFilter: 'blur(20px) saturate(180%)', WebkitBackdropFilter: 'blur(20px) saturate(180%)' }}>
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex justify-between items-center h-14">
            <div className="flex items-center gap-4">
              <div className="w-10 h-10 rounded-xl flex items-center justify-center" style={{ background: 'linear-gradient(135deg, #14A89E 0%, #3CBCB4 100%)', boxShadow: '0 2px 10px rgba(60,188,180,0.3)' }}>
                <OsirisCareLeaf className="w-5 h-5" color="white" />
              </div>
              <div>
                <h1 className="text-lg font-semibold text-slate-900">{dashboard?.org.name}</h1>
                <p className="text-sm text-slate-500">
                  {dashboard?.org.partner_brand || 'OsirisCare'} Compliance Portal
                </p>
              </div>
            </div>

            <div className="flex items-center gap-4">
              {/* Notifications */}
              <div className="relative">
                <button
                  onClick={() => setShowNotifications(!showNotifications)}
                  className="relative p-2 text-slate-500 hover:text-teal-600 rounded-lg hover:bg-teal-50"
                >
                  <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
                  </svg>
                  {dashboard && dashboard.unread_notifications > 0 && (
                    <span className="absolute -top-1 -right-1 w-5 h-5 bg-red-500 text-white text-xs rounded-full flex items-center justify-center">
                      {dashboard.unread_notifications}
                    </span>
                  )}
                </button>

                {showNotifications && (
                  <div className="absolute right-0 mt-2 w-80 bg-white rounded-2xl shadow-lg border border-slate-100 z-50">
                    <div className="p-4 border-b border-slate-200">
                      <h3 className="font-semibold text-slate-900">Notifications</h3>
                    </div>
                    <div className="max-h-96 overflow-y-auto">
                      {notifications.length === 0 ? (
                        <p className="p-4 text-slate-500 text-sm text-center">No notifications</p>
                      ) : (
                        notifications.map((n) => (
                          <div
                            key={n.id}
                            className={`p-4 border-b border-slate-100 ${!n.is_read ? 'bg-teal-50' : ''}`}
                          >
                            <p className="font-medium text-slate-900 text-sm">{n.title}</p>
                            <p className="text-slate-600 text-sm mt-1">{n.message}</p>
                          </div>
                        ))
                      )}
                    </div>
                  </div>
                )}
              </div>

              {/* User Menu */}
              <div className="flex items-center gap-3">
                <div className="text-right">
                  <p className="text-sm font-medium text-slate-900">{user?.name || user?.email}</p>
                  <p className="text-xs text-slate-500 capitalize">{user?.role}</p>
                </div>
                <button
                  onClick={handleLogout}
                  className="p-2 text-slate-500 hover:text-teal-600 rounded-lg hover:bg-teal-50"
                  title="Sign out"
                >
                  <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
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
        {driftConfigSite ? (
          <ClientDriftConfig
            siteId={driftConfigSite.id}
            siteName={driftConfigSite.name}
            onBack={() => setDriftConfigSite(null)}
          />
        ) : (
        <>
        {/* KPIs */}
        <div className="grid grid-cols-1 md:grid-cols-4 gap-6 mb-8">
          {/* Compliance Score */}
          <div className="bg-white rounded-2xl shadow-sm border border-slate-100 p-6 hover:shadow-md transition-shadow">
            <div className="flex items-center gap-3 mb-3">
              <div className="w-10 h-10 rounded-xl flex items-center justify-center" style={{ background: 'linear-gradient(135deg, #14A89E 0%, #3CBCB4 100%)' }}>
                <OsirisCareLeaf className="w-5 h-5" color="white" />
              </div>
              <p className="text-sm font-medium text-slate-500">Compliance Score</p>
            </div>
            <span className={`text-4xl font-bold tabular-nums ${getScoreColor(dashboard?.kpis.compliance_score || 0)}`}>
              {dashboard?.kpis.compliance_score.toFixed(1)}%
            </span>
            <div className={`mt-4 h-2 rounded-full ${getScoreBg(dashboard?.kpis.compliance_score || 0)}`}>
              <div
                className={`h-full rounded-full transition-all ${dashboard?.kpis.compliance_score && dashboard.kpis.compliance_score >= 95 ? 'bg-green-500' : dashboard?.kpis.compliance_score && dashboard.kpis.compliance_score >= 80 ? 'bg-yellow-500' : 'bg-red-500'}`}
                style={{ width: `${dashboard?.kpis.compliance_score || 0}%` }}
              />
            </div>
          </div>

          {/* Checks Passed */}
          <div className="bg-white rounded-2xl shadow-sm border border-slate-100 p-6 hover:shadow-md transition-shadow">
            <div className="flex items-center gap-3 mb-3">
              <div className="w-10 h-10 rounded-xl bg-green-100 flex items-center justify-center">
                <svg className="w-5 h-5 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                </svg>
              </div>
              <p className="text-sm font-medium text-slate-500">Controls Passed</p>
            </div>
            <div className="flex items-end gap-2">
              <span className="text-4xl font-bold text-green-600 tabular-nums">{dashboard?.kpis.passed}</span>
              <span className="text-slate-500 mb-1 tabular-nums">/ {dashboard?.kpis.total_checks}</span>
            </div>
            <p className="mt-2 text-sm text-slate-500">Last 24 hours</p>
          </div>

          {/* Issues */}
          <div className="bg-white rounded-2xl shadow-sm border border-slate-100 p-6 hover:shadow-md transition-shadow">
            <div className="flex items-center gap-3 mb-3">
              <div className="w-10 h-10 rounded-xl bg-red-100 flex items-center justify-center">
                <svg className="w-5 h-5 text-red-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                </svg>
              </div>
              <p className="text-sm font-medium text-slate-500">Issues Detected</p>
            </div>
            <div className="flex items-end gap-4">
              <div>
                <span className="text-4xl font-bold text-red-600 tabular-nums">{dashboard?.kpis.failed}</span>
                <p className="text-xs text-slate-500">Failed</p>
              </div>
              <div>
                <span className="text-2xl font-bold text-yellow-600 tabular-nums">{dashboard?.kpis.warnings}</span>
                <p className="text-xs text-slate-500">Warnings</p>
              </div>
            </div>
          </div>

          {/* Sites */}
          <div className="bg-white rounded-2xl shadow-sm border border-slate-100 p-6 hover:shadow-md transition-shadow">
            <div className="flex items-center gap-3 mb-3">
              <div className="w-10 h-10 rounded-xl bg-cyan-100 flex items-center justify-center">
                <svg className="w-5 h-5 text-cyan-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
                </svg>
              </div>
              <p className="text-sm font-medium text-slate-500">Sites Monitored</p>
            </div>
            <span className="text-4xl font-bold text-slate-900 tabular-nums">{dashboard?.sites.length || 0}</span>
            <p className="mt-2 text-sm text-slate-500">
              {dashboard?.org.provider_count} provider{dashboard?.org.provider_count !== 1 ? 's' : ''}
            </p>
          </div>
        </div>

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

        {/* Protect Your Devices — Agent Downloads */}
        {agentInfo && agentInfo.sites.length > 0 && (
        <div className="mb-8 rounded-2xl overflow-hidden border-2 border-teal-200" style={{ background: 'linear-gradient(135deg, rgba(20,168,158,0.06) 0%, rgba(60,188,180,0.03) 100%)' }}>
          <div className="p-6 sm:p-8">
            <div className="flex items-start justify-between gap-6 flex-wrap">
              <div className="flex items-center gap-4">
                <div className="w-14 h-14 rounded-2xl flex items-center justify-center flex-shrink-0" style={{ background: 'linear-gradient(135deg, #14A89E 0%, #3CBCB4 100%)', boxShadow: '0 4px 14px rgba(60,188,180,0.35)' }}>
                  <svg className="w-7 h-7 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 3v2m6-2v2M9 19v2m6-2v2M5 9H3m2 6H3m18-6h-2m2 6h-2M7 19h10a2 2 0 002-2V7a2 2 0 00-2-2H7a2 2 0 00-2 2v10a2 2 0 002 2zM9 9h6v6H9V9z" />
                  </svg>
                </div>
                <div>
                  <h2 className="text-xl font-bold text-slate-900">Protect Your Devices</h2>
                  <p className="text-slate-600 mt-1">
                    Install the OsirisCare agent on your Mac and Windows workstations for real-time HIPAA compliance monitoring.
                  </p>
                </div>
              </div>
              <span className="px-3 py-1 bg-teal-100 text-teal-700 text-xs font-semibold rounded-full whitespace-nowrap">
                Agent v{agentInfo.agent_version}
              </span>
            </div>

            {/* Platform cards */}
            <div className="mt-6 grid grid-cols-1 md:grid-cols-2 gap-4">
              {/* macOS Card */}
              <div className="bg-white rounded-xl border border-slate-200 p-5">
                <div className="flex items-center gap-3 mb-3">
                  <div className="w-10 h-10 bg-slate-900 rounded-lg flex items-center justify-center">
                    <svg className="w-5 h-5 text-white" viewBox="0 0 24 24" fill="currentColor">
                      <path d="M18.71 19.5c-.83 1.24-1.71 2.45-3.05 2.47-1.34.03-1.77-.79-3.29-.79-1.53 0-2 .77-3.27.82-1.31.05-2.3-1.32-3.14-2.53C4.25 17 2.94 12.45 4.7 9.39c.87-1.52 2.43-2.48 4.12-2.51 1.28-.02 2.5.87 3.29.87.78 0 2.26-1.07 3.8-.91.65.03 2.47.26 3.64 1.98-.09.06-2.17 1.28-2.15 3.81.03 3.02 2.65 4.03 2.68 4.04-.03.07-.42 1.44-1.38 2.83M13 3.5c.73-.83 1.94-1.46 2.94-1.5.13 1.17-.34 2.35-1.04 3.19-.69.85-1.83 1.51-2.95 1.42-.15-1.15.41-2.35 1.05-3.11z"/>
                    </svg>
                  </div>
                  <div>
                    <h3 className="font-semibold text-slate-900">macOS Agent</h3>
                    <p className="text-xs text-slate-500">Universal (Intel + Apple Silicon)</p>
                  </div>
                </div>
                <p className="text-sm text-slate-600 mb-4">
                  12 compliance checks: FileVault, Gatekeeper, SIP, Firewall, Updates, Screen Lock, and more. Attempts automated remediation of 6 common issues; escalates others to your administrator.
                </p>
                <div className="text-xs text-slate-500 mb-4 flex items-center gap-2">
                  <svg className="w-4 h-4 text-teal-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                  Works with Jamf, Intune, Mosyle, Kandji, or manual install
                </div>
              </div>

              {/* Windows Card */}
              <div className="bg-white rounded-xl border border-slate-200 p-5">
                <div className="flex items-center gap-3 mb-3">
                  <div className="w-10 h-10 bg-blue-600 rounded-lg flex items-center justify-center">
                    <svg className="w-5 h-5 text-white" viewBox="0 0 24 24" fill="currentColor">
                      <path d="M3 12V6.75l8-1.25V12H3zm0 .5h8v6.5l-8-1.25V12.5zM11.5 12V5.25L21 3.5V12h-9.5zm0 .5H21v8.5l-9.5-1.75V12.5z"/>
                    </svg>
                  </div>
                  <div>
                    <h3 className="font-semibold text-slate-900">Windows Agent</h3>
                    <p className="text-xs text-slate-500">Windows 10/11 (64-bit)</p>
                  </div>
                </div>
                <p className="text-sm text-slate-600 mb-4">
                  8 compliance checks: BitLocker, Defender, Patches, Firewall, Screen Lock, WinRM, and more. Auto-deployed via GPO on domain networks.
                </p>
                <div className="text-xs text-slate-500 mb-4 flex items-center gap-2">
                  <svg className="w-4 h-4 text-teal-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                  Auto-deploys via Active Directory GPO when available
                </div>
              </div>
            </div>

            {/* Per-site download section */}
            <div className="mt-6">
              <h3 className="text-sm font-semibold text-slate-700 mb-3">Download for your sites</h3>
              <div className="space-y-3">
                {agentInfo.sites.map((site) => (
                  <div key={site.site_id} className="bg-white rounded-xl border border-slate-200">
                    <button
                      onClick={() => setAgentSiteExpanded(agentSiteExpanded === site.site_id ? null : site.site_id)}
                      className="w-full flex items-center justify-between p-4 hover:bg-slate-50 transition rounded-xl"
                    >
                      <div className="flex items-center gap-3">
                        <div className={`w-2.5 h-2.5 rounded-full ${site.appliances.length > 0 ? 'bg-green-500' : 'bg-amber-400'}`} />
                        <div className="text-left">
                          <p className="font-medium text-slate-900">{site.clinic_name}</p>
                          <p className="text-xs text-slate-500">
                            {site.appliances.length > 0
                              ? `Appliance: ${site.appliances[0].ip}`
                              : 'No appliance connected'}
                          </p>
                        </div>
                      </div>
                      <svg className={`w-5 h-5 text-slate-400 transition-transform ${agentSiteExpanded === site.site_id ? 'rotate-180' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                      </svg>
                    </button>

                    {agentSiteExpanded === site.site_id && (
                      <div className="border-t border-slate-200 p-4">
                        {site.appliances.length === 0 ? (
                          <div className="text-center py-4">
                            <p className="text-sm text-amber-600 font-medium">No appliance connected to this site yet.</p>
                            <p className="text-xs text-slate-500 mt-1">Contact your MSP to set up an appliance before deploying agents.</p>
                          </div>
                        ) : (
                          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
                            <button
                              onClick={() => downloadConfig(site.site_id)}
                              disabled={downloadingConfig === site.site_id}
                              className="flex items-center gap-2 px-4 py-3 bg-teal-600 text-white rounded-lg hover:bg-teal-700 transition text-sm font-medium disabled:opacity-50"
                            >
                              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                              </svg>
                              {downloadingConfig === site.site_id ? 'Downloading...' : 'Config File'}
                            </button>

                            <button
                              onClick={() => downloadInstallScript(site.site_id)}
                              className="flex items-center gap-2 px-4 py-3 bg-slate-800 text-white rounded-lg hover:bg-slate-900 transition text-sm font-medium"
                            >
                              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 9l3 3-3 3m5 0h3M5 20h14a2 2 0 002-2V6a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
                              </svg>
                              Install Script
                            </button>

                            <button
                              onClick={() => downloadMobileConfig(site.site_id)}
                              className="flex items-center gap-2 px-4 py-3 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition text-sm font-medium"
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
                              className="flex items-center gap-2 px-4 py-3 bg-white text-slate-700 border border-slate-300 rounded-lg hover:bg-slate-50 transition text-sm font-medium"
                            >
                              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                              </svg>
                              View Config
                            </a>
                          </div>
                        )}

                        {site.appliances.length > 0 && (
                          <div className="mt-4 p-3 bg-slate-50 rounded-lg">
                            <p className="text-xs font-medium text-slate-700 mb-2">Quick Install (macOS)</p>
                            <div className="flex items-center gap-2">
                              <code className="flex-1 text-xs bg-slate-900 text-green-400 p-2 rounded font-mono overflow-x-auto">
                                curl -sL {window.location.origin}/api/client/agent/install-script/{site.site_id} | sudo bash
                              </code>
                              <button
                                onClick={() => {
                                  navigator.clipboard.writeText(
                                    `curl -sL ${window.location.origin}/api/client/agent/install-script/${site.site_id} | sudo bash`
                                  );
                                }}
                                className="p-2 text-slate-500 hover:text-teal-600 rounded"
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
        <div className="bg-white rounded-2xl shadow-sm border border-slate-100">
          <div className="p-6 border-b border-slate-200">
            <h2 className="text-lg font-semibold text-slate-900">Your Sites</h2>
            <p className="text-sm text-slate-500 mt-1">Click a site to view detailed compliance status</p>
          </div>

          {dashboard?.sites.length === 0 ? (
            <div className="p-8 text-center">
              <svg className="w-12 h-12 text-slate-300 mx-auto mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
              </svg>
              <p className="text-slate-600">No sites configured yet</p>
            </div>
          ) : (
            <div className="divide-y divide-slate-200">
              {dashboard?.sites.map((site) => (
                <div
                  key={site.site_id}
                  className="flex items-center justify-between p-6 hover:bg-teal-50/50 transition"
                >
                  <div className="flex items-center gap-4">
                    <div className={`w-3 h-3 rounded-full ${site.status === 'active' ? 'bg-green-500' : 'bg-slate-400'}`} />
                    <div>
                      <h3 className="font-medium text-slate-900">{site.clinic_name}</h3>
                      <p className="text-sm text-slate-500">{site.site_id}</p>
                    </div>
                  </div>
                  <div className="flex items-center gap-8">
                    <div className="text-right">
                      <p className="text-sm font-medium text-slate-900">{site.evidence_count} checks</p>
                      <p className="text-xs text-slate-500">
                        {site.last_evidence
                          ? `Last: ${new Date(site.last_evidence).toLocaleDateString()}`
                          : 'No data yet'
                        }
                      </p>
                    </div>
                    <span className={`px-3 py-1 text-xs font-medium rounded-full ${site.tier === 'essential' ? 'bg-blue-100 text-blue-700' : site.tier === 'professional' ? 'bg-purple-100 text-purple-700' : 'bg-slate-100 text-slate-700'}`}>
                      {site.tier || 'Standard'}
                    </span>
                    <button
                      onClick={() => setDriftConfigSite({ id: site.site_id, name: site.clinic_name })}
                      className="text-xs text-teal-600 hover:text-teal-700 font-medium"
                    >
                      Security Checks
                    </button>
                    <Link
                      to="/client/evidence"
                      className="text-xs text-teal-600 hover:text-teal-700 font-medium"
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
            className="bg-white rounded-2xl shadow-sm border border-slate-100 p-6 hover:border-teal-300 hover:shadow-md transition-all"
          >
            <div className="flex items-center gap-4">
              <div className="w-12 h-12 bg-teal-100 rounded-lg flex items-center justify-center">
                <svg className="w-6 h-6 text-teal-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4" />
                </svg>
              </div>
              <div>
                <h3 className="font-semibold text-slate-900">HIPAA Compliance</h3>
                <p className="text-sm text-slate-500">Risk assessments, policies & more</p>
              </div>
            </div>
          </Link>

          <Link
            to="/client/evidence"
            className="bg-white rounded-2xl shadow-sm border border-slate-100 p-6 hover:border-teal-300 hover:shadow-md transition-all"
          >
            <div className="flex items-center gap-4">
              <div className="w-12 h-12 bg-teal-100 rounded-lg flex items-center justify-center">
                <svg className="w-6 h-6 text-teal-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                </svg>
              </div>
              <div>
                <h3 className="font-semibold text-slate-900">Evidence Archive</h3>
                <p className="text-sm text-slate-500">View compliance evidence</p>
              </div>
            </div>
          </Link>

          <Link
            to="/client/reports"
            className="bg-white rounded-2xl shadow-sm border border-slate-100 p-6 hover:border-teal-300 hover:shadow-md transition-all"
          >
            <div className="flex items-center gap-4">
              <div className="w-12 h-12 bg-purple-100 rounded-lg flex items-center justify-center">
                <svg className="w-6 h-6 text-purple-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 17v-2m3 2v-4m3 4v-6m2 10H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                </svg>
              </div>
              <div>
                <h3 className="font-semibold text-slate-900">Compliance Reports</h3>
                <p className="text-sm text-slate-500">Real-time & monthly PDFs</p>
              </div>
            </div>
          </Link>

          <Link
            to="/client/healing-logs"
            className="bg-white rounded-2xl shadow-sm border border-slate-100 p-6 hover:border-teal-300 hover:shadow-md transition-all"
          >
            <div className="flex items-center gap-4">
              <div className="w-12 h-12 bg-emerald-100 rounded-lg flex items-center justify-center">
                <svg className="w-6 h-6 text-emerald-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
                </svg>
              </div>
              <div>
                <h3 className="font-semibold text-slate-900">Healing Activity</h3>
                <p className="text-sm text-slate-500">Auto-healing logs & approvals</p>
              </div>
            </div>
          </Link>

          <Link
            to="/client/escalations"
            className="bg-white rounded-2xl shadow-sm border border-slate-100 p-6 hover:border-red-300 hover:shadow-md transition-all"
          >
            <div className="flex items-center gap-4">
              <div className="w-12 h-12 bg-red-100 rounded-lg flex items-center justify-center">
                <svg className="w-6 h-6 text-red-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4.5c-.77-.833-2.694-.833-3.464 0L3.34 16.5c-.77.833.192 2.5 1.732 2.5z" />
                </svg>
              </div>
              <div>
                <h3 className="font-semibold text-slate-900">Escalations</h3>
                <p className="text-sm text-slate-500">L3 tickets & routing</p>
              </div>
            </div>
          </Link>

          <Link
            to="/client/settings"
            className="bg-white rounded-2xl shadow-sm border border-slate-100 p-6 hover:border-teal-300 hover:shadow-md transition-all"
          >
            <div className="flex items-center gap-4">
              <div className="w-12 h-12 bg-slate-100 rounded-lg flex items-center justify-center">
                <svg className="w-6 h-6 text-slate-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                </svg>
              </div>
              <div>
                <h3 className="font-semibold text-slate-900">Account Settings</h3>
                <p className="text-sm text-slate-500">Manage users and preferences</p>
              </div>
            </div>
          </Link>

          <Link
            to="/client/help"
            className="bg-white rounded-2xl shadow-sm border border-slate-100 p-6 hover:border-teal-300 hover:shadow-md transition-all"
          >
            <div className="flex items-center gap-4">
              <div className="w-12 h-12 bg-blue-100 rounded-lg flex items-center justify-center">
                <svg className="w-6 h-6 text-blue-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
              </div>
              <div>
                <h3 className="font-semibold text-slate-900">Help & Docs</h3>
                <p className="text-sm text-slate-500">How-to guides and FAQs</p>
              </div>
            </div>
          </Link>
        </div>
        </>
        )}
      </main>

      {/* Footer */}
      <footer className="border-t border-slate-200/60 mt-12 py-6">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <p className="text-center text-sm text-slate-400">
            Powered by OsirisCare HIPAA Compliance Monitoring Platform
          </p>
          <p className="text-[10px] text-label-tertiary text-center mt-4 max-w-2xl mx-auto leading-relaxed">
            OsirisCare provides automated compliance monitoring and does not constitute legal advice, HIPAA certification, or a guarantee of regulatory compliance. All metrics represent point-in-time observations. Consult qualified compliance professionals for formal assessments.
          </p>
        </div>
      </footer>
    </div>
  );
};

export default ClientDashboard;
