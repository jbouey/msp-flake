import React from 'react';
import { NavLink } from 'react-router-dom';
import type { ClientOverview, HealthStatus } from '../../types';
import { getHealthColor } from '../../tokens/style-tokens';

interface User {
  username: string;
  role: 'admin' | 'operator';
  displayName: string;
}

interface SidebarProps {
  clients: ClientOverview[];
  onClientSelect?: (siteId: string) => void;
  selectedClient?: string | null;
  user?: User | null;
  onLogout?: () => void;
}

interface NavItem {
  path: string;
  label: string;
  icon: React.ReactNode;
  adminOnly?: boolean;
}

const navItems: NavItem[] = [
  {
    path: '/',
    label: 'Dashboard',
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6" />
      </svg>
    ),
  },
  {
    path: '/onboarding',
    label: 'Onboarding',
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M18 9v3m0 0v3m0-3h3m-3 0h-3m-2-5a4 4 0 11-8 0 4 4 0 018 0zM3 20a6 6 0 0112 0v1H3v-1z" />
      </svg>
    ),
  },
  {
    path: '/runbooks',
    label: 'Runbooks',
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
      </svg>
    ),
  },
  {
    path: '/learning',
    label: 'Learning',
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
      </svg>
    ),
  },
  {
    path: '/reports',
    label: 'Reports',
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 17v-2m3 2v-4m3 4v-6m2 10H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
      </svg>
    ),
  },
  {
    path: '/audit-logs',
    label: 'Audit Logs',
    adminOnly: true,
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
      </svg>
    ),
  },
];

const HealthDot: React.FC<{ status: HealthStatus }> = ({ status }) => {
  const color = getHealthColor(status);
  return (
    <span
      className="w-2 h-2 rounded-full flex-shrink-0"
      style={{ backgroundColor: color }}
    />
  );
};

export const Sidebar: React.FC<SidebarProps> = ({
  clients,
  onClientSelect,
  selectedClient,
  user,
  onLogout,
}) => {
  const filteredNavItems = navItems.filter(
    (item) => !item.adminOnly || user?.role === 'admin'
  );

  const userInitials = user?.displayName
    ? user.displayName
        .split(' ')
        .map((n) => n[0])
        .join('')
        .toUpperCase()
    : 'U';

  return (
    <aside className="glass-sidebar w-64 h-screen flex flex-col fixed left-0 top-0">
      {/* Logo */}
      <div className="p-6 border-b border-separator-light">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 bg-accent-primary rounded-ios-md flex items-center justify-center">
            <span className="text-white font-bold text-lg">M</span>
          </div>
          <div>
            <h1 className="font-semibold text-label-primary">Malachor</h1>
            <p className="text-xs text-label-tertiary">Central Command</p>
          </div>
        </div>
      </div>

      {/* Clients Section */}
      <div className="p-4 border-b border-separator-light">
        <h2 className="text-xs font-semibold text-label-tertiary uppercase tracking-wide mb-3">
          Clients
        </h2>
        <div className="space-y-1 max-h-48 overflow-y-auto">
          {clients.map((client) => (
            <button
              key={client.site_id}
              onClick={() => onClientSelect?.(client.site_id)}
              className={`
                w-full flex items-center gap-2 px-3 py-2 rounded-ios-sm text-left
                transition-colors duration-150
                ${selectedClient === client.site_id
                  ? 'bg-accent-tint text-accent-primary'
                  : 'text-label-primary hover:bg-separator-light'
                }
              `}
            >
              <HealthDot status={client.health.status} />
              <span className="text-sm truncate">{client.name}</span>
            </button>
          ))}
          {clients.length === 0 && (
            <p className="text-sm text-label-tertiary px-3 py-2">
              No clients yet
            </p>
          )}
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 p-4 overflow-y-auto">
        <h2 className="text-xs font-semibold text-label-tertiary uppercase tracking-wide mb-3">
          Pages
        </h2>
        <div className="space-y-1">
          {filteredNavItems.map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              className={({ isActive }) => `
                flex items-center gap-3 px-3 py-2 rounded-ios-sm
                transition-colors duration-150
                ${isActive
                  ? 'bg-accent-tint text-accent-primary'
                  : 'text-label-primary hover:bg-separator-light'
                }
              `}
            >
              {item.icon}
              <span className="text-sm font-medium">{item.label}</span>
            </NavLink>
          ))}
        </div>
      </nav>

      {/* Bottom section - User info */}
      <div className="p-4 border-t border-separator-light">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 bg-accent-primary rounded-full flex items-center justify-center">
            <span className="text-white text-sm font-medium">{userInitials}</span>
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium text-label-primary truncate">
              {user?.displayName || 'User'}
            </p>
            <p className="text-xs text-label-tertiary capitalize">{user?.role || 'Guest'}</p>
          </div>
          {onLogout && (
            <button
              onClick={onLogout}
              className="p-1.5 hover:bg-separator-light rounded-ios-sm transition-colors"
              title="Sign out"
            >
              <svg className="w-4 h-4 text-label-tertiary" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" />
              </svg>
            </button>
          )}
        </div>
      </div>
    </aside>
  );
};

export default Sidebar;
