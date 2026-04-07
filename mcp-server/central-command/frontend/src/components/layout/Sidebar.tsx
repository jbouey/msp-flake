import React from 'react';
import { NavLink } from 'react-router-dom';
import type { ClientOverview, HealthStatus } from '../../types';
import { useNotificationSummary } from '../../hooks';
import { OsirisCareLeaf } from '../shared';

interface User {
  username: string;
  role: 'admin' | 'operator' | 'readonly' | 'companion';
  displayName: string;
}

interface SidebarProps {
  clients: ClientOverview[];
  onClientSelect?: (siteId: string) => void;
  selectedClient?: string | null;
  user?: User | null;
  onLogout?: () => void;
  isOpen?: boolean;
  onClose?: () => void;
}

interface NavItem {
  path: string;
  label: string;
  icon: React.ReactNode;
  adminOnly?: boolean;
  section: 'operations' | 'admin';
}

const navItems: NavItem[] = [
  {
    path: '/',
    label: 'Dashboard',
    section: 'operations',
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6" />
      </svg>
    ),
  },
  {
    path: '/sites',
    label: 'Sites',
    section: 'operations',
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
      </svg>
    ),
  },
  {
    path: '/organizations',
    label: 'Organizations',
    section: 'operations',
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
      </svg>
    ),
  },
  {
    path: '/notifications',
    label: 'Notifications',
    section: 'operations',
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
      </svg>
    ),
  },
  {
    path: '/incidents',
    label: 'Incidents',
    section: 'operations',
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
      </svg>
    ),
  },
  {
    path: '/l4-queue',
    label: 'L4 Queue',
    section: 'operations',
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M3 4h13M3 8h9m-9 4h6m4 0l4-4m0 0l4 4m-4-4v12" />
      </svg>
    ),
  },
  {
    path: '/evidence',
    label: 'Evidence',
    section: 'operations',
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
      </svg>
    ),
  },
  {
    path: '/onboarding',
    label: 'Onboarding',
    section: 'operations',
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M18 9v3m0 0v3m0-3h3m-3 0h-3m-2-5a4 4 0 11-8 0 4 4 0 018 0zM3 20a6 6 0 0112 0v1H3v-1z" />
      </svg>
    ),
  },
  {
    path: '/partners',
    label: 'Partners',
    section: 'admin',
    adminOnly: true,
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z" />
      </svg>
    ),
  },
  {
    path: '/users',
    label: 'Users',
    section: 'admin',
    adminOnly: true,
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 4.354a4 4 0 110 5.292M15 21H3v-1a6 6 0 0112 0v1zm0 0h6v-1a6 6 0 00-9-5.197M13 7a4 4 0 11-8 0 4 4 0 018 0z" />
      </svg>
    ),
  },
  {
    path: '/runbooks',
    label: 'Runbooks',
    section: 'admin',
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
      </svg>
    ),
  },
  {
    path: '/runbook-config',
    label: 'Runbook Config',
    section: 'admin',
    adminOnly: true,
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
      </svg>
    ),
  },
  {
    path: '/learning',
    label: 'Learning',
    section: 'admin',
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
      </svg>
    ),
  },
  {
    path: '/rule-builder',
    label: 'Rule Builder',
    section: 'admin',
    adminOnly: true,
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M19.428 15.428a2 2 0 00-1.022-.547l-2.387-.477a6 6 0 00-3.86.517l-.318.158a6 6 0 01-3.86.517L6.05 15.21a2 2 0 00-1.806.547M8 4h8l-1 1v5.172a2 2 0 00.586 1.414l5 5c1.26 1.26.367 3.414-1.415 3.414H4.828c-1.782 0-2.674-2.154-1.414-3.414l5-5A2 2 0 009 10.172V5L8 4z" />
      </svg>
    ),
  },
  {
    path: '/cve-watch',
    label: 'CVE Watch',
    section: 'admin',
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
      </svg>
    ),
  },
  {
    path: '/compliance-library',
    label: 'Compliance Library',
    section: 'admin',
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
      </svg>
    ),
  },
  {
    path: '/fleet-updates',
    label: 'Fleet Updates',
    section: 'admin',
    adminOnly: true,
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" />
      </svg>
    ),
  },
  {
    path: '/vpn',
    label: 'VPN',
    section: 'admin',
    adminOnly: true,
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
      </svg>
    ),
  },
  {
    path: '/reports',
    label: 'Reports',
    section: 'admin',
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 17v-2m3 2v-4m3 4v-6m2 10H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
      </svg>
    ),
  },
  {
    path: '/system-health',
    label: 'System Health',
    section: 'admin',
    adminOnly: true,
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M5.25 14.25h13.5m-13.5 0a3 3 0 01-3-3m3 3a3 3 0 100 6h13.5a3 3 0 100-6m-16.5-3a3 3 0 013-3h13.5a3 3 0 013 3m-19.5 0a4.5 4.5 0 01.9-2.7L5.737 5.1a3.375 3.375 0 012.7-1.35h7.126c1.062 0 2.062.5 2.7 1.35l2.587 3.45a4.5 4.5 0 01.9 2.7m0 0a3 3 0 01-3 3m0 3h.008v.008h-.008v-.008zm0-6h.008v.008h-.008v-.008zm-3 6h.008v.008h-.008v-.008zm0-6h.008v.008h-.008v-.008z" />
      </svg>
    ),
  },
  {
    path: '/pipeline-health',
    label: 'Pipeline Health',
    section: 'admin',
    adminOnly: true,
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M21 8.25c0-2.485-2.099-4.5-4.688-4.5-1.935 0-3.597 1.126-4.312 2.733-.715-1.607-2.377-2.733-4.313-2.733C5.1 3.75 3 5.765 3 8.25c0 7.22 9 12 9 12s9-4.78 9-12z" />
      </svg>
    ),
  },
  {
    path: '/audit-logs',
    label: 'Audit Logs',
    section: 'admin',
    adminOnly: true,
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
      </svg>
    ),
  },
  {
    path: '/docs',
    label: 'Documentation',
    section: 'admin',
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
      </svg>
    ),
  },
  {
    path: '/logs',
    label: 'Logs',
    section: 'admin',
    adminOnly: true,
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M4 6h16M4 10h16M4 14h16M4 18h12" />
      </svg>
    ),
  },
  {
    path: '/settings',
    label: 'Settings',
    section: 'admin',
    adminOnly: true,
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
      </svg>
    ),
  },
];

const statusDotClass: Record<HealthStatus, string> = {
  critical: 'status-dot status-dot-critical',
  warning: 'status-dot status-dot-warning',
  healthy: 'status-dot status-dot-healthy status-dot-online',
};

const HealthDot: React.FC<{ status: HealthStatus }> = ({ status }) => {
  return <span className={statusDotClass[status] || 'status-dot status-dot-neutral'} />;
};

export const Sidebar: React.FC<SidebarProps> = ({
  clients,
  onClientSelect,
  selectedClient: _selectedClient,
  user,
  onLogout,
  isOpen = false,
  onClose,
}) => {
  const [adminOpen, setAdminOpen] = React.useState(false);
  const { data: notificationSummary } = useNotificationSummary();
  const unreadCount = notificationSummary?.unread || 0;

  const filteredNavItems = navItems.filter(
    (item) => !item.adminOnly || user?.role === 'admin'
  );
  const operationsItems = filteredNavItems.filter((item) => item.section === 'operations');
  const adminItems = filteredNavItems.filter((item) => item.section === 'admin');

  const userInitials = user?.displayName
    ? user.displayName
        .split(' ')
        .map((n) => n[0])
        .join('')
        .toUpperCase()
    : 'U';

  return (
    <>
      {/* Mobile overlay */}
      {isOpen && (
        <div
          className="fixed inset-0 bg-black/40 z-40 lg:hidden"
          onClick={onClose}
        />
      )}
      <aside className={`glass-sidebar w-64 h-dvh flex flex-col fixed left-0 top-0 z-50 transition-transform duration-300 ease-in-out lg:translate-x-0 ${isOpen ? 'translate-x-0' : '-translate-x-full'}`} style={{ height: '100dvh' }}>
      {/* Logo */}
      <div className="px-5 py-5 border-b border-separator-light">
        <div className="flex items-center gap-3">
          <div
            className="w-10 h-10 rounded-ios-md flex items-center justify-center"
            style={{
              background: 'linear-gradient(135deg, #14A89E 0%, #3CBCB4 100%)',
              boxShadow: '0 2px 12px rgba(60, 188, 180, 0.35)',
            }}
          >
            <OsirisCareLeaf className="w-5 h-5" color="white" />
          </div>
          <div>
            <h1 className="font-semibold text-label-primary tracking-tight">OsirisCare</h1>
            <p className="text-xs text-label-tertiary">Compliance Dashboard</p>
          </div>
        </div>
      </div>

      {/* Fleet Health Summary */}
      <div className="px-4 pt-4 pb-3">
        <h2 className="text-[10px] font-semibold text-label-tertiary uppercase tracking-wider mb-2.5 px-1">
          Fleet Status
        </h2>
        {(() => {
          const activeSites = clients.filter(c => c.status !== 'inactive');
          const online = activeSites.filter(c => c.appliance_count > 0 && c.health.status === 'healthy').length;
          const warning = activeSites.filter(c => c.appliance_count > 0 && c.health.status !== 'healthy').length;
          const notDeployed = activeSites.filter(c => c.appliance_count === 0).length;
          const needsAttention = activeSites.filter(c => c.appliance_count > 0 && c.health.status !== 'healthy');
          return (
            <>
              <button
                onClick={() => onClientSelect?.('')}
                className="w-full px-2.5 py-2 rounded-ios-sm hover:bg-fill-quaternary transition-colors text-left"
              >
                <div className="flex flex-col gap-1.5">
                  {online > 0 && (
                    <span className="flex items-center gap-2">
                      <span className="status-dot status-dot-healthy status-dot-online" />
                      <span className="text-xs font-medium text-health-healthy tabular-nums">{online}</span>
                      <span className="text-xs text-label-primary">Online</span>
                    </span>
                  )}
                  {warning > 0 && (
                    <span className="flex items-center gap-2">
                      <span className="status-dot status-dot-warning" />
                      <span className="text-xs font-medium text-health-warning tabular-nums">{warning}</span>
                      <span className="text-xs text-label-primary">Warning</span>
                    </span>
                  )}
                  {notDeployed > 0 && (
                    <span className="flex items-center gap-2">
                      <span className="status-dot status-dot-neutral" />
                      <span className="text-xs font-medium text-label-tertiary tabular-nums">{notDeployed}</span>
                      <span className="text-xs text-label-primary">Not Deployed</span>
                    </span>
                  )}
                  {clients.length === 0 && (
                    <span className="text-xs text-label-tertiary">No sites connected</span>
                  )}
                </div>
                <span className="text-[10px] text-label-secondary mt-1.5 block">{activeSites.length} total sites</span>
              </button>
              {/* Show sites that need attention (max 3) */}
              {needsAttention.length > 0 && (
                <div className="mt-1.5 space-y-0.5">
                  {needsAttention.slice(0, 3).map(client => (
                    <button
                      key={client.site_id}
                      onClick={() => onClientSelect?.(client.site_id)}
                      className="w-full flex items-center gap-2 px-2.5 py-1 rounded-ios-sm text-left hover:bg-fill-quaternary transition-colors"
                    >
                      <HealthDot status={client.health.status} />
                      <span className="text-xs text-label-secondary truncate">{client.name}</span>
                    </button>
                  ))}
                  {needsAttention.length > 3 && (
                    <p className="text-[10px] text-label-tertiary px-2.5 py-0.5">
                      +{needsAttention.length - 3} more need attention
                    </p>
                  )}
                </div>
              )}
            </>
          );
        })()}
      </div>

      {/* Navigation */}
      <nav className="flex-1 px-3 pt-3 pb-2 overflow-y-auto border-t border-separator-medium">
        {/* Operations section */}
        <h2 className="text-[10px] font-semibold text-label-tertiary uppercase tracking-wider mb-2 px-2">
          Operations
        </h2>
        <div className="space-y-0.5">
          {operationsItems.map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              end={item.path === '/'}
              onClick={onClose}
              className={({ isActive }) =>
                `nav-item ${isActive ? 'nav-item-active' : 'text-label-secondary'}`
              }
            >
              {item.icon}
              <span className="text-sm font-medium flex-1">{item.label}</span>
              {item.path === '/notifications' && unreadCount > 0 && (
                <span className="min-w-[18px] h-[18px] flex items-center justify-center text-[10px] font-semibold rounded-full bg-ios-red text-white px-1">
                  {unreadCount > 99 ? '99+' : unreadCount}
                </span>
              )}
            </NavLink>
          ))}
        </div>

        {/* Admin section (collapsible) */}
        {adminItems.length > 0 && (
          <div className="mt-4">
            <h2
              className="text-[10px] font-semibold text-label-tertiary uppercase tracking-wider mb-2 px-2 flex items-center justify-between cursor-pointer"
              onClick={() => setAdminOpen(!adminOpen)}
            >
              Admin
              <svg
                className={`w-3 h-3 transition-transform ${adminOpen ? 'rotate-180' : ''}`}
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
              >
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
              </svg>
            </h2>
            {adminOpen && (
              <div className="space-y-0.5">
                {adminItems.map((item) => (
                  <NavLink
                    key={item.path}
                    to={item.path}
                    end={item.path === '/'}
                    onClick={onClose}
                    className={({ isActive }) =>
                      `nav-item ${isActive ? 'nav-item-active' : 'text-label-secondary'}`
                    }
                  >
                    {item.icon}
                    <span className="text-sm font-medium flex-1">{item.label}</span>
                  </NavLink>
                ))}
              </div>
            )}
          </div>
        )}
      </nav>

      {/* Bottom section - User info */}
      <div className="px-4 py-3 border-t border-separator-light">
        <div className="flex items-center gap-3">
          <div
            className="w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0"
            style={{
              background: 'linear-gradient(135deg, #14A89E 0%, #3CBCB4 100%)',
            }}
          >
            <span className="text-white text-xs font-semibold">{userInitials}</span>
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium text-label-primary truncate">
              {user?.displayName || 'User'}
            </p>
            <p className="text-[11px] text-label-tertiary capitalize">{user?.role || 'Guest'}</p>
          </div>
          {onLogout && (
            <button
              onClick={onLogout}
              className="p-1.5 hover:bg-fill-tertiary rounded-ios-sm transition-colors focus-visible:ring-2 focus-visible:ring-accent-primary focus-visible:ring-offset-2"
              title="Sign out"
              aria-label="Sign out"
            >
              <svg className="w-4 h-4 text-label-tertiary" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" />
              </svg>
            </button>
          )}
        </div>
      </div>
    </aside>
    </>
  );
};

export default Sidebar;
